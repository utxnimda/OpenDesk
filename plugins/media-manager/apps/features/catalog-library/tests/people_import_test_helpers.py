"""Explicit simulated user decisions; production never chooses these defaults."""
from copy import deepcopy
from people_catalog import localization


def cast(preview):
    return [{**deepcopy(entry["candidate"]), **deepcopy(relation), "id": entry["candidate"]["id"]} for entry in preview["entries"]
            for relation in entry["relations"] if relation["kind"] == "work" and relation.get("character_name")]


def selections(preview, repository=None, *, relations=True):
    if not preview:
        return {}
    if "people_preview" in preview:
        return selections(preview["people_preview"], repository, relations=relations)
    if preview.get("sources") is not None:
        result = {}
        for source in preview["sources"]:
            result.update(selections(source, repository, relations=relations))
        return result
    result = []
    for entry in preview.get("entries", []):
        owner = entry.get("identity_owner_id")
        target = next((row for row in entry["targets"] if row["id"] == owner), None)
        diff = target["diff"] if owner else entry["creation"]["diff"]
        old = repository.read(owner) if owner and repository else None
        baseline = (old or {}).get("source_values", {}).get(preview["provider"], {})
        fields, overrides = [], {}
        for row in diff:
            field = row["field"]
            if not row["selectable"] or not row["after"] or row.get("must_override"):
                continue
            if old:
                # Simulated user leaves manual edits and merged target profiles
                # unchecked; no production code infers this decision.
                if owner != entry["candidate"]["id"]:
                    continue
                current = localization.field_value(old, field) if localization.is_field(field) else old.get(field)
                present = (field in localization.field_paths(old)) if localization.is_field(field) else field in old
                if present and (current != baseline[field] if field in baseline else bool(current) or current == ""):
                    continue
                if field == "birth_date":
                    overrides[field] = {**deepcopy(old.get(field, {})), **deepcopy(row["after"])}
            fields.append(field)
        candidate = entry["candidate"]
        if candidate["type"] == "person" and candidate["taxonomy"]["reality"] == "unknown" and not owner:
            if "taxonomy" not in fields:
                fields.append("taxonomy")
            overrides["taxonomy"] = {"category": "person", "reality": "real", "subtype": "individual"}
        result.append({"external_id": entry["external_id"], "action": "reuse" if owner else "create",
            **({"target_id": owner} if owner else {}), "fields": fields, "overrides": overrides,
            "relations": [row["id"] for row in entry["relations"]] if relations else []})
    return {preview["snapshot_id"]: result}
