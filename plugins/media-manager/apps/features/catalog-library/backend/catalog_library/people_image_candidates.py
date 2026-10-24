"""Explicit TMDB stills for manual character-avatar selection.

This adapter has no people/work repository writer and performs no face or role
matching. The route layer must sign returned candidates against the person and
revision, then verify that signature before calling ``download_candidate``.
Only the visible page's w500 images are cached; browser image URLs are local.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from copy import deepcopy
import hashlib
import math
import re
import time
from urllib.parse import urlsplit

from people_catalog.model import validate_avatar_reference
from work_catalog_yaml.operation_progress import report_progress

from . import assets, people_assets, providers, tmdb_source

PAGE_SIZE = 8
MAX_CANDIDATES = 30
MAX_PAGES = math.ceil(MAX_CANDIDATES / PAGE_SIZE)
REQUEST_BUDGET = 60
_PAGE_PATH = re.compile(
    r"/(?P<kind>tv|movie)/(?P<id>[1-9][0-9]{0,9})(?:-[^/?#]{1,200})?"
    r"(?:/season/(?P<season>[0-9]{1,3})/episode/(?P<episode>[1-9][0-9]{0,4}))?/?\Z"
)


def _number(value, label, minimum, maximum):
    if value is None or value == "":
        return None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,5}", value):
        value = int(value)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"TMDB {label} 必须为 {minimum}～{maximum} 的整数")
    return value


def parse_target(value, *, media_type="tv", season_number=None, episode_number=None):
    """Normalize a numeric ID or an official TV/movie (optionally episode) URL."""
    if not isinstance(media_type, str) or media_type not in {"tv", "movie"}:
        raise ValueError("TMDB 剧照作品类型只能为 tv 或 movie")
    season = _number(season_number, "季编号", 0, 999)
    episode = _number(episode_number, "集编号", 1, 99999)
    if isinstance(value, str) and "://" in value:
        try:
            parsed = urlsplit(value)
            valid = (len(value) <= 2048 and not any(ord(char) <= 32 for char in value)
                     and parsed.scheme == "https" and parsed.hostname in {"www.themoviedb.org", "themoviedb.org"}
                     and parsed.port in {None, 443} and parsed.username is None and parsed.password is None
                     and not parsed.query and not parsed.fragment)
            match = _PAGE_PATH.fullmatch(parsed.path) if valid else None
        except ValueError:
            match = None
        if not match:
            raise ValueError("请输入 TMDB 作品 ID 或官方作品/单集 HTTPS 页面地址")
        media_type, value = match["kind"], match["id"]
        if match["season"] is not None:
            url_season, url_episode = int(match["season"]), int(match["episode"])
            if season is not None and season != url_season or episode is not None and episode != url_episode:
                raise ValueError("填写的季/集编号与 TMDB 页面地址不一致")
            season, episode = url_season, url_episode
    identity = providers._positive_id(value, "剧照作品 ID")
    if media_type == "movie" and (season is not None or episode is not None):
        raise ValueError("TMDB 电影剧照不接受季/集编号")
    if (season is None) != (episode is None):
        raise ValueError("获取单集剧照须同时填写季和集；整部作品剧照请将两项留空（季接口仅有海报）")
    scope = f"/{media_type}/{identity}"
    result = {"provider": "tmdb", "media_type": media_type, "subject_id": identity}
    if season is not None:
        result.update(season_number=season, episode_number=episode)
        scope += f"/season/{season}/episode/{episode}"
    return {**result, "source_url": "https://www.themoviedb.org" + scope,
            "api_path": scope + "/images", "image_kind": "still" if episode is not None else "backdrop"}


def _target(value):
    if not isinstance(value, dict):
        raise ValueError("TMDB 剧照目标无效")
    result = parse_target(value.get("subject_id"), media_type=value.get("media_type"),
                          season_number=value.get("season_number"), episode_number=value.get("episode_number"))
    if value != result:
        raise ValueError("TMDB 剧照目标与作品范围不一致；请重新获取候选")
    return result


def _candidate_id(target, file_path):
    return hashlib.sha256(("tmdb-still\0" + target["api_path"] + "\0" + file_path).encode("utf-8")).hexdigest()


def _candidate(target, row):
    if not isinstance(row, dict):
        raise ValueError("TMDB 剧照条目不是对象")
    file_path = row.get("file_path")
    if not isinstance(file_path, str) or not tmdb_source._POSTER_PATH.fullmatch(file_path):
        raise ValueError("TMDB 剧照文件路径无效")
    width, height = row.get("width"), row.get("height")
    if type(width) is not int or type(height) is not int:
        raise ValueError("TMDB 剧照尺寸无效")
    assets._image_dimensions((width, height), "TMDB 剧照")
    return {"id": _candidate_id(target, file_path), "file_path": file_path,
            "width": width, "height": height, "kind": target["image_kind"]}


def _acquire(target, row, *, people_root=None, deadline=None):
    url = tmdb_source._image_url(row["file_path"])
    previous = row.get("thumbnail")
    if previous:
        try:
            validate_avatar_reference(previous)
            if previous["source_url"] == url:
                people_assets.read_avatar(previous["asset_id"], root=people_root)
                return deepcopy(previous)
        except (ValueError, OSError):
            pass
    data, mime = assets.download_image(url, user_agent=providers.USER_AGENT,
        validate_url=tmdb_source.validate_cover_url, deadline=deadline, label="TMDB 剧照")
    info = assets.store_image(data, mime, root=people_assets.asset_root(people_root), label="TMDB 剧照")
    return {"asset_id": info["sha256"] + "." + info["extension"], "source_url": url}


def _cache_warning(row, exc):
    # Asset adapters expose bounded messages. Never echo arbitrary transport or
    # OS exception text (which may contain a proxy URL/credential).
    reason = str(exc) if isinstance(exc, ValueError) and str(exc).startswith(("TMDB 剧照", "人物图片", "图片")) else f"图片缓存失败（{type(exc).__name__}）"
    return f"{row['file_path']}：{reason[:300]}；未生成可选择的缩略图"


def fetch_candidates(target, *, page=1, people_root=None, deadline=None):
    """Return up to eight local thumbnails from the first thirty valid stills."""
    target = _target(target)
    if type(page) is not int or not 1 <= page <= MAX_PAGES:
        raise ValueError(f"TMDB 剧照页码必须为 1～{MAX_PAGES} 的整数")
    deadline = min(deadline, time.monotonic() + REQUEST_BUDGET) if deadline is not None else time.monotonic() + REQUEST_BUDGET
    report_progress("正在获取 TMDB 剧照候选")
    raw = tmdb_source._request_json(target["api_path"], deadline=deadline)
    key = "stills" if target["image_kind"] == "still" else "backdrops"
    if not isinstance(raw, dict) or not isinstance(raw.get(key), list):
        raise ValueError("TMDB 剧照响应结构无效；未获取头像候选")
    # Episode images identify the episode, not the parent series. The trusted
    # requested endpoint remains the scope; series/movie responses identify it.
    providers._positive_id(raw.get("id"), "剧照响应 ID")
    if key == "backdrops" and raw["id"] != target["subject_id"]:
        raise ValueError("TMDB 剧照响应作品 ID 不一致；未获取头像候选")
    pool, seen, warnings, invalid, limited = [], set(), [], 0, False
    for source in raw[key]:
        try:
            row = _candidate(target, source)
        except ValueError:
            invalid += 1
            continue
        if row["file_path"] in seen:
            continue
        if len(pool) == MAX_CANDIDATES:
            limited = True
            break
        seen.add(row["file_path"])
        pool.append(row)
    if invalid:
        warnings.append(f"已跳过 {invalid} 个路径或尺寸无效的 TMDB 剧照条目")
    visible = pool[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    completed = 0
    if visible:
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="nimda-stills") as executor:
            jobs = {executor.submit(copy_context().run, _acquire, target, row,
                     people_root=people_root, deadline=deadline): row for row in visible}
            for future in as_completed(jobs):
                row = jobs[future]
                try:
                    row["thumbnail"] = future.result()
                    row["thumbnail_url"] = people_assets.avatar_url(row["thumbnail"])
                    row["selectable"] = True
                except (ValueError, OSError, RuntimeError) as exc:
                    row.update(thumbnail={}, thumbnail_url="", selectable=False, warning=_cache_warning(row, exc))
                    warnings.append(row["warning"])
                completed += 1
                report_progress(f"已缓存剧照 {completed}/{len(visible)}", completed=completed, total=len(visible), unit="图片")
    return {"target": target, "page": page, "page_size": PAGE_SIZE, "total": len(pool),
            "has_more": page * PAGE_SIZE < len(pool), "limited": limited,
            "candidates": visible, "warnings": warnings}


def download_candidate(target, candidate, *, people_root=None, deadline=None):
    """Materialize a route-verified signed candidate, without applying it.

    Candidate IDs validate consistency, not authorization: the caller must
    verify its person/revision-bound token, never accept unsigned request rows.
    """
    target = _target(target)
    normalized = _candidate(target, candidate)
    if candidate.get("id") != normalized["id"] or candidate.get("kind") != normalized["kind"]:
        raise ValueError("TMDB 剧照候选与已预览的作品/图片不一致；请重新获取候选")
    avatar = _acquire(target, candidate, people_root=people_root, deadline=deadline)
    evidence = {"provider": "tmdb-still", "external_id": f"{target['media_type']}:{target['subject_id']}",
                "page_url": target["source_url"], "image_kind": target["image_kind"],
                "file_path": normalized["file_path"]}
    for key in ("season_number", "episode_number"):
        if key in target:
            evidence[key] = str(target[key])
    return {"avatar": avatar, "evidence": evidence, "warnings": []}
