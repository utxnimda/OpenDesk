"""Read-only Chinese Wikipedia adapter for explicit, local-first supplements.

The import pipeline owns persistence and confirmation. This adapter never writes
work data and never turns absent/unparseable Wikipedia fields into deletions.
Only exact dates in a recognized TV infobox are proposed; prose is not a schema.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from html import unescape
from html.parser import HTMLParser
from http.client import HTTPException
import json
import re
import socket
import ssl
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from work_catalog_yaml.operation_progress import operation_context, report_progress

from .network import failure_reason

PROVIDER = "wikipedia"
API_ROOT = "https://zh.wikipedia.org/w/api.php"
API_ROOTS = {"zh": API_ROOT, "ko": "https://ko.wikipedia.org/w/api.php"}
USER_AGENT = "utxnimda/NimdaClaw/1.0 (https://github.com/utxnimda/NimdaClaw)"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_WIKITEXT_LENGTH = 1024 * 1024
REQUEST_TIMEOUT = 12
TOTAL_TIMEOUT = 30
PAGE_SIZE = 20
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
DISPLAY_VARIANT = "zh-hans"
_INFOBOXES = {"infobox television", "infobox korean drama", "電視節目", "电视节目", "電視劇資訊框", "电视剧信息框"}
_NAME_FIELDS = ("show_name", "name", "劇名", "剧名", "名稱", "名称")
_ALIAS_FIELDS = ("original_name", "show_name_2", "別名", "别名", "中國大陸名稱", "中国大陆名称", "台灣名稱", "台湾名称", "港澳名稱", "港澳名称")
_DATE_FIELDS = {
    "air_date_start": ("first_aired", "first_airdate", "first_release", "首播日期", "開始日期", "开始日期"),
    "air_date_end": ("last_aired", "last_airdate", "last_release", "完結日期", "完结日期", "結束日期", "结束日期"),
}


def _positive_id(value: Any, label="pageid") -> int:
    if type(value) not in (int, str) or not re.fullmatch(r"[1-9][0-9]{0,11}", str(value)):
        raise ValueError(f"中文维基百科 {label} 必须为正整数")
    return int(value)


def _text(value: Any, maximum=1000) -> str:
    return value.strip() if isinstance(value, str) and len(value) <= maximum else ""


def article_url(title: str) -> str:
    """Encode once, including literal percent signs in titles such as 1%."""
    return "https://zh.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe="()")


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("中文维基百科请求超过总时限；作品数据未修改")
    return min(REQUEST_TIMEOUT, remaining)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("中文维基百科 API 返回 HTTP 重定向，已拒绝访问其他地址")


def _reject_constant(_value):
    raise ValueError("中文维基百科响应包含非有限数字")


def _request_json(query: dict, *, deadline=None, language="zh", action="query", variant=DISPLAY_VARIANT) -> dict:
    """Fixed-origin read-only query/parse; proxy discovery remains enabled."""
    if not isinstance(language, str) or language not in API_ROOTS:
        raise ValueError("维基百科来源语言仅支持 zh 或 ko")
    if action not in {"query", "parse"} or action == "parse" and language != "zh":
        raise ValueError("维基百科仅允许正文查询或中文显示转换")
    if not isinstance(variant, str) or variant not in {"zh-hans", "zh-hant"}:
        raise ValueError("中文显示转换仅支持简体或繁体")
    deadline = time.monotonic() + TOTAL_TIMEOUT if deadline is None else deadline
    params = {**query, "action": action, "format": "json", "formatversion": "2", "utf8": "1", "maxlag": "5"}
    if language == "zh":
        params.update(variant=variant, uselang=variant)
    encoded = urlencode(params)
    request = Request(API_ROOTS[language] if action == "parse" else API_ROOTS[language] + "?" + encoded,
        data=encoded.encode("utf-8") if action == "parse" else None, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Encoding": "identity",
        **({"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"} if action == "parse" else {})},
        method="POST" if action == "parse" else "GET")
    with operation_context(stage=("韩文" if language == "ko" else "中文") + "维基百科来源请求", action=str(query.get("list") or query.get("prop") or "query")):
        try:
            with build_opener(_NoRedirects()).open(request, timeout=_remaining(deadline)) as response:
                length = response.headers.get("Content-Length")
                if length and (not length.isdecimal() or int(length) > MAX_RESPONSE_BYTES):
                    raise ValueError("中文维基百科响应超出安全大小限制")
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
                        raise ValueError("中文维基百科响应超出安全大小限制")
                    chunks.append(chunk)
                if length and size != int(length):
                    raise ValueError("中文维基百科响应未完整接收；作品数据未修改")
            try:
                payload = json.loads(b"".join(chunks).decode("utf-8"), parse_constant=_reject_constant)
            except (UnicodeError, ValueError, RecursionError):
                raise ValueError("中文维基百科响应不是有效 JSON") from None
            if not isinstance(payload, dict):
                raise ValueError("中文维基百科响应结构异常")
            if payload.get("error"):
                error = payload["error"]
                code = _text(error.get("code"), 80) if isinstance(error, dict) else "unknown"
                raise ValueError(f"中文维基百科 API 请求失败（{code or 'unknown'}）；请稍后重试，作品数据未修改")
            return payload
        except HTTPError as exc:
            code = exc.code
            exc.close()
            raise ValueError(f"中文维基百科请求失败（HTTP {code}）；作品数据未修改") from None
        except (URLError, TimeoutError, socket.timeout, socket.gaierror, ssl.SSLError, ConnectionError, HTTPException) as exc:
            raise ValueError(f"中文维基百科连接失败：{failure_reason(exc)}；请检查网络或代理，作品数据未修改") from None


class _ConvertedText(HTMLParser):
    """Read only the inert, numbered text containers we sent for conversion."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values, self.current, self.parts = {}, None, []

    def handle_starttag(self, tag, attrs):
        identity = dict(attrs).get("id", "")
        if tag == "div" and re.fullmatch(r"nimda-(?:hans|hant)-[0-9]+", identity):
            if self.current is not None or identity in self.values:
                raise ValueError("简体显示转换返回重复字段")
            self.current, self.parts = identity, []
        elif self.current is not None:
            if tag != "br":
                raise ValueError("简体显示转换包含非文本字段")
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "div" and self.current is not None:
            self.values[self.current] = "".join(self.parts)
            self.current, self.parts = None, []

    def handle_data(self, value):
        if self.current is not None:
            self.parts.append(value)


