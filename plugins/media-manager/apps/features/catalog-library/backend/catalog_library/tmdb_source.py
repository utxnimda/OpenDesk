"""TMDB TV/season adapter. Only explicit sync uses the network.

Credentials never enter URLs, returned snapshots or error messages. Provider
payloads are adapted to the same candidates consumed by the shared import flow.
"""
from __future__ import annotations

from datetime import datetime, timezone
from http.client import HTTPException
import os
from pathlib import Path
import re
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from work_catalog_yaml.layout import feature_config_path
from work_catalog_yaml.operation_progress import operation_context, report_progress
from work_catalog_yaml.yaml_io import load_yaml_string

from . import assets
from .network import failure_reason

API_ROOT = "https://api.themoviedb.org/3"
ENV_TOKEN = "TMDB_API_READ_ACCESS_TOKEN"
MAX_CONFIG_BYTES = 64 * 1024
_PATH = re.compile(
    r"/(?:search/tv|person/[1-9][0-9]{0,9}|movie/[1-9][0-9]{0,9}/images|"
    r"tv/[1-9][0-9]{0,9}(?:/images|/season/[0-9]{1,3}"
    r"(?:/aggregate_credits|/episode/[1-9][0-9]{0,4}/(?:credits|images))?)?)\Z"
)
_POSTER_PATH = re.compile(r"/[A-Za-z0-9_-]{1,160}\.(?:jpg|jpeg|png|webp)\Z")


def _token(config_path=None):
    """Read a server-only token, without surfacing YAML parser secret excerpts."""
    value = os.environ.get(ENV_TOKEN, "").strip()
    if not value:
        path = Path(config_path) if config_path is not None else feature_config_path("catalog-library").with_name("providers.local.yaml")
        try:
            if path.exists():
                if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) or not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
                    raise ValueError("TMDB 服务端凭据文件必须为不超过 64 KiB 的普通文件")
                with path.open("rb") as stream:
                    raw = stream.read(MAX_CONFIG_BYTES + 1)
                if len(raw) > MAX_CONFIG_BYTES:
                    raise ValueError("TMDB 服务端凭据文件过大")
                try:
                    config = load_yaml_string(raw.decode("utf-8-sig"))
                except Exception:
                    raise ValueError("TMDB 服务端凭据文件不是有效 YAML；请检查配置格式（日志不会输出凭据内容）") from None
                if not isinstance(config, dict) or not isinstance(config.get("tmdb"), dict):
                    raise ValueError("TMDB 服务端配置缺少 tmdb 节点")
                value = config["tmdb"].get("read_access_token", "")
        except OSError:
            raise ValueError("无法读取 TMDB 服务端凭据文件；请检查文件权限") from None
    if not isinstance(value, str) or len(value) > 4096 or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise ValueError("TMDB API Read Access Token 格式无效；请检查服务端配置")
    value = value.strip()
    if not value:
        raise ValueError("尚未配置 TMDB API Read Access Token。请在服务端环境变量 TMDB_API_READ_ACCESS_TOKEN 或 config/features/catalog-library/providers.local.yaml 的 tmdb.read_access_token 中填写；不要将凭据写入作品数据")
    return value


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("TMDB API 返回重定向，已拒绝转发凭据到其他地址")


