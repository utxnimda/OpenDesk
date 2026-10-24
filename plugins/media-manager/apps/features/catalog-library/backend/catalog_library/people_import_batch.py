"""Signed, read-only main-to-supplement people plans for a source batch.

Earlier default creations and safe fills are searchable before any DB write. Their
profiles live only in content-addressed preview cache, never in the people DB.
"""
from copy import deepcopy

from people_catalog.model import validate_entity
from people_catalog.repository import PeopleRepository, safe_path
from people_catalog.source_links import build_source_index
from work_catalog_yaml.operation_progress import report_progress

from . import providers
from .people_field_merge import is_alias_field

MAX_PLANNED = 10000


def _root(repository):
    return safe_path(repository.root / "sources" / "_batch")


def repository_for(proposal, repository):
    """Overlay the signed preview baseline while retaining the disk inventory."""
    context = proposal.get("people_batch_context")
    if context is None:
        return repository
    from . import people_import
    if not isinstance(context, dict) or set(context) != {"snapshot_id", "sha256"}:
        raise ValueError("人物批次预览上下文无效，请重新预览")
    cached = people_import._cached_repository(repository)
    snapshot = providers._read_snapshot(context["snapshot_id"], context["sha256"], _root(cached))
    entities, planned = snapshot.get("entities"), snapshot.get("planned")
    if (snapshot.get("kind") != "people_import_batch" or snapshot.get("source_role") not in {"primary", "supplement"}
            or snapshot.get("inventory_sha256") != people_import._hash(cached.import_versions)
            or not isinstance(entities, list) or len(entities) > MAX_PLANNED or not isinstance(planned, dict)):
        raise ValueError("人物批次基线或人物库版本已变化，请重新预览")
    rows = {row["id"]: validate_entity(row) for row in entities}
    if len(rows) != len(entities) or set(rows) - set(cached.import_rows) != set(planned):
        raise ValueError("人物批次待新增身份基线无效，请重新预览")
    for identity, origin in planned.items():
        if (not isinstance(origin, dict) or set(origin) != {"provider", "snapshot_id", "external_id"}
                or origin["provider"] not in {"bangumi", "tmdb", "wikipedia"}
                or not isinstance(origin["snapshot_id"], str) or not providers._HASH.fullmatch(origin["snapshot_id"])
                or not isinstance(origin["external_id"], str) or ":" not in origin["external_id"]):
            raise ValueError("人物批次来源依赖无效，请重新预览")

    class Overlay:
        root = cached.root
        import_rows = {**cached.import_rows, **rows}
        import_versions = cached.import_versions
        import_planned = deepcopy(planned)
        import_role = snapshot["source_role"]

        def read(self, identity):
            return deepcopy(self.import_rows.get(identity))

        def _bytes(self, identity):
            return cached._bytes(identity)

    result = Overlay()
    build_source_index(result)
    return result


def annotate(display, repository):
    """Add display-only dependency hints consistently before plan hashing."""
    planned = getattr(repository, "import_planned", {})
    if hasattr(repository, "import_role"):
        display["source_role"] = repository.import_role
    for card in [*display.get("targets", []),
                 *(card for entry in display.get("entries", []) for card in entry.get("targets", []))]:
        origin = planned.get(card["id"])
        if origin:
            card.update(planned=True, planned_provider=origin["provider"],
                        planned_snapshot_id=origin["snapshot_id"], planned_external_id=origin["external_id"])
    return display


def validate_choices(proposal, snapshot, choices, repository, pending):
    """Do not silently recreate or retarget a cancelled/changed earlier card."""
    from . import people_import
    overlay = repository_for(proposal, repository)
    planned = getattr(overlay, "import_planned", {})
    if not proposal.get("people_batch_context"):
        return
    if not isinstance(choices, list):
        raise ValueError("人物选择必须为数组")
    index = build_source_index(overlay)
    entries = {row["external_id"]: row for row in people_import._entries(snapshot)}
    for choice in choices:
        if not isinstance(choice, dict) or choice.get("action") == "skip":
            continue
        if not isinstance(choice.get("external_id"), str):
            continue
        entry = entries.get(choice["external_id"])
        if entry is None:
            continue
        owner = people_import._owner(entry["candidate"], index)
        if choice.get("action") == "create" and owner in planned:
            raise ValueError("前序主来源已计划新增此人物；请选择复用，不能在辅助来源重复创建")
        identity = choice.get("target_id") if choice.get("action") == "reuse" else None
        if identity is not None and not isinstance(identity, str):
            continue
        expected = overlay.read(identity) if identity else None
        if expected is None:
            continue
        actual = pending.get(identity) or repository.read(identity)
        if not actual or actual.get("type") != expected["type"] or actual.get("merged_into"):
            raise ValueError("辅助来源依赖的前序人物已取消或改选，请重新预览或选择更新目标")
        if identity in planned:
            origin = planned[identity]
            kind, external_id = origin["external_id"].split(":", 1)
            if not any(ref.get("provider") == origin["provider"] and ref.get("entity_type") == kind
                       and str(ref.get("external_id")) == external_id for ref in actual.get("source_refs", [])):
                raise ValueError("辅助来源依赖的前序人物身份已改变，请重新预览")
        fields = choice.get("fields", [])
        if not isinstance(fields, list):
            continue
        overrides = choice.get("overrides", {})
        compared = {value for value in fields if isinstance(value, str)}
        if owner != identity:
            # A cross-source name suggestion depends on the displayed names;
            # an exact source identity does not. Unselected profile edits must
            # not block a later exact-source, link-only reuse.
            compared.update({"name", "name_cn", "aliases"})
            compared.update(field for field in people_import._fields(actual, expected)
                            if field.startswith(("localized.name.", "localized.aliases.")))
        for field in compared:
            current = people_import._value(actual, field)
            if current != people_import._value(expected, field):
                if (owner == identity and field in fields and is_alias_field(field)
                        and isinstance(overrides, dict) and field not in overrides):
                    # Proven source identity plus an additive union is safe
                    # against the latest pending list, including a cancelled
                    # earlier fill. Explicit replacements and name-only
                    # identity suggestions still require the exact baseline.
                    continue
                # Repeated scopes may explicitly adopt the same source value.
                # That is idempotent, not an overwrite of an unseen change.
                if (field in fields and isinstance(overrides, dict) and field not in overrides
                        and current == people_import._value(entry["candidate"], field)):
                    continue
                raise ValueError("前序人物资料已改变，辅助来源显示的更新前值不再一致；请取消本次辅助更新，先保存前序人物，再重新预览辅助来源并确认更新字段")