def simplified_display(values, *, deadline=None, warnings=None):
    """Convert public display strings, never raw wikitext or canonical titles.

    MediaWiki's rendered language converter handles full zh-hans rules. Query
    revision content remains unconverted; adding a variant flag alone is not a
    wikitext conversion. This read-only parse escapes all wiki/HTML syntax and
    retains the server response as separate snapshot evidence. Native Korean
    and canonical page/link identities are never replaced by this projection.
    """
    originals = list(dict.fromkeys(value for value in values if isinstance(value, str) and re.search(r"[\u3400-\u9fff]", value)))
    if not originals:
        return {}, None
    deadline = time.monotonic() + TOTAL_TIMEOUT if deadline is None else deadline
    try:
        if len(originals) > 2000 or sum(map(len, originals)) > 120000 or any(len(value) > 100000 for value in originals):
            raise ValueError("简体显示转换超过安全大小限制")
        def inert(value):
            value = re.sub(r"[^\w\s]|_", lambda match: "&#" + str(ord(match[0])) + ";", value)
            return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")
        def convert(inputs, variant):
            prefix = variant.removeprefix("zh-")
            content = "\n".join(f'<div id="nimda-{prefix}-{index}">{inert(value)}</div>'
                                for index, value in enumerate(inputs))
            raw = _request_json({"text": content, "contentmodel": "wikitext", "prop": "text",
                                 "disablelimitreport": "1", "disableeditsection": "1", "disabletoc": "1"},
                                deadline=deadline, action="parse", **({"variant": variant} if variant != DISPLAY_VARIANT else {}))
            rendered = raw.get("parse", {}).get("text") if isinstance(raw.get("parse"), dict) else None
            if not isinstance(rendered, str) or len(rendered) > MAX_RESPONSE_BYTES:
                raise ValueError("中文显示转换缺少有效正文")
            parsed = _ConvertedText()
            parsed.feed(rendered)
            parsed.close()
            if parsed.current is not None or set(parsed.values) != {f"nimda-{prefix}-{index}" for index in range(len(inputs))}:
                raise ValueError("中文显示转换字段不完整")
            values = [parsed.values[f"nimda-{prefix}-{index}"] for index in range(len(inputs))]
            if any(not value or len(value) > 100000 for value in values):
                raise ValueError("中文显示转换值无效")
            return values, raw

        converted, raw = convert(originals, DISPLAY_VARIANT)
        original_languages = {original: "zh-Hans" if original == simplified else "und"
                              for original, simplified in zip(originals, converted)}
        evidence = {"variant": DISPLAY_VARIANT, "input": originals, "raw": raw,
                    "original_languages": original_languages}
        # Verify only changed source strings. Failure of this optional second
        # rendering never discards the already acquired simplified display.
        uncertain = [value for value in originals if original_languages[value] == "und"]
        if uncertain:
            try:
                traditional, raw_traditional = convert(uncertain, "zh-hant")
                evidence["traditional_verification"] = {"input": uncertain, "raw": raw_traditional}
                for original, rendered in zip(uncertain, traditional):
                    if original == rendered:
                        original_languages[original] = "zh-Hant"
            except (ValueError, OSError, RuntimeError) as exc:
                warning = "中文维基百科原文繁体校验暂不可用；已取得的简体资料仍保留：" + str(exc)[:300]
                if warnings is not None and warning not in warnings:
                    warnings.append(warning)
        return dict(zip(originals, converted)), evidence
    except (ValueError, OSError, RuntimeError) as exc:
        warning = "中文维基百科简体显示转换暂不可用；保留原始来源文字，其他资料仍可采用：" + str(exc)[:300]
        if warnings is not None and warning not in warnings:
            warnings.append(warning)
        return {}, None


