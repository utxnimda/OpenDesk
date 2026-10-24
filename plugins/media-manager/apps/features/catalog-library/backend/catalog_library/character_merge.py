"""Explicit, offline merging of existing character profiles and their links.

Names only suggest candidates. A signed full-library preview, explicit target
and field list are required before the existing transactional writers run.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from itertools import islice
import time
import unicodedata

from collection_detail.save import catalog_write_transaction
from people_catalog.model import entity_id, validate_entity, validate_people_refs
from people_catalog.repository import PeopleRepository, safe_path
from people_catalog.source_links import build_source_index
from people_catalog import taxonomy, localization
from work_catalog_yaml.operation_progress import operation_context, report_progress
from work_catalog_yaml.persistence import directory_write_transaction

from . import people_inventory as repair
from . import people, people_assets, providers, service

MODE = "character-merge"
MAX_CANDIDATES = 200
_FIELDS = {
    "name": "原名 / 名称", "name_cn": "中文名", "aliases": "别名",
    "summary": "角色简介", "infobox": "详细资料", "career": "职业",
    "gender": "性别", "birth_date": "出生日期", "avatar": "角色头像",
}
_LIST_FIELDS = {"aliases", "infobox", "career"}
_WARNINGS = [
    "同名或别名仅提示可能重复，不代表同一角色；不会自动合并。",
    "只合并明确选择的两个既有角色。默认保留目标资料；来源、角色来源与演员 / 声优关系合并保留。",
    "确认后更新所有作品中的对应角色引用；旧角色文件保留为已合并记录，不触碰媒体或图片文件。",
]


def _people_state(repository, *, parse=True):
    """Hash fresh bytes every time; reuse validated, content-keyed YAML parses.

    Unlike browse inventory this includes retired records, which are required
    for merge-chain and canonical source-ownership validation.
    """
    from .people_library import _cached_entity
    paths = list(islice(safe_path(repository.root / "db").glob("*.yaml"), repair.MAX_PEOPLE + 1))
    if len(paths) > repair.MAX_PEOPLE:
        raise ValueError("角色合并扫描超过人物数量安全限制")
    rows, versions, total = {}, {}, 0
    for path in sorted(paths):
        identity = entity_id(path.stem)
        content = repository._bytes(identity)
        if content is None:
            raise ValueError("人物数据库在角色扫描期间已变化，请重新预览")
        total += len(content)
        if total > repair.MAX_TOTAL_BYTES:
            raise ValueError("角色合并扫描超过人物大小安全限制")
        versions[identity] = hashlib.sha256(content).hexdigest()
        if parse:
            rows[identity] = _cached_entity(repository, identity, content)
    return rows, versions


def _people_versions(repository):
    return _people_state(repository, parse=False)[1]


def _source_index(repository, entities):
    class Snapshot:
        root = repository.root

        def read(self, identity):
            return entities.get(identity)

    return build_source_index(Snapshot())


def _names(person):
    values = [person.get("name", ""), person.get("name_cn", ""), *person.get("aliases", [])]
    localized = localization.project(person)
    values.extend(localized["name"].values())
    values.extend(alias for names in localized["aliases"].values() for alias in names)
    return {" ".join(unicodedata.normalize("NFKC", value).casefold().split())
            for value in values if isinstance(value, str) and value.strip()}


def _character(entities, identity):
    identity = entity_id(identity)
    person = entities.get(identity)
    if (not person or person.get("type") != "character" or person.get("merged_into")
            or not taxonomy.is_fictional_person(person)):
        raise ValueError("角色合并只能选择分类已确认为虚拟人物的既有 character 档案，不能合并组织、机体、现实人物、待分类或已退休档案")
    return person


def _blocked(donor, target, entities):
    if donor["id"] == target["id"]:
        return "角色不能与自身合并"
    if any(row.get("merged_into") == donor["id"] for row in entities.values()):
        return "待合并角色已经接收其他退休档案，不能形成合并链；请保留该角色作为目标"
    return ""


def _summary(person):
    localized = localization.project(person)
    localized["summary"] = {key: value[:1000] for key, value in localized["summary"].items()}
    return {"id": person["id"], "type": person["type"], "kind": person.get("kind", ""),
            "name": person["name"], "name_cn": person.get("name_cn", ""),
            "aliases": deepcopy(person.get("aliases", [])), "summary": person.get("summary", "")[:1000],
            "localized": localized,
            "avatar_url": people_assets.avatar_url(person.get("avatar")),
            "taxonomy": taxonomy.project(person), "taxonomy_label": taxonomy.label(person),
            "portrayals": [{"actor_id": row["actor_id"], "scope": deepcopy(row["scope"]),
                "qualifier": deepcopy(row.get("qualifier")), "role_group": row.get("role_group", ""),
                "original_name": row["original_name"]} for row in person.get("role_origins", [])],
            "source_refs": deepcopy(person.get("source_refs", []))}


def candidates(body, *, settings=None, people_root=None, data_root=None, source_root=None):
    body = providers._body(body, {"person_id"})
    repository = PeopleRepository(people_root)
    entities, versions = _people_state(repository)
    donor = _character(entities, body.get("person_id"))
    names, choices = _names(donor), []
    for identity, target in entities.items():
        if (identity == donor["id"] or target.get("type") != "character" or target.get("merged_into")
                or not taxonomy.is_fictional_person(target)):
            continue
        matched = sorted(names & _names(target))
        if matched:
            reason = _blocked(donor, target, entities)
            choices.append({**_summary(target), "matched_names": matched, "blocked": bool(reason), "reason": reason})
    choices.sort(key=lambda row: (row["blocked"], row["name_cn"] or row["name"], row["id"]))
    if _people_versions(repository) != versions:
        raise ValueError("角色数据库在候选扫描期间已变化，请刷新后重试")
    return {"ok": True, "mode": MODE, "person_id": donor["id"], "candidates": choices[:MAX_CANDIDATES],
            "total": len(choices), "truncated": len(choices) > MAX_CANDIDATES, "warnings": deepcopy(_WARNINGS)}


def _state(settings, repository):
    work_revision = repair._work_versions(settings)
    works = service._records(settings)
    if len(works) > repair.MAX_WORKS:
        raise ValueError("角色合并作品扫描超过安全数量限制")
    for work in works:
        validate_people_refs(work["record"].get("people_refs", []))
    from .role_catalog import _revision
    entities, versions = _people_state(repository)
    entity_revision = _revision(versions, list(entities))
    if (_people_versions(repository) != versions
            or repair._work_versions(settings) != work_revision):
        raise ValueError("人物或作品数据库在角色合并扫描期间已变化，请重新预览")
    return works, entities, {"work_revision": work_revision, "people_revision": repair._hash(versions),
                             "entity_revision": entity_revision}


def _union(before, incoming):
    if not isinstance(before, list) or not isinstance(incoming, list):
        raise ValueError("角色来源或关联字段必须为数组，不能自动改写异常资料")
    result = deepcopy(before)
    for value in incoming:
        if value not in result:
            result.append(deepcopy(value))
    return result


def _empty(field):
    return [] if field in _LIST_FIELDS else {} if field in {"birth_date", "avatar"} else ""


def _diff(target, donor):
    result = []
    for field, label in _FIELDS.items():
        if ("localized" in target or "localized" in donor) and field in {"name", "name_cn", "summary", "aliases"}:
            continue
        before = deepcopy(target.get(field, _empty(field)))
        value = deepcopy(donor.get(field, _empty(field)))
        available = bool(value)
        after = (_union(before, value) if field in _LIST_FIELDS else
                 {**before, **value} if field == "birth_date" and isinstance(before, dict) and isinstance(value, dict)
                 else value) if available else before
        result.append({"field": field, "label": label, "before": before, "after": after,
                       "changed": before != after, "selectable": available and before != after,
                       "selected": False, "reason": "" if available else "待合并角色未提供此资料，保留目标值"})
    if "localized" in target or "localized" in donor:
        for field in localization.field_paths(donor, target):
            before, value = localization.field_value(target, field), localization.field_value(donor, field)
            after = value if value else before
            result.append({"field": field, "label": localization.label(field), "before": before, "after": after,
                           "changed": before != after, "selectable": bool(value) and before != after,
                           "selected": False, "reason": "" if value else "此语种没有新资料，保留目标值"})
    return result


def _retarget_refs(refs, donor_id, target_id):
    result = []
    for original in refs:
        row = deepcopy(original)
        if row.get("type") == "character" and row["id"] == donor_id:
            row["id"] = target_id
        if row.get("type") == "person" and row.get("character_id") == donor_id:
            row["character_id"] = target_id
        if row not in result:
            result.append(row)
    return validate_people_refs(result)


def _plan(person_id, target_id, works, entities, repository):
    donor, target = _character(entities, person_id), _character(entities, target_id)
    reason = _blocked(donor, target, entities)
    if reason:
        raise ValueError(reason)
    index = _source_index(repository, entities)
    for person in (donor, target):
        for source in person.get("source_refs", []):
            if index.find(source["provider"], "character", source["external_id"]) != person["id"]:
                raise ValueError("角色来源身份不由当前档案唯一持有，不能合并")
    # Validate the mandatory identity/relation union before offering any plan.
    _merged(target, donor, [], _diff(target, donor))
    affected = []
    for work in works:
        refs = work["record"].get("people_refs", [])
        count = sum(row["type"] == "character" and row["id"] == donor["id"] for row in refs)
        actor_count = sum(row["type"] == "person" and row.get("character_id") == donor["id"] for row in refs)
        if count or actor_count:
            _retarget_refs(refs, donor["id"], target["id"])
            affected.append({"ref": deepcopy(work["ref"]), "name": work.get("name", ""),
                             "relation_count": count, "actor_relation_count": actor_count})
    if len(affected) > 200:
        raise ValueError("受影响作品超过单次 200 部安全事务限制，未保存任何修改")
    return {"person_id": donor["id"], "target_id": target["id"], "donor": _summary(donor),
            "target": _summary(target), "diff": _diff(target, donor), "selected_fields": [],
            "affected_works": affected, "affected_work_count": len(affected),
            "relation_union": {field: len(_union(target.get(field, []), donor.get(field, [])))
                               for field in ("source_refs", "role_origins", "voice_actors")}}


def preview(body, *, settings, people_root=None, data_root=None, source_root=None):
    body = providers._body(body, {"person_id", "target_id"})
    repository = PeopleRepository(people_root)
    with operation_context(stage="角色手动合并预览", action="只读核对角色与全部作品引用"):
        report_progress("正在扫描独立角色档案和作品引用；不会自动合并或写入数据库")
        works, entities, revisions = _state(settings, repository)
        plan = _plan(body.get("person_id"), body.get("target_id"), works, entities, repository)
    if repair._hash(_people_versions(repository)) != revisions["people_revision"] or repair._work_versions(settings) != revisions["work_revision"]:
        raise ValueError("数据库在角色合并预览期间已变化，请重新预览")
    payload = {"kind": MODE, "person_id": plan["person_id"], "target_id": plan["target_id"],
               "scope": providers._catalog_scope(settings), "people_scope": people._root_scope(repository),
               **revisions, "plan_sha256": repair._hash(plan), "expires": int(time.time()) + providers.PREVIEW_LIFETIME}
    return {"ok": True, "mode": MODE, **plan, "preview_token": providers._sign(payload), "warnings": deepcopy(_WARNINGS)}


def _merged(target, donor, selected, diff):
    updated = deepcopy(target)
    from people_catalog import profile_variants
    profile_variants.merge_exports(updated, donor)
    for field in ("source_refs", "role_origins", "voice_actors"):
        if field in target or field in donor:
            updated[field] = _union(target.get(field, []), donor.get(field, []))
    fields = {row["field"]: row for row in diff}
    for field in selected:
        if localization.is_field(field):
            updated.setdefault("localized", localization.project(target))
            localization.set_field(updated, field, deepcopy(fields[field]["after"]))
        else:
            updated[field] = deepcopy(fields[field]["after"])
    if selected:
        imported = updated.setdefault("source_values", {}).setdefault("character_merge", {})
        field_sources = updated.setdefault("field_sources", {})
        if not isinstance(imported, dict) or not isinstance(field_sources, dict):
            raise ValueError("目标角色字段来源结构无效，不能覆盖异常资料")
        imported[donor["id"]] = {field: localization.field_value(donor, field) if localization.is_field(field)
                               else deepcopy(donor[field]) for field in selected}
        for field in selected:
            field_sources[field] = {"provider": "local_character_merge", "donor_id": donor["id"],
                "donor_sha256": repair._hash(donor), "source_refs": deepcopy(donor.get("source_refs", []))}
    return validate_entity(updated)


def apply(body, *, settings, people_root=None, data_root=None, source_root=None):
    body = providers._body(body, {"person_id", "target_id", "preview_token", "selected_fields"})
    person_id, target_id = entity_id(body.get("person_id")), entity_id(body.get("target_id"))
    payload = providers._verify_token(body.get("preview_token"))
    repository = PeopleRepository(people_root)
    if (payload.get("kind") != MODE or payload.get("person_id") != person_id or payload.get("target_id") != target_id
            or payload.get("scope") != providers._catalog_scope(settings)
            or payload.get("people_scope") != people._root_scope(repository)):
        raise ValueError("角色合并凭据与明确选择的角色或数据库不一致")
    selected = body.get("selected_fields")
    if (not isinstance(selected, list) or len(selected) > len(_FIELDS) + 3 * len(localization.LANGUAGES)
            or any(not isinstance(field, str) or field not in _FIELDS and not localization.is_field(field) for field in selected)
            or len(set(selected)) != len(selected)):
        raise ValueError("必须明确提供不重复的角色合并字段列表；空数组表示只转移身份与关联")
    if not repository.root.is_dir():
        raise ValueError("人物数据库已不存在，请重新预览")
    catalog_root = safe_path(settings.filesystem_root)
    safe_path(repository.root / ".nimda-catalog.lock")
    safe_path(catalog_root / ".nimda-catalog.lock")
    with directory_write_transaction(repository.root), catalog_write_transaction(catalog_root):
        works, entities, revisions = _state(settings, repository)
        if any(payload.get(key) != value for key, value in revisions.items()):
            raise ValueError("人物或作品数据库在角色合并预览后已变化，请重新预览")
        plan = _plan(person_id, target_id, works, entities, repository)
        if payload.get("plan_sha256") != repair._hash(plan):
            raise ValueError("角色合并计划已变化，请重新预览")
        allowed = {row["field"] for row in plan["diff"] if row["selectable"]}
        if set(selected) - allowed:
            raise ValueError("只能采用本次角色预览中非空且可选的字段")
        donor, target = entities[person_id], entities[target_id]
        if "avatar" in selected:
            people_assets.read_avatar(donor["avatar"]["asset_id"], root=repository.root)
        updated = _merged(target, donor, selected, plan["diff"])
        retired = validate_entity({**deepcopy(donor), "merged_into": target_id, "source_refs": []})
        updates = [updated, retired]
        expected = {**entities, target_id: updated, person_id: retired}
        edits = []
        for work in works:
            original = work["record"]
            refs = _retarget_refs(original.get("people_refs", []), person_id, target_id)
            if refs != original.get("people_refs", []):
                record = deepcopy(original)
                record["people_refs"] = refs
                edits.append({"ref": deepcopy(work["ref"]), "record": record})

        def save_works():
            current, versions = _people_state(repository)
            if current != expected or repair._work_versions(settings) != revisions["work_revision"]:
                raise ValueError("角色合并保存前数据库发生并发变化，已取消本次修改")
            index = _source_index(repository, current)
            for source in updated.get("source_refs", []):
                if index.find(source["provider"], "character", source["external_id"]) != target_id:
                    raise ValueError("角色合并后的来源身份未唯一转移至目标")
            if _people_versions(repository) != versions:
                raise ValueError("角色来源核对期间发生并发变化，已取消本次修改")
            return service.apply_edits({"edits": edits}, settings=settings, data_root=data_root) if edits else {"ok": True, "records": []}

        with operation_context(stage="角色手动合并保存", action="原子保存角色及全部受影响作品"):
            report_progress(f"正在合并角色 {donor['name']} → {target['name']}，更新 {len(edits)} 部作品并保留历史备份；不触碰媒体文件")
            result = repository.apply_batch(updates, expected_revision=revisions["entity_revision"],
                                            revision_ids=list(entities), after_write=save_works)
    return {**result, "ok": True, "mode": MODE, "person_id": person_id, "target_id": target_id,
            "merged_count": 1, "affected_work_count": len(edits), "people_updated": True,
            "person": {**updated, "avatar_url": people_assets.avatar_url(updated.get("avatar"))},
            "warnings": ["已执行明确确认的角色合并；目标 ID 保留，旧角色文件保留，媒体和图片文件未修改。"]}