def _request_json(path, *, query=None, deadline=None):
    from . import providers as common
    if not isinstance(path, str) or not _PATH.fullmatch(path):
        raise ValueError("不支持的 TMDB API 路径")
    deadline = deadline if deadline is not None else time.monotonic() + common.REQUEST_TOTAL_TIMEOUT
    request = Request(API_ROOT + path + ("?" + urlencode(query) if query else ""), headers={
        "Authorization": "Bearer " + _token(), "User-Agent": common.USER_AGENT,
        "Accept": "application/json", "Accept-Encoding": "identity"}, method="GET")
    for attempt in range(2):
        with operation_context(stage="TMDB 来源请求", action=path):
            try:
                with build_opener(_NoRedirects()).open(request, timeout=common._remaining(deadline)) as response:
                    length = response.headers.get("Content-Length")
                    if length and (not length.isdecimal() or int(length) > common.MAX_RESPONSE_BYTES):
                        raise ValueError("TMDB API 响应超出安全大小限制")
                    if response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
                        raise ValueError("TMDB API 响应包含不支持的内容编码")
                    data = common._read_response(response, deadline)
                    if length and len(data) != int(length):
                        raise ValueError("TMDB API 响应未完整接收")
                    return common._load_json(data)
            except HTTPError as exc:
                status = exc.code
                retry_after = exc.headers.get("Retry-After") if exc.headers is not None else None
                exc.close()
                if attempt == 0 and (status == 429 or 500 <= status <= 599):
                    report_progress(f"TMDB 暂不可用（HTTP {status}），重试 1 次")
                    common._wait_retry(common._retry_delay(retry_after), deadline)
                    continue
                suffix = "；请检查服务端 TMDB API Read Access Token" if status in {401, 403} else ""
                raise ValueError(f"TMDB 请求失败（HTTP {status}）{suffix}；未修改作品数据") from None
            except (URLError, TimeoutError, socket.timeout, socket.gaierror, ssl.SSLError, ConnectionError, HTTPException) as exc:
                # The exception text can contain request state. The common
                # classifier emits only known, non-sensitive failure labels.
                reason = failure_reason(exc)
                if attempt == 0:
                    report_progress(f"TMDB 网络请求失败：{reason}；重试 1 次")
                    common._wait_retry(0.25, deadline)
                    continue
                raise ValueError(f"TMDB 连接失败或超时：{reason}；未修改作品数据") from None


def parse_season_number(value=1):
    if type(value) is not int or not 0 <= value <= 999:
        raise ValueError("TMDB season_number 必须为 0～999 的整数（0 表示特别篇）")
    return value


def source_url(subject_id, season=None):
    from .providers import _positive_id
    url = f"https://www.themoviedb.org/tv/{_positive_id(subject_id)}"
    return url if season is None else f"{url}/season/{parse_season_number(season)}"


def _summary(row):
    from .providers import _optional_text, _positive_id
    identity = _positive_id(row.get("id"))
    return {"id": identity, "name": _optional_text(row.get("original_name"), 1000) or _optional_text(row.get("name"), 1000),
            "name_cn": _optional_text(row.get("name"), 1000), "date": _optional_text(row.get("first_air_date"), 40),
            "type": "tv", "cover_url": "", "source_url": source_url(identity)}


def search(query, *, offset=0, deadline=None):
    page = offset // 20 + 1
    result = _request_json("/search/tv", query={"query": query, "include_adult": "false", "language": "zh-CN", "page": page}, deadline=deadline)
    if not isinstance(result, dict) or not isinstance(result.get("results"), list) or len(result["results"]) > 20:
        raise ValueError("TMDB 搜索响应结构异常")
    if any(not isinstance(row, dict) for row in result["results"]):
        raise ValueError("TMDB 搜索响应含无效作品记录")
    total = result.get("total_results", len(result["results"]))
    if type(total) is not int or total < 0:
        raise ValueError("TMDB 搜索总数无效")
    rows = [_summary(row) for row in result["results"] if not row.get("adult")]
    return {"provider": "tmdb", "results": rows, "total": total, "limit": 20, "offset": (page - 1) * 20}


def _season_rows(season, subject_id, number):
    from .providers import MAX_EPISODES, _positive_id
    if not isinstance(season, dict) or season.get("season_number") != number or not isinstance(season.get("episodes"), list) or len(season["episodes"]) > MAX_EPISODES:
        raise ValueError("TMDB 季信息或章节列表不完整；未采用部分章节")
    seen, numbers = set(), set()
    for row in season["episodes"]:
        if not isinstance(row, dict):
            raise ValueError("TMDB 章节结构无效")
        identity = _positive_id(row.get("id"), "episode_id")
        episode = row.get("episode_number")
        if type(episode) is not int or not 1 <= episode <= 99999 or identity in seen or episode in numbers or row.get("season_number", number) != number or row.get("show_id", subject_id) != subject_id:
            raise ValueError("TMDB 章节 ID、季编号或集数重复/不一致；未采用部分章节")
        seen.add(identity)
        numbers.add(episode)
    return season["episodes"]


def _episode(row, translated=None):
    from .providers import _date, _optional_text
    translated = translated or {}
    result = {"id": f"tmdb:{row['id']}", "source_id": row["id"], "type": 0,
              "ep": row["episode_number"], "sort": row["episode_number"],
              "title": _optional_text(row.get("name"), 1000),
              "name_cn": _optional_text(translated.get("name"), 1000),
              "air_date": _date(row.get("air_date")), "duration": ""}
    runtime = row.get("runtime")
    if type(runtime) is not int or not 0 < runtime <= 1440:
        runtime = translated.get("runtime")
    if type(runtime) is int and 0 < runtime <= 1440:
        result["duration_seconds"] = runtime * 60
        result["duration"] = f"{runtime}m"
    return result