def replay_display_conversion(evidence):
    """Read a signed snapshot's existing conversion proof without any I/O."""
    if not isinstance(evidence, dict) or evidence.get("variant") != DISPLAY_VARIANT:
        return {}
    inputs = evidence.get("input")
    raw = evidence.get("raw")
    rendered = raw.get("parse", {}).get("text") if isinstance(raw, dict) and isinstance(raw.get("parse"), dict) else None
    if (not isinstance(inputs, list) or len(inputs) > 2000
            or any(not isinstance(value, str) or len(value) > 100000 for value in inputs)
            or sum(map(len, inputs)) > 120000 or not isinstance(rendered, str) or len(rendered) > MAX_RESPONSE_BYTES):
        return {}
    try:
        parsed = _ConvertedText()
        parsed.feed(rendered)
        parsed.close()
        if parsed.current is not None or set(parsed.values) != {f"nimda-hans-{index}" for index in range(len(inputs))}:
            return {}
        values = [parsed.values[f"nimda-hans-{index}"] for index in range(len(inputs))]
        return dict(zip(inputs, values)) if all(value and len(value) <= 100000 for value in values) else {}
    except (ValueError, KeyError, TypeError):
        return {}


def display_language(value, mapping, variants=None):
    """Language of the displayed value, using actual conversion evidence.

    Canonical page titles and revision text are not implicitly zh-Hans. A
    failed optional conversion must retain their real variant or uncertainty.
    """
    if value in mapping:
        return "zh-Hans"
    from people_catalog.localization import language
    matches = {language(key) for key, text in (variants or {}).items() if text == value}
    return next((key for key in ("zh-Hans", "zh-Hant") if key in matches), "")


def add_localized_display(localized, kind, original, mapping, evidence=None, variants=None):
    """Retain the real source script and its proven simplified rendering."""
    from people_catalog import localization
    if not original:
        return
    original_hint = display_language(original, {}, variants)
    if not original_hint and isinstance(evidence, dict):
        original_hint = evidence.get("original_languages", {}).get(original, "")
    if original_hint in {"zh-Hans", "zh-Hant"} and localized.get(kind, {}).get("und") == original:
        # This provider projection had only a guessed unknown slot before the
        # actual converter evidence arrived; avoid two copies of one value.
        localized[kind].pop("und")
    localization.add_evidence(localized, kind, original, hint=original_hint)
    if original in mapping:
        localization.add_evidence(localized, kind, mapping[original], hint="zh-Hans")


