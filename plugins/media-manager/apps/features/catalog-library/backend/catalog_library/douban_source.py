"""Bounded public Douban movie-page supplements, never an authenticated API.

Exact IDs/links and bounded official website searches are accepted. No cookies,
browser impersonation, redirect following, challenges or external APIs.
All parsed values remain preview candidates with the original HTML as evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
from http.client import HTTPException
import json
import re
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from work_catalog_yaml.operation_progress import operation_context, report_progress

from .network import failure_reason
from .ratings import validate_rating

PROVIDER = "douban"
USER_AGENT = "utxnimda/NimdaClaw/1.0 (https://github.com/utxnimda/NimdaClaw)"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_JSONLD_BYTES = 512 * 1024
REQUEST_TIMEOUT = 12
TOTAL_TIMEOUT = 25
_ID = re.compile(r"[1-9][0-9]{0,11}\Z")
_SUBJECT = re.compile(r"/subject/([1-9][0-9]{0,11})/?\Z")
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_BLOCKED = "豆瓣要求登录、安全验证或限制公开访问，已停止获取；请在浏览器中自行查看，不会尝试绕过验证"


def _positive_id(value):
    if type(value) not in {int, str} or not _ID.fullmatch(str(value)):
        raise ValueError("豆瓣 subject_id 必须为正整数 ID")
    return int(value)


def source_url(subject_id):
    return f"https://movie.douban.com/subject/{_positive_id(subject_id)}/"


def _safe_parts(value, *, relative=False, hosts=("movie.douban.com",)):
    if not isinstance(value, str) or not value or len(value) > 2048 or "\\" in value or any(ord(char) < 33 for char in value):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.query or parsed.fragment:
        return None
    if relative and value.startswith("/") and not value.startswith("//") and not parsed.netloc and not parsed.scheme:
        return parsed
    if parsed.scheme != "https" or parsed.netloc.lower() not in hosts:
        return None
    return parsed


def _subject_link(value, *, relative=False):
    parsed = _safe_parts(value, relative=relative)
    match = _SUBJECT.fullmatch(parsed.path) if parsed else None
    return int(match[1]) if match else None


def _target(value):
    if isinstance(value, str) and value.strip() == value and _ID.fullmatch(value):
        return int(value)
    identity = _subject_link(value)
    if identity is None:
        raise ValueError("豆瓣作品 ID 或条目链接无效")
    return identity


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("豆瓣公开页面请求超过总时限；作品数据未修改")
    return min(REQUEST_TIMEOUT, remaining)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("豆瓣公开页面返回重定向，已停止获取（可能需要登录或安全验证）；不会访问跳转地址")


def _request_html(subject_id, *, deadline=None):
    return _request_public_html(source_url(subject_id), deadline=deadline)


def _request_public_html(url, *, deadline=None):
    """One fixed-origin public GET; redirects and authenticated APIs stay closed."""
    from . import douban_search
    if not isinstance(url, str) or not (_subject_link(url) or douban_search.is_search_url(url)):
        raise ValueError("不支持的豆瓣公开页面地址；仅允许作品和标准官网搜索路径，不读取人物详情")
    deadline = min(time.monotonic() + TOTAL_TIMEOUT, deadline) if deadline is not None else time.monotonic() + TOTAL_TIMEOUT
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html", "Accept-Encoding": "identity"}, method="GET")
    with operation_context(stage="豆瓣公开来源请求", action="读取已选择的公开页面"):
        try:
            with build_opener(_NoRedirects()).open(request, timeout=_remaining(deadline)) as response:
                if getattr(response, "status", 200) in {401, 403, 429}:
                    raise ValueError(_BLOCKED)
                if hasattr(response, "geturl") and response.geturl() != url:
                    raise ValueError("豆瓣返回其他地址，已停止获取，不会继续访问安全验证页")
                content_type = response.headers.get("Content-Type", "text/html").split(";", 1)[0].strip().lower()
                if content_type not in {"text/html", "application/xhtml+xml"}:
                    raise ValueError("豆瓣响应不是公开 HTML 条目；未采用任何字段")
                if response.headers.get("Content-Encoding", "identity").lower() not in {"identity", ""}:
                    raise ValueError("豆瓣响应使用不支持的内容编码，已停止获取")
                length = response.headers.get("Content-Length")
                if length and (len(length) > 16 or not length.isdecimal() or int(length) > MAX_RESPONSE_BYTES):
                    raise ValueError("豆瓣公开页面响应超过安全大小限制")
                chunks, size = [], 0
                read = getattr(response, "read1", response.read)
                while True:
                    _remaining(deadline)
                    chunk = read(min(65536, MAX_RESPONSE_BYTES + 1 - size))
                    _remaining(deadline)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ValueError("豆瓣公开页面响应超过安全大小限制")
                    chunks.append(chunk)
                if length and size != int(length):
                    raise ValueError("豆瓣公开页面响应未完整接收；未采用任何字段")
            try:
                return b"".join(chunks).decode("utf-8-sig")
            except UnicodeError:
                raise ValueError("豆瓣公开页面不是有效 UTF-8 HTML") from None
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if code in {401, 403, 429}:
                raise ValueError(f"{_BLOCKED}（HTTP {code}）") from None
            raise ValueError(f"豆瓣公开页面请求失败（HTTP {code}）；作品数据未修改") from None
        except (URLError, TimeoutError, socket.timeout, socket.gaierror, ssl.SSLError, ConnectionError, HTTPException) as exc:
            raise ValueError(f"豆瓣连接失败：{failure_reason(exc)}；请检查网络或代理，作品数据未修改") from None


def _text(value, maximum=1000):
    return value.strip() if isinstance(value, str) and len(value) <= maximum else ""


def _chinese(value):
    return bool(re.search(r"[\u3400-\u9fff]", value)) and not re.search(r"[\u3040-\u30ff\uac00-\ud7af]", value)


class _Page(HTMLParser):
    """Only fixed work identity and rating JSON-LD are imported; titles identify search results."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.scripts, self.canonicals, self.meta = [], [], [], {}
        self.title = []
        self.script = None
        self.blocked = False
        self.nodes = 0

    def _inside(self, tag=None, identity=None):
        return any((tag is None or name == tag) and (identity is None or attrs.get("id") == identity) for name, attrs in self.stack)

    def handle_starttag(self, tag, attributes):
        self.nodes += 1
        if self.nodes > 50000 or len(self.stack) > 128:
            raise ValueError("豆瓣 HTML 结构超过安全限制")
        attrs = {key: value if isinstance(value, str) else "" for key, value in attributes}
        if tag == "link" and "canonical" in attrs.get("rel", "").lower().split():
            self.canonicals.append(attrs.get("href", ""))
        if tag == "meta":
            key = attrs.get("property", attrs.get("name", "")).lower()
            if self._inside(tag="head") and key in {"og:title", "description", "og:description"}:
                self.meta.setdefault(key, []).append(attrs.get("content", ""))
            if attrs.get("http-equiv", "").lower() == "refresh":
                self.blocked = True
        if tag == "form" and re.search(r"(?:sec\.douban\.com|accounts\.douban\.com|/login|/captcha)", attrs.get("action", ""), re.I):
            self.blocked = True
        if tag == "input" and re.search(r"captcha|captcha-solution|verifycode", attrs.get("name", ""), re.I):
            self.blocked = True
        if tag == "script":
            self.script = {"text": [], "jsonld": attrs.get("type", "").lower() == "application/ld+json", "head": self._inside(tag="head")}
        if tag not in _VOID:
            self.stack.append((tag, attrs))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            content = "".join(self.script["text"])
            if re.search(r"(?:window\.)?location(?:\.href|\.replace)?\s*(?:=|\()\s*['\"](?:https?:)?//(?:sec|accounts)\.douban\.com", content, re.I):
                self.blocked = True
            if self.script["jsonld"]:
                if len(self.scripts) >= 32 or len(content.encode("utf-8")) > MAX_JSONLD_BYTES:
                    raise ValueError("豆瓣结构化数据超过安全大小限制")
                self.scripts.append((content, self.script["head"]))
            self.script = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, data):
        if self.script is not None:
            self.script["text"].append(data)
            return
        if self._inside(tag="style"):
            return
        if self._inside(tag="title"):
            self.title.append(data)


