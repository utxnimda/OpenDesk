"""Evidence-only Korean source discovery for an already-bound Chinese work.

This module does not search by a work title, write files, or import people. It
only follows one explicit Korean Wikipedia language link and requires the two
articles to share the same Wikidata item before returning immutable evidence.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
import time
from urllib.parse import quote

from work_catalog_yaml.operation_progress import report_progress

from . import wikipedia_source as wiki

TOTAL_TIMEOUT = 20
_ITEM = re.compile(r"Q[1-9][0-9]{0,11}\Z")


def _warn(warnings, message):
    message = "韩文角色来源补充：" + str(message)[:500] + "；保留已有资料，其他中文补充仍继续"
    if warnings is not None and message not in warnings:
        warnings.append(message)
    report_progress(message, context={"stage": "韩文角色来源补充", "status": "warning"})


def _title(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 300
            or any(char in value for char in "|:#[]{}\\") or any(ord(char) < 32 for char in value)):
        raise ValueError("韩文语言链接标题无效，未搜索或访问其他条目")
    return value.strip().replace("_", " ")


def _item(page):
    props = page.get("pageprops")
    if not isinstance(props, dict) or "disambiguation" in props:
        raise ValueError("作品条目缺少身份属性或为消歧义页")
    value = props.get("wikibase_item")
    if not isinstance(value, str) or not _ITEM.fullmatch(value):
        raise ValueError("作品条目缺少可确认的 Wikidata 身份")
    return value


def _article(raw, *, expected_title, zh_article, discovery, wikidata_item):
    page = wiki._one_page(raw)
    if _item(page) != wikidata_item:
        raise ValueError("中韩条目 Wikidata 身份不一致，未采用韩文角色")
    # MediaWiki may normalize whitespace or resolve a redirect. Its explicit
    # mapping must connect the exact language-link title to the returned title.
    reached = {_title(expected_title)}
    mappings = []
    query = raw.get("query", {})
    for key in ("normalized", "redirects"):
        entries = query.get(key, [])
        if not isinstance(entries, list) or len(entries) > 10 or any(not isinstance(entry, dict) for entry in entries):
            raise ValueError("韩文条目标题关联证据格式无效")
        mappings.extend(entries)
    for _ in range(11):
        added = {_title(entry.get("to")) for entry in mappings if _title(entry.get("from")) in reached}
        if added <= reached:
            break
        reached.update(added)
    if _title(page["title"]) not in reached:
        raise ValueError("韩文条目标题与已确认语言链接不一致")
    revisions = page.get("revisions")
    if not isinstance(revisions, list) or len(revisions) != 1 or not isinstance(revisions[0], dict):
        raise ValueError("韩文作品条目缺少唯一修订")
    revision = revisions[0]
    revid = wiki._positive_id(revision.get("revid"), "revision_id")
    slots = revision.get("slots")
    main = slots.get("main") if isinstance(slots, dict) else None
    content = wiki._text(main.get("content"), wiki.MAX_WIKITEXT_LENGTH) if isinstance(main, dict) else ""
    if not content:
        raise ValueError("韩文作品条目缺少有效修订正文")
    return {"pageid": page["pageid"], "title": page["title"], "revid": revid,
            "url": "https://ko.wikipedia.org/wiki/" + quote(page["title"].replace(" ", "_"), safe="()"),
            "revision_url": "https://ko.wikipedia.org/w/index.php?oldid=" + str(revid),
            "language": "ko", "license": "CC BY-SA 4.0", "license_url": wiki.LICENSE_URL,
            "attribution": "韩文维基百科贡献者", "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "raw": deepcopy(raw), "work_identity": {"chinese_pageid": zh_article["pageid"],
                "chinese_title": zh_article["title"], "chinese_revid": zh_article["revid"],
                "korean_title": expected_title, "wikidata_item": wikidata_item,
                "language_link_response": deepcopy(discovery)}}


def fetch_for_work(chinese_article, *, deadline=None, warnings=None):
    """Return verified Korean work articles; optional failures never propagate.

    ``chinese_article`` is the already-bound, revision-bearing Chinese article
    obtained by the caller. A ``warnings`` list may collect nonfatal diagnostics.
    Only the fixed zh/ko Wikipedia APIs are used, with a shared bounded deadline.
    """
    deadline = min(time.monotonic() + TOTAL_TIMEOUT, deadline) if deadline is not None else time.monotonic() + TOTAL_TIMEOUT
    try:
        if not isinstance(chinese_article, dict):
            raise ValueError("缺少已确认的中文作品条目")
        pageid = wiki._positive_id(chinese_article.get("pageid"))
        wiki._positive_id(chinese_article.get("revid"), "revision_id")
        _title(chinese_article.get("title"))
        bound_raw = chinese_article.get("raw")
        if not isinstance(bound_raw, dict):
            raise ValueError("中文作品来源缺少原始响应")
        bound = wiki._one_page(bound_raw)
        if bound["pageid"] != pageid or bound["title"] != chinese_article["title"]:
            raise ValueError("中文作品来源与已确认条目身份不一致")
        revisions = bound.get("revisions")
        if (not isinstance(revisions, list) or len(revisions) != 1 or not isinstance(revisions[0], dict)
                or revisions[0].get("revid") != chinese_article["revid"]):
            raise ValueError("中文作品来源与已确认修订不一致")
        if chinese_article.get("language", "zh") != "zh":
            raise ValueError("仅能从已确认的中文作品条目查找韩文关联")
        if time.monotonic() >= deadline:
            raise ValueError("已达到来源查询总时限")
        discovery = wiki._request_json({"pageids": pageid, "prop": "info|langlinks|pageprops",
            "ppprop": "wikibase_item|disambiguation", "lllang": "ko", "lllimit": "1"}, deadline=deadline, language="zh")
        page = wiki._one_page(discovery)
        if page["pageid"] != pageid:
            raise ValueError("中文作品语言链接返回了其他条目")
        item = _item(page)
        links = page.get("langlinks", [])
        if not isinstance(links, list) or len(links) > 1 or any(not isinstance(link, dict) for link in links):
            raise ValueError("中文作品韩文语言链接不唯一")
        if not links:
            _warn(warnings, "已关联中文作品没有韩文语言链接，未按名称猜测其他作品")
            return []
        if links[0].get("lang") != "ko":
            raise ValueError("语言链接并非韩文作品")
        title = _title(links[0].get("title", links[0].get("*")))
        if time.monotonic() >= deadline:
            raise ValueError("已达到来源查询总时限")
        raw = wiki._request_json({"titles": title, "prop": "info|revisions|pageprops", "ppprop": "wikibase_item|disambiguation",
            "rvslots": "main", "rvprop": "ids|timestamp|content", "rvlimit": "1", "redirects": "1"}, deadline=deadline, language="ko")
        result = _article(raw, expected_title=title, zh_article=chinese_article, discovery=discovery, wikidata_item=item)
        report_progress(f"已确认韩文维基作品条目 {result['pageid']} 与中文条目的 Wikidata 身份一致")
        return [result]
    except (ValueError, OSError, RuntimeError) as exc:
        _warn(warnings, exc)
        return []