def _search_target(query: Any) -> tuple[str, str | int]:
    if not isinstance(query, str) or not query.strip() or any(ord(char) < 32 for char in query):
        raise ValueError("中文维基百科搜索词不能为空或包含控制字符")
    query = query.strip()
    if re.match(r"^[a-z][a-z0-9+.-]*:", query, re.I) or query.startswith("//"):
        if len(query) > 2000 or "\\" in query:
            raise ValueError("中文维基百科条目网址无效")
        try:
            parsed = urlsplit(query)
            prefix = next((prefix for prefix in ("/wiki/", "/zh-hans/") if parsed.path.startswith(prefix)), None)
            if (parsed.scheme != "https" or parsed.netloc.lower() != "zh.wikipedia.org" or not prefix
                    or parsed.query and parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True) != {"variant": [DISPLAY_VARIANT]}):
                raise ValueError
            title = unquote(parsed.path[len(prefix):], errors="strict").replace("_", " ").strip()
        except (ValueError, UnicodeError):
            raise ValueError("仅支持中文维基 /wiki/ 或 /zh-hans/ 条目链接，可附 variant=zh-hans") from None
        if not title or len(title) > 300 or "|" in title or any(ord(char) < 32 for char in title):
            raise ValueError("中文维基百科条目标题无效")
        return "titles", title
    if len(query) > 200:
        raise ValueError("中文维基百科搜索词不能超过 200 字符")
    if re.fullmatch(r"[1-9][0-9]{0,11}", query):
        return "pageids", int(query)
    return "search", query


def _one_page(payload: dict) -> dict:
    pages = payload.get("query", {}).get("pages") if isinstance(payload.get("query"), dict) else None
    if not isinstance(pages, list) or len(pages) != 1 or not isinstance(pages[0], dict):
        raise ValueError("中文维基百科条目响应异常，未找到唯一条目")
    page = pages[0]
    if page.get("missing") or page.get("invalid") or page.get("ns") != 0:
        raise ValueError("中文维基百科条目不存在或不是正文条目")
    _positive_id(page.get("pageid"))
    if not _text(page.get("title")):
        raise ValueError("中文维基百科条目缺少有效标题")
    return page


def _summary_row(page: dict) -> dict:
    title = _text(page.get("title"))
    variants = page.get("varianttitles") if isinstance(page.get("varianttitles"), dict) else {}
    display = _text(variants.get("zh-hans")) or _text(variants.get("zh-cn")) or title
    return {"id": _positive_id(page.get("pageid")), "name": display, "name_cn": display,
            "title": title, "date": "", "cover_url": "", "source_url": article_url(title)}


def search(query: str, *, offset=0, deadline=None) -> dict:
    if type(offset) is not int or not 0 <= offset <= 1000:
        raise ValueError("中文维基百科搜索 offset 必须为 0～1000 的整数")
    kind, value = _search_target(query)
    deadline = time.monotonic() + TOTAL_TIMEOUT if deadline is None else deadline
    report_progress("正在搜索中文维基百科条目；搜索不会修改作品数据")
    if kind != "search":
        payload = _request_json({kind: str(value), "prop": "info", "inprop": "url|varianttitles", "redirects": "1"}, deadline=deadline)
        rows, total = [_summary_row(_one_page(payload))], 1
    else:
        payload = _request_json({"list": "search", "srsearch": value, "srnamespace": "0", "srlimit": PAGE_SIZE,
                                 "sroffset": offset, "srprop": "", "srinfo": "totalhits"}, deadline=deadline)
        result = payload.get("query")
        matches = result.get("search") if isinstance(result, dict) else None
        if not isinstance(matches, list) or len(matches) > PAGE_SIZE:
            raise ValueError("中文维基百科搜索响应结构异常")
        if any(not isinstance(row, dict) or row.get("ns") != 0 or not _text(row.get("title")) for row in matches):
            raise ValueError("中文维基百科搜索响应包含无效条目")
        rows = [_summary_row(row) for row in matches]
        info = result.get("searchinfo", {})
        total = info.get("totalhits", len(rows)) if isinstance(info, dict) else len(rows)
        if type(total) is not int or total < 0:
            raise ValueError("中文维基百科搜索总数无效")
    warnings = []
    mapping, evidence = simplified_display([row["name"] for row in rows], deadline=deadline, warnings=warnings)
    for row in rows:
        row["name"] = row["name_cn"] = mapping.get(row["name"], row["name"])
    report_progress(f"中文维基百科搜索完成，当前页 {len(rows)} 条候选")
    return {"provider": PROVIDER, "results": rows, "total": total, "limit": PAGE_SIZE, "offset": offset,
            "warnings": warnings, "display_variant": DISPLAY_VARIANT if evidence else "", "display_conversion": evidence}


