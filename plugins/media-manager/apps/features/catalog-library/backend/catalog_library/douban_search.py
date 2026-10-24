"""Bounded public Douban website search, without using an authorized-access API.

Tracking URLs are decoded locally only; neither trackers nor result detail
pages are requested. Search rows are candidates, never identity auto-matches.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from work_catalog_yaml.operation_progress import report_progress

from . import douban_source as source

MAX_RESULTS = 20
MAX_QUERY_LENGTH = 200
_CATEGORIES = {"work": "1002"}
_TYPES = {"work": {"电影", "电视剧"}}


def _category(kind):
    if not isinstance(kind, str) or kind not in _CATEGORIES:
        raise ValueError("豆瓣仅支持作品评分搜索，不支持人物搜索")
    return _CATEGORIES[kind]


def _query(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > MAX_QUERY_LENGTH
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("豆瓣搜索词必须为 1 至 200 个字符的非空文本")
    return value.strip()


def search_url(query, *, kind="work"):
    return "https://www.douban.com/search?" + urlencode({"cat": _category(kind), "q": _query(query)})


def _www_url(value, path):
    if (not isinstance(value, str) or not value or len(value) > 4096 or "\\" in value
            or any(ord(char) < 33 for char in value) or re.search(r"%(?![0-9a-fA-F]{2})", value)):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.netloc.lower() != "www.douban.com" or parsed.path != path or parsed.fragment:
            return None
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=12)
    except ValueError:
        return None
    if len({key for key, _ in pairs}) != len(pairs):
        return None
    return dict(pairs)


def is_search_url(value):
    params = _www_url(value, "/search")
    if params is None or set(params) != {"cat", "q"} or params["cat"] not in _CATEGORIES.values():
        return False
    try:
        _query(params["q"])
    except ValueError:
        return False
    return True


def normalize_query(value, *, kind="work"):
    category = _category(kind)
    if not isinstance(value, str):
        raise ValueError("豆瓣搜索词或官网搜索链接必须为文本")
    value = value.strip()
    # A supplied URL is never reinterpreted as a keyword or sent to its host.
    if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", value) or value.startswith(("/", "\\")) or "://" in value:
        params = _www_url(value, "/search")
        if not is_search_url(value) or params["cat"] != category:
            raise ValueError("请使用对应类型的标准豆瓣官网搜索链接（作品 cat=1002）；不接受其他网址")
        return _query(params["q"])
    query = _query(value)
    if kind == "work" and query.isdecimal():
        raise ValueError("豆瓣作品数字 ID 无效，请使用正整数条目 ID")
    return query


def _destination(href, kind):
    params = _www_url(href, "/link2/")
    if params is not None:
        if "url" not in params or params.get("cat_id", _category(kind)) != _category(kind):
            return None
        href = params["url"]
    if kind == "work":
        identity = source._subject_link(href)
        return (identity, source.source_url(identity)) if identity else None


def _clean(parts, limit=2000):
    value = " ".join("".join(parts).split())
    return value if len(value) <= limit else ""


class _SearchPage(source._Page):
    def __init__(self):
        super().__init__()
        self.results, self.row = [], None
        self.row_depth = 0
        self.row_anchor = None
        self.paragraph = None
        self.result_blocks = 0
        self.has_container = False
        self.container_text = []

    def _class_inside(self, name):
        return any(name in attrs.get("class", "").split() for _, attrs in self.stack)

    def _title_inside(self):
        return self.row is not None and self._inside(tag="h3") and self._class_inside("content") and self._class_inside("title")

    def handle_starttag(self, tag, attributes):
        attrs = {key: value if isinstance(value, str) else "" for key, value in attributes}
        classes = attrs.get("class", "").split()
        if tag == "div" and "search-result" in classes:
            self.has_container = True
        if tag == "div" and "result" in classes and self._class_inside("search-result") and self._class_inside("result-list"):
            self.result_blocks += 1
            if self.result_blocks > 100 or self.row is not None:
                raise ValueError("豆瓣搜索结果数量或嵌套结构超过安全限制")
            self.row = {"title": [], "anchors": [], "cast": [], "paragraphs": []}
            self.row_depth = len(self.stack) + 1
        if tag == "a" and self._title_inside():
            self.row_anchor = {"href": attrs.get("href", ""), "text": []}
            self.row["anchors"].append(self.row_anchor)
        if tag == "p" and self.row is not None and self._class_inside("content"):
            self.paragraph = []
            self.row["paragraphs"].append(self.paragraph)
        super().handle_starttag(tag, attributes)

    def handle_data(self, data):
        if self.script is None and not self._inside(tag="style"):
            if self._class_inside("search-result"):
                self.container_text.append(data)
            if self._title_inside():
                self.row["title"].append(data)
                if self.row_anchor is not None:
                    self.row_anchor["text"].append(data)
            if self.row is not None and self._class_inside("subject-cast"):
                self.row["cast"].append(data)
            if self.paragraph is not None:
                self.paragraph.append(data)
        super().handle_data(data)

    def handle_endtag(self, tag):
        if tag == "a":
            self.row_anchor = None
        if tag == "p":
            self.paragraph = None
        super().handle_endtag(tag)
        if self.row is not None and len(self.stack) < self.row_depth:
            self.results.append(self.row)
            self.row, self.row_anchor, self.paragraph = None, None, None


def parse_html(html, *, kind="work"):
    """Return safe typed search candidates; unrecognized pages are not 'zero'."""
    _category(kind)
    if not isinstance(html, str) or len(html.encode("utf-8")) > source.MAX_RESPONSE_BYTES:
        raise ValueError("豆瓣搜索页面超过安全大小限制")
    page = _SearchPage()
    page.feed(html)
    page.close()
    title = "".join(page.title)
    if page.blocked or re.search(r"登录豆瓣|豆瓣登录|安全验证|访问异常|验证码|captcha|access denied|robot check", title, re.I):
        raise ValueError(source._BLOCKED)
    if not page.has_container or page.row is not None:
        raise ValueError("豆瓣搜索页面结构未能确认；请通过官网检索，不会将失败当作无结果")
    rows, seen, warnings = [], set(), []
    for result in page.results:
        label = re.match(r"^\s*\[([^]]+)\]", _clean(result["title"]))
        if not label or label[1] not in _TYPES[kind] or len(result["anchors"]) != 1:
            continue
        link = result["anchors"][0]
        target = _destination(link["href"], kind)
        name = _clean(link["text"], 1000)
        if target is None or not name or target[0] in seen:
            continue
        identity, url = target
        cast = _clean(result["cast"])
        paragraphs = [_clean(parts) for parts in result["paragraphs"]]
        description = cast or " / ".join(value for value in paragraphs if value)
        if len(description) > 2000:
            description = description[:2000]
        years = re.findall(r"(?:^|/)\s*((?:18|19|20|21)[0-9]{2})\s*(?=/|$)", cast)
        year = years[-1] if len(set(years)) == 1 else ""
        row = {"id": identity, "external_id": str(identity), "name": name, "name_cn": name if source._chinese(name) else "",
            "title": name, "source_url": url, "type": label[1], "kind": kind, "year": year, "date": year,
            "description": description, "cover_url": ""}
        if kind == "work":
            row["media_type"] = "movie" if label[1] == "电影" else "tv"
        rows.append(row)
        seen.add(identity)
        if len(rows) >= MAX_RESULTS:
            warnings.append("豆瓣官网搜索本次最多展示 20 条候选；未自动翻页，请缩小搜索词或在官网继续检索。")
            break
    empty_message = re.search(r"没有找到|没有搜到|未找到|无相关结果|no results", _clean(page.container_text, source.MAX_RESPONSE_BYTES), re.I)
    if not rows and not empty_message:
        warnings.append("豆瓣官网页面未提供可安全确认的对应类型结果；可能页面结构变化，请在官网核对，不代表没有相关条目。")
    return rows, warnings


def search(query, *, kind="work", offset=0, deadline=None):
    if type(offset) is not int or offset != 0:
        raise ValueError("豆瓣官网搜索只读取第一页，offset 必须为 0")
    query = normalize_query(query, kind=kind)
    url = search_url(query, kind=kind)
    report_progress("正在读取豆瓣官网公开搜索页；遇到登录或安全验证即停止")
    try:
        html = source._request_public_html(url, deadline=deadline)
        rows, warnings = parse_html(html, kind=kind)
        unavailable = not rows and bool(warnings)
    except (ValueError, OSError, RuntimeError) as exc:
        rows, unavailable = [], True
        warnings = [str(exc)[:1000] + "；可打开官网检索链接自行查看并粘贴明确条目链接，不会重试或绕过验证。"]
    return {"provider": "douban", "kind": kind, "query": query, "results": rows, "total": None if unavailable else len(rows),
            "limit": MAX_RESULTS, "offset": 0, "search_url": url, "warnings": warnings, "unavailable": unavailable}
