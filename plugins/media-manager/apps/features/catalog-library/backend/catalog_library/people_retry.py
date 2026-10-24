"""Retry failed TMDB evidence inside an existing signed, read-only preview."""
from copy import deepcopy
import time

from people_catalog.repository import PeopleRepository
from work_catalog_yaml.operation_progress import operation_context, report_progress

from . import people, people_import, people_import_batch, providers, source_sync, tmdb_people


def _proposals(payload):
    return ([payload["people_proposal"]] if payload.get("people_proposal") else []) + payload.get("supplement_people_proposals", [])


def _display(proposal, item, repository, settings):
    if (proposal.get("selection_only") is not True or proposal.get("ref") != item["ref"]
            or proposal.get("scope") != providers._catalog_scope(settings)
            or proposal.get("people_scope") != people._root_scope(repository)
            or proposal.get("inventory_sha256") != people_import._hash(repository.import_versions)):
        raise ValueError("人物预览或人物库已变化，请重新预览后重试")
    snapshot = people_import._snapshot(proposal, repository)
    overlay = people_import_batch.repository_for(proposal, repository)
    display, _ = people_import.preview(snapshot, proposal["snapshot_id"], item["record"], overlay)
    if people_import._hash(display) != proposal.get("selection_plan_sha256"):
        raise ValueError("人物对应关系或字段在预览后已变化，请重新预览")
    return snapshot, display


def _aggregate(displays):
    return {"sources": displays,
            "changes": [row for display in displays for row in display.get("changes", [])],
            "warnings": list(dict.fromkeys(warning for display in displays for warning in display.get("warnings", [])))}


def _warnings(current, removed, added):
    removed = set(removed)
    return list(dict.fromkeys([warning for warning in current if warning not in removed] + added))


def _work_node(payload, *, source_root):
    snapshot = providers._read_snapshot(payload.get("snapshot_id"), payload.get("snapshot_sha256"),
        source_root, provider=payload.get("provider", "bangumi"))
    return {"payload": payload, "snapshot": snapshot, "proposals": _proposals(payload)}


def _update_work(node, displays, *, removed, added, source_root):
    payload, snapshot = node["payload"], node["snapshot"]
    snapshot["people_warnings"] = _warnings(snapshot.get("people_warnings", []), removed, added)
    snapshot_id, digest = providers._save_snapshot(snapshot, source_root)
    payload.update(snapshot_id=snapshot_id, snapshot_sha256=digest)
    return {"preview_token": providers._sign(payload), "snapshot_id": snapshot_id, "snapshot_sha256": digest,
            "people_preview": _aggregate(displays) if payload.get("supplement_people_proposals") else displays[0]}