def rebase(entries, item, *, people_root=None):
    """Re-sign displays against ordered default creations and safe profile fills."""
    from . import people_import
    candidates = []
    for entry in entries:
        payload = providers._verify_token(entry["preview"]["preview_token"])
        proposals = ([payload["people_proposal"]] if payload.get("people_proposal") else []) + payload.get("supplement_people_proposals", [])
        if any(proposal.get("selection_only") for proposal in proposals):
            candidates.append((entry, payload, proposals))
    if not candidates:
        return entries
    repository = people_import._cached_repository(PeopleRepository(people_root))
    pending, origins = {}, {}
    for entry, payload, proposals in candidates:
        projected = []
        for proposal in proposals:
            if not proposal.get("selection_only"):
                raise ValueError("旧版人物来源不能与统一批次计划混用，请重新预览")
            if proposal.get("inventory_sha256") != people_import._hash(repository.import_versions):
                raise ValueError("多来源读取期间人物库已变化，请重新预览")
            context = {"schema_version": 1, "provider": "bangumi", "kind": "people_import_batch",
                       "inventory_sha256": proposal["inventory_sha256"], "source_role": entry["binding"]["role"],
                       "entities": [deepcopy(pending[key]) for key in sorted(pending)], "planned": deepcopy(origins)}
            context_id, digest = providers._save_snapshot(context, _root(repository))
            proposal["people_batch_context"] = {"snapshot_id": context_id, "sha256": digest}
            overlay = repository_for(proposal, repository)
            snapshot = people_import._snapshot(proposal, repository)
            display, _versions = people_import.preview(snapshot, proposal["snapshot_id"], item["record"], overlay)
            proposal["selection_plan_sha256"] = people_import._hash(display)
            display["selection_token"] = providers._sign({**proposal, "kind": people_import.MODE, "expires": payload["expires"]})
            projected.append(display)
            counts = {action: sum(row["action"] == action for row in display["entries"])
                      for action in ("create", "reuse", "skip")}
            report_progress(f"人物顺序预览：默认新增 {counts['create']} 项、复用 {counts['reuse']} 项、待核对 {counts['skip']} 项",
                context={"stage": "人物主辅顺序预览", "provider": proposal["provider"],
                         "source_role": entry["binding"]["role"], "action": "仅生成预览计划", "count": len(display["entries"])})
            by_key = {value["external_id"]: value for value in people_import._entries(snapshot)}
            for row in display["entries"]:
                if row["action"] not in {"create", "reuse"}:
                    continue
                candidate = deepcopy(by_key[row["external_id"]])
                people_import._stamp_sources(candidate["candidate"], proposal["snapshot_id"])
                choice = {"action": row["action"], "fields": row["selected_fields"], "overrides": row.get("overrides", {})}
                previous = overlay.read(row["target_id"]) if row["action"] == "reuse" else None
                entity = people_import._selected(candidate, choice, previous, overlay)
                pending[entity["id"]] = entity
                if row["action"] == "create":
                    origins[entity["id"]] = {"provider": proposal["provider"], "snapshot_id": proposal["snapshot_id"],
                                              "external_id": row["external_id"]}
                if len(pending) > MAX_PLANNED:
                    raise ValueError("批次人物计划超过数量安全上限，请分批预览")
        original = entry["preview"].get("people_preview") or {}
        entry["preview"]["people_preview"] = ({**original, "sources": projected} if "sources" in original else projected[0])
        entry["preview"]["preview_token"] = providers._sign(payload)
    return entries
