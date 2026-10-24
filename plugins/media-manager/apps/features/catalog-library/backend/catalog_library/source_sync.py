"""Multi-source previews with one signed, versioned work commit.

Remote failures are isolated while fetching. Confirmation is entirely local and
fails closed on stale work, changed snapshots or invalid people proposals.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import time

from work_catalog_yaml.layout import feature_data_root
from work_catalog_yaml.operation_progress import report_progress

from . import providers, service
from .source_bindings import normalized_refs

MAX_BINDINGS = 32
TOTAL_NETWORK_BUDGET = 240
_PRIMARY_ONLY = {"name", "metadata.episodes", "metadata.cover"}


def _root(source_root):
    return Path(source_root) / "_sync" if source_root is not None else feature_data_root("catalog-library") / "sources" / "_sync"


def _request(binding, ref):
    provider = providers._provider(binding.get("provider"))
    bounds = providers._episode_range(binding.get("episode_range"))
    season = binding.get("season_number") if provider == "tmdb" else None
    if provider == "tmdb":
        # Unlike a new search, refreshing a binding must never guess season 1.
        providers._adapter(provider).parse_season_number(season)
    if (binding.get("scope") or "") != providers._source_scope(bounds, provider=provider, season_number=season):
        raise ValueError("来源范围与季/章节标识不一致，请重新选择来源")
    body = {"ref": ref, "provider": provider, "subject_id": binding.get("external_id"),
            "replace_binding_id": binding["binding_id"]}
    if bounds is not None:
        body["episode_range"] = bounds
    if season is not None:
        body["season_number"] = season
    return body


def _locally_modified(record, field):
    value = providers._field(record, field)
    if not value:
        # Empty local fields receive their first available value by default,
        # even when they were cleared after a previous import.
        return False
    provenance = record.get("metadata", {}).get("field_sources", {}).get(field, {})
    previous = provenance.get("imported_value_sha256") if isinstance(provenance, dict) else None
    if isinstance(previous, str) and providers._HASH.fullmatch(previous):
        return hashlib.sha256(providers._json_bytes(value)).hexdigest() != previous
    # A populated field with no tracked import belongs to the user too; an
    # untracked empty field can safely receive its first value.
    return bool(value)


def _alias_projection(record, entries, *, primary=None, alias_binding=None):
    """One order-independent additive alias plan for UI and confirmation."""
    primary = providers._field(record, "name") if primary is None else primary
    existing = list(providers._field(record, "metadata.aliases") or [])
    merged = list(existing)
    for entry in entries:
        if entry["binding"].get("provider") == "douban":
            continue
        title = next((row["after"] for row in entry["preview"]["diff"] if row["field"] == "name"), "")
        merged = providers._merge_aliases(merged, [title], primary)
    keys = {providers._alias_key(value) for value in existing}
    automatic = [value for value in merged if providers._alias_key(value) not in keys]
    if alias_binding is not None:
        for entry in entries:
            if entry["binding"].get("provider") == "douban":
                continue
            if alias_binding not in {"all", entry["binding"]["binding_id"]}:
                continue
            aliases = next((row["after"] for row in entry["preview"]["diff"] if row["field"] == "metadata.aliases"), [])
            merged = providers._merge_aliases(merged, aliases, primary)
    return {"after": merged, "automatic": automatic}


def _diff(record, entries):
    grouped = {}
    for entry in entries:
        binding, preview = entry["binding"], entry["preview"]
        for row in preview["diff"]:
            if binding.get("provider") == "douban" and row.get("field") != "metadata.ratings.douban":
                continue
            if not row.get("selectable"):
                continue
            grouped.setdefault(row["field"], []).append({**deepcopy(row), "binding_id": binding["binding_id"],
                "provider": binding["provider"], "role": binding["role"]})
    result = []
    for field, label in providers._FIELDS.items():
        choices = grouped.get(field, [])
        if not choices:
            continue
        choices.sort(key=lambda row: (row["role"] != "primary", row["provider"], row["binding_id"]))
        before = providers._field(record, field)
        locally_modified = _locally_modified(record, field)
        if field == "metadata.aliases":
            merged = _alias_projection(record, entries, alias_binding="all")["after"]
            recommended = {**deepcopy(choices[0]), "binding_id": "all", "provider": "multiple", "after": merged}
            # Aliases are additive evidence, not mutually exclusive providers.
            choices = [recommended]
        else:
            eligible = [row for row in choices if row["role"] == "primary"] if field in _PRIMARY_ONLY else choices
            provenance = record.get("metadata", {}).get("field_sources", {}).get(field, {})
            owner = provenance.get("binding_id") if isinstance(provenance, dict) else None
            # Keep the field's chosen source if still available; supplement
            # sources may refresh their own fields but cannot take ownership of
            # title/chapters/cover merely because a primary request failed.
            recommended = next((row for row in eligible if row["binding_id"] == owner), None)
            if recommended is None and isinstance(provenance, dict):
                recommended = next((row for row in eligible if row["provider"] == provenance.get("provider") and
                    any(entry["binding"]["binding_id"] == row["binding_id"] and
                        str(entry["binding"].get("external_id")) == str(provenance.get("external_id")) for entry in entries)), None)
            recommended = recommended or (eligible[0] if eligible else choices[0])
        row = deepcopy(recommended)
        row.update(field=field, label=label, before=deepcopy(before), changed=before != row["after"],
                   locally_modified=locally_modified, choices=deepcopy(choices), selectable=True)
        safe_role = field not in _PRIMARY_ONLY or row["role"] == "primary"
        conflicts = len({hashlib.sha256(providers._json_bytes(choice["after"])).hexdigest() for choice in choices
                         if choice["role"] == "primary"}) > 1
        # Multiple equal-priority chapter/title values require a human choice;
        # network completion order is never a data policy.
        row["default_selected"] = bool(row["changed"] and safe_role and
            (field == "metadata.aliases" or (not before and not conflicts)))
        if locally_modified and field != "metadata.aliases":
            row["reason"] = "保留本地已有或手动修改的数据；明确勾选后才会替换。"
        elif not safe_role:
            row["reason"] = "补充来源不自动覆盖主标题、章节或封面；如确需采用，请明确选择。"
        elif conflicts and field != "metadata.aliases":
            row["reason"] = "多个主来源提供不同值，请核对并选择采用来源。"
        elif before and row["changed"] and field != "metadata.aliases":
            row["reason"] = "来源内容与已有资料不同，请确认是否采用。"
        result.append(row)
    name = next((row for row in result if row["field"] == "name"), None)
    aliases = next((row for row in result if row["field"] == "metadata.aliases"), None)
    if name and aliases:
        for row in [aliases, *aliases["choices"]]:
            row["default_name_binding_id"] = name["binding_id"]
            row["after_by_name_binding"] = {candidate["binding_id"]: _alias_projection(record, entries,
                primary=candidate["after"], alias_binding=row["binding_id"])["after"] for candidate in name["choices"]}
            row["after_when_name_selected"] = row["after_by_name_binding"][name["binding_id"]]
    return result


def _people_previews(candidate):
    preview = candidate.get("people_preview")
    return (preview.get("sources") or [preview]) if preview else []


def _people_proposals(entries):
    result = []
    for entry in entries:
        payload = providers._verify_token(entry["preview"]["preview_token"])
        result.extend(([payload["people_proposal"]] if payload.get("people_proposal") else [])
                      + payload.get("supplement_people_proposals", []))
    return result


def _people_scope(proposal):
    return (proposal.get("provider"), proposal.get("subject_id"), proposal.get("season_number"),
            providers._json_bytes(proposal.get("episode_range")))


def _replace_enriched_people(entries, candidate):
    """A later Wiki supplement replaces, not duplicates, its raw TMDB plan.

    Both previews are internal signed products. Re-sign the earlier work-only
    item before batch rebasing, so confirmation sees exactly one people plan
    containing the newest supplement and still commits every work source.
    """
    payload = providers._verify_token(candidate["preview_token"])
    scopes = {_people_scope(row) for row in payload.get("supplement_people_proposals", [])
              if row.get("provider") == "tmdb"}
    if not scopes:
        return
    for entry in entries:
        preview = entry["preview"]
        old = providers._verify_token(preview["preview_token"])
        proposals = ([old["people_proposal"]] if old.get("people_proposal") else []) + old.get("supplement_people_proposals", [])
        removed = {row["snapshot_id"] for row in proposals if _people_scope(row) in scopes}
        if not removed:
            continue
        if (old.get("people_proposal") or {}).get("snapshot_id") in removed:
            old["people_proposal"] = None
        old["supplement_people_proposals"] = [row for row in old.get("supplement_people_proposals", []) if row["snapshot_id"] not in removed]
        children = [row for row in _people_previews(preview) if row.get("snapshot_id") not in removed]
        preview["people_preview"] = {"sources": children, "changes": [], "warnings": []} if children else None
        preview["preview_token"] = providers._sign(old)


def preview(body, *, settings, source_root=None, assets_root=None, people_root=None):
    body = providers._body(body, {"ref", "binding_ids"})
    item = providers._catalog_item(body.get("ref"), settings)
    providers._check_version(item, body["ref"])
    bindings = normalized_refs(item["record"])
    ids = body.get("binding_ids")
    if ids is not None and (not isinstance(ids, list) or not ids or any(not isinstance(value, str) for value in ids)
                            or len(ids) != len(set(ids))):
        raise ValueError("binding_ids 必须为非空且不重复的来源标识数组")
    enabled = {row["binding_id"]: row for row in bindings if row["enabled"] and row.get("provider") in providers._PROVIDERS}
    if ids is not None and not set(ids).issubset(enabled):
        raise ValueError("选中的来源不存在或已停用，请重新加载")
    selected = [enabled[key] for key in (ids if ids is not None else enabled)]
    if not selected:
        raise ValueError("没有启用的已绑定来源，请先选择来源条目")
    if len(selected) > MAX_BINDINGS:
        raise ValueError(f"一次最多刷新 {MAX_BINDINGS} 个来源")
    # Main-source people establish the first cards. Supplements search those
    # in-memory plans as well as the existing library before suggesting updates.
    selected.sort(key=lambda row: (row["role"] != "primary", row["provider"] == "wikipedia", row["provider"], row["binding_id"]))
    entries, statuses, warnings = [], [], []
    deadline = time.monotonic() + TOTAL_NETWORK_BUDGET
    for binding in selected:
        status = {"binding_id": binding["binding_id"], "provider": binding["provider"],
                  "external_id": binding.get("external_id"), "role": binding["role"]}
        try:
            if time.monotonic() >= deadline:
                raise ValueError("多来源刷新已达到总网络时限；请单独重试此来源")
            report_progress("正在刷新作品来源", context={"binding_id": binding["binding_id"], "name": item.get("name", ""),
                "provider": binding["provider"], "stage": "多来源获取"})
            candidate = providers.preview_provider_import(_request(binding, item["ref"]), settings=settings,
                source_root=source_root, assets_root=assets_root, people_root=people_root, _deadline=deadline,
                _prior_people_proposals=_people_proposals(entries))
            _replace_enriched_people(entries, candidate)
            entries.append({"binding": binding, "preview": candidate})
            status.update(status="ok", warnings=candidate.get("warnings", []))
            warnings.extend(candidate.get("warnings", []))
        except (ValueError, OSError, RuntimeError) as exc:
            status.update(status="error", error=str(exc)[:2000])
            warnings.append(f"{binding['provider']} {binding.get('external_id')}: {str(exc)[:2000]}；此来源与已有字段保留")
            report_progress(warnings[-1], detail="多来源刷新：单个来源失败", context={"binding_id": binding["binding_id"],
                "name": item.get("name", ""), "provider": binding["provider"], "stage": "多来源获取", "reason": str(exc)[:2000]})
        statuses.append(status)
    # A write concurrent with the remote requests invalidates the whole preview.
    providers._check_version(providers._catalog_item(item["ref"], settings), item["ref"])
    from . import people_import_batch
    people_import_batch.rebase(entries, item, people_root=people_root)
    diff = _diff(item["record"], entries)
    aliases = _alias_projection(item["record"], entries)["automatic"]
    result = {"ref": deepcopy(item["ref"]), "diff": diff, "selected_fields": [row["field"] for row in diff if row["default_selected"]],
              "sources": statuses, "warnings": list(dict.fromkeys(warnings)), "automatic_aliases": aliases,
              "can_apply": bool(entries)}
    name = next((row for row in diff if row["field"] == "name"), None)
    if name:
        result["default_name_binding_id"] = name["binding_id"]
        result["automatic_aliases_by_name_binding"] = {candidate["binding_id"]: _alias_projection(item["record"], entries,
            primary=candidate["after"])["automatic"] for candidate in name["choices"]}
    people_previews = [child for entry in entries for child in _people_previews(entry["preview"])]
    result["people_preview"] = {"sources": people_previews,
        "changes": [change for preview in people_previews for change in preview.get("changes", [])],
        "warnings": [warning for preview in people_previews for warning in preview.get("warnings", [])]} if people_previews else None
    if not entries:
        return {**result, "preview_token": None}
    expiry = min(providers._verify_token(entry["preview"]["preview_token"])["expires"] for entry in entries)
    snapshot = {"schema_version": 1, "provider": "bangumi", "kind": "source_sync", "entries": entries,
                "result": result, "expires": expiry}
    snapshot_id, digest = providers._save_snapshot(snapshot, _root(source_root))
    payload = {"kind": "source_sync", "ref": item["ref"], "scope": providers._catalog_scope(settings),
               "snapshot_id": snapshot_id, "snapshot_sha256": digest, "expires": expiry}
    return {**result, "preview_token": providers._sign(payload)}


def apply(body, *, settings, source_root=None, assets_root=None, people_root=None, data_root=None):
    body = providers._body(body, {"ref", "preview_token", "selected_fields", "field_choices", "people_selections"})
    payload = providers._verify_token(body.get("preview_token"))
    if payload.get("kind") != "source_sync" or payload.get("scope") != providers._catalog_scope(settings) or body.get("ref") != payload.get("ref"):
        raise ValueError("多来源预览与作品或数据库不一致，请重新预览")
    snapshot = providers._read_snapshot(payload["snapshot_id"], payload["snapshot_sha256"], _root(source_root))
    if snapshot.get("kind") != "source_sync" or snapshot.get("result", {}).get("ref") != payload["ref"]:
        raise ValueError("多来源预览快照不一致")
    item = providers._catalog_item(payload["ref"], settings)
    providers._check_version(item, payload["ref"])
    selected, choices = body.get("selected_fields"), body.get("field_choices", {})
    rows = {row["field"]: row for row in snapshot["result"]["diff"]}
    if (not isinstance(selected, list) or any(not isinstance(field, str) or field not in rows for field in selected)
            or len(selected) != len(set(selected)) or not isinstance(choices, dict) or set(choices) - set(selected)):
        raise ValueError("采用字段或来源选项无效，请重新预览")
    assignments = {}
    for field in selected:
        chosen = choices.get(field, rows[field]["binding_id"])
        if not isinstance(chosen, str) or chosen not in {row["binding_id"] for row in rows[field]["choices"]}:
            raise ValueError("所选字段来源不在已签名预览中")
        assignments[field] = chosen
    from . import people
    child_proposals = []
    child_selection_ids = []
    for entry in snapshot["entries"]:
        child = providers._verify_token(entry["preview"]["preview_token"])
        children = ([child["people_proposal"]] if child.get("people_proposal") else []) + child.get("supplement_people_proposals", [])
        child_proposals.extend(children)
        child_selection_ids.append({row["snapshot_id"] for row in children if row.get("selection_only")})
    people.with_supplement_selections(child_proposals, body.get("people_selections"))
    people_selections = body.get("people_selections") or {}
    record, proposals, aliases, warnings = deepcopy(item["record"]), [], [], list(snapshot["result"]["warnings"])
    for entry, selection_ids in zip(snapshot["entries"], child_selection_ids):
        binding, candidate = entry["binding"], entry["preview"]
        fields = [field for field, binding_id in assignments.items() if binding_id in {binding["binding_id"], "all"} and
                  any(row["field"] == field and row.get("selectable") for row in candidate["diff"])]
        prepared = providers.apply_provider_import({"provider": binding["provider"], "ref": candidate["ref"],
            "snapshot_id": candidate["snapshot_id"], "preview_token": candidate["preview_token"], "selected_fields": fields,
            "people_selections": {key: rows for key, rows in people_selections.items() if key in selection_ids}},
            settings=settings, source_root=source_root, assets_root=assets_root, people_root=people_root,
            data_root=data_root, _prepare_only=True, _record_override=record)
        record = prepared["record"]
        for field in prepared["applied_fields"]:
            record["metadata"]["field_sources"][field]["binding_id"] = binding["binding_id"]
        aliases.extend(prepared["automatic_aliases_added"])
        warnings.extend(prepared["warnings"])
        proposals.extend(prepared.get("people_proposals", [prepared["people_proposal"]] if prepared["people_proposal"] else []))
    alias_plan = _alias_projection(item["record"], snapshot["entries"], primary=providers._field(record, "name"),
                                   alias_binding=assignments.get("metadata.aliases"))
    aliases = alias_plan["automatic"]
    # Preparations may add a title before a later source is chosen as the final
    # primary name. Always converge to the signed aggregate plan, even when the
    # final aliases equal the original (otherwise temporary aliases leak out).
    if "metadata.aliases" in selected or alias_plan["after"] or "aliases" in record.get("metadata", {}):
        providers._set_field(record, "metadata.aliases", alias_plan["after"])
    if alias_plan["after"] != (providers._field(item["record"], "metadata.aliases") or []) or "metadata.aliases" in selected:
        record["metadata"].setdefault("field_sources", {})["metadata.aliases"] = {
            "provider": "multiple", "binding_id": assignments.get("metadata.aliases", "all"),
            "snapshot_id": payload["snapshot_id"], "imported_value_sha256": hashlib.sha256(providers._json_bytes(alias_plan["after"])).hexdigest()}
    else:
        original_sources = item["record"].get("metadata", {}).get("field_sources", {})
        if "metadata.aliases" in original_sources:
            record["metadata"].setdefault("field_sources", {})["metadata.aliases"] = deepcopy(original_sources["metadata.aliases"])
        else:
            record["metadata"].get("field_sources", {}).pop("metadata.aliases", None)
    if proposals:
        from . import people
        result = people.apply_many_with_work(proposals, item, record, settings=settings, data_root=data_root, people_root=people_root)
    else:
        result = service.apply_edits({"edits": [{"ref": item["ref"], "record": record}]}, settings=settings, data_root=data_root)
        result["people_updated"] = False
    return {**result, "applied_fields": selected, "automatic_aliases_added": list(dict.fromkeys(aliases)) if result.get("db_committed") else [],
            "sources": snapshot["result"]["sources"], "warnings": list(dict.fromkeys([*warnings, *result.get("warnings", [])]))}
