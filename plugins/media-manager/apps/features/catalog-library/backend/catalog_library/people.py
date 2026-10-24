"""Explicit source people imports; normal views read only independent local YAML."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import time

from people_catalog.model import source_identity, validate_entity, validate_people_refs
from people_catalog.repository import PeopleRepository, safe_path
from people_catalog.source_links import build_source_index
from work_catalog_yaml.operation_progress import operation_context, report_progress

from . import people_assets, people_portraits, providers, service
from .people_birth_dates import merge_birth_date, normalize_birth_date

MAX_CHARACTERS = 200
MAX_PERSONS = 300
MAX_ACTORS_PER_CHARACTER = 30
IMPORT_TIMEOUT = 120
_PROFILE_FIELDS = ("name", "name_cn", "summary", "aliases", "infobox", "career", "kind", "gender", "birth_date", "avatar")


def _warn(warnings, subject_id, object_id, field, reason, *, report=True):
    message = (f"Bangumi 作品 {subject_id} / {object_id} / {field}：{str(reason)[:1000]}；"
               "未获取的字段留空，已有资料保留")
    if message not in warnings:
        warnings.append(message)
        if report:
            report_progress(message, context={"stage": "人物来源警告", "object": object_id,
                            "action": field, "reason": str(reason)[:1000], "status": "warning"})


def _request_optional(path, *, deadline, warnings, subject_id, object_id, field):
    # Only the remote read is optional. Identity, size/path safety, local data,
    # snapshot integrity and CAS failures must never pass through this handler.
    try:
        value = providers._request_json(path, deadline=deadline)
        if value is None:
            _warn(warnings, subject_id, object_id, field, "来源响应为空")
        return value
    except (ValueError, OSError) as exc:
        if isinstance(exc, ValueError) and any(part in str(exc) for part in (
                "安全大小", "重定向", "不支持的 Bangumi API 路径")):
            raise
        _warn(warnings, subject_id, object_id, field, exc)
        return None


def _scope(subject_id):
    return {"provider": "bangumi", "subject_id": str(subject_id)}


def _root_scope(repository):
    return hashlib.sha256(str(repository.root).encode("utf-8")).hexdigest()


def _proposal_hash(record, entities):
    return hashlib.sha256(providers._json_bytes({"record": record, "entities": entities})).hexdigest()


def with_supplement_selections(proposals, selections=None):
    """Attach explicit choices only after callers verify their signed proposals."""
    selections = {} if selections is None else selections
    allowed = {row["snapshot_id"] for row in proposals if row.get("selection_only")}
    if (not isinstance(selections, dict) or len(selections) > 32 or set(selections) - allowed or
            any(not isinstance(rows, list) or len(rows) > 1000 for rows in selections.values())):
        raise ValueError("人物选择必须属于本次预览；新增也必须明确选择已预览候选，不能采用其他快照")
    result = deepcopy(proposals)
    for proposal in result:
        if proposal.get("selection_only"):
            proposal["_people_selections"] = deepcopy(selections.get(proposal["snapshot_id"], []))
    return result


def _subject(body, item):
    bindings = {str(row.get("external_id")) for row in item["record"].get("source_refs", []) if row.get("provider") == "bangumi"}
    if body.get("subject_id") is not None:
        subject_id = providers._positive_id(body["subject_id"])
        if str(subject_id) not in bindings:
            raise ValueError("请先关联并确认该 Bangumi 作品来源，再更新人物资料")
        return subject_id
    if len(bindings) != 1:
        raise ValueError("请先关联 Bangumi 来源；存在多个来源时请选择要更新的作品")
    return providers._positive_id(next(iter(bindings)))


def _selection(body, item):
    """Resolve exactly one enabled binding, never guess a provider or season."""
    from .source_bindings import normalized_refs
    from . import tmdb_source
    provider = body.get("provider", "bangumi")
    if provider not in {"bangumi", "tmdb", "wikipedia"}:
        raise ValueError("当前来源不支持人物同步，请选择 Bangumi、TMDB 或维基百科")
    rows = [row for row in normalized_refs(item["record"]) if row.get("provider") == provider and row.get("enabled") is not False]
    binding = body.get("binding_id")
    if binding is not None:
        if not isinstance(binding, str) or not binding:
            raise ValueError("人物来源 binding_id 无效")
        rows = [row for row in rows if row["binding_id"] == binding]
    subject = body.get("subject_id", body.get("external_id"))
    if subject is not None:
        subject = providers._positive_id(subject)
        rows = [row for row in rows if str(row.get("external_id")) == str(subject)]
    if body.get("scope") is not None:
        rows = [row for row in rows if row.get("scope", "") == body["scope"]]
    if "episode_range" in body:
        interval = providers._episode_range(body["episode_range"])
        rows = [row for row in rows if row.get("episode_range") == interval]
    if provider == "tmdb" and "season_number" in body:
        number = tmdb_source.parse_season_number(body["season_number"])
        rows = [row for row in rows if row.get("season_number") == number]
    # Legacy Bangumi calls identify one subject rather than one episode binding;
    # all its credits are subject-wide, so duplicate scopes remain compatible.
    if provider == "bangumi" and binding is None and rows and len({row.get("external_id") for row in rows}) == 1:
        rows = rows[:1]
    if len(rows) != 1:
        raise ValueError("请先关联并启用对应作品来源；有多个来源或季时请准确选择一条人物来源")
    row = rows[0]
    result = {"provider": provider, "subject_id": providers._positive_id(row.get("external_id")), "binding_id": row["binding_id"]}
    if provider == "tmdb":
        result.update(season_number=tmdb_source.parse_season_number(row.get("season_number")), episode_range=providers._episode_range(row.get("episode_range")))
    return result


def _proposal_source(proposal, final_record):
    """Check the signed source remains present in the final work being saved."""
    provider = proposal.get("provider", "bangumi")
    if proposal.get("independent_source") is True and provider == "wikipedia":
        return {"provider": provider, "subject_id": proposal["subject_id"]}
    body = {"provider": provider, "subject_id": proposal.get("subject_id")}
    if proposal.get("binding_id") is not None:
        body["binding_id"] = proposal["binding_id"]
    if provider == "tmdb":
        body.update(season_number=proposal.get("season_number"), episode_range=proposal.get("episode_range"))
    return _selection(body, {"record": final_record})


def _rows(value, label, maximum):
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"Bangumi {label}必须为最多 {maximum} 项对象数组；未保存部分人物")
    return value


def _fetch_snapshot(subject_id, *, deadline=None):
    deadline = min(time.monotonic() + IMPORT_TIMEOUT, deadline) if deadline is not None else time.monotonic() + IMPORT_TIMEOUT
    warnings, unavailable_lists, unavailable_details, unavailable_actors = [], [], [], []
    report_progress(f"正在读取 Bangumi {subject_id} 的角色与演职员")
    lists = {}
    for key, label, maximum in (("characters", "角色列表", MAX_CHARACTERS), ("persons", "演职员列表", MAX_PERSONS)):
        value = _request_optional(f"/v0/subjects/{subject_id}/{key}", deadline=deadline,
            warnings=warnings, subject_id=subject_id, object_id=key, field=label)
        if value is None or not isinstance(value, list):
            unavailable_lists.append(key)
            if value is not None:
                _warn(warnings, subject_id, key, label, "响应不是数组")
            value = []
        lists[key] = _rows(value, label, maximum)
    characters, persons = lists["characters"], lists["persons"]
    requested = set()
    character_ids = set()
    for row in characters:
        identity = providers._positive_id(row.get("id"), "character_id")
        if identity in character_ids:
            raise ValueError("Bangumi 角色列表含重复 ID；未保存部分人物")
        character_ids.add(identity)
        requested.add(("character", identity))
        actors = row.get("actors")
        if not isinstance(actors, list) or any(not isinstance(actor, dict) for actor in actors):
            unavailable_actors.append(identity)
            _warn(warnings, subject_id, f"character:{identity}", "actors", "声优列表缺失或格式无效")
            row["actors"] = actors = []
        for actor in _rows(actors, "角色声优", MAX_ACTORS_PER_CHARACTER):
            requested.add(("person", providers._positive_id(actor.get("id"), "person_id")))
    for row in persons:
        requested.add(("person", providers._positive_id(row.get("id"), "person_id")))
    if sum(kind == "person" for kind, _ in requested) > MAX_PERSONS:
        raise ValueError(f"人物总数超过 {MAX_PERSONS} 项安全上限；未保存部分人物")
    details = {}
    snapshot_size = len(providers._json_bytes({"characters": characters, "persons": persons})) + 1024

    def fetch(identity):
        kind, external_id = identity
        key = f"{kind}:{external_id}"
        value = _request_optional(f"/v0/{'characters' if kind == 'character' else 'persons'}/{external_id}",
            deadline=deadline, warnings=warnings, subject_id=subject_id, object_id=key, field="详情")
        if value is None or not isinstance(value, dict):
            if value is not None:
                _warn(warnings, subject_id, key, "详情", "响应不是对象")
            return key, None
        if type(value.get("id")) is not int or value["id"] != external_id:
            raise ValueError(f"Bangumi {kind} {external_id} 详情 ID 或结构不一致")
        if value.get("nsfw"):
            raise ValueError(f"Bangumi {kind} {external_id} 不属于公开人物导入范围")
        return key, value

    # Small bounded parallelism keeps an ordinary cast within the total budget;
    # every HTTP read/retry still uses the shared provider's timeout/proxy policy.
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="nimda-people") as executor:
        futures = [executor.submit(copy_context().run, fetch, identity) for identity in sorted(requested)]
        try:
            for future in as_completed(futures):
                key, value = future.result()
                if value is None:
                    unavailable_details.append(key)
                    continue
                snapshot_size += len(providers._json_bytes({key: value}))
                if snapshot_size > providers.MAX_SNAPSHOT_BYTES:
                    raise ValueError("人物来源快照超过安全大小限制；未保存部分人物")
                details[key] = value
                report_progress(f"已读取人物详情 {len(details)}/{len(requested)}", completed=len(details), total=len(requested), unit="人物")
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    snapshot = {"schema_version": 1, "provider": "bangumi", "kind": "people", "subject_id": subject_id,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "warnings": sorted(warnings),
                "raw": {"characters": characters, "persons": persons, "details": details,
                        "unavailable_lists": sorted(unavailable_lists),
                        "unavailable_details": sorted(unavailable_details),
                        "unavailable_actors": sorted(unavailable_actors)}}
    if len(providers._json_bytes(snapshot)) > providers.MAX_SNAPSHOT_BYTES:
        raise ValueError("人物来源快照超过安全大小限制；未保存部分人物")
    return snapshot


def _profile(kind, raw, *, warnings=None, subject_id="", unavailable=False, avatar=None, report=True):
    from people_catalog import localization
    identity = providers._positive_id(raw.get("id"), f"{kind}_id")
    warnings = warnings if warnings is not None else []
    missing = set(_PROFILE_FIELDS) if unavailable else set()

    def optional(field, value, parser, default):
        if value is None:
            missing.add(field)
            return deepcopy(default)
        try:
            result = parser(value)
        except (ValueError, TypeError) as exc:
            missing.add(field)
            _warn(warnings, subject_id, f"{kind}:{identity}", field, exc)
            return deepcopy(default)
        if result in ("", [], {}):
            missing.add(field)
        return result

    def text(field, maximum):
        return optional(field, raw.get(field), lambda value: providers._text(value, maximum), "")

    infobox = optional("infobox", raw.get("infobox"), lambda value: _rows(value, "人物 infobox", 200), [])
    name_cn = text("name_cn", 1000)
    aliases = []
    name_evidence = []
    for item in infobox:
        if not isinstance(item.get("key"), str) or item["key"] not in {"别名", "中文名", "英文名"}:
            continue
        value = item.get("value")
        values = value if isinstance(value, list) else [value]
        for entry in values:
            val = entry.get("v") if isinstance(entry, dict) else entry
            if isinstance(val, str):
                val = optional("aliases", val, lambda value: providers._text(value, 1000), "")
                if val:
                    aliases.append(val)
                    hint = {"中文名": "zh-Hans", "英文名": "en"}.get(item["key"], "")
                    if isinstance(entry, dict):
                        hint = {"英文名": "en", "日文名": "ja", "纯假名": "ja", "韩文名": "ko",
                                "中文名": "zh-Hans"}.get(entry.get("k"), hint)
                    name_evidence.append((val, hint))
                    if item.get("key") == "中文名" and not name_cn:
                        name_cn = val
                        missing.discard("name_cn")
    if len(aliases) > 200:
        _warn(warnings, subject_id, f"{kind}:{identity}", "aliases", "人物别名超过 200 项")
        aliases = []
        missing.add("aliases")
    if not aliases:
        missing.add("aliases")
    source_type = raw.get("type")
    if type(source_type) is not int or source_type not in ({1, 2, 3, 4} if kind == "character" else {1, 2, 3}):
        missing.add("kind")
        if source_type is not None:
            _warn(warnings, subject_id, f"{kind}:{identity}", "kind", "人物/角色细分类别无效")
    kinds = ({1: "character", 2: "mechanic", 3: "ship", 4: "organization"} if kind == "character" else
             {1: "individual", 2: "corporation", 3: "association"})
    career = raw.get("career") or []
    if not isinstance(career, list) or len(career) > 200 or any(not isinstance(row, str) or len(row) > 100 for row in career):
        _warn(warnings, subject_id, f"{kind}:{identity}", "career", "字段必须为最多 200 项字符串数组")
        career = []
    if not career:
        missing.add("career")
    normalized_birth = normalize_birth_date(raw, infobox=infobox)
    birth = normalized_birth["birth_date"]
    for issue in normalized_birth["issues"]:
        if issue.get("recovered"):
            if report:
                report_progress(f"生日格式已兼容：{raw.get('name') or kind + ':' + str(identity)}；{issue['reason']}",
                    detail=f"原始来源：{issue.get('raw')}",
                    context={"stage": "人物生日校验", "object": f"{kind}:{identity}", "action": issue["field"], "reason": issue["reason"]})
        else:
            _warn(warnings, subject_id, f"{kind}:{identity}", issue["field"], issue["reason"], report=report)
    if not normalized_birth["available"]:
        missing.add("birth_date")
    if not avatar:
        missing.add("avatar")
    else:
        missing.discard("avatar")
    summary = optional("summary", raw.get("summary", raw.get("short_summary")),
                       lambda value: providers._text(value, 100000), "")
    name = text("name", 1000)
    localized = {}
    localization.add_evidence(localized, "name", name, hint="ja")
    localization.add_evidence(localized, "name", name_cn, hint="zh-Hans")
    localization.add_evidence(localized, "summary", summary, hint=raw.get("summary_language", ""))
    for alias, hint in name_evidence:
        localization.add_evidence(localized, "name", alias, hint=hint)
        localization.add_evidence(localized, "aliases", [alias], hint=hint)
    return {"id": source_identity("bangumi", kind, identity), "schema_version": 1, "type": kind,
            "kind": kinds.get(source_type, "") if type(source_type) is int else "", "name": name, "name_cn": name_cn,
            "summary": summary,
            "localized": localization.validate(localized),
            "aliases": list(dict.fromkeys(aliases)), "infobox": deepcopy(infobox), "career": career,
            "gender": text("gender", 100), "birth_date": birth, "avatar": deepcopy(avatar or {}),
            "_birth_date_result": normalized_birth, "_unavailable_fields": sorted(missing)}


def _merge_entity(previous, incoming, *, source, actors=None):
    from people_catalog import localization, profile_variants
    from . import people_infobox
    from .people_field_merge import field_merge, has_value, profile_field_merge
    if previous and previous["type"] != incoming["type"]:
        raise ValueError("人物数据库中存在类型冲突")
    merged = deepcopy(previous) if previous else {"id": incoming["id"], "schema_version": 1, "type": incoming["type"]}
    profile_variants.capture(merged, incoming, source, seed_current=bool(previous))
    provider = source["provider"]
    old_values = merged.get("source_values", {}).get(provider, {})
    if not isinstance(old_values, dict):
        raise ValueError("人物来源字段快照无效，请先修复本地人物记录")
    if incoming.get("_birth_date_result", {}).get("available"):
        # Birthday components have independent availability. A provider may
        # know the month/day while omitting a previously imported year.
        incoming = deepcopy(incoming)
        incoming["birth_date"] = merge_birth_date(old_values.get("birth_date", {}), incoming["_birth_date_result"])
        incoming["_unavailable_fields"] = [key for key in incoming.get("_unavailable_fields", []) if key != "birth_date"]
    for field in _PROFILE_FIELDS:
        # Local edits diverging from the previous imported value are deliberate.
        # Never overwrite them silently during a subsequent provider refresh.
        if field in incoming.get("_unavailable_fields", []) and field in merged:
            continue
        if field == "avatar" and merged.get("avatar") and merged.get("field_sources", {}).get("avatar", {}).get("provider") == "manual":
            continue
        if field == "aliases":
            merged[field] = field_merge(field, merged.get(field), incoming[field])["merged_value"] or []
            continue
        if field == "infobox":
            for path in people_infobox.field_paths(incoming, merged):
                decision = field_merge(path, people_infobox.field_value(merged, path), people_infobox.field_value(incoming, path))
                if decision["default_selected"]:
                    people_infobox.set_field(merged, path, decision["merged_value"])
            merged.setdefault(field, [])
            continue
        if not has_value(field, merged.get(field)) and has_value(field, incoming[field]):
            merged[field] = deepcopy(incoming[field])
            continue
        if field in merged and field in old_values and merged[field] != old_values[field]:
            # An explicitly emptied field is also a deliberate local edit.
            continue
        if field not in merged or merged[field] == old_values.get(field) or merged[field] in (None, "", [], {}):
            merged[field] = deepcopy(incoming[field])
    merged.setdefault("source_values", {})[provider] = {
        key: deepcopy(old_values[key] if key in incoming.get("_unavailable_fields", []) and key in old_values else incoming[key])
        for key in _PROFILE_FIELDS}
    # Language slots coexist. Name/summary versions follow the same source
    # preferences as confirmed imports; other genuine conflicts stay local.
    # Empty slots fill and aliases accumulate under the shared merge policy.
    localized = localization.project(previous or {})
    proposed = localization.project(incoming)
    for kind, slots in proposed.items():
        unavailable = incoming.get("_unavailable_fields", [])
        if kind in unavailable and (kind != "name" or "name_cn" in unavailable):
            continue
        for lang, value in slots.items():
            # Raw compatibility mirrors may belong to a language whose slot
            # has not been visited yet. Never let a traditional name_cn seed
            # a guessed simplified value while assembling the proven slots.
            context = {**merged, "name": "", "name_cn": "", "summary": "", "localized": localized}
            decision = profile_field_merge("localized." + kind + "." + lang, localized[kind].get(lang), value,
                                           context, incoming, source)
            if decision["default_selected"] or lang not in localized[kind]:
                localized[kind][lang] = deepcopy(decision["merged_value"])
    merged["localized"] = localization.validate(localized)
    if incoming.get("field_sources"):
        for path, evidence in incoming["field_sources"].items():
            if path == "avatar" and merged.get("avatar") and merged.get("field_sources", {}).get("avatar", {}).get("provider") == "manual":
                continue
            slot = profile_variants.slot(merged, path)
            if slot and profile_variants.effective_preferences(merged).get(slot[0], {}).get(slot[1], {}).get("locked"):
                continue
            merged.setdefault("field_sources", {})[path] = deepcopy(evidence)
    profile_variants.collect_aliases(merged)
    profile_variants.refresh_projection_provenance(merged)
    for field in ("name", "name_cn", "summary"):
        if previous and field in incoming.get("_unavailable_fields", []) and field in previous:
            merged[field] = deepcopy(previous[field])
    merged["source_values"][provider]["localized"] = proposed
    old_sources = merged.get("source_refs", [])
    merged["source_refs"] = [deepcopy(row) for row in old_sources if not (
        row.get("provider") == provider and row.get("entity_type") == source["entity_type"] and row.get("external_id") == source["external_id"])] + [source]
    if actors is not None:
        target_scope = actors[0]["scope"] if actors else source["import_scope"]
        current = [deepcopy(row) for row in merged.get("voice_actors", []) if row.get("scope") == target_scope]
        scope_key = f"{target_scope['provider']}:{target_scope['subject_id']}"
        actor_values = merged["source_values"].setdefault("voice_actors", {})
        if not isinstance(actor_values, dict):
            raise ValueError("角色声优来源快照无效")
        previous_actors = actor_values.get(scope_key, [])
        if not current or current == previous_actors:
            current = actors
        merged["voice_actors"] = [deepcopy(row) for row in merged.get("voice_actors", []) if row.get("scope") != target_scope] + current
        actor_values[scope_key] = deepcopy(actors)
    return validate_entity(merged)


def _build(snapshot, snapshot_id, record, repository, *, warnings=None, report=True, selections=None, validated_selections=False):
    if snapshot.get("provider") not in {"bangumi", "tmdb"}:
        raise ValueError("此来源不支持人物资料导入")
    if snapshot.get("provider") == "tmdb":
        from . import tmdb_people
        return tmdb_people.build(snapshot, snapshot_id, record, repository, warnings=warnings, report=report)
    if snapshot.get("kind") != "people":
        raise ValueError("来源快照不是人物同步快照，请重新预览")
    subject_id = providers._positive_id(snapshot.get("subject_id"))
    raw = snapshot.get("raw")
    if not isinstance(raw, dict) or not isinstance(raw.get("details"), dict):
        raise ValueError("人物快照结构无效")
    characters = _rows(raw.get("characters"), "角色列表", MAX_CHARACTERS)
    persons = _rows(raw.get("persons"), "演职员列表", MAX_PERSONS)
    if len(raw["details"]) > MAX_CHARACTERS + MAX_PERSONS:
        raise ValueError("人物详情数量超过安全限制")
    scope = _scope(subject_id)
    warnings = warnings if warnings is not None else []
    unavailable_lists = raw.get("unavailable_lists", [])
    unavailable_details = raw.get("unavailable_details", [])
    unavailable_actors = raw.get("unavailable_actors", [])
    if (not isinstance(unavailable_lists, list) or any(key not in {"characters", "persons"} for key in unavailable_lists) or
            not isinstance(unavailable_details, list) or any(not isinstance(key, str) for key in unavailable_details) or
            not isinstance(unavailable_actors, list) or any(type(key) is not int for key in unavailable_actors)):
        raise ValueError("人物快照的缺失字段标记无效")
    previous_refs = validate_people_refs(record.get("people_refs", []))
    source_index = build_source_index(repository)
    refs = [row for row in previous_refs if row.get("source") != scope or
            ("characters" if row["type"] == "character" else "persons") in unavailable_lists]
    actors_by_character = {}
    requested, fallback, pending_refs = set(), {}, []

    def relation_text(row, key, maximum, identity):
        previous_value = next((ref.get("role" if key == "relation" else key, "") for ref in previous_refs
                               if ref["id"] == identity and ref.get("source") == scope), "")
        if row.get(key) is None:
            return previous_value
        try:
            return providers._text(row.get(key), maximum)
        except ValueError as exc:
            _warn(warnings, subject_id, identity, key, exc)
            return previous_value

    for row in characters:
        external_id = providers._positive_id(row.get("id"))
        identity = source_index.resolve("bangumi", "character", external_id)
        pending_refs.append({"id": identity, "type": "character", "role": relation_text(row, "relation", 200, identity), "source": scope})
        fallback[("character", external_id)] = row
        actors = []
        for actor in _rows(row.get("actors", []), "角色声优", MAX_ACTORS_PER_CHARACTER):
            actor_id = providers._positive_id(actor.get("id"))
            requested.add(("person", actor_id))
            fallback.setdefault(("person", actor_id), actor)
            entry = {"person_id": source_index.resolve("bangumi", "person", actor_id), "scope": scope, "role": "CV"}
            if entry not in actors:
                actors.append(entry)
        actors_by_character[external_id] = None if external_id in unavailable_actors else actors
        requested.add(("character", external_id))
    for row in persons:
        external_id = providers._positive_id(row.get("id"))
        requested.add(("person", external_id))
        fallback[("person", external_id)] = row
        identity = source_index.resolve("bangumi", "person", external_id)
        ref = {"id": identity, "type": "person",
               "role": relation_text(row, "relation", 200, identity),
               "eps": relation_text(row, "eps", 2000, identity), "source": scope}
        if ref not in pending_refs:
            pending_refs.append(ref)
    profiles, previous_entities, skipped = {}, {}, set()
    for kind, external_id in sorted(requested):
        identity = source_index.resolve("bangumi", kind, external_id)
        if identity not in previous_entities:
            previous_entities[identity] = repository.read(identity)
        previous = previous_entities[identity]
        key = f"{kind}:{external_id}"
        detail = raw["details"].get(key)
        unavailable = key in unavailable_details
        if unavailable:
            detail = {"id": external_id, "name": fallback[(kind, external_id)].get("name")}
        if not isinstance(detail, dict) or type(detail.get("id")) is not int or detail["id"] != external_id:
            raise ValueError("人物快照缺少对应角色/声优详情；拒绝保存悬空关联")
        if detail.get("nsfw"):
            raise ValueError(f"Bangumi {kind} {external_id} 不属于公开人物导入范围")
        incoming = _profile(kind, detail, warnings=warnings, subject_id=subject_id, unavailable=unavailable,
                            avatar=snapshot.get("avatars", {}).get(key), report=report)
        incoming["id"] = identity
        if not incoming["name"] and previous is None:
            skipped.add(identity)
            _warn(warnings, subject_id, key, "name", "缺少可确认的人物名称，本人物及其新增关联跳过")
            continue
        profiles[(kind, external_id)] = incoming
    for ref in pending_refs:
        if ref["id"] not in skipped and ref not in refs:
            refs.append(ref)
    entities, changes = [], []
    grouped = {}
    for (kind, external_id), incoming in profiles.items():
        grouped.setdefault(incoming["id"], []).append((kind, external_id, incoming))
    for identity, sources in grouped.items():
        previous = previous_entities[identity]
        merged = previous
        prior_values = (previous or {}).get("source_values", {}).get("bangumi", {})
        protected = {field: deepcopy(previous[field]) for field in _PROFILE_FIELDS
                     if previous and field in previous and (field not in prior_values or previous[field] != prior_values[field])} if len(sources) > 1 else {}
        actors = None
        if sources[0][0] == "character":
            available = [actors_by_character[external_id] for _kind, external_id, _incoming in sources
                         if actors_by_character.get(external_id) is not None]
            if available:
                actors = []
                for rows in available:
                    for actor in rows:
                        if actor["person_id"] not in skipped and actor not in actors:
                            actors.append(deepcopy(actor))
                redirected = any(source_identity("bangumi", kind, external_id) in source_index.retired
                                 for kind, external_id, _incoming in sources)
                if redirected or len(available) != len(sources):
                    # A retired alias, or an unreadable alias endpoint, must not
                    # erase the manually merged character's existing scoped CVs.
                    for actor in (previous or {}).get("voice_actors", []):
                        if actor.get("scope") == scope and actor not in actors:
                            actors.append(deepcopy(actor))
        for index, (kind, external_id, incoming) in enumerate(sources):
            source = {"provider": "bangumi", "entity_type": kind, "external_id": str(external_id),
                      "snapshot_id": snapshot_id, "fetched_at": snapshot["fetched_at"], "import_scope": scope}
            before = merged
            merged = _merge_entity(before, incoming, source=source,
                                   actors=actors if index == len(sources) - 1 else None)
            for field, value in protected.items():
                merged[field] = deepcopy(value)
            if source_identity("bangumi", kind, external_id) in source_index.retired and before:
                # A merge does not authorize a retired donor's future refresh
                # to replace the target profile, even when both used Bangumi.
                # Keep its evidence separate from the target's primary baseline.
                for field in _PROFILE_FIELDS:
                    if field in before:
                        merged[field] = deepcopy(before[field])
                values = merged.setdefault("source_values", {})
                original_values = before.get("source_values", {})
                if "bangumi" in original_values:
                    values["bangumi"] = deepcopy(original_values["bangumi"])
                else:
                    values.pop("bangumi", None)
                aliases = values.setdefault("bangumi_merged_sources", {})
                if not isinstance(aliases, dict):
                    raise ValueError("合并角色的 Bangumi 来源快照格式无效")
                aliases[str(external_id)] = {field: deepcopy(incoming[field]) for field in _PROFILE_FIELDS}
                merged = validate_entity(merged)
        entities.append(merged)
        changed = [key for key in (*_PROFILE_FIELDS, "voice_actors") if (previous or {}).get(key) != merged.get(key)]
        changes.append({"id": merged["id"], "type": kind, "name": merged["name"],
                        "action": "create" if previous is None else "update" if changed else "unchanged", "changed_fields": changed,
                        "diff": [{"field": key, "before": deepcopy((previous or {}).get(key)), "after": deepcopy(merged.get(key))} for key in changed],
                        "preserved_fields": [key for key in _PROFILE_FIELDS if merged.get(key) != incoming.get(key)]})
    # Include local-only dependencies in the revision and batch validation too.
    # This closes the CAS gap when a failed endpoint preserves an existing cast.
    known = {row["id"] for row in entities}
    pending = [ref["id"] for ref in refs]
    pending.extend(ref["character_id"] for ref in refs if ref.get("character_id"))
    pending.extend(actor["person_id"] for row in entities for actor in row.get("voice_actors", []))
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
    return updated, entities, changes


def project(record, *, people_root=None, entities=None):
    """Read just referenced local files; no HTTP or source snapshot reads."""
    result = {"characters": [], "persons": [], "cast": [], "warnings": []}
    try:
        refs = validate_people_refs(record.get("people_refs", []))
    except ValueError as exc:
        result["warnings"].append(str(exc))
        return result
    if not refs:
        return result
    cache = {row["id"]: row for row in (entities or [])}
    repository = None

    def load(identity, kind):
        nonlocal repository
        from people_catalog.taxonomy import project as project_taxonomy, label as taxonomy_label
        from people_catalog.localization import project as project_localized
        try:
            if identity not in cache:
                repository = repository or PeopleRepository(people_root)
                cache[identity] = repository.read(identity)
            row = cache[identity]
            if not row or row["type"] != kind:
                raise ValueError(f"人物引用缺失或类型不一致：{identity}")
            return {key: deepcopy(value) for key, value in row.items() if key in {
                "id", "type", "kind", "name", "name_cn", "summary", "aliases", "infobox", "career", "gender", "birth_date", "taxonomy"}} | {
                    "taxonomy": project_taxonomy(row), "taxonomy_label": taxonomy_label(row),
                    "localized": project_localized(row),
                    "missing": False, "avatar_url": people_assets.avatar_url(row.get("avatar"))}
        except (ValueError, OSError) as exc:
            result["warnings"].append(str(exc))
            return {"id": identity, "type": kind, "name": "人物资料缺失", "missing": True}

    for ref in refs:
        row = {**load(ref["id"], ref["type"]), "role": ref["role"], "eps": ref.get("eps", ""), "source": deepcopy(ref.get("source", {}))}
        for field in ("qualifier", "role_group"):
            if field in ref:
                row[field] = deepcopy(ref[field])
        if ref["type"] == "character":
            row["actors"] = []
            for actor in (cache.get(ref["id"]) or {}).get("voice_actors", []):
                if actor.get("scope") == ref.get("source"):
                    row["actors"].append({**load(actor["person_id"], "person"), "role": actor.get("role", "CV")})
            result["characters"].append(row)
        elif "character_name" in ref:
            row["character_name"] = ref["character_name"]
            row["character_name_cn"] = ref.get("character_name_cn", "")
            imported = ref.get("source_values", {})
            row["character_name_overrides"] = [field for field in ("character_name", "character_name_cn")
                if field in ref and (field in imported and ref[field] != imported[field]
                                     or field not in imported and ref[field] == "")]
            if ref.get("character_id"):
                character = load(ref["character_id"], "character")
                row["character"] = character
                if not character.get("missing"):
                    from people_catalog.localization import compatibility
                    display_character = compatibility(character)
                    for field, entity_field in (("character_name", "name"), ("character_name_cn", "name_cn")):
                        # Work-local overrides (including explicit clears) take
                        # priority over a shared character profile. Unmodified
                        # source text follows edits made to the global entity.
                        local_override = field in row["character_name_overrides"]
                        if not local_override:
                            row[field] = display_character.get(entity_field, "")
            result["cast"].append(row)
        else:
            result["persons"].append(row)
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    return result


def preview(body, *, settings, data_root=None, people_root=None):
    body = providers._body(body, {"provider", "ref", "subject_id", "external_id", "binding_id", "season_number", "scope", "episode_range"})
    item = providers._catalog_item(body.get("ref"), settings)
    providers._check_version(item, body["ref"])
    selected = _selection(body, item)
    result = preview_for_import(item, selected["subject_id"], settings=settings, people_root=people_root,
                                provider=selected["provider"], season_number=selected.get("season_number"), episode_range=selected.get("episode_range"))
    result["proposal"]["binding_id"] = selected["binding_id"]
    result["preview"]["binding_id"] = selected["binding_id"]
    payload = {**result["proposal"], "kind": "people", "expires": int(time.time()) + providers.PREVIEW_LIFETIME}
    return {"ok": True, "ref": deepcopy(item["ref"]), "preview_token": providers._sign(payload), **result["preview"]}


def preview_for_import(item, subject_id, *, settings, people_root=None, deadline=None, provider="bangumi", season_number=None, episode_range=None, season_data=None, original_language=None, source_snapshot=None, supplement_record=None):
    """Prepare a people proposal for a bound or newly selected work source.

    The caller embeds ``proposal`` in its signed, expiring work-preview token.
    Only ``preview`` is a UI projection; confirmation rebuilds from the hashed
    local snapshot and must never fetch remote data.

    ``supplement_record`` is optional in-memory source-selection context. It
    affects only evidence discovery, never the original record used for the
    proposal hash, revision checks or eventual source-binding confirmation.
    """
    subject_id = providers._positive_id(subject_id)
    if provider not in {"bangumi", "tmdb", "wikipedia"}:
        raise ValueError("不支持的人物来源")
    if source_snapshot is not None and provider != "wikipedia":
        raise ValueError("仅维基百科人物补充接受已读取的作品快照")
    if supplement_record is not None and not isinstance(supplement_record, dict):
        raise ValueError("人物补充来源上下文必须为作品对象")
    deadline = min(time.monotonic() + IMPORT_TIMEOUT, deadline) if deadline is not None else time.monotonic() + IMPORT_TIMEOUT
    repository = PeopleRepository(people_root)
    with operation_context(stage="人物来源预览", action="读取角色及演职员资料", name=item.get("name", "")):
        if provider == "wikipedia":
            from . import wikipedia_people
            snapshot = wikipedia_people.fetch_people_snapshot(subject_id, source_snapshot=source_snapshot, deadline=deadline,
                                                               original_language=original_language)
        elif provider == "tmdb":
            from . import tmdb_people
            snapshot = tmdb_people.fetch_snapshot(subject_id, season_number=season_number, episode_range=episode_range, deadline=deadline,
                                                   season_data=season_data, original_language=original_language)
            from . import wikipedia_people
            wikipedia_people.enrich(snapshot, supplement_record if supplement_record is not None else item["record"], deadline=deadline)
            tmdb_people.prepare_portraits(snapshot, repository, deadline=deadline)
        else:
            snapshot = _fetch_snapshot(subject_id, deadline=deadline)
            people_portraits.prepare(snapshot, repository,
                lambda identity, field, reason: _warn(snapshot["warnings"], subject_id, identity, field, reason), deadline=deadline)
        from . import people_import
        return people_import.prepare(snapshot, item, settings=settings, repository=repository)


def apply(body, *, settings, data_root=None, people_root=None):
    body = providers._body(body, {"provider", "ref", "subject_id", "snapshot_id", "preview_token", "binding_id", "season_number", "people_selections"})
    payload = providers._verify_token(body.get("preview_token"))
    repository = PeopleRepository(people_root)
    if (payload.get("kind") != "people" or body.get("ref") != payload.get("ref") or
            ("snapshot_id" in body and body["snapshot_id"] != payload.get("snapshot_id")) or payload.get("scope") != providers._catalog_scope(settings) or
            payload.get("people_scope") != _root_scope(repository) or
            any(key in body and body[key] != payload.get(key, "bangumi" if key == "provider" else None) for key in ("provider", "binding_id", "season_number")) or
            (body.get("subject_id") is not None and providers._positive_id(body["subject_id"]) != payload.get("subject_id"))):
        raise ValueError("人物预览与作品、来源或数据库不一致，请重新预览")
    item = providers._catalog_item(payload["ref"], settings)
    providers._check_version(item, payload["ref"])
    _proposal_source(payload, item["record"])
    payload = with_supplement_selections([payload], body.get("people_selections"))[0]
    return apply_with_work(payload, item, item["record"], settings=settings, data_root=data_root, people_root=people_root)


def apply_with_work(proposal, item, final_record, *, settings, data_root=None, people_root=None):
    """Commit a verified people proposal and the complete final work once.

    ``proposal`` must come from the caller's verified signed token. Its content
    is independently bound to the original work, snapshot and people database.
    The shared repository rolls back people writes if the work writer fails.
    """
    if proposal.get("selection_only"):
        from . import people_import
        return people_import.apply_proposals([proposal], item, final_record, settings=settings, people_root=people_root, data_root=data_root)
    repository = PeopleRepository(people_root)
    if (not isinstance(proposal, dict) or proposal.get("ref") != item["ref"] or
            proposal.get("scope") != providers._catalog_scope(settings) or
            proposal.get("people_scope") != _root_scope(repository)):
        raise ValueError("人物预览与作品、来源或数据库不一致，请重新预览")
    providers._check_version(item, proposal["ref"])
    selected = _proposal_source(proposal, final_record)
    subject_id, provider = selected["subject_id"], selected["provider"]
    snapshot_id = proposal.get("snapshot_id")
    snapshot = providers._read_snapshot(snapshot_id, snapshot_id, safe_path(repository.root / "sources" / provider), provider=provider)
    if snapshot.get("subject_id") != subject_id or provider == "tmdb" and (snapshot.get("season_number") != selected["season_number"] or snapshot.get("episode_range") != selected["episode_range"]):
        raise ValueError("人物快照与来源作品不一致")
    warnings = list(snapshot.get("warnings", []))
    updated, entities, changes = _build(snapshot, snapshot_id, item["record"], repository, warnings=warnings, report=False)
    if _proposal_hash(updated, entities) != proposal.get("proposal_sha256"):
        raise ValueError("人物或作品内容在预览后已变化，请重新预览")
    combined = deepcopy(final_record)
    if "people_refs" in updated:
        combined["people_refs"] = updated["people_refs"]

    def save_work():
        if provider in {"tmdb", "bangumi"}:
            # An unrelated UUID may claim the same external identity after
            # _build without changing this batch's entity revision. Recheck
            # ownership under apply_batch's shared lock before saving the work;
            # a failure rolls back only this batch, not the concurrent owner.
            from people_catalog.source_links import build_source_index
            source_index = build_source_index(repository)
            for entity in entities:
                for source in entity.get("source_refs", []):
                    if source.get("provider") in {"tmdb", "bangumi"} and source_index.find(
                            source["provider"], source["entity_type"], source["external_id"]) != entity["id"]:
                        raise ValueError("人物或角色来源身份在预览后已变化，请重新预览")
        return service.apply_edits({"edits": [{"ref": proposal["ref"], "record": combined}]}, settings=settings, data_root=data_root)

    with operation_context(stage="人物确认保存", action="保存独立人物 YAML 与作品引用", name=item.get("name", "")):
        result = repository.apply_batch(entities, expected_revision=proposal["revision"], after_write=save_work)
        report_progress(f"已同步 {len(entities)} 项人物资料与作品关联")
    result_warnings = list(dict.fromkeys([*result.get("warnings", []), *warnings]))
    return {**result, "people": project(combined, people_root=people_root, entities=entities), "people_changes": changes,
            "people_updated": bool(changes or updated.get("people_refs", []) != item["record"].get("people_refs", [])),
            "warnings": result_warnings, "warning_count": len(result_warnings)}


def apply_many_with_work(proposals, item, final_record, *, settings, data_root=None, people_root=None):
    """Combine verified people proposals and commit the work exactly once.

    Every child was previewed against the same original work. Validate each
    against that original, then compose in memory; never commit intermediate
    work versions or discard earlier subjects' people references.
    """
    if proposals and all(row.get("selection_only") for row in proposals):
        from . import people_import
        return people_import.apply_proposals(proposals, item, final_record, settings=settings, people_root=people_root, data_root=data_root)
    if any(row.get("selection_only") for row in proposals):
        raise ValueError("新旧人物选择预览不能混合，请刷新全部来源预览")
    if len(proposals) == 1:
        return apply_with_work(proposals[0], item, final_record, settings=settings, data_root=data_root, people_root=people_root)
    from work_catalog_yaml.persistence import directory_write_transaction
    from .role_catalog import _inventory, _revision
    repository = PeopleRepository(people_root)
    repository.root.mkdir(parents=True, exist_ok=True)
    safe_path(repository.root / ".nimda-catalog.lock")
    with directory_write_transaction(repository.root):
        # Capture physical bytes before any proposal validation/composition.
        # Re-reading a fresh revision after Overlay builds would silently bless
        # an external edit and let the stale pending records overwrite it.
        original_versions = _inventory(repository)
        validated, seen, warnings = [], set(), []
        for proposal in proposals:
            if (not isinstance(proposal, dict) or proposal.get("ref") != item["ref"] or
                    proposal.get("scope") != providers._catalog_scope(settings) or
                    proposal.get("people_scope") != _root_scope(repository)):
                raise ValueError("人物聚合提案与作品或数据库不一致")
            providers._check_version(item, proposal["ref"])
            selected = _proposal_source(proposal, final_record)
            subject_id, provider = selected["subject_id"], selected["provider"]
            snapshot_id = proposal.get("snapshot_id")
            snapshot = providers._read_snapshot(snapshot_id, snapshot_id, safe_path(repository.root / "sources" / provider), provider=provider)
            if snapshot.get("subject_id") != subject_id or provider == "tmdb" and (snapshot.get("season_number") != selected["season_number"] or snapshot.get("episode_range") != selected["episode_range"]):
                raise ValueError("人物快照与来源作品不一致")
            updated, entities, _changes = _build(snapshot, snapshot_id, item["record"], repository, warnings=warnings, report=False)
            if (_proposal_hash(updated, entities) != proposal.get("proposal_sha256") or
                    repository.revision([row["id"] for row in entities]) != proposal.get("revision")):
                raise ValueError("人物或作品内容在预览后已变化，请重新预览")
            warnings.extend(snapshot.get("warnings", []))
            source_key = (provider, subject_id, snapshot.get("season_number", -1), providers._json_bytes(snapshot.get("episode_range")).decode("utf-8"))
            if source_key not in seen:
                validated.append((source_key, snapshot_id, snapshot))
                seen.add(source_key)
        combined, pending, changes = deepcopy(final_record), {}, []

        class Overlay:
            root = repository.root

            def read(self, identity):
                return deepcopy(pending[identity]) if identity in pending else repository.read(identity)

        for _subject_id, snapshot_id, snapshot in sorted(validated, key=lambda value: value[0]):
            combined, entities, changed = _build(snapshot, snapshot_id, combined, Overlay(), warnings=warnings, report=False)
            pending.update((entity["id"], entity) for entity in entities)
            changes.extend(changed)

        def save_work():
            current_versions = _inventory(repository)
            if ({identity: version for identity, version in current_versions.items() if identity not in pending}
                    != {identity: version for identity, version in original_versions.items() if identity not in pending}):
                raise ValueError("人物来源库在聚合保存期间已变化，请重新预览")
            if any(snapshot.get("provider") in {"bangumi", "tmdb"} for _key, _id, snapshot in validated):
                from people_catalog.source_links import build_source_index
                source_index = build_source_index(repository)
                for entity in pending.values():
                    for source in entity.get("source_refs", []):
                        if source.get("provider") in {"bangumi", "tmdb"} and source_index.find(
                                source["provider"], source["entity_type"], source["external_id"]) != entity["id"]:
                            raise ValueError("人物或角色来源身份在预览后已变化，请重新预览")
            return service.apply_edits({"edits": [{"ref": item["ref"], "record": combined}]}, settings=settings, data_root=data_root)

        if _inventory(repository) != original_versions:
            raise ValueError("人物数据库在聚合规划期间已变化，请重新预览")
        result = repository.apply_batch(list(pending.values()), expected_revision=_revision(original_versions, pending), after_write=save_work)
    return {**result, "people_updated": bool(changes or combined.get("people_refs", []) != item["record"].get("people_refs", [])),
            "people_changes": changes, "warnings": list(dict.fromkeys([*warnings, *result.get("warnings", [])]))}
