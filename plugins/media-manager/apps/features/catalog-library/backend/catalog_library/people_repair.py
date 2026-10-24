"""Preview-first birthday repair using immutable local provider evidence only."""
from __future__ import annotations

from copy import deepcopy
import time

from people_catalog.model import MAX_REFERENCES, source_identity, validate_people_refs
from people_catalog.repository import PeopleRepository, safe_path
from work_catalog_yaml.operation_progress import operation_context, report_progress

from . import people, providers
from .people_birth_dates import merge_birth_date, normalize_birth_date


def _related_entities(record, repository):
    refs = validate_people_refs(record.get("people_refs", []))
    scopes = {}
    for row in refs:
        scopes.setdefault(row["id"], []).append(row.get("source"))
    pending = [row["id"] for row in refs]
    entities, seen = [], set()
    while pending:
        identity = pending.pop()
        if identity in seen:
            continue
        seen.add(identity)
        if len(seen) > MAX_REFERENCES:
            raise ValueError("当前作品关联人物超过安全数量限制")
        row = repository.read(identity)
        if row:
            entities.append(row)
            pending.extend(actor["person_id"] for actor in row.get("voice_actors", [])
                           if actor.get("scope") in scopes.get(identity, []))
    return sorted(entities, key=lambda row: row["id"])


def _build(record, repository, *, report=True):
    """Never infer corrections from locally edited infobox fields or live HTTP."""
    entities = _related_entities(record, repository)
    snapshots, updates, changes, warnings = {}, [], [], []
    for previous in entities:
        sources = [source for source in previous.get("source_refs", []) if source.get("provider") == "bangumi"]
        if not sources:
            continue
        source = sources[-1]
        try:
            external_id = providers._positive_id(source.get("external_id"))
            if source_identity("bangumi", previous["type"], external_id) != previous["id"]:
                raise ValueError("本地人物标识与来源身份不一致")
            snapshot_id = source.get("snapshot_id")
            if not isinstance(snapshot_id, str) or not providers._HASH.fullmatch(snapshot_id):
                raise ValueError("来源快照标识无效")
            if snapshot_id not in snapshots:
                snapshots[snapshot_id] = providers._read_snapshot(snapshot_id, snapshot_id,
                    safe_path(repository.root / "sources" / "bangumi"))
            snapshot = snapshots[snapshot_id]
            if snapshot.get("kind") != "people" or snapshot.get("provider") != "bangumi":
                raise ValueError("人物来源快照类型无效")
            raw_container = snapshot.get("raw")
            details = raw_container.get("details") if isinstance(raw_container, dict) else None
            if not isinstance(details, dict):
                raise ValueError("人物来源快照详情结构无效")
            raw = details.get(f"{previous['type']}:{external_id}")
            if not isinstance(raw, dict) or type(raw.get("id")) is not int or raw["id"] != external_id:
                raise ValueError("本地快照缺少身份一致的原始人物资料")
            normalized = normalize_birth_date(raw)
            for issue in normalized["issues"]:
                message = f"{previous['name']} / {issue['field']}：{issue['reason']}；原始来源：{issue.get('raw')}"
                if not issue.get("recovered") and message not in warnings:
                    warnings.append(message)
                elif issue.get("recovered") and report:
                    report_progress(f"生日来源格式已识别：{previous['name']}", detail=message,
                        context={"stage": "生日修复预览", "object": previous["id"], "action": "校验原始生日证据"})
            imported = previous.get("source_values", {}).get("bangumi", {})
            if not isinstance(imported, dict):
                raise ValueError("人物来源字段快照结构无效")
            old_source = imported.get("birth_date")
            if not isinstance(old_source, dict) or not normalized["available"]:
                continue
            target = merge_birth_date(old_source, normalized)
            if target == old_source:
                continue
            manual = previous.get("birth_date", {}) != old_source
            changed = [] if manual else ["birth_date"]
            change = {"id": previous["id"], "type": previous["type"], "name": previous["name"],
                      "action": "keep" if manual else "update", "changed_fields": changed,
                      "diagnostics": deepcopy(normalized["issues"]),
                      "preserved_fields": ["birth_date"] if manual else [],
                      "diff": [] if manual else [{"field": "birth_date", "before": deepcopy(previous.get("birth_date", {})), "after": target}]}
            changes.append(change)
            if manual:
                if report:
                    report_progress(f"保留手工生日：{previous['name']}", context={"stage": "生日修复预览", "object": previous["id"], "action": "保留手工修改"})
                continue
            updated = deepcopy(previous)
            updated["birth_date"] = deepcopy(target)
            updated["source_values"]["bangumi"]["birth_date"] = deepcopy(target)
            updates.append(updated)
            if report:
                report_progress(f"生日修复待确认：{previous['name']}，{old_source} → {target}",
                    context={"stage": "生日修复预览", "object": previous["id"], "action": "根据本地原始来源校验生日", "path": str(repository.path(previous["id"]))})
        except (ValueError, OSError) as exc:
            message = f"{previous['name']} / 生日修复：{exc}；保留现有资料"
            if message not in warnings:
                warnings.append(message)
    return {"entities": entities, "updates": updates, "changes": changes, "warnings": warnings}


