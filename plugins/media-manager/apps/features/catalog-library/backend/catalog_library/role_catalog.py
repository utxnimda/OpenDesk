"""Independent local characters for live-action credits; no name matching.

The actor relation retains its source text and gains a character UUID. A role
portrait is deliberately empty, never copied from the actor. Explicit offline
preview/apply signs both the work and the entire bounded source-owner inventory.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from itertools import islice
import time

from collection_detail.save import catalog_write_transaction
from people_catalog.model import entity_id, source_identity, validate_entity, validate_people_refs
from people_catalog.repository import PeopleRepository, safe_path
from people_catalog.source_links import build_source_index
from people_catalog import taxonomy, portrayal
from work_catalog_yaml.operation_progress import report_progress
from work_catalog_yaml.persistence import directory_write_transaction

from . import people, providers, service

MODE = "role-catalog"
MAX_ENTITIES = 10000
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def _hash(value):
    return hashlib.sha256(providers._json_bytes(value)).hexdigest()


def _identity_scope(value):
    # Episode subsets describe participation, not a different role identity.
    return {key: deepcopy(item) for key, item in value.items() if key != "episode_range"}


def _work_key(record, role_scope):
    identity = record.get("id")
    if isinstance(identity, str) and identity.strip() and len(identity) <= 250:
        return "work:" + identity
    # Legacy records have no local UUID. Their confirmed provider subject/season
    # is stable under title edits and avoids merging same names across works.
    return "source:" + _hash(_identity_scope(role_scope))


def _origin(record, ref):
    original = ref.get("source_values", {}).get("character_name") or ref["character_name"]
    origin = {"work_key": _work_key(record, ref["source"]), "actor_id": ref["id"],
              "scope": _identity_scope(ref["source"]), "original_name": original}
    if ref.get("credit_id"):
        origin["credit_id"] = ref["credit_id"]
    if qualifier := portrayal.qualifier_for(ref):
        origin["qualifier"] = qualifier
    if ref.get("role_group"):
        origin["role_group"] = portrayal.validate_role_group(ref["role_group"])
    # Even malformed duplicate credit IDs cannot join two actors' roles. Group
    # evidence is evaluated separately and never changes this source identity.
    discriminator = ["credit", ref["id"], ref["credit_id"]] if ref.get("credit_id") else ["actor-role", ref["id"], original]
    origin["external_id"] = "role:" + _hash([origin["work_key"], origin["scope"], discriminator])
    return origin


def _same_origin(old, incoming):
    if _identity_scope(old["scope"]) != incoming["scope"] or old["actor_id"] != incoming["actor_id"]:
        return False
    if old.get("credit_id") and incoming.get("credit_id"):
        return old["credit_id"] == incoming["credit_id"]
    return old["actor_id"] == incoming["actor_id"] and old["original_name"] == incoming["original_name"]


def _group_key(origin):
    group = origin.get("role_group")
    return _hash([origin["work_key"], _identity_scope(origin["scope"]), group]) if group else None


def retain_origin(origins, incoming):
    """Fill missing portrayal evidence; keep separately confirmed age stages."""
    for position, old in enumerate(origins):
        if old["external_id"] != incoming["external_id"] or not _same_origin(old, incoming):
            continue
        if portrayal.conflicting_stage(old, incoming):
            continue
        merged = deepcopy(old)
        for field in ("qualifier", "role_group"):
            if field not in merged and field in incoming:
                merged[field] = deepcopy(incoming[field])
        origins[position] = merged
        return
    if incoming not in origins:
        origins.append(deepcopy(incoming))


def materialize(record, entities, repository, warnings=None, *, _source_index=None, _name_evidence=None):
    """Return a pure proposal, reusing explicit/source IDs, never same names."""
    warnings = warnings if warnings is not None else []
    from people_catalog import localization
    if not isinstance(entities, list) or len(entities) > 1000:
        raise ValueError("角色建档关联人物超过安全数量限制")
    updated = deepcopy(record)
    refs = validate_people_refs(record.get("people_refs", []))
    output = {row["id"]: validate_entity(row) for row in entities}
    if len(output) != len(entities):
        raise ValueError("角色建档输入包含重复人物 ID")
    candidates = [row for row in refs if row["type"] == "person" and row.get("character_name", "").strip()
                  and row.get("source", {}).get("provider") in {"tmdb", "douban", "wikipedia"}]
    if not candidates:
        updated["people_refs"] = refs
        return updated, list(output.values()), []
    index = _source_index or build_source_index(repository)
    local_sources = {}
    originals = {}
    role_actors = {}

    def read(identity):
        return output.get(identity) or repository.read(identity)

    # A shared group is explicit same-row source evidence, not a normalized
    # name. Existing separate owners are never silently merged by new evidence.
    groups = {}
    identity_repository = getattr(repository, "group_identity_repository", repository)
    group_index = build_source_index(identity_repository) if identity_repository is not repository and any(ref.get("role_group") for ref in candidates) else index
    for ref in candidates:
        if qualifier := portrayal.qualifier_for(ref):
            ref.setdefault("qualifier", qualifier)
        origin = _origin(record, ref)
        if key := _group_key(origin):
            owners = groups.setdefault(key, set())
            owner = ref.get("character_id") or group_index.find(ref["source"]["provider"], "character", origin["external_id"])
            if owner:
                owners.add(group_index.retired.get(owner, owner))
            # A normalizer may project new profiles through an empty repository
            # while resolving old pre-UUID/translated ownership read-only.
            lookup = getattr(repository, "group_role_owners", None)
            if lookup:
                owners.update(lookup(ref, read(ref["id"]), origin, candidates))
    if groups:
        for identity in group_index.entity_ids:
            if group_index.entity_types[identity] != "character":
                continue
            for origin in (identity_repository.read(identity) or {}).get("role_origins", []):
                key = _group_key(origin)
                if key in groups:
                    groups[key].add(identity)
        for owners in groups.values():
            if len(owners) > 1:
                warnings.append("同一年龄分支来源已对应多个角色卡；保留现有身份，请手动确认合并，不会自动合并已有卡。")
    # The unqualified main portrayal or an explicitly adult portrayal supplies
    # the base profile. An unqualified main actor is not presumed to be adult.
    # Reference order itself remains unchanged.
    ordered = sorted(candidates, key=lambda ref: 0 if ref.get("role_group") and portrayal.stage_for(ref) in {"", "adult"} else 1)
    for ref in ordered:
        actor = read(ref["id"])
        if not actor or actor["type"] != "person" or actor.get("merged_into") or not taxonomy.is_real_person(actor):
            raise ValueError("饰演角色的演员必须是已确认分类的现实人物，不能是组织、其他实体、待分类或已合并档案；请先检查人物资料与作品关联")
        origin = _origin(record, ref)
        provider = ref["source"]["provider"]
        previous = None
        if ref.get("character_id"):
            previous = read(ref["character_id"])
            if not previous or previous["type"] != "character" or not taxonomy.is_fictional_person(previous):
                raise ValueError("演员关联的独立角色不存在或类型错误")
            if previous.get("merged_into"):
                previous = read(previous["merged_into"])
                if (not previous or previous["type"] != "character" or previous.get("merged_into")
                        or not taxonomy.is_fictional_person(previous)):
                    raise ValueError("已合并角色目标不存在或形成合并链")
            matches = [old for old in previous.get("role_origins", []) if _same_origin(old, origin)]
            if not matches and not origin.get("credit_id"):
                matches = [old for old in previous.get("role_origins", []) if not old.get("credit_id")
                           and old["actor_id"] == origin["actor_id"] and _identity_scope(old["scope"]) == origin["scope"]]
            if len(matches) == 1:
                # Keep the original identity even when legacy work obtains an
                # ID, the role gains a translation, or its imported name changes.
                old_origin = deepcopy(matches[0])
                for field in ("qualifier", "role_group"):
                    if field in origin and field not in old_origin:
                        old_origin[field] = deepcopy(origin[field])
                origin = old_origin
            bound = index.find(provider, "character", origin["external_id"])
            if bound and bound != previous["id"]:
                raise ValueError("演员角色 ID 与已绑定的角色来源身份冲突")
            identity = previous["id"]
        else:
            identity = local_sources.get((provider, origin["external_id"])) or index.resolve(provider, "character", origin["external_id"])
            previous = read(identity)
            owners = groups.get(_group_key(origin), set())
            if previous is None and len(owners) == 1:
                identity = next(iter(owners))
                previous = read(identity)
        if previous and (previous["type"] != "character" or previous.get("merged_into") or not taxonomy.is_fictional_person(previous)):
            raise ValueError("角色来源身份指向无效或已合并的实体")
        originals.setdefault(identity, deepcopy(repository.read(identity)))
        local_sources[(provider, origin["external_id"])] = identity
        if key := _group_key(origin):
            groups.setdefault(key, set()).add(identity)
        imported = ref.get("source_values", {})
        # A relation's manual display override belongs to this work, not every
        # work sharing the character. Only import the confirmed source baseline.
        incoming = {"id": identity, "schema_version": 1, "type": "character", "kind": "character",
                    "name": imported.get("character_name", ref["character_name"]),
                    "name_cn": imported.get("character_name_cn", ref.get("character_name_cn", "")),
                    "summary": "", "aliases": [], "infobox": [], "career": [], "gender": "", "birth_date": {}, "avatar": {}}
        if not incoming["name"] and not previous:
            incoming["name"] = ref["character_name"]
        evidence_key = (ref["id"], incoming["name"], incoming["name_cn"])
        evidence = (_name_evidence or {}).get(evidence_key)
        # Language variants come from the exact source relation in the signed
        # snapshot. Bare legacy Chinese role labels do not prove a variant.
        if evidence is None:
            known = localization.project(previous or {})["name"]
            evidence = {"name": {lang: value for lang, value in known.items()
                                  if value in {incoming["name"], incoming["name_cn"]}}}
        incoming["localized"] = localization.validate(evidence)
        qualifier = portrayal.qualifier_for(ref)
        if qualifier:
            incoming["name"] = portrayal.base_name(incoming["name"], qualifier)
            incoming["name_cn"] = portrayal.base_name(incoming["name_cn"], qualifier)
            incoming["localized"]["name"] = {lang: portrayal.base_name(value, qualifier)
                for lang, value in incoming["localized"].get("name", {}).items()}
        localization.add_evidence(incoming["localized"], "name", incoming["name"])
        localization.add_evidence(incoming["localized"], "name", incoming["name_cn"])
        incoming["_unavailable_fields"] = [key for key in people._PROFILE_FIELDS if key not in {"name", "name_cn", "kind"} or not incoming[key]]
        if previous and (len(previous.get("role_origins", [])) > 1
                         or previous["id"] != source_identity(provider, "character", origin["external_id"])):
            # After an explicit merge, retain the chosen target's display
            # profile; another imported role must not rename that target.
            incoming["_unavailable_fields"] = list(people._PROFILE_FIELDS)
        source = {"provider": provider, "entity_type": "character", "external_id": origin["external_id"],
                  "import_scope": deepcopy(origin["scope"]), "evidence_kind": "work_actor_credit"}
        merged = people._merge_entity(previous, incoming, source=source)
        origins = merged.setdefault("role_origins", [])
        retain_origin(origins, origin)
        output[identity] = validate_entity(merged)
        ref["character_id"] = identity
        role_actors.setdefault(identity, [])
        if ref["id"] not in role_actors[identity]:
            role_actors[identity].append(ref["id"])
    if len(output) > 1000:
        raise ValueError("角色建档后的人物批次超过 1000 项安全限制")
    changes = []
    for identity, actor_ids in role_actors.items():
        previous, entity = originals[identity], output[identity]
        fields = [key for key in (*people._PROFILE_FIELDS, "localized", "source_refs", "role_origins") if (previous or {}).get(key) != entity.get(key)]
        changes.append({"id": identity, "type": "character", "name": entity["name"], "name_cn": entity.get("name_cn", ""),
            "actor_id": actor_ids[0], "actor_ids": actor_ids, "selected": False,
            "action": "create" if previous is None else "update" if fields else "unchanged",
            "changed_fields": fields, "diff": [{"field": key, "before": deepcopy((previous or {}).get(key)), "after": deepcopy(entity.get(key))} for key in fields],
            "preserved_fields": [key for key in people._PROFILE_FIELDS if previous and previous.get(key) == entity.get(key)]})
    updated["people_refs"] = validate_people_refs(refs)
    return updated, list(output.values()), changes


def _inventory(repository):
    paths = list(islice(safe_path(repository.root / "db").glob("*.yaml"), MAX_ENTITIES + 1))
    if len(paths) > MAX_ENTITIES:
        raise ValueError("角色来源校验超过人物数量安全限制")
    versions, total = {}, 0
    for path in paths:
        identity = entity_id(path.stem)
        data = repository._bytes(identity)
        if data is None:
            raise ValueError("人物数据库扫描期间已变化")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("角色来源校验超过人物大小安全限制")
        versions[identity] = hashlib.sha256(data).hexdigest()
    return versions


def _revision(versions, identities):
    """Repository CAS derived from captured bytes, never a post-plan reread."""
    digest = hashlib.sha256()
    for identity in sorted(set(identities)):
        digest.update(identity.encode("ascii"))
        digest.update(bytes.fromhex(versions[identity]) if identity in versions else b"missing")
    return digest.hexdigest()


def _plan(body, settings, repository):
    before = _inventory(repository)
    item = providers._catalog_item(body.get("ref"), settings)
    providers._check_version(item, body["ref"])
    updated, entities, changes = materialize(item["record"], [], repository)
    if _inventory(repository) != before:
        raise ValueError("人物数据库在角色预览期间已变化，请重试")
    plan_hash = _hash([updated, entities, changes])
    return item, updated, entities, changes, before, plan_hash


def preview(body, *, settings, data_root=None, people_root=None):
    body = providers._body(body, {"ref"})
    repository = PeopleRepository(people_root)
    report_progress("正在读取本作品演员角色并校验全库来源身份；不联网、不修改数据库")
    item, updated, entities, changes, versions, plan_hash = _plan(body, settings, repository)
    payload = {"kind": MODE, "ref": deepcopy(item["ref"]), "scope": providers._catalog_scope(settings),
        "people_scope": people._root_scope(repository), "inventory_sha256": _hash(versions), "plan_sha256": plan_hash,
        "expires": int(time.time()) + providers.PREVIEW_LIFETIME}
    report_progress(f"角色建档预览完成：{len(changes)} 个独立角色，默认不选择；等待明确确认")
    return {"ok": True, "mode": MODE, "ref": deepcopy(item["ref"]), "preview_token": providers._sign(payload),
            "entries": changes, "warnings": ["同名角色不会自动合并；演员照片不会用作角色图片。仅明确选择的角色会保存，其他作品保持不变。"]}


def apply(body, *, settings, data_root=None, people_root=None):
    body = providers._body(body, {"ref", "preview_token", "character_ids"})
    payload = providers._verify_token(body.get("preview_token"))
    repository = PeopleRepository(people_root)
    if (payload.get("kind") != MODE or payload.get("ref") != body.get("ref") or payload.get("scope") != providers._catalog_scope(settings)
            or payload.get("people_scope") != people._root_scope(repository)):
        raise ValueError("角色建档凭据与作品或人物库不一致")
    selected = body.get("character_ids")
    if not isinstance(selected, list) or not 1 <= len(selected) <= 1000:
        raise ValueError("请明确选择要建档或关联的角色，没有默认修改")
    selected = [entity_id(identity) for identity in selected]
    if len(set(selected)) != len(selected):
        raise ValueError("选择的角色 ID 不能重复")
    repository.root.mkdir(parents=True, exist_ok=True)
    safe_path(repository.root / ".nimda-catalog.lock")
    catalog_root = safe_path(settings.filesystem_root)
    safe_path(catalog_root / ".nimda-catalog.lock")
    with directory_write_transaction(repository.root), catalog_write_transaction(catalog_root):
        item, updated, entities, changes, versions, plan_hash = _plan(body, settings, repository)
        if payload.get("inventory_sha256") != _hash(versions) or payload.get("plan_sha256") != plan_hash:
            raise ValueError("角色或来源身份在预览后已变化，请重新预览")
        if set(selected) - {change["id"] for change in changes}:
            raise ValueError("选择的角色不属于本次预览")
        selected_set = set(selected)
        record = deepcopy(item["record"])
        original_refs = record.get("people_refs", [])
        record["people_refs"] = [deepcopy(after if after.get("character_id") in selected_set else before)
                                for before, after in zip(original_refs, updated["people_refs"])]
        writes = [row for row in entities if row["id"] in selected_set and row != repository.read(row["id"])]
        revision = _revision(versions, selected)

        def save_work():
            current = _inventory(repository)
            write_ids = {row["id"] for row in writes}
            if ({key: value for key, value in current.items() if key not in write_ids}
                    != {key: value for key, value in versions.items() if key not in write_ids}):
                raise ValueError("保存角色前出现并发来源变更，已取消本次保存")
            index = build_source_index(repository)
            for row in writes:
                if repository.read(row["id"]) != row:
                    raise ValueError("角色内容在保存期间被修改")
                for source in row.get("source_refs", []):
                    if index.find(source["provider"], "character", source["external_id"]) != row["id"]:
                        raise ValueError("角色来源没有唯一归属已选择实体")
            return service.apply_edits({"edits": [{"ref": item["ref"], "record": record}]}, settings=settings, data_root=data_root)

        report_progress(f"正在保存明确选择的 {len(selected)} 个角色与本作品演员关联")
        result = repository.apply_batch(writes, expected_revision=revision, revision_ids=selected, after_write=save_work)
    report_progress(f"角色建档已完成：选择 {len(selected)} 个，更新实体 {len(writes)} 个；没有自动合并同名角色")
    return {**result, "mode": MODE, "people_updated": bool(writes), "character_count": len(selected),
            "people_changes": [row for row in changes if row["id"] in selected_set]}