def fetch_snapshot(subject_id, *, season_number=1, deadline=None, cover_only=False):
    from . import providers as common
    number = parse_season_number(season_number)
    common._positive_id(subject_id)
    report_progress(f"正在获取 TMDB 电视剧 {subject_id} 第 {number} 季")
    subject = _request_json(f"/tv/{subject_id}", query={"language": "zh-CN"}, deadline=deadline)
    if not isinstance(subject, dict) or subject.get("id") != subject_id or subject.get("adult"):
        raise ValueError("TMDB 作品 ID 不一致、作品不存在或不属于公开范围")
    summary = _summary(subject)
    season = _request_json(f"/tv/{subject_id}/season/{number}", query={"language": "zh-CN"}, deadline=deadline)
    if not isinstance(season, dict) or type(season.get("season_number")) is not int or season["season_number"] != number:
        raise ValueError("TMDB 季编号与请求不一致")
    # A poster refresh must not depend on unrelated chapter field quality.
    rows_cn = [] if cover_only else _season_rows(season, subject_id, number)
    original_language = subject.get("original_language")
    original_season = None
    warnings = []
    rows = rows_cn
    if not cover_only and isinstance(original_language, str) and re.fullmatch(r"[a-z]{2}", original_language) and original_language != "zh":
        try:
            original_season = _request_json(f"/tv/{subject_id}/season/{number}", query={"language": original_language}, deadline=deadline)
            original_rows = _season_rows(original_season, subject_id, number)
            if {row["id"]: row["episode_number"] for row in original_rows} != {row["id"]: row["episode_number"] for row in rows_cn}:
                raise ValueError("中文与原文章节列表 ID 或集数不一致")
            rows = original_rows
        except (ValueError, OSError, RuntimeError) as exc:
            warnings.append(f"TMDB 原文章节读取失败：{exc}；本次保留可用的中文章节信息。")
    by_id = {row["id"]: row for row in rows_cn}
    url = source_url(subject_id, number)
    summary.update({"source_url": url, "season_number": number, "season_name": common._optional_text(season.get("name"), 1000),
                    "date": common._optional_text(season.get("air_date"), 40)})
    candidates = {"name": summary["name"], "metadata.aliases": [name for name in [summary["name_cn"]] if name and name != summary["name"]],
                  "metadata.summary": common._optional_text(season.get("overview"), 100000) or common._optional_text(subject.get("overview"), 100000),
                  "air_date_start": common._date(season.get("air_date")),
                  "metadata.episodes": [] if cover_only else [_episode(row, by_id.get(row["id"])) for row in rows],
                  "metadata.cover": None}
    if not cover_only and not candidates["air_date_start"]:
        warnings.append("TMDB 当前季缺少有效首播日期；没有用整个系列的日期替代。")
    poster = season.get("poster_path") or subject.get("poster_path")
    return {"schema_version": 1, "provider": "tmdb", "subject_id": subject_id, "season_number": number,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source_url": url,
            "subject": summary, "raw": {"series": subject, "season": season, "original_season": original_season},
            "source_metadata": {"media_type": "tv", "season_number": number}, "candidates": candidates,
            "poster_path": poster, "warnings": warnings}


def _image_url(value):
    if not isinstance(value, str) or not _POSTER_PATH.fullmatch(value):
        raise ValueError("TMDB 没有有效的海报路径")
    return "https://image.tmdb.org/t/p/w500" + value


def validate_cover_url(url):
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname == "image.tmdb.org" and parsed.port in {None, 443}
                 and parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment
                 and parsed.path.startswith("/t/p/w500/") and _POSTER_PATH.fullmatch(parsed.path[len("/t/p/w500"):]))
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("封面地址不属于允许的 TMDB HTTPS 图片 CDN 路径")
    return url


def acquire_cover(snapshot, *, user_agent, root=None, deadline=None):
    url = _image_url(snapshot.get("poster_path"))
    data, mime = assets.download_image(url, user_agent=user_agent, validate_url=validate_cover_url, deadline=deadline, label="TMDB 封面")
    return {**assets.store_image(data, mime, root=root), "provider": "tmdb", "external_id": str(snapshot["subject_id"]), "source_url": url}