def _jsonld(content):
    def reject(_value):
        raise ValueError("豆瓣结构化数据包含非有限数字")
    try:
        value = json.loads(content, parse_constant=reject)
    except (ValueError, RecursionError):
        return []
    pending, movies, visited = [(value, 0)], [], 0
    while pending:
        node, depth = pending.pop()
        visited += 1
        if visited > 1000 or depth > 8:
            raise ValueError("豆瓣结构化数据嵌套或数量超过安全限制")
        if isinstance(node, list):
            pending.extend((item, depth + 1) for item in node)
        elif isinstance(node, dict):
            kinds = node.get("@type", [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if isinstance(kinds, list) and any(kind in {"Movie", "TVSeries", "https://schema.org/Movie", "https://schema.org/TVSeries"} for kind in kinds if isinstance(kind, str)):
                movies.append(node)
            if "@graph" in node:
                pending.append((node["@graph"], depth + 1))
    return movies


def _rating(value, subject_id, fetched_at):
    if not isinstance(value, dict):
        return None
    score, votes, scale = value.get("ratingValue"), value.get("ratingCount"), value.get("bestRating", 10)
    if isinstance(score, bool) or isinstance(scale, bool) or type(votes) not in {str, int} or isinstance(votes, bool):
        return None
    if isinstance(votes, str):
        if len(votes) > 20 or not re.fullmatch(r"(?:[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+)", votes):
            return None
        votes = int(votes.replace(",", ""))
    try:
        score, scale = float(score), float(scale)
        if score <= 0:
            return None
        return validate_rating(PROVIDER, {"score": score, "scale": scale, "votes": votes,
            "source_url": source_url(subject_id), "external_id": str(subject_id), "fetched_at": fetched_at})
    except (ValueError, TypeError, OverflowError):
        return None


def parse_html(html, subject_id, *, fetched_at=None):
    """Parse one bounded HTML page into a deterministic, offline snapshot."""
    subject_id = _positive_id(subject_id)
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ValueError("豆瓣公开页面超过安全大小限制")
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    page = _Page()
    page.feed(html)
    page.close()
    title = "".join(page.title).strip()
    if page.blocked or re.search(r"登录豆瓣|豆瓣登录|安全验证|访问异常|验证码|captcha|access denied|robot check", title, re.I):
        raise ValueError(_BLOCKED)
    canonical_ids = [_subject_link(value) for value in page.canonicals]
    if canonical_ids and (None in canonical_ids or set(canonical_ids) != {subject_id}):
        raise ValueError("豆瓣页面 canonical 与请求条目身份不一致，未采用任何字段")
    explicit, anonymous, warnings = [], [], []
    for content, in_head in page.scripts:
        movies = _jsonld(content)
        if not movies:
            warnings.append("豆瓣结构化数据缺失或无法安全解析；未猜测评分")
        for movie in movies:
            ids = [_subject_link(movie[key], relative=True) for key in ("url", "@id") if key in movie]
            if ids and None not in ids and set(ids) == {subject_id}:
                explicit.append(movie)
            elif not any(key in movie for key in ("url", "@id")) and in_head and canonical_ids:
                anonymous.append(movie)
    candidates = explicit or anonymous
    if len(candidates) > 1:
        raise ValueError("豆瓣页面存在多个无法唯一确认的作品结构化记录")
    if not candidates and not canonical_ids:
        raise ValueError("豆瓣页面缺少可验证的条目身份（可能为登录或安全验证页）；未采用任何字段")
    movie = candidates[0] if candidates else {}
    if page.scripts and not candidates:
        raise ValueError("豆瓣结构化作品身份与当前条目不一致或无效；未采用推荐作品数据")
    name = _text(movie.get("name"))
    if not name:
        values = page.meta.get("og:title", [])
        name = _text(values[0]) if len(values) == 1 else _text(re.sub(r"\s*\(豆瓣\)\s*$", "", title))
    fields = {}
    rating = _rating(movie.get("aggregateRating"), subject_id, fetched_at)
    if rating:
        fields["metadata.ratings.douban"] = rating
    else:
        warnings.append("豆瓣评分或评分人数缺失/无效；保留本地评分，不以零分替代")
    subject = {"id": subject_id, "name": name or f"豆瓣条目 {subject_id}", "name_cn": name if _chinese(name) else "",
               "title": name, "date": "", "cover_url": "", "source_url": source_url(subject_id)}
    return {"schema_version": 1, "provider": PROVIDER, "subject_id": subject_id, "fetched_at": fetched_at,
            "source_url": source_url(subject_id), "subject": subject, "candidates": fields, "warnings": list(dict.fromkeys(warnings)),
            "source_metadata": {"page_title": name or title, "language": "zh", "attribution": "豆瓣电影公开条目",
                                "source_url": source_url(subject_id), "external_id": str(subject_id)},
            "kind": "work", "raw": {"html": html}}


def fetch_snapshot(subject_id, *, cover_only=False, deadline=None):
    subject_id = _positive_id(subject_id)
    if cover_only:
        raise ValueError("豆瓣补充来源不下载封面或头像，请使用已有图片来源")
    report_progress(f"正在读取豆瓣公开条目 {subject_id}；遇到登录或安全验证即停止")
    snapshot = parse_html(_request_html(subject_id, deadline=deadline), subject_id)
    report_progress(f"豆瓣公开条目 {subject_id} 读取完成；候选等待确认，数据库未修改")
    return snapshot


def search(query, *, offset=0, deadline=None, kind="work"):
    if kind != "work":
        raise ValueError("豆瓣仅支持作品评分搜索，不支持人物搜索")
    if type(offset) is not int or offset != 0:
        raise ValueError("豆瓣公开搜索只读取第一页，offset 必须为 0")
    from . import douban_search
    if kind == "work" and isinstance(query, str) and (_ID.fullmatch(query) or _subject_link(query)):
        subject_id = _target(query)
        try:
            snapshot = fetch_snapshot(subject_id, deadline=deadline)
        except (ValueError, OSError, RuntimeError) as exc:
            return {"provider": PROVIDER, "kind": "work", "results": [], "total": None, "limit": 1, "offset": 0,
                "search_url": douban_search.search_url(str(subject_id)), "source_url": source_url(subject_id), "unavailable": True,
                "warnings": [str(exc)[:1000] + "；可在官网自行查看该条目，不会重试或绕过验证。"]}
        return {"provider": PROVIDER, "results": [snapshot["subject"]], "total": 1, "limit": 1, "offset": 0,
                "search_url": douban_search.search_url(str(subject_id)), "warnings": [], "unavailable": False}
    return douban_search.search(query, kind=kind, offset=offset, deadline=deadline)