def _split_template(content: str) -> list[str]:
    """Split only top-level pipes; nested templates and wikilinks stay intact."""
    parts, start, braces, links, index = [], 0, 0, 0, 0
    while index < len(content):
        token = content[index:index + 2]
        if token in {"{{", "}}", "[[", "]]"}:
            if token == "{{":
                braces += 1
            elif token == "}}":
                braces = max(0, braces - 1)
            elif token == "[[":
                links += 1
            else:
                links = max(0, links - 1)
            index += 2
            continue
        if content[index] == "|" and not braces and not links:
            parts.append(content[start:index])
            start = index + 1
        index += 1
    return parts + [content[start:]]


def _infobox(wikitext: str) -> dict[str, list[str]]:
    wikitext = re.sub(r"<!--.*?-->", "", wikitext, flags=re.S)
    start, depth, index = -1, 0, 0
    while index < len(wikitext) - 1:
        token = wikitext[index:index + 2]
        if token == "{{":
            if depth == 0:
                start = index + 2
            depth += 1
            if depth > 100:
                return {}
            index += 2
            continue
        if token == "}}" and depth:
            depth -= 1
            if depth == 0:
                parts = _split_template(wikitext[start:index])
                template = re.sub(r"[ _]+", " ", parts[0].strip()).lower().removeprefix("template:")
                if template in _INFOBOXES:
                    fields = {}
                    for field in parts[1:]:
                        key, separator, value = field.partition("=")
                        if separator:
                            fields.setdefault(key.strip().lower(), []).append(value.strip())
                    return fields
            index += 2
            continue
        index += 1
    return {}


def _without_annotations(value: str) -> str:
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    value = re.sub(r"<ref\b[^>]*?/>|<ref\b[^>]*>.*?</ref\s*>", "", value, flags=re.I | re.S)
    return value.strip()


def _plain_name(value: str) -> str:
    value = _without_annotations(value)
    # Only well understood formatting templates; do not invent a generic wiki parser.
    for _ in range(8):
        previous = value
        value = re.sub(r"\{\{(?:lang|nowrap|small)\|([^{}]*)\}\}",
                       lambda match: match.group(1).split("|")[-1], value, flags=re.I)
        if previous == value:
            break
    value = re.sub(r"\[\[([^\[\]]+)\]\]", lambda match: match.group(1).split("|")[-1], value)
    value = re.sub(r"<[^>]+>", "", value).replace("'''", "").replace("''", "")
    if any(mark in value for mark in ("{{", "}}", "[[", "]]", "-{", "}-")):
        return ""
    return _text(unescape(value), 1000)


def _date_value(value: str) -> str:
    value = _without_annotations(value)
    match = re.fullmatch(r"\{\{\s*(?:start ?date|end ?date|開始日期|开始日期|結束日期|结束日期)\s*\|([^{}]+)\}\}", value, re.I)
    if match:
        args = [part.strip() for part in match.group(1).split("|")]
        while args and not args[-1]:
            args.pop()
        values = [part for part in args if "=" not in part]
        if len(values) != 3 or any(not re.fullmatch(r"\d{1,4}", part) for part in values):
            return ""
        parts = values
    else:
        value = _plain_name(value)
        match = re.fullmatch(r"([0-9]{4})(?:-([0-9]{1,2})-([0-9]{1,2})|年\s*([0-9]{1,2})月\s*([0-9]{1,2})日)", value)
        if not match:
            return ""
        parts = (match[1], match[2] or match[4], match[3] or match[5])
    try:
        return date(*map(int, parts)).strftime("%Y%m%d")
    except ValueError:
        return ""


