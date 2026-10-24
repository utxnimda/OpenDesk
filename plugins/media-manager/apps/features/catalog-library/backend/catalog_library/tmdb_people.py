"""TMDB TV cast/crew adaptation into the independent local people repository.

Only preview uses the network. The immutable snapshot includes the exact season
and optional episode scope; failed fields/lists never clear existing relations.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from copy import deepcopy
from datetime import date, datetime, timezone
import re
import time

from people_catalog.model import source_identity, validate_people_refs
from people_catalog.source_links import build_source_index
from work_catalog_yaml.operation_progress import report_progress

from . import people_assets, providers, tmdb_source, tmdb_people_retry

MAX_PEOPLE = 300
MAX_RELATIONS = 800
MAX_EPISODE_REQUESTS = 100
TOTAL_TIMEOUT = 120
_JOBS = {"Director": "导演", "Writer": "编剧", "Screenplay": "编剧", "Story": "原案", "Producer": "制片人",
         "Executive Producer": "执行制片人", "Original Music Composer": "音乐", "Director of Photography": "摄影",
         "Editor": "剪辑", "Casting": "选角", "Production Design": "美术", "Costume Design": "服装设计",
         "Sound": "音效", "Creator": "创作者"}


def scope(subject_id, season_number, episode_range=None):
    value = {"provider": "tmdb", "subject_id": str(providers._positive_id(subject_id)),
             "season_number": tmdb_source.parse_season_number(season_number)}
    if episode_range is not None:
        value["episode_range"] = providers._episode_range(episode_range)
    return value


def warn(warnings, subject_id, season, identity, field, reason, *, report=True):
    message = f"TMDB 电视剧 {subject_id} 第 {season} 季 / {identity} / {field}：{str(reason)[:1000]}；本次缺失字段留空，已有资料与关联保留"
    if message not in warnings:
        warnings.append(message)
        if report:
            report_progress(message, context={"stage": "TMDB 人物来源警告", "object": str(identity),
                                             "action": field, "reason": str(reason)[:1000], "status": "warning"})
    return message


def _optional(path, *, deadline, warnings, subject_id, season, field, language="zh-CN", failures=None):
    def failed(reason):
        message = warn(warnings, subject_id, season, path, field, reason)
        if failures is not None:
            tmdb_people_retry.record_failure(failures, path=path, language=language, field=field,
                                             reason=reason, warning=message)
        return None
    if time.monotonic() >= deadline:
        return failed("已达到人物同步总时间上限")
    try:
        return tmdb_source._request_json(path, query={"language": language}, deadline=deadline)
    except (ValueError, OSError, RuntimeError) as exc:
        if isinstance(exc, ValueError) and any(key in str(exc) for key in ("安全大小", "重定向", "不支持的 TMDB API 路径")):
            raise
        return failed(exc)


def _parallel(items, function):
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="nimda-tmdb-people") as pool:
        futures = [pool.submit(copy_context().run, function, item) for item in items]
        try:
            result, total_bytes = [], 0
            for future in as_completed(futures):
                value = future.result()
                total_bytes += len(providers._json_bytes(value))
                if total_bytes > providers.MAX_SNAPSHOT_BYTES:
                    raise ValueError("TMDB 人物来源超过安全大小限制")
                result.append(value)
            return result
        except BaseException:
            for future in futures:
                future.cancel()
            raise


def _chinese(value):
    """A localized Chinese value, never a Hangul/Kana alias posing as Chinese."""
    return isinstance(value, str) and bool(re.search(r"[\u3400-\u9fff]", value)) and not re.search(r"[\u3040-\u30ff\uac00-\ud7af]", value)


def _credit_id(entry):
    value = entry.get("credit_id")
    return value if isinstance(value, str) and 0 < len(value) <= 200 else ""


def _credit_index(result, episode):
    """An ambiguous ID is deliberately unmatchable; row order is not identity."""
    indexed = {}
    for category in ("cast", "crew"):
        rows = result.get(category, []) if isinstance(result, dict) else []
        if not isinstance(rows, list) or len(rows) > MAX_PEOPLE:
            continue
        for row in rows:
            if not isinstance(row, dict) or type(row.get("id")) is not int or row.get("adult"):
                continue
            entries = row.get("roles" if category == "cast" else "jobs") if episode is None else [row]
            if not isinstance(entries, list) or len(entries) > 100:
                continue
            for entry in entries:
                if not isinstance(entry, dict) or not _credit_id(entry):
                    continue
                key = (category, row["id"], _credit_id(entry))
                indexed[key] = None if key in indexed else entry
    return indexed


def fetch_snapshot(subject_id, *, season_number, episode_range=None, deadline=None, season_data=None, original_language=None, _requester=None):
    subject_id = providers._positive_id(subject_id)
    season_number = tmdb_source.parse_season_number(season_number)
    interval = providers._episode_range(episode_range)
    deadline = min(time.monotonic() + TOTAL_TIMEOUT, deadline) if deadline is not None else time.monotonic() + TOTAL_TIMEOUT
    warnings, unavailable, profiles, relations, fallback, localized_fallback = [], set(), {}, [], {}, {}
    failures = []

    def optional(path, **kwargs):
        return (_requester or _optional)(path, failures=failures, **kwargs)

    season = season_data
    if season is None:
        season = optional(f"/tv/{subject_id}/season/{season_number}", deadline=deadline, warnings=warnings,
                           subject_id=subject_id, season=season_number, field="季身份")
    if season is not None and (not isinstance(season, dict) or season.get("season_number") != season_number):
        raise ValueError("TMDB 人物季身份与请求不一致")
    subject = None
    if original_language is None and season is not None:
        subject = optional(f"/tv/{subject_id}", deadline=deadline, warnings=warnings,
                            subject_id=subject_id, season=season_number, field="作品原语言")
        if subject is not None and (not isinstance(subject, dict) or type(subject.get("id")) is not int or subject["id"] != subject_id):
            raise ValueError("TMDB 人物作品身份与请求不一致")
        original_language = (subject or {}).get("original_language")
    if not isinstance(original_language, str) or not re.fullmatch(r"[a-z]{2,3}(?:-[A-Z]{2})?", original_language):
        original_language = ""
    original_query = "zh-CN" if original_language in {"zh", "zh-CN"} else original_language
    report_progress(f"正在获取 TMDB {subject_id} 第 {season_number} 季演员与制作人员")
    jobs = []
    if season is None:
        unavailable.update(("cast", "crew"))
    elif interval is None:
        jobs = [(f"/tv/{subject_id}/season/{season_number}/aggregate_credits", providers._positive_id(season.get("id")), None)]
    else:
        episodes = tmdb_source._season_rows(season, subject_id, season_number)
        chosen = [episode for episode in episodes if interval["start"] <= episode["episode_number"] <= interval["end"]]
        if not chosen or len(chosen) > MAX_EPISODE_REQUESTS:
            warn(warnings, subject_id, season_number, "episode_range", "演员范围", "范围没有有效章节或超过 100 集；不会改用整季演员")
            unavailable.update(("cast", "crew"))
        else:
            jobs = [(f"/tv/{subject_id}/season/{season_number}/episode/{episode['episode_number']}/credits", episode["id"], episode["episode_number"]) for episode in chosen]

    def credit(job):
        path, identity, episode = job
        localized = optional(path, deadline=deadline, warnings=warnings, subject_id=subject_id, season=season_number, field="演员/制作人员列表")
        original = (localized if original_query == "zh-CN" else
                    optional(path, deadline=deadline, warnings=warnings, subject_id=subject_id, season=season_number,
                              field="原语言演员/制作人员列表", language=original_query) if original_query else None)
        for result in (localized, original):
            if result is not None and (not isinstance(result, dict) or type(result.get("id")) is not int or result["id"] != identity):
                raise ValueError("TMDB 人物列表与季或章节身份不一致")
        return path, episode, localized, original

    credit_results = sorted(_parallel(jobs, credit), key=lambda value: value[0])
    for path, episode, localized, original in credit_results:
        localized_index = _credit_index(localized, episode)
        original_index = _credit_index(original, episode)
        for category in ("cast", "crew"):
            local_rows = localized.get(category, []) if isinstance(localized, dict) else []
            if isinstance(local_rows, list) and len(local_rows) <= MAX_PEOPLE:
                for row in local_rows:
                    if isinstance(row, dict) and type(row.get("id")) is int and not row.get("adult"):
                        localized_fallback.setdefault(str(row["id"]), deepcopy(row))
        for category in ("cast", "crew"):
            # Original-language credits establish the official relation. A
            # failed optional translation cannot remove otherwise valid cast.
            result = original if isinstance(original, dict) and isinstance(original.get(category), list) else localized
            rows = result.get(category) if isinstance(result, dict) else None
            if not isinstance(rows, list) or len(rows) > MAX_PEOPLE or any(not isinstance(row, dict) for row in rows):
                unavailable.add(category)
                warn(warnings, subject_id, season_number, path, category, "列表缺失或格式/数量无效")
                continue
            for row in rows:
                identity = providers._positive_id(row.get("id"), "person_id")
                if row.get("adult"):
                    unavailable.add(category)
                    warn(warnings, subject_id, season_number, identity, category, "人物不属于公开导入范围")
                    continue
                fallback.setdefault(identity, deepcopy(row))
                entries = row.get("roles" if category == "cast" else "jobs") if episode is None else [row]
                if not isinstance(entries, list) or len(entries) > 100 or any(not isinstance(entry, dict) for entry in entries):
                    unavailable.add(category)
                    warn(warnings, subject_id, season_number, identity, category, "饰演角色或职责列表缺失/无效")
                    continue
                for entry in entries:
                    field = "character" if category == "cast" else "job"
                    try:
                        value = providers._text(entry.get(field), 1000 if category == "cast" else 200)
                    except ValueError as exc:
                        unavailable.add(category)
                        warn(warnings, subject_id, season_number, identity, field, exc)
                        continue
                    count = entry.get("episode_count")
                    participation = str(episode) if episode is not None else f"{count} 集" if type(count) is int and 0 < count <= 99999 else ""
                    relation = {"person_id": identity, "category": category, "value": value, "eps": participation}
                    credit_id = _credit_id(entry)
                    if credit_id:
                        relation["credit_id"] = credit_id
                    if category == "cast":
                        credit_key = (category, identity, credit_id)
                        translated = (localized_index.get(credit_key) if credit_id and
                                      (result is localized or original_index.get(credit_key) is not None) else None)
                        # Missing IDs must not be matched by actor or position.
                        # A zh-CN fallback already represents its own row.
                        chinese = entry.get("character") if result is localized else (translated or {}).get("character")
                        if _chinese(chinese):
                            relation["value_cn"] = providers._text(chinese, 1000)
                        relation["original_available"] = result is original
                    if relation not in relations:
                        relations.append(relation)
                    if len(relations) > MAX_RELATIONS:
                        raise ValueError("TMDB 人物关系超过安全数量限制")
    if len(fallback) > MAX_PEOPLE:
        raise ValueError("TMDB 人物总数超过 300 项安全限制")
    if interval is not None:
        grouped = {}
        for relation in relations:
            key = (relation["person_id"], relation["category"], relation["value"], relation.get("credit_id", ""), relation.get("value_cn", ""))
            if key not in grouped:
                grouped[key] = (deepcopy(relation), set())
            grouped[key][1].add(int(relation["eps"]))
        relations = [{**relation, "eps": ", ".join(str(number) for number in sorted(episodes))}
                     for _, (relation, episodes) in sorted(grouped.items())]

    def person(identity):
        value = optional(f"/person/{identity}", deadline=deadline, warnings=warnings, subject_id=subject_id, season=season_number, field="人物详情")
        if value is not None and (not isinstance(value, dict) or type(value.get("id")) is not int or value["id"] != identity):
            raise ValueError("TMDB 人物详情 ID 与请求不一致")
        if value is not None and value.get("adult"):
            warn(warnings, subject_id, season_number, identity, "人物详情", "人物不属于公开导入范围")
            value = None
        return identity, value

    for identity, value in _parallel(sorted(fallback), person):
        profiles[str(identity)] = value
        report_progress(f"已读取 TMDB 人物 {len(profiles)}/{len(fallback)}", completed=len(profiles), total=len(fallback), unit="人物")
    snapshot = {"schema_version": 1, "provider": "tmdb", "kind": "people", "subject_id": subject_id,
                "season_number": season_number, "episode_range": interval, "original_language": original_language,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "warnings": sorted(set(warnings)),
                "fetch_warnings": sorted(set(warnings)), "retry_failures": sorted(failures, key=lambda value: value["id"]),
                "raw": {"credits": [(path, episode, localized) for path, episode, localized, _ in credit_results],
                        "credits_original": [(path, episode, original) for path, episode, _, original in credit_results],
                        "subject": subject, "season": deepcopy(season), "relations": relations, "fallback": {str(key): value for key, value in fallback.items()},
                        "fallback_localized": localized_fallback,
                        "details": profiles, "unavailable_lists": sorted(unavailable)}}
    tmdb_people_retry.label_failures(snapshot)
    if len(providers._json_bytes(snapshot)) > providers.MAX_SNAPSHOT_BYTES:
        raise ValueError("TMDB 人物快照超过安全大小限制")
    return snapshot


def prepare_portraits(snapshot, repository, *, deadline=None, identities=None):
    deadline = min(time.monotonic() + 45, deadline) if deadline is not None else time.monotonic() + 45
    raw, warnings = snapshot["raw"], snapshot["warnings"]
    selected = sorted(raw["fallback"]) if identities is None else sorted(set(identities))
    if any(identity not in raw["fallback"] for identity in selected):
        raise ValueError("TMDB 人物图片重试身份不属于当前快照")
    failures = snapshot.setdefault("retry_failures", [])

    def acquire(identity):
        fallback = raw["fallback"][identity]
        profile = raw["details"].get(identity) or {}
        image = profile.get("profile_path") or fallback.get("profile_path")
        if not image:
            return identity, None
        previous = repository.read(source_identity("tmdb", "person", identity))
        try:
            if time.monotonic() >= deadline:
                raise ValueError("已达到人物图片同步总时间上限")
            result = people_assets.acquire_tmdb_avatar({"profile_path": image}, user_agent=providers.USER_AGENT,
                     root=repository.root, deadline=deadline, previous=(previous or {}).get("avatar"))
            if identities is not None:
                report_progress(f"TMDB 人物 {identity} 图片重试成功", context={
                    "stage": "TMDB 人物失败项重试", "object": str(identity), "action": "avatar", "status": "success"})
            return identity, result
        except (ValueError, OSError, RuntimeError) as exc:
            message = warn(warnings, snapshot["subject_id"], snapshot["season_number"], identity, "avatar", exc)
            tmdb_people_retry.record_failure(failures, path=f"/person/{identity}", language="", field="avatar",
                                             reason=exc, warning=message)
            if identities is not None:
                report_progress(f"TMDB 人物 {identity} 图片重试仍失败：{str(exc)[:1000]}", context={
                    "stage": "TMDB 人物失败项重试", "object": str(identity), "action": "avatar", "status": "warning"})
            return identity, None

    avatars = {} if identities is None else deepcopy(snapshot.get("avatars", {}))
    avatars.update(dict(_parallel(selected, acquire)))
    snapshot["avatars"] = avatars
    failures.sort(key=lambda value: value["id"])
    tmdb_people_retry.label_failures(snapshot)


def retry_snapshot(snapshot, failure_ids=None, *, repository, deadline=None):
    """Create a fresh preview snapshot, requesting only selected failed items."""
    return tmdb_people_retry.retry_snapshot(snapshot, failure_ids, repository=repository, deadline=deadline)


def _profile(raw, fallback, avatar, warnings, snapshot, *, report):
    from .people import _PROFILE_FIELDS
    from people_catalog import localization
    identity = providers._positive_id(fallback.get("id"))
    raw = raw or {}
    missing = set()

    def optional(field, value, parser, default):
        try:
            result = parser(value)
        except (ValueError, TypeError) as exc:
            if value is not None:
                warn(warnings, snapshot["subject_id"], snapshot["season_number"], identity, field, exc, report=report)
            result = deepcopy(default)
        if result in (None, "", [], {}):
            missing.add(field)
        return result

    name = optional("name", raw.get("name") or fallback.get("name") or fallback.get("original_name"), lambda value: providers._text(value, 1000), "")
    original = optional("name", fallback.get("original_name") or name, lambda value: providers._text(value, 1000), name)
    def aliases_list(values):
        if not isinstance(values, list) or len(values) > 200:
            raise ValueError("别名必须为最多 200 项数组")
        return [providers._text(value, 1000) for value in values]

    aliases = optional("aliases", raw.get("also_known_as"), aliases_list, [])
    aliases = list(dict.fromkeys([value for value in [name, *aliases] if value and value != original]))[:200]
    localized_rows = snapshot.get("raw", {}).get("fallback_localized", {})
    if not isinstance(localized_rows, dict):
        raise ValueError("TMDB 中文人物后备快照无效")
    localized = localized_rows.get(str(identity), {})
    if not isinstance(localized, dict):
        raise ValueError("TMDB 中文人物后备字段无效")
    localized_name = optional("name_cn", localized.get("name"), lambda value: providers._text(value, 1000), "")
    credit_name = optional("name_cn", fallback.get("name"), lambda value: providers._text(value, 1000), "")
    chinese = next((value for value in [name, localized_name, credit_name, *aliases] if _chinese(value)), "")
    summary = optional("summary", raw.get("biography"), lambda value: providers._text(value, 100000), "")
    birthday = optional("birth_date", raw.get("birthday"), lambda value: {"year": date.fromisoformat(value).year, "month": date.fromisoformat(value).month, "day": date.fromisoformat(value).day}, {})
    gender = {1: "女性", 2: "男性", 3: "非二元"}.get(raw.get("gender") if type(raw.get("gender")) is int else None, "")
    career = [raw["known_for_department"]] if isinstance(raw.get("known_for_department"), str) and len(raw["known_for_department"]) <= 100 else []
    infobox = []
    for key, label in (("place_of_birth", "出生地"), ("deathday", "逝世日期")):
        value = optional("infobox", raw.get(key), lambda entry: providers._text(entry, 1000), "")
        if value:
            infobox.append({"key": label, "value": value})
    fields = {"name": original, "name_cn": chinese, "summary": summary, "aliases": aliases, "infobox": infobox,
              "career": career, "kind": "individual", "gender": gender, "birth_date": birthday, "avatar": avatar or {}}
    missing = {key for key in _PROFILE_FIELDS if not fields[key]}
    multilingual = {}
    localization.add_evidence(multilingual, "name", original, hint=snapshot.get("original_language", ""))
    for value in (name, localized_name, chinese):
        localization.add_evidence(multilingual, "name", value, hint="zh-Hans")
    for alias in aliases:
        localization.add_evidence(multilingual, "name", alias)
        localization.add_evidence(multilingual, "aliases", [alias])
    # The existing person-details request is explicitly zh-CN. Script checking
    # keeps an untranslated Latin fallback out of the Chinese/English slots.
    localization.add_evidence(multilingual, "summary", summary, hint="zh-Hans")
    fields["localized"] = localization.validate(multilingual)
    return {"id": source_identity("tmdb", "person", identity), "schema_version": 1, "type": "person", **fields,
            "_unavailable_fields": sorted(missing)}


def build(snapshot, snapshot_id, record, repository, *, warnings=None, report=True):
    from people_catalog import localization, profile_variants
    from .people import _merge_entity, _PROFILE_FIELDS
    from . import role_catalog
    if snapshot.get("kind") != "people":
        raise ValueError("来源快照不是人物同步快照")
    target_scope = scope(snapshot.get("subject_id"), snapshot.get("season_number"), snapshot.get("episode_range"))
    raw = snapshot.get("raw")
    if (not isinstance(raw, dict) or not isinstance(raw.get("details"), dict) or not isinstance(raw.get("fallback"), dict)
            or not isinstance(raw.get("relations"), list) or len(raw["relations"]) > MAX_RELATIONS or len(raw["fallback"]) > MAX_PEOPLE
            or not isinstance(raw.get("unavailable_lists"), list) or any(value not in {"cast", "crew"} for value in raw["unavailable_lists"])):
        raise ValueError("TMDB 人物快照结构无效")
    warnings = warnings if warnings is not None else []
    supplement = raw.get("wikipedia_people") or {}
    if (not isinstance(supplement, dict) or not isinstance(supplement.get("people", {}), dict)
            or len(supplement.get("people", {})) > MAX_PEOPLE
            or not isinstance(supplement.get("relations", []), list) or len(supplement.get("relations", [])) > MAX_RELATIONS):
        raise ValueError("维基百科人物补充快照结构无效")
    old_refs = validate_people_refs(record.get("people_refs", []))
    source_index = build_source_index(repository)
    refs = [ref for ref in old_refs if ref.get("source") != target_scope or ("cast" if "character_name" in ref else "crew") in raw["unavailable_lists"]]
    entities, changes, skipped = [], [], set()
    for external_id, fallback in sorted(raw["fallback"].items(), key=lambda value: int(value[0])):
        if not isinstance(fallback, dict) or str(providers._positive_id(fallback.get("id"))) != external_id:
            raise ValueError("TMDB 人物快照身份无效")
        detail = raw["details"].get(external_id)
        if detail is not None and (not isinstance(detail, dict) or str(providers._positive_id(detail.get("id"))) != external_id):
            raise ValueError("TMDB 人物详情身份不一致")
        identity = source_index.resolve("tmdb", "person", external_id)
        previous = repository.read(identity)
        incoming = _profile(detail, fallback, snapshot.get("avatars", {}).get(external_id), warnings, snapshot, report=report)
        incoming["id"] = identity
        profile_variants.capture(incoming, incoming, {"provider": "tmdb", "external_id": external_id}, seed_current=False)
        enrichment = supplement.get("people", {}).get(external_id, {})
        if not isinstance(enrichment, dict):
            raise ValueError("维基百科人物补充字段无效")
        from . import wikipedia_people
        reparsed_names = wikipedia_people.reparse_person_name_evidence(supplement, external_id)
        if reparsed_names is not None:
            enrichment = {**enrichment, **reparsed_names}
        if not incoming["name_cn"] and _chinese(enrichment.get("name_cn")):
            incoming["name_cn"] = providers._text(enrichment["name_cn"], 1000)
        wiki_aliases = enrichment.get("aliases", [])
        if not isinstance(wiki_aliases, list) or len(wiki_aliases) > 200:
            raise ValueError("维基百科人物补充别名格式无效")
        incoming["aliases"] = list(dict.fromkeys([*incoming["aliases"], *(
            providers._text(alias, 1000) for alias in wiki_aliases)]))[:200]
        # Only an explicitly evidenced native name may supersede TMDB's
        # romanization. Merely appearing in a Korean show's cast is no evidence.
        if enrichment.get("name_source") == "native_name" and enrichment.get("name"):
            original_name = providers._text(enrichment["name"], 1000)
            if incoming["name"] and incoming["name"] != original_name:
                incoming["aliases"] = list(dict.fromkeys([*incoming["aliases"], incoming["name"]]))[:200]
            incoming["name"] = original_name
        enrichment_localized = localization.project(enrichment)
        for kind, slots in enrichment_localized.items():
            for lang, value in slots.items():
                path = f"localized.{kind}.{lang}"
                evidence = enrichment.get("field_sources", {}).get(path, {})
                if (kind == "name" and isinstance(value, str) and value and evidence.get("provider") == "wikipedia"
                        and evidence.get("external_id")):
                    previous_name = localization.field_value(incoming, path)
                    localization.set_field(incoming, path, value)
                    incoming.setdefault("field_sources", {})[path] = deepcopy(evidence)
                    if previous_name and previous_name != value:
                        aliases_path = "localized.aliases." + lang
                        localization.set_field(incoming, aliases_path, localization.merge_aliases(
                            localization.field_value(incoming, aliases_path), [previous_name]))
                    if lang in {"zh-Hans", "zh-Hant"}:
                        incoming["name_cn"] = value
                else:
                    localization.add_evidence(incoming["localized"], kind, value, hint=lang)
        localization.add_evidence(incoming["localized"], "name", incoming["name"])
        localization.add_evidence(incoming["localized"], "name", incoming["name_cn"],
                                  hint=localization.chinese_name_hint(incoming))
        incoming["_unavailable_fields"] = sorted(key for key in _PROFILE_FIELDS if not incoming[key])
        if not incoming["name"] and not previous:
            skipped.add(identity)
            warn(warnings, snapshot["subject_id"], snapshot["season_number"], identity, "name", "缺少可确认姓名，跳过新增人物", report=report)
            continue
        source = {"provider": "tmdb", "entity_type": "person", "external_id": external_id, "snapshot_id": snapshot_id,
                  "fetched_at": snapshot["fetched_at"], "import_scope": target_scope}
        merged = _merge_entity(previous, incoming, source=source)
        entities.append(merged)
        changed = [key for key in _PROFILE_FIELDS if (previous or {}).get(key) != merged.get(key)]
        changes.append({"id": identity, "type": "person", "name": merged["name"], "action": "create" if previous is None else "update" if changed else "unchanged",
                        "changed_fields": changed, "diff": [{"field": key, "before": deepcopy((previous or {}).get(key)), "after": deepcopy(merged.get(key))} for key in changed],
                        "preserved_fields": [key for key in _PROFILE_FIELDS if merged.get(key) != incoming.get(key)]})
    role_name_evidence = {}
    for relation in raw["relations"]:
        if not isinstance(relation, dict):
            raise ValueError("TMDB 人物关系格式无效")
        identity = source_index.resolve("tmdb", "person", providers._positive_id(relation.get("person_id")))
        if identity in skipped:
            continue
        if relation.get("category") not in {"cast", "crew"}:
            raise ValueError("TMDB 人物关系类别无效")
        ref = {"id": identity, "type": "person", "source": target_scope, "eps": providers._text(relation.get("eps"), 2000),
               "role": "演员" if relation["category"] == "cast" else _JOBS.get(relation["value"], relation["value"])}
        wiki_original = False
        if relation["category"] == "cast":
            from people_catalog import portrayal
            localized_role = {}
            localization.add_evidence(localized_role, "name", relation["value"], hint=snapshot.get("original_language", ""))
            localization.add_evidence(localized_role, "name", relation.get("value_cn", ""), hint="zh-Hans")
            ref["character_name"] = providers._text(relation["value"], 1000)
            if qualifier := portrayal.parse_qualifier(relation["value"]):
                ref["qualifier"] = qualifier
            if "value_cn" in relation:
                ref["character_name_cn"] = providers._text(relation["value_cn"], 1000)
            matches = [entry for entry in supplement.get("relations", []) if isinstance(entry, dict)
                       and str(entry.get("person_id")) == str(relation["person_id"])
                       and entry.get("original_value") == relation["value"]
                       and (not entry.get("credit_id") or entry["credit_id"] == relation.get("credit_id"))]
            if len(matches) == 1:
                for field in ("qualifier", "role_group"):
                    if field in matches[0]:
                        ref[field] = deepcopy(matches[0][field])
                for lang, value in localization.validate(matches[0].get("localized", {})).get("name", {}).items():
                    localization.add_evidence(localized_role, "name", value, hint=lang)
                if not ref.get("character_name_cn") and _chinese(matches[0].get("character_name_cn")):
                    ref["character_name_cn"] = providers._text(matches[0]["character_name_cn"], 1000)
                if matches[0].get("character_name"):
                    ref["character_name"] = providers._text(matches[0]["character_name"], 1000)
                    wiki_original = True
        if relation.get("credit_id"):
            ref["credit_id"] = providers._text(relation["credit_id"], 200)
        if relation["category"] == "cast":
            actor_refs = [old for old in old_refs if old.get("source") == target_scope and old["id"] == identity and "character_name" in old]
            previous_refs = [old for old in actor_refs if old.get("credit_id") == ref["credit_id"]] if ref.get("credit_id") else []
            legacy_manual_name = False
            if not previous_refs:
                legacy = [old for old in actor_refs if not old.get("credit_id")]
                incoming_names = {relation["value"], relation.get("value_cn"), ref["character_name"]}
                previous_refs = [old for old in legacy if old["character_name"] in incoming_names]
                # Older references have no credit identity. A unique actor/role
                # pair is safe to migrate; ambiguous multi-role actors are not.
                actor_relations = [row for row in raw["relations"] if isinstance(row, dict) and row.get("person_id") == relation["person_id"] and row.get("category") == "cast"]
                if not previous_refs and len(legacy) == 1 and len(actor_relations) == 1:
                    previous_refs = legacy
                    legacy_manual_name = legacy[0]["character_name"] not in incoming_names
            old = previous_refs[0] if len(previous_refs) == 1 else {}
            for field in ("qualifier", "role_group"):
                if field not in ref and field in old:
                    ref[field] = deepcopy(old[field])
            if old.get("character_id"):
                ref["character_id"] = old["character_id"]
            # Legacy snapshots keep their exact historical representation.
            # New bilingual refs retain imported values to distinguish manual
            # edits (including empty strings) from a missing remote field.
            if ref.get("credit_id") or "character_name_cn" in ref or "source_values" in old:
                values = {key: ref[key] for key in ("character_name", "character_name_cn") if key in ref}
                previous_values = old.get("source_values", {})
                for key in ("character_name", "character_name_cn"):
                    if key not in old:
                        continue
                    if key == "character_name" and ((relation.get("original_available") is False and not wiki_original) or not ref[key] or legacy_manual_name):
                        ref[key] = old[key]
                        if key in previous_values:
                            values[key] = previous_values[key]
                    elif key not in ref:
                        ref[key] = old[key]
                        if key in previous_values:
                            values[key] = previous_values[key]
                    elif key in previous_values and old[key] != previous_values[key]:
                        ref[key] = old[key]
                    elif key == "character_name_cn" and key not in previous_values:
                        ref[key] = old[key]
                ref["source_values"] = values
            baseline = ref.get("source_values", ref)
            role_name_evidence[(identity, baseline.get("character_name", ""),
                                baseline.get("character_name_cn", ""))] = localized_role
        if relation["category"] in raw["unavailable_lists"] and any(
                old.get("source") == target_scope and all(old.get(key) == ref.get(key) for key in ("id", "role", "character_name")) for old in refs):
            continue
        if ref not in refs:
            refs.append(ref)
    # Preserved relations are part of the same revision/rollback transaction.
    known = {entity["id"] for entity in entities}
    pending = [ref["id"] for ref in refs]
    while pending:
        identity = pending.pop()
        if identity in known:
            continue
        known.add(identity)
        previous = repository.read(identity)
        if previous:
            entities.append(previous)
            pending.extend(actor["person_id"] for actor in previous.get("voice_actors", []))
    updated = deepcopy(record)
    updated["people_refs"] = validate_people_refs(refs)
    updated, entities, role_changes = role_catalog.materialize(updated, entities, repository, warnings,
        _source_index=source_index, _name_evidence=role_name_evidence)
    return updated, entities, [*changes, *role_changes]