def _digest(plan):
    return people._proposal_hash({}, plan["updates"])


def preview(body, *, settings, data_root=None, people_root=None):
    body = providers._body(body, {"ref"})
    item = providers._catalog_item(body.get("ref"), settings)
    providers._check_version(item, body["ref"])
    repository = PeopleRepository(people_root)
    with operation_context(stage="本地生日检查", action="预览当前作品关联人物生日", name=item.get("name", "")):
        plan = _build(item["record"], repository)
        identities = [row["id"] for row in plan["updates"]]
        payload = {"kind": "people-birthday-repair", "ref": deepcopy(item["ref"]),
                   "scope": providers._catalog_scope(settings), "people_scope": people._root_scope(repository),
                   "revision": repository.revision(identities), "proposal_sha256": _digest(plan),
                   "expires": int(time.time()) + providers.PREVIEW_LIFETIME}
        count = len(plan["updates"])
        report_progress(f"已检查 {len(plan['entities'])} 项本地人物，{count} 项生日可修复；尚未写入数据库")
        return {"ok": True, "mode": "birthday_repair", "ref": deepcopy(item["ref"]),
                "preview_token": providers._sign(payload), "changes": plan["changes"],
                "repair_count": count, "checked_count": len(plan["entities"]),
                "warnings": plan["warnings"], "warning_count": len(plan["warnings"])}


def apply(body, *, settings, data_root=None, people_root=None):
    body = providers._body(body, {"ref", "preview_token"})
    payload = providers._verify_token(body.get("preview_token"))
    repository = PeopleRepository(people_root)
    if (payload.get("kind") != "people-birthday-repair" or payload.get("ref") != body.get("ref") or
            payload.get("scope") != providers._catalog_scope(settings) or
            payload.get("people_scope") != people._root_scope(repository)):
        raise ValueError("生日修复预览与当前作品或人物数据库不一致，请重新预览")
    item = providers._catalog_item(body.get("ref"), settings)
    providers._check_version(item, body["ref"])
    plan = _build(item["record"], repository, report=False)
    if _digest(plan) != payload.get("proposal_sha256"):
        raise ValueError("人物生日或来源证据在预览后已变化，请重新预览")
    with operation_context(stage="确认生日修复", action="仅保存已确认的人物生日", name=item.get("name", "")):
        if plan["updates"]:
            repository.apply_batch(plan["updates"], expected_revision=payload["revision"])
        count = len(plan["updates"])
        report_progress(f"已修复 {count} 项人物生日；未改动作品、媒体文件或其他人物字段")
    return {"ok": True, "repair_count": count, "people_updated": bool(count),
            "people": people.project(item["record"], people_root=people_root),
            "records": [{"ref": deepcopy(item["ref"]), "record": deepcopy(item["record"])}],
            "warnings": plan["warnings"], "warning_count": len(plan["warnings"])}
