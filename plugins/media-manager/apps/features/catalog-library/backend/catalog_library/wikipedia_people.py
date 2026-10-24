"""Evidence-only supplements from already-bound Chinese Wikipedia work pages.

Preview is the only network phase. All pages, revisions and proposed values are
kept in the TMDB snapshot for its existing hash/signature and offline apply.
Only explicit cast lists/tables and verified cross-language work links are used.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
import time
import unicodedata

from work_catalog_yaml.operation_progress import report_progress
from people_catalog import portrayal

from . import korean_people_source, providers, wikipedia_cast, wikipedia_source as wiki
from .source_bindings import normalized_refs

MAX_WORK_PAGES = 3
MAX_PERSON_PAGES = 20
MAX_SUPPLEMENT_BYTES = 8 * 1024 * 1024
TOTAL_TIMEOUT = 30
_HAN = re.compile(r"[\u3400-\u9fff]")
_HANGUL = re.compile(r"[\uac00-\ud7af]")
_PERSON_BOXES = {"infobox person", "infobox actor", "infobox actor actress", "infobox entertainer",
                 "藝人", "艺人", "藝人簡介", "艺人简介", "演員資訊框", "演员信息框", "人物"}
_NAME_KEYS = {"name", "姓名", "中文名", "中文名子", "本名", "原名", "name_chi", "chinesename"}
_NATIVE_KEYS = {"native_name", "本名", "原文名", "原文名子", "hangul", "諺文", "谚文", "韓文名", "韩文名", "外文名", "日文名", "日語名", "日语名"}
_ALIAS_KEYS = _NAME_KEYS | _NATIVE_KEYS | {"英文名", "罗马拼音", "羅馬拼音", "rr", "mr", "birth_name", "other_names", "別名", "别名"}
_BIRTH_KEYS = {"本名", "原名", "birth_name"}


def _norm(value):
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold() if isinstance(value, str) else ""


def _romanized_key(value):
    # Used only after the actor is uniquely identified. Korean given names may
    # separate syllables with a space or hyphen (Hae In / Hae-in); this is not
    # fuzzy actor matching or an inferred transliteration.
    return re.sub(r"[\s\-\u2010\u2011]", "", _norm(value))


def _warn(snapshot, reason):
    message = "中文维基百科人物补充：" + reason + "；未确认字段不采用，已有资料与关联保留"
    if message not in snapshot.setdefault("warnings", []):
        snapshot["warnings"].append(message)
        report_progress(message, context={"stage": "中文维基百科人物补充", "provider": "wikipedia",
                        "subject_id": str(snapshot.get("subject_id", "")), "season": snapshot.get("season_number"),
                        "reason": reason, "status": "warning"})


def _title(value):
    value = value.strip().replace("_", " ")
    if (not value or len(value) > 300 or any(char in value for char in "|:#[]{}\\")
            or any(ord(char) < 32 for char in value)):
        return ""
    return value


_names = wikipedia_cast.names
_cast_rows = wikipedia_cast.cast_rows


def _korean_work(article, original_language):
    if original_language:
        return original_language == "ko"
    value = _wikitext(article)
    boxes = re.findall(r"\{\{\s*([^{}|\n]+)", wiki._without_annotations(value))
    if any(re.sub(r"[ _]+", " ", name.strip()).lower() == "infobox korean drama" for name in boxes):
        return True
    fields = wiki._infobox(value)
    languages = {wiki._plain_name(raw).strip().casefold() for key in ("language", "語言", "语言", "語言版本", "语言版本")
                 for raw in fields.get(key, []) if raw.strip()}
    return languages == {"ko"} or languages == {"korean"} or languages == {"韓語"} or languages == {"韩语"} or languages == {"한국어"}


def _native_roles(article, profiles, linked_rows, warnings, *, deadline, original_language, sources=None, unresolved=None):
    """Join only verified work-language links and unique explicit actor names.

    This never searches actor names and never joins tables by row position.
    Ambiguous actors or multiple roles remain Chinese-only for manual review.
    """
    if not linked_rows or not _korean_work(article, original_language):
        return []
    pages, rows = [], []
    size = len(providers._json_bytes({"work": article, "profiles": profiles}))
    try:
        if sources is None:
            sources = korean_people_source.fetch_for_work(article, deadline=deadline, warnings=warnings)
        for source in sources:
            added = len(providers._json_bytes(source))
            if size + added > MAX_SUPPLEMENT_BYTES:
                raise ValueError("韩文作品证据超过补充安全大小限制")
            pending = []
            cast, issues = wikipedia_cast.parse(_wikitext(source), language="ko", unresolved=pending)
            if unresolved is not None:
                for row in pending:
                    row["source"] = {"provider": "wikipedia", "pageid": source["pageid"], "url": source["url"], "language": "ko"}
                unresolved.extend(pending)
            warnings.extend("韩文角色补充：" + message for message in issues)
            size += added
            pages.append(source)
            for row in cast:
                row["source"] = {key: deepcopy(source[key]) for key in ("pageid", "revid", "url", "revision_url", "license", "license_url", "attribution")}
                row["source"]["language"] = "ko"
                rows.append(row)
    except (ValueError, OSError, RuntimeError) as error:
        warnings.append("韩文角色补充未完成：" + str(error)[:300] + "；中文资料仍可确认")
    known = {profile["external_id"]: {_norm(name) for name in [profile.get("name", ""), *profile.get("aliases", [])] if _norm(name)}
             for profile in profiles}
    matched = {}
    for row in rows:
        aliases = {_norm(name) for name in row["actor"]["names"] if _norm(name)}
        owners = [identity for identity, names in known.items() if names & aliases]
        if len(owners) == 1:
            matched.setdefault(owners[0], []).append(row)
        elif len(owners) > 1:
            warnings.append("韩文演员身份不唯一，未采用对应角色：" + row["actor_raw"][:200])
    for relation, chinese in linked_rows:
        # Already explicit original-language text from the Chinese table has
        # precedence over an optional supplement, including official English.
        if chinese.get("character_name"):
            continue
        qualifier = (chinese.get("qualifier") or {}).get("value", "")
        eligible = [row for row in matched.get(relation["external_id"], [])
                    if portrayal.compatible_stage(chinese, row)
                    or qualifier == "adult" and not row.get("qualifier")]
        unique_chinese = {(_norm(other["character_name_cn"]), (row.get("qualifier") or {}).get("value", ""))
                          for other, row in linked_rows if other["external_id"] == relation["external_id"]}
        if len(unique_chinese) > 1:
            exact = {_norm(name) for name in chinese["role"]["names"]}
            eligible = [row for row in eligible if exact & {_norm(name) for name in row["role"]["names"]}]
        names = {row["character_name"] for row in eligible if row.get("character_name")}
        if len(names) == 1:
            relation["character_name"] = next(iter(names))
            relation["evidence"]["translations"] = [{"source": deepcopy(row["source"]),
                "actor_cell": row["actor_raw"], "role_cell": row["role_raw"], "qualifier": deepcopy(row.get("qualifier"))}
                for row in eligible]
        elif eligible or matched.get(relation["external_id"]):
            warnings.append("演员的韩文角色对应不唯一，保留中文并等待手工确认：" + relation["character_name_cn"][:200])
    return pages


def fetch_people_snapshot(subject_id, *, source_snapshot=None, deadline=None, original_language=None, evidence_snapshot=None):
    """Use explicit actor article links, independently of any TMDB binding."""
    from people_catalog import localization
    deadline = min(time.monotonic() + TOTAL_TIMEOUT, deadline) if deadline is not None else time.monotonic() + TOTAL_TIMEOUT
    subject_id = wiki._positive_id(subject_id)
    warnings, profiles, relations, unresolved, fetched, linked_rows = [], [], [], [], {}, []
    if source_snapshot is not None:
        raw = source_snapshot.get("raw", {})
        page = wiki._one_page(raw)
        if page["pageid"] != subject_id:
            raise ValueError("维基人物演员表与明确选择的作品页面不一致")
        article = _article_response(raw)
    else:
        article = _article(pageid=subject_id, deadline=deadline)
    rows, parse_warnings = wikipedia_cast.parse(_wikitext(article), language="zh", unresolved=unresolved)
    warnings.extend(parse_warnings)
    cached = evidence_snapshot if isinstance(evidence_snapshot, dict) and any(
        page.get("pageid") == subject_id and page.get("language", "zh") == "zh"
        for page in evidence_snapshot.get("work_pages", [])) else {}
    cached_actors = {}
    for page in cached.get("person_pages", []):
        cached_actors[page["title"]] = page
        for alias in [*page.get("raw", {}).get("query", {}).get("redirects", []),
                      *page.get("raw", {}).get("query", {}).get("normalized", [])]:
            if isinstance(alias.get("from"), str):
                cached_actors[alias["from"]] = page
    # Work-level role translations must not wait behind up to twenty optional
    # actor profiles. Resolve their network evidence first; joining still uses
    # the exact actor names established by those profiles below.
    korean_sources = [page for page in cached.get("work_pages", []) if page.get("language") == "ko"]
    if rows and _korean_work(article, original_language):
        try:
            if not korean_sources:
                korean_sources = korean_people_source.fetch_for_work(article, deadline=deadline, warnings=warnings)
        except (ValueError, OSError, RuntimeError) as error:
            warnings.append("韩文角色补充未完成：" + str(error)[:300] + "；中文资料仍可确认")
    mapping, conversion = wiki.simplified_display([row["character_name_cn"] for row in rows], deadline=deadline, warnings=warnings)
    for row in rows:
        links = row.get("actor", {}).get("links", [])
        if len(links) != 1:
            unresolved.append({"name": " / ".join(row.get("actor", {}).get("names", [])),
                "character_name": row.get("character_name", ""), "character_name_cn": row.get("character_name_cn", ""),
                "reason": "演员表未提供唯一明确人物链接；请通过指定维基人物页确认身份，不创建空白档案"})
            continue
        title = links[0]
        if title not in fetched:
            if len(fetched) >= MAX_PERSON_PAGES or title not in cached_actors and time.monotonic() >= deadline:
                warnings.append("维基独立人物读取达到数量或时间上限；未读取条目不会创建")
                continue
            try:
                fetched[title] = (_profile_from_article(cached_actors[title], deadline=deadline) if title in cached_actors else
                                  fetch_person_profile(title, deadline=deadline))
            except (ValueError, OSError, RuntimeError) as error:
                fetched[title] = None
                warnings.append(f"人物链接 {title} 未能确认：{str(error)[:300]}；不会创建空白档案")
        person = fetched[title]
        if person is None:
            continue
        if not any(value["external_id"] == person["external_id"] for value in profiles):
            profiles.append(person)
        relation = {"external_id": person["external_id"], "role": "演员",
            "character_name": row.get("character_name") or row.get("character_name_cn") or "",
            "character_name_cn": row.get("character_name_cn", ""),
            "evidence": {"actor_cell": row.get("actor_raw", ""), "role_cell": row.get("role_raw", "")}}
        for key in ("qualifier", "role_group"):
            if row.get(key):
                relation[key] = deepcopy(row[key])
        if relation not in relations:
            relations.append(relation)
            linked_rows.append((relation, row))
    korean_pages = _native_roles(article, profiles, linked_rows, warnings, deadline=deadline,
                                 original_language=original_language, sources=korean_sources, unresolved=unresolved)
    for row in relations:
        localized = {}
        localization.add_evidence(localized, "name", row.get("character_name", ""))
        wiki.add_localized_display(localized, "name", row["character_name_cn"], mapping, conversion)
        row["localized"] = localization.validate(localized)
        row["character_name_cn"] = mapping.get(row["character_name_cn"], row["character_name_cn"])
    result = {"schema_version": 1, "provider": "wikipedia", "kind": "people", "subject_id": subject_id,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "warnings": warnings,
        "raw": {"work_article": article, "korean_work_articles": korean_pages, "profiles": profiles, "relations": relations,
                "unresolved": unresolved, "display_conversion": conversion}}
    if len(providers._json_bytes(result)) > providers.MAX_SNAPSHOT_BYTES:
        raise ValueError("维基人物证据超过快照安全大小")
    return result


def _article(*, pageid=None, title=None, deadline):
    target = {"pageids": wiki._positive_id(pageid)} if pageid is not None else {"titles": _title(title or "")}
    if not next(iter(target.values())):
        raise ValueError("演员链接不是安全的中文维基正文标题")
    raw = wiki._request_json({**target, "prop": "info|extracts|revisions|pageprops", "inprop": "varianttitles",
                             "ppprop": "disambiguation", "rvslots": "main", "rvprop": "ids|timestamp|content",
                             "rvlimit": "1", "exintro": "1", "explaintext": "1", "exlimit": "1", "redirects": "1"}, deadline=deadline)
    return _article_response(raw)


def _article_response(raw):
    """Validate shared API or already-fetched evidence with identical rules."""
    page = wiki._one_page(raw)
    if isinstance(page.get("pageprops"), dict) and "disambiguation" in page["pageprops"]:
        raise ValueError("演员或作品链接指向消歧义页")
    revisions = page.get("revisions")
    revision = revisions[0] if isinstance(revisions, list) and len(revisions) == 1 and isinstance(revisions[0], dict) else {}
    revid = wiki._positive_id(revision.get("revid"), "revision_id")
    slots = revision.get("slots")
    main = slots.get("main", {}) if isinstance(slots, dict) else {}
    wikitext = wiki._text(main.get("content"), wiki.MAX_WIKITEXT_LENGTH) if isinstance(main, dict) else ""
    if not wikitext:
        raise ValueError("作品或演员条目缺少有效修订正文")
    return {"pageid": page["pageid"], "title": page["title"], "revid": revid,
            "url": wiki.article_url(page["title"]), "revision_url": "https://zh.wikipedia.org/w/index.php?oldid=" + str(revid),
            "license": "CC BY-SA 4.0", "license_url": wiki.LICENSE_URL, "attribution": "中文维基百科贡献者",
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "raw": raw}


def _wikitext(article):
    return article["raw"]["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"]


def _person_names(article):
    from people_catalog import localization
    page = article["raw"]["query"]["pages"][0]
    names, chinese, native, evidence, romanized, facts, name_fields = [], [], [], [], [], [], []
    localized = {}
    variants = page.get("varianttitles")
    # Scan balanced top-level templates; unknown prose and nested templates do
    # not become identity evidence.
    value = wiki._without_annotations(_wikitext(article))
    depth, start, index = 0, 0, 0
    while index < len(value) - 1:
        token = value[index:index + 2]
        if token == "{{":
            if not depth:
                start = index + 2
            depth += 1
            if depth > 30:
                break
            index += 2
            continue
        if token == "}}" and depth:
            depth -= 1
            if not depth:
                parts = wiki._split_template(value[start:index])
                template = re.sub(r"[ _]+", " ", parts[0].strip()).lower().removeprefix("template:")
                if template in _PERSON_BOXES or template == "infobox korean name":
                    fields = {}
                    for field in parts[1:]:
                        key, separator, raw = field.partition("=")
                        if separator:
                            fields.setdefault(key.strip().lower(), []).append(raw)
                    native_language = [wiki._plain_name(raw).lower() for key in ("外文", "native_name_lang") for raw in fields.get(key, [])]
                    korean_name = (template == "infobox korean name" or native_language == ["ko"]
                                   or any(_HANGUL.search(wiki._plain_name(raw)) for key in _NATIVE_KEYS for raw in fields.get(key, [])))
                    fact_labels = {"gender": "性别", "性別": "性别", "性别": "性别", "birth_date": "出生日期", "出生日期": "出生日期",
                                   "birth_place": "出生地", "出生地點": "出生地", "出生地点": "出生地", "出生地": "出生地",
                                   "occupation": "职业", "職業": "职业", "职业": "职业", "nationality": "国籍", "國籍": "国籍", "国籍": "国籍"}
                    for key, label in fact_labels.items():
                        for raw in fields.get(key, []):
                            fact_value = wiki._plain_name(raw)
                            if fact_value and {"key": label, "value": fact_value} not in facts:
                                facts.append({"key": label, "value": fact_value})
                    for field in parts[1:]:
                        key, separator, raw = field.partition("=")
                        key = key.strip().lower()
                        if separator and key in _ALIAS_KEYS:
                            parsed = _names(raw)
                            hint = ("en" if key == "英文名" else
                                    "ko-Latn" if key in {"羅馬拼音", "罗马拼音", "rr", "mr"} and korean_name else
                                    native_language[0] if key in _NATIVE_KEYS and len(native_language) == 1 else
                                    "ja" if key in {"日文名", "日語名", "日语名"} else "")
                            for name in parsed["names"]:
                                if len(name) <= 1000:
                                    # A native-language infobox describes the person,
                                    # not every atom in a bilingual birth-name field.
                                    atom_hint = "" if hint == "ko" and _HAN.search(name) and not _HANGUL.search(name) else hint
                                    priority = 2 if key in _BIRTH_KEYS else 0 if key in _NAME_KEYS | _NATIVE_KEYS else 1
                                    name_fields.append((priority, key, name, atom_hint))
                            if key in {"羅馬拼音", "罗马拼音", "rr"}:
                                romanized.extend(parsed["names"])
                            if key == "native_name" or key in {"外文名", "本名"} and native_language == ["ko"]:
                                evidence.extend({"field": key, "language": "ko", "value": name} for name in parsed["names"]
                                                if _HANGUL.search(name) and not _HAN.search(name))
            index += 2
            continue
        index += 1
    for _, key, name, hint in sorted(name_fields, key=lambda item: item[0]):
        names.append(name)
        localization.add_evidence(localized, "name", name, hint=hint)
        localization.add_evidence(localized, "aliases", [name], hint=hint)
        if key in _NAME_KEYS and _HAN.search(name) and not _HANGUL.search(name):
            chinese.append(name)
        if key in _NATIVE_KEYS and _HANGUL.search(name):
            native.append(name)
    # Explicit infobox names outrank article disambiguation titles. A variant
    # title can prove another script spelling only when it includes that same
    # primary name, never by stripping arbitrary parentheses from a title.
    title_variants = variants if isinstance(variants, dict) else {}
    primary_chinese = chinese[0] if chinese else ""
    allow_variants = not primary_chinese or primary_chinese in [page["title"], *title_variants.values()]
    for key, name in title_variants.items():
        if (allow_variants and isinstance(name, str) and name.strip() and len(name) <= 200
                and not any(char in name for char in "()（）") and localization.language(key)):
            names.append(name)
            localization.add_evidence(localized, "name", name, hint=key)
            localization.add_evidence(localized, "aliases", [name], hint=key)
    if not any(char in page["title"] for char in "()（）"):
        names.append(page["title"])
    primary_native = [item for item in evidence if item["field"] not in _BIRTH_KEYS]
    evidence = primary_native or evidence
    return {"names": list(dict.fromkeys(names)), "chinese": list(dict.fromkeys(chinese)),
            "native_names": list(dict.fromkeys(native)), "native_evidence": evidence, "romanized": romanized,
            "infobox": facts[:30], "localized": localization.validate(localized)}


def fetch_person_profile(page_id_or_title, *, deadline=None):
    """Read one explicitly selected person page; never search/match or persist.

    Return a bounded snapshot usable by the standalone signed-preview layer.
    Canonical page ID/title and raw revision are retained; display conversion is
    a separate optional projection. Apply must consume this snapshot offline.
    """
    deadline = min(time.monotonic() + TOTAL_TIMEOUT, deadline) if deadline is not None else time.monotonic() + TOTAL_TIMEOUT
    if type(page_id_or_title) is int or isinstance(page_id_or_title, str) and page_id_or_title.isdecimal():
        article = _article(pageid=wiki._positive_id(page_id_or_title), deadline=deadline)
    else:
        if not isinstance(page_id_or_title, str):
            raise ValueError("请选择明确的维基人物条目标题或页面 ID")
        kind, selected = wiki._search_target(page_id_or_title)
        article = _article(pageid=selected, deadline=deadline) if kind == "pageids" else _article(title=selected, deadline=deadline)
    return _profile_from_article(article, deadline=deadline)


def reparse_person_name_evidence(supplement, identity):
    """Correct old provider name projections using proven raw actor evidence.

    Pure/offline: an existing exact TMDB/Wiki actor mapping is required. This
    never searches a name, invents a language variant, or changes source IDs.
    """
    from people_catalog import localization
    matches = {str(row.get("wikipedia_pageid")) for row in supplement.get("actor_matches", [])
               if str(row.get("tmdb_person_id")) == str(identity)}
    if len(matches) != 1:
        return None
    articles = [row for row in supplement.get("person_pages", []) if str(row.get("pageid")) in matches]
    if len(articles) != 1:
        return None
    try:
        article = _article_response(articles[0]["raw"])
        if str(article["pageid"]) not in matches:
            return None
        person = _person_names(article)
    except (ValueError, KeyError, TypeError):
        return None
    localized = deepcopy(person["localized"])
    conversion = supplement.get("display_conversion")
    mapping = wiki.replay_display_conversion(conversion)
    chinese = person["chinese"][0] if person["chinese"] else ""
    wiki.add_localized_display(localized, "name", chinese, mapping, conversion)
    for name in person["names"]:
        if name in mapping:
            localization.add_evidence(localized, "aliases", [mapping[name]], hint="zh-Hans")
    source = {"provider": "wikipedia", "external_id": str(article["pageid"]), "source_url": article["url"]}
    return {"localized": localization.validate(localized), "name_cn": mapping.get(chinese, chinese),
            "aliases": list(dict.fromkeys([*(mapping.get(value, value) for value in person["names"]), *person["names"]]))[:100],
            "source": {key: article[key] for key in ("pageid", "revid", "url", "revision_url", "license", "license_url", "attribution")},
            "field_sources": {f"localized.name.{lang}": deepcopy(source) for lang in localized.get("name", {})}}


def _profile_from_article(article, *, deadline):
    """Project already-fetched actor evidence without downloading it twice."""
    from people_catalog import localization
    person = _person_names(article)
    # A work/disambiguation page must not become a person profile just because
    # the user selected it. Require an explicit supported person infobox.
    boxes = re.findall(r"\{\{\s*([^{}|\n]+)", wiki._without_annotations(_wikitext(article)))
    if not any(re.sub(r"[ _]+", " ", value.strip()).lower().removeprefix("template:") in _PERSON_BOXES for value in boxes):
        raise ValueError("所选维基条目没有可确认的人物信息框，未采用作品或不明条目")
    page = article["raw"]["query"]["pages"][0]
    variants = page.get("varianttitles") if isinstance(page.get("varianttitles"), dict) else {}
    chinese = person["chinese"][0] if person["chinese"] else page["title"] if _HAN.search(page["title"]) and not any(mark in page["title"] for mark in "()（）") else ""
    native = list(dict.fromkeys(person["native_names"]))
    original_name = native[0] if native else chinese or page["title"]
    summary = wiki._text(page.get("extract"), 100000)
    warnings = []
    values = [chinese, summary, *person["names"], *(fact["value"] for fact in person["infobox"])]
    mapping, evidence = wiki.simplified_display(values, deadline=deadline, warnings=warnings)
    aliases = list(dict.fromkeys([*(mapping.get(value, value) for value in person["names"]), *person["names"]]))[:100]
    source_metadata = {key: article[key] for key in ("license", "license_url", "attribution", "revision_url")}
    source_metadata.update(page_title=article["title"], revision_id=article["revid"], language="zh")
    if evidence:
        source_metadata["display_variant"] = wiki.DISPLAY_VARIANT
    localized = deepcopy(person["localized"])
    wiki.add_localized_display(localized, "name", chinese, mapping, evidence, variants)
    wiki.add_localized_display(localized, "summary", summary, mapping, evidence)
    for alias in aliases:
        # Already classified official names/variants retain their evidence.
        known = [lang for lang, value in localized.get("name", {}).items() if value == alias]
        hint = "zh-Hans" if alias in mapping.values() else known[0] if len(known) == 1 else ""
        if not hint and isinstance(evidence, dict):
            hint = evidence.get("original_languages", {}).get(alias, "")
        localization.add_evidence(localized, "aliases", [alias], hint=hint)
    result = {"provider": "wikipedia", "external_id": str(article["pageid"]), "fetched_at": article["fetched_at"],
              "name": original_name if _HANGUL.search(original_name) else mapping.get(original_name, original_name),
              "name_cn": mapping.get(chinese, chinese), "aliases": aliases, "summary": mapping.get(summary, summary),
              "localized": localization.validate(localized),
              "infobox": [{"key": fact["key"], "value": mapping.get(fact["value"], fact["value"])} for fact in person["infobox"]],
              "source_url": article["url"], "source_metadata": source_metadata,
              "raw": article["raw"], "display_conversion": evidence, "warnings": warnings}
    if len(providers._json_bytes(result)) > MAX_SUPPLEMENT_BYTES:
        raise ValueError("维基人物资料快照超过安全大小限制")
    return result


def _known_names(raw, identity):
    fallback, detail = raw["fallback"][identity], raw.get("details", {}).get(identity) or {}
    localized = raw.get("fallback_localized", {}).get(identity) or {}
    aliases = detail.get("also_known_as", [])
    values = {value for row in (fallback, detail, localized) for value in (row.get("name"), row.get("original_name")) if isinstance(value, str)}
    if isinstance(aliases, list):
        values.update(value for value in aliases if isinstance(value, str))
    return values


def _role_qualifier(value):
    """Only explicit age markers identify a young/adult performance."""
    return (portrayal.parse_qualifier(value) or {}).get("value", "")


def _role_proposal(snapshot, identity, relation, cast, candidates, work_count, overflow):
    qualifier = _role_qualifier(relation.get("value", ""))
    source_role = {"original_name": relation.get("value", "")}
    eligible = [row for row in candidates if portrayal.compatible_stage(source_role, row)
                or {qualifier, row.get("qualifier", {}).get("value", "")} == {"adult", ""}]
    known = {_norm(relation.get(key, "")) for key in ("value", "value_cn")} - {""}
    exact = [row for row in eligible if known & {_norm(name) for name in row["role"]["names"]}]
    role_keys = {(_norm(item.get("value")), item.get("credit_id", "")) for item in cast}
    # A unique performance within one confirmed work may join translations by
    # exact actor identity. Multiple roles require explicit role-name evidence.
    chosen = eligible if len(role_keys) == 1 and work_count == 1 else exact
    sources = {row["source"].get("binding_id", str(row["source"]["pageid"])) for row in chosen}
    chinese = {row["character_name_cn"] for row in chosen if row["character_name_cn"]}
    native = {row["character_name"] for row in chosen if row["character_name"]}
    if not chosen or len(sources) != 1 or overflow:
        _warn(snapshot, f"TMDB 人物 {identity}，角色 {relation.get('value', '')} 的角色或作品/季证据不唯一")
        return None
    if len(chinese) > 1:
        _warn(snapshot, f"TMDB 人物 {identity}，角色 {relation.get('value', '')} 的中文角色证据不唯一")
        chinese = set()
    if len(native) > 1:
        _warn(snapshot, f"TMDB 人物 {identity}，角色 {relation.get('value', '')} 的韩文角色证据不唯一")
        native = set()
    chinese = next(iter(chinese), "")
    native = next(iter(native), "") if snapshot.get("original_language") == "ko" else ""
    if _HAN.search(relation.get("value_cn", "")) or _HAN.search(relation.get("value", "")):
        chinese = ""
    if native == relation.get("value") and not chinese:
        native = ""
    if not chinese and not native:
        return None
    proposal = {"person_id": int(identity), "original_value": relation.get("value", ""),
                "character_name_cn": chinese, "source": chosen[0]["source"],
                "evidence": {"actor_cell": chosen[0]["actor_raw"], "role_cell": chosen[0]["role_raw"],
                    "translations": [{"source": row["source"], "actor_cell": row["actor_raw"],
                                      "role_cell": row["role_raw"], "qualifier": row.get("qualifier")}
                                     for row in chosen]}}
    for key in ("qualifier", "role_group"):
        values = [row.get(key) for row in chosen]
        if values and values[0] and all(value == values[0] for value in values):
            proposal[key] = deepcopy(values[0])
    if relation.get("credit_id"):
        proposal["credit_id"] = relation["credit_id"]
    if native:
        proposal["character_name"] = native
    return proposal


def enrich(snapshot, record, *, deadline=None):
    from people_catalog import localization
    """Mutate and return a TMDB snapshot; failures never block its preview."""
    bindings = [ref for ref in normalized_refs(record) if ref.get("provider") == "wikipedia" and ref.get("enabled") is True]
    if not bindings:
        return snapshot
    raw = snapshot.get("raw", {})
    if snapshot.get("provider") != "tmdb" or not isinstance(raw.get("fallback"), dict):
        return snapshot
    report_progress(f"正在使用已确认中文维基百科作品条目补充 TMDB {snapshot.get('subject_id')} 第 {snapshot.get('season_number')} 季人物资料")
    deadline = min(time.monotonic() + TOTAL_TIMEOUT, deadline) if deadline is not None else time.monotonic() + TOTAL_TIMEOUT
    supplement = {"schema_version": 2, "work_pages": [], "person_pages": [], "people": {}, "relations": [], "actor_matches": [], "unresolved": []}
    snapshot_size = len(providers._json_bytes(snapshot))
    size, rows, fetched, seen_pages = 0, [], {}, set()

    def keep(article, collection):
        nonlocal size
        added = len(providers._json_bytes(article))
        if size + added > MAX_SUPPLEMENT_BYTES or snapshot_size + size + added > providers.MAX_SNAPSHOT_BYTES - 65536:
            raise ValueError("补充快照达到安全大小限制")
        size += added
        supplement[collection].append(article)

    for binding in bindings[:MAX_WORK_PAGES]:
        try:
            if time.monotonic() >= deadline:
                raise ValueError("已达到补充总时限")
            pageid = wiki._positive_id(binding.get("external_id"))
            if pageid in seen_pages:
                continue
            article = _article(pageid=pageid, deadline=deadline)
            seen_pages.add(pageid)
            keep(article, "work_pages")
            articles = [article]
            if snapshot.get("original_language") == "ko":
                for korean in korean_people_source.fetch_for_work(article, deadline=deadline, warnings=snapshot.setdefault("warnings", [])):
                    try:
                        keep(korean, "work_pages")
                        articles.append(korean)
                    except (ValueError, OSError, RuntimeError) as exc:
                        _warn(snapshot, f"韩文作品 {korean.get('pageid')}：{str(exc)[:300]}，中文资料仍继续解析")
            for source_article in articles:
                language = source_article.get("language", "zh")
                try:
                    unresolved_rows = []
                    cast_rows, parse_warnings = wikipedia_cast.parse(_wikitext(source_article), language=language, unresolved=unresolved_rows)
                except ValueError as exc:
                    _warn(snapshot, f"{language} 作品条目 {source_article['pageid']} 解析失败：{str(exc)[:300]}")
                    continue
                for row in unresolved_rows:
                    row["source"] = {"provider": "wikipedia", "pageid": source_article["pageid"],
                                     "url": source_article["url"], "binding_id": binding["binding_id"], "language": language}
                supplement["unresolved"].extend(unresolved_rows)
                for reason in parse_warnings:
                    _warn(snapshot, f"{language} 作品条目 {source_article['pageid']}：{reason}")
                if not cast_rows:
                    _warn(snapshot, f"{language} 作品条目 {source_article['pageid']} 没有可安全解析的演员/角色表或列表")
                for row in cast_rows:
                    row["source"] = {key: source_article[key] for key in ("pageid", "revid", "url", "revision_url", "license", "license_url", "attribution")}
                    row["source"].update(binding_id=binding["binding_id"], language=language)
                    rows.append(row)
        except (ValueError, OSError, RuntimeError) as exc:
            _warn(snapshot, str(exc)[:500])
    if len(bindings) > MAX_WORK_PAGES:
        _warn(snapshot, "已关联作品条目超过本次读取上限，角色存在跨作品歧义")
    # Simplifying known role text is work-level evidence, not an optional actor
    # detail. Reserve its turn before serial profile requests consume the budget.
    role_mapping, role_conversion = wiki.simplified_display(
        [row["character_name_cn"] for row in rows], deadline=deadline, warnings=snapshot.setdefault("warnings", []))
    if role_conversion is not None:
        added = len(providers._json_bytes(role_conversion))
        if size + added > MAX_SUPPLEMENT_BYTES or snapshot_size + size + added > providers.MAX_SNAPSHOT_BYTES - 65536:
            role_mapping, role_conversion = {}, None
            _warn(snapshot, "角色简体转换证据达到快照大小限制，保留来源原文")
        else:
            size += added
            supplement["role_display_conversion"] = role_conversion
    names = {identity: _known_names(raw, identity) for identity in raw["fallback"]}
    normal = {identity: {_norm(name) for name in values if _norm(name)} for identity, values in names.items()}
    needs = {identity for identity, values in names.items() if not any(_HAN.search(value) for value in values)}
    # Fetch only exact, unique actor links already present in the bound work.
    for row in rows:
        if row["source"]["language"] != "zh":
            continue
        matches = [identity for identity, known in normal.items() if known & {_norm(name) for name in row["actor"]["names"]}]
        if len(matches) == 1 and matches[0] not in needs and snapshot.get("original_language") != "ko":
            continue
        if len(row["actor"]["links"]) != 1:
            continue
        title = row["actor"]["links"][0]
        if title not in fetched:
            if len(fetched) >= MAX_PERSON_PAGES or time.monotonic() >= deadline:
                _warn(snapshot, "演员链接补充已达到数量或时间上限")
                continue
            fetched[title] = None
            try:
                article = _article(title=title, deadline=deadline)
                person = _person_names(article)
                keep(article, "person_pages")
                fetched[title] = (article, person)
            except (ValueError, OSError, RuntimeError) as exc:
                _warn(snapshot, f"演员链接 {title}：{str(exc)[:300]}")
        if fetched.get(title):
            article, person = fetched[title]
            row["actor"]["names"] = list(dict.fromkeys([*row["actor"]["names"], *person["names"]]))
            row["person"] = person
            row["person_source"] = {key: article[key] for key in ("pageid", "revid", "url", "revision_url", "license", "license_url", "attribution")}
    matched = {}
    for row in rows:
        matches = [identity for identity, known in normal.items() if known & {_norm(name) for name in row["actor"]["names"]}]
        if len(matches) == 1:
            matched.setdefault(matches[0], []).append(row)
        elif len(matches) > 1:
            _warn(snapshot, f"演员表姓名同时匹配多个 TMDB 人物，未自动绑定：{row['actor_raw'][:200]}；{row['source']['url']}")
        else:
            _warn(snapshot, f"演员 {row['actor_raw'][:200]} 未找到唯一的 TMDB 人物对应，未采用角色 {row['role_raw'][:200]}；{row['source']['url']}")
    for identity, candidates in matched.items():
        actor_pages = {row["person_source"]["pageid"] for row in candidates if row.get("person_source")}
        actor_links = {title for row in candidates if row["source"]["language"] == "zh" for title in row["actor"]["links"]}
        if len(actor_pages) > 1 or len(actor_links) > 1 and any(not fetched.get(title) for title in actor_links):
            _warn(snapshot, f"TMDB 人物 {identity} 的同名演员链接身份不唯一")
            continue
        if len(actor_pages) == 1:
            # Exact identity evidence from the bound work and the linked actor
            # article; independent Wiki-only roles can reuse this same actor.
            supplement["actor_matches"].append({"tmdb_person_id": identity,
                "wikipedia_pageid": str(next(iter(actor_pages)))})
        localized = {}
        for row in candidates:
            for kind, slots in row.get("person", {}).get("localized", {}).items():
                for lang, value in slots.items():
                    localization.add_evidence(localized, kind, value, hint=lang)
        if localized:
            supplement["people"].setdefault(identity, {})["localized"] = localized
            if len(actor_pages) == 1:
                source = next(row["person_source"] for row in candidates if row.get("person_source"))
                supplement["people"][identity]["source"] = deepcopy(source)
        original = raw["fallback"][identity].get("original_name", "")
        native_candidates = [(evidence, row) for row in candidates for evidence in row.get("person", {}).get("native_evidence", [])
                             if _romanized_key(original) in {_romanized_key(name) for name in row.get("person", {}).get("romanized", [])}]
        if (snapshot.get("original_language") == "ko" and original and not _HANGUL.search(original)
                and len({evidence["value"] for evidence, row in native_candidates}) == 1):
            evidence, row = native_candidates[0]
            supplement["people"].setdefault(identity, {}).update({"name": evidence["value"], "name_source": "native_name",
                                              "native_name_evidence": evidence, "source": row["person_source"]})
        if identity in needs:
            chinese = {name for row in candidates for name in row["actor"]["names"]
                       if _HAN.search(name) and not _HANGUL.search(name) and not any(char in name for char in "()（）")}
            # Chinese variants are allowed aliases, but choose the actual actor
            # cell label as the display name rather than an arbitrary set item.
            primary = {name for row in candidates for name in _names(row["actor_raw"])["names"]
                       if _HAN.search(name) and not _HANGUL.search(name) and not any(char in name for char in "()（）")}
            if not primary:
                primary = {name for row in candidates for name in row.get("person", {}).get("chinese", [])
                           if not any(char in name for char in "()（）")}
            if len(primary) == 1:
                supplement["people"].setdefault(identity, {}).update({"name_cn": next(iter(primary)), "aliases": sorted(chinese),
                                                  "source": candidates[0].get("person_source", candidates[0]["source"]),
                                                  "work_source": candidates[0]["source"],
                                                  "evidence": {"actor_cell": candidates[0]["actor_raw"]}})
            elif len(primary) > 1:
                _warn(snapshot, f"TMDB 人物 {identity} 的中文姓名证据冲突")
        cast = [relation for relation in raw.get("relations", []) if str(relation.get("person_id")) == identity and relation.get("category") == "cast"]
        for relation in cast:
            proposal = _role_proposal(snapshot, identity, relation, cast, candidates, len(seen_pages), len(bindings) > MAX_WORK_PAGES)
            if proposal:
                supplement["relations"].append(proposal)
    display_values = [value for person in supplement["people"].values() for value in [person.get("name_cn", ""), *person.get("aliases", [])]]
    display_values.extend(row.get("character_name_cn", "") for row in supplement["relations"])
    mapping, conversion = wiki.simplified_display(display_values, deadline=deadline, warnings=snapshot.setdefault("warnings", []))
    if conversion is not None:
        added = len(providers._json_bytes(conversion))
        if size + added > MAX_SUPPLEMENT_BYTES or snapshot_size + size + added > providers.MAX_SNAPSHOT_BYTES - 65536:
            mapping, conversion = {}, None
            _warn(snapshot, "简体显示转换证据达到快照大小限制，保留原始维基文字")
    mapping = {**role_mapping, **mapping}
    for person in supplement["people"].values():
        original_chinese = person.get("name_cn", "")
        localized = person.setdefault("localized", {})
        wiki.add_localized_display(localized, "name", original_chinese, mapping, conversion)
        if person.get("name_cn"):
            person["name_cn"] = mapping.get(person["name_cn"], person["name_cn"])
        if person.get("aliases"):
            person["aliases"] = list(dict.fromkeys([*(mapping.get(value, value) for value in person["aliases"]), *person["aliases"]]))[:100]
        for alias in person.get("aliases", []):
            hint = "zh-Hans" if alias in mapping.values() else ""
            if not hint and isinstance(conversion, dict):
                hint = conversion.get("original_languages", {}).get(alias, "")
            localization.add_evidence(localized, "aliases", [alias], hint=hint)
        if person.get("source", {}).get("pageid"):
            source = person["source"]
            person["field_sources"] = {f"localized.{kind}.{lang}": {
                "provider": "wikipedia", "external_id": str(source["pageid"]), "source_url": source["url"]}
                for kind in ("name", "summary") for lang in localized.get(kind, {})}
        if conversion:
            person.setdefault("source", {})["display_variant"] = wiki.DISPLAY_VARIANT
    for relation in supplement["relations"]:
        value = relation.get("character_name_cn", "")
        localized = {}
        localization.add_evidence(localized, "name", relation.get("character_name", ""))
        evidence = role_conversion if value in role_mapping else conversion
        wiki.add_localized_display(localized, "name", value, mapping, evidence)
        relation["localized"] = localization.validate(localized)
        if value:
            relation["character_name_cn"] = mapping.get(value, value)
        if evidence:
            relation["source"]["display_variant"] = wiki.DISPLAY_VARIANT
    if conversion is not None:
        supplement["display_conversion"] = conversion
    if not supplement["people"] and not supplement["relations"] and rows:
        _warn(snapshot, "演员姓名或角色缺少唯一、精确的匹配证据")
    if supplement["work_pages"]:
        raw["wikipedia_people"] = supplement
        if len(providers._json_bytes(snapshot)) > providers.MAX_SNAPSHOT_BYTES:
            raw.pop("wikipedia_people", None)
            _warn(snapshot, "完整人物快照超出安全大小限制，已忽略本次维基补充")
    imported = raw.get("wikipedia_people", {})
    report_progress(f"维基百科人物补充完成：读取 {len(supplement['work_pages'])} 个作品条目、{len(supplement['person_pages'])} 个人物条目，"
                    f"提供 {sum(bool(person.get('name_cn')) for person in raw.get('wikipedia_people', {}).get('people', {}).values())} 项中文姓名、"
                    f"{sum(bool(row.get('character_name_cn')) for row in imported.get('relations', []))} 项中文角色、"
                    f"{sum(bool(row.get('character_name')) for row in imported.get('relations', []))} 项原语角色候选")
    return snapshot