def _candidates(page: dict, wikitext: str, warnings: list[str]) -> dict:
    title = _text(page.get("title"))
    fields = _infobox(wikitext)
    aliases = []
    for key in (*_NAME_FIELDS, *_ALIAS_FIELDS):
        for value in fields.get(key, []):
            for part in re.split(r"<br\s*/?>|\n", value, flags=re.I):
                name = _plain_name(part)
                if name and name not in aliases:
                    aliases.append(name)
    name = next((value for key in _NAME_FIELDS for raw in fields.get(key, [])
                 if (value := _plain_name(raw))), title)
    variants = page.get("varianttitles", {})
    if isinstance(variants, dict):
        for key in ("zh-cn", "zh-hans", "zh-tw", "zh-hant", "zh-hk"):
            variant = _text(variants.get(key))
            if variant and variant not in aliases and variant != name:
                aliases.append(variant)
    if title != name and title not in aliases:
        aliases.append(title)
    result = {"name": name, "metadata.aliases": aliases[:100]}
    summary = _text(page.get("extract"), 100000)
    if summary:
        result["metadata.summary"] = summary
    else:
        warnings.append("中文维基百科未返回有效的中文简介；保留本地简介")
    for target, keys in _DATE_FIELDS.items():
        raw_dates = [raw for key in keys for raw in fields.get(key, []) if raw.strip()]
        if not raw_dates:
            continue
        dates = {_date_value(raw) for raw in raw_dates}
        if "" in dates or len(dates) != 1:
            warnings.append(f"中文维基百科 {target} 含多种播出安排、无效值或不完整日期；未猜测日期，保留本地值")
        else:
            result[target] = dates.pop()
    if result.get("air_date_end", "99999999") < result.get("air_date_start", "00000000"):
        result.pop("air_date_start", None)
        result.pop("air_date_end", None)
        warnings.append("中文维基百科开播日期晚于完结日期；两项日期均未采用")
    return result


def fetch_snapshot(subject_id: int, *, deadline=None, cover_only=False) -> dict:
    pageid = _positive_id(subject_id)
    if cover_only:
        raise ValueError("中文维基百科仅用于文字补充，请通过 Bangumi 或 TMDB 获取封面")
    deadline = time.monotonic() + TOTAL_TIMEOUT if deadline is None else deadline
    report_progress(f"正在读取中文维基百科条目 {pageid} 的中文资料")
    raw = _request_json({"pageids": pageid, "prop": "info|extracts|revisions|pageprops", "inprop": "url|varianttitles",
                         "ppprop": "disambiguation", "rvslots": "main", "rvprop": "ids|timestamp|content",
                         "rvlimit": "1", "exintro": "1", "explaintext": "1", "exlimit": "1", "redirects": "1"}, deadline=deadline)
    page = _one_page(raw)
    if isinstance(page.get("pageprops"), dict) and "disambiguation" in page["pageprops"]:
        raise ValueError("选中的中文维基百科条目是消歧义页，请选择具体作品版本")
    revisions = page.get("revisions")
    revision = revisions[0] if isinstance(revisions, list) and revisions and isinstance(revisions[0], dict) else {}
    revision_id = _positive_id(revision.get("revid"), "revision_id")
    warnings = []
    slots = revision.get("slots", {})
    main = slots.get("main", {}) if isinstance(slots, dict) else {}
    wikitext = _text(main.get("content"), MAX_WIKITEXT_LENGTH) if isinstance(main, dict) else ""
    if not wikitext:
        warnings.append("中文维基百科未返回有效的信息框文本；只提供可读取的标题和简介，不猜测播出日期")
    candidates = _candidates(page, wikitext, warnings)
    mapping, evidence = simplified_display([page["title"], candidates["name"], *candidates["metadata.aliases"], candidates.get("metadata.summary", "")], deadline=deadline, warnings=warnings)
    for field in ("name", "metadata.summary"):
        if field in candidates:
            candidates[field] = mapping.get(candidates[field], candidates[field])
    candidates["metadata.aliases"] = list(dict.fromkeys([*(mapping.get(value, value) for value in candidates["metadata.aliases"]), *candidates["metadata.aliases"]]))[:100]
    source_url = article_url(page["title"])
    resolved_id = _positive_id(page["pageid"])
    subject = {**_summary_row(page), "name": mapping.get(page["title"], _summary_row(page)["name"]), "name_cn": candidates["name"], "date": candidates.get("air_date_start", "")}
    report_progress(f"中文维基百科条目 {resolved_id} 读取完成；{len(candidates)} 项候选等待确认")
    return {"schema_version": 1, "provider": PROVIDER, "subject_id": resolved_id,
            "fetched_at": datetime.now(timezone.utc).isoformat(), "source_url": source_url,
            "subject": subject, "raw": raw, "candidates": candidates, "warnings": warnings, "display_conversion": evidence,
            "source_metadata": {"page_title": page["title"], "revision_id": revision_id,
                "revision_url": "https://zh.wikipedia.org/w/index.php?oldid=" + str(revision_id),
                "license": "CC BY-SA 4.0", "license_url": LICENSE_URL,
                "attribution": "中文维基百科贡献者", "language": "zh",
                **({"display_variant": DISPLAY_VARIANT} if evidence else {})}}