def retry(body, *, settings, people_root=None, source_root=None, data_root=None):
    """Only failed IDs from signed snapshots cause network reads; no DB writes."""
    body = providers._body(body, {"ref", "preview_token", "snapshot_id", "failure_ids"})
    payload = providers._verify_token(body.get("preview_token"))
    if payload.get("scope") != providers._catalog_scope(settings) or payload.get("ref") != body.get("ref"):
        raise ValueError("重试凭据与当前作品或数据库不一致，请重新预览")
    item = providers._catalog_item(payload["ref"], settings)
    providers._check_version(item, payload["ref"])
    repository = people_import._cached_repository(PeopleRepository(people_root))
    nodes, batch = [], None
    if payload.get("kind") == "people":
        people._proposal_source(payload, item["record"])
        nodes = [{"payload": payload, "proposals": [payload]}]
    elif payload.get("kind") == "source_sync":
        batch = providers._read_snapshot(payload["snapshot_id"], payload["snapshot_sha256"], source_sync._root(source_root))
        if batch.get("kind") != "source_sync" or batch.get("result", {}).get("ref") != item["ref"]:
            raise ValueError("多来源重试快照不一致")
        for entry in batch["entries"]:
            child = providers._verify_token(entry["preview"]["preview_token"])
            if child.get("scope") != payload["scope"] or child.get("ref") != item["ref"]:
                raise ValueError("多来源子预览不属于当前作品")
            nodes.append({**_work_node(child, source_root=source_root), "entry": entry})
    elif payload.get("kind") is None and payload.get("provider") in providers._PROVIDERS:
        nodes = [_work_node(payload, source_root=source_root)]
    else:
        raise ValueError("此类预览不支持人物失败项重试")

    matched, projections = [], {}
    for node in nodes:
        for proposal in node["proposals"]:
            snapshot, display = _display(proposal, item, repository, settings)
            projections[id(proposal)] = display
            if proposal["snapshot_id"] == body.get("snapshot_id"):
                matched.append((node, proposal, snapshot, display))
    if len(matched) != 1:
        raise ValueError("失败项必须属于当前预览中唯一的人物来源快照")
    node, proposal, snapshot, previous = matched[0]
    if proposal.get("provider") != "tmdb":
        raise ValueError("当前仅支持 TMDB 人物来源的失败项重试")

    with operation_context(stage="人物失败项重试", action="只重试失败证据，保留成功结果", name=item.get("name", "")):
        updated = tmdb_people.retry_snapshot(snapshot, body.get("failure_ids"), repository=repository,
                                            deadline=time.monotonic() + people.IMPORT_TIMEOUT)
        providers._check_version(providers._catalog_item(payload["ref"], settings), payload["ref"])
        if people_import._inventory(repository) != repository.import_versions:
            raise ValueError("人物库在重试期间已变化，请重新预览；未修改作品或人物")
        prepared = people_import.prepare(updated, item, settings=settings, repository=repository)
        replacement, display = prepared["proposal"], prepared["preview"]
        for key in ("binding_id", "independent_source"):
            if key in proposal:
                replacement[key] = deepcopy(proposal[key])
                display[key] = deepcopy(proposal[key])
        old_id, new_id = proposal["snapshot_id"], replacement["snapshot_id"]
        removed, added = previous.get("warnings", []), display.get("warnings", [])
        if payload.get("kind") == "people":
            replacement.update(kind="people", expires=payload["expires"])
            result = {**display, "ref": deepcopy(item["ref"]), "preview_token": providers._sign(replacement)}
        else:
            proposal.clear()
            proposal.update(replacement)
            projections[id(proposal)] = display
            displays = [projections[id(child)] for child in node["proposals"]]
            for child, child_display in zip(node["proposals"], displays):
                child_display["selection_token"] = providers._sign({**child, "kind": people_import.MODE,
                    "expires": node["payload"]["expires"]})
            result = _update_work(node, displays, removed=removed, added=added, source_root=source_root)
            if batch is not None:
                entry = node["entry"]
                entry["preview"].update(result)
                entry["preview"]["warnings"] = _warnings(entry["preview"].get("warnings", []), removed, added)
                people_import_batch.rebase(batch["entries"], item, people_root=people_root)
                aggregate = deepcopy(batch["result"])
                aggregate["people_preview"] = _aggregate([child for value in batch["entries"]
                    for child in source_sync._people_previews(value["preview"])])
                aggregate["warnings"] = _warnings(aggregate.get("warnings", []), removed, added)
                for status in aggregate.get("sources", []):
                    if status.get("binding_id") == entry["binding"]["binding_id"]:
                        status["warnings"] = deepcopy(entry["preview"]["warnings"])
                batch["result"] = aggregate
                snapshot_id, digest = providers._save_snapshot(batch, source_sync._root(source_root))
                payload.update(snapshot_id=snapshot_id, snapshot_sha256=digest)
                result = {**aggregate, "preview_token": providers._sign(payload)}
        report_progress("失败项重试预览已更新；成功数据保留，等待统一确认保存",
            context={"stage": "人物失败项重试", "provider": "tmdb", "count": len(display.get("retry_failures", [])),
                     "action": "仅保存来源快照，未修改作品或人物"})
        return {"ok": True, "preview_patch": result, "snapshot_replacements": {old_id: new_id},
                "warning_replacements": {"removed": removed, "added": added}}
