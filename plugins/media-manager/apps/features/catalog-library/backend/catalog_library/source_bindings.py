"""Pure source-binding projections and local default policy selection.

Legacy bindings acquire deterministic identities only in returned projections.
Reading a work never migrates its stored record or queries an external source.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any


ROLES = frozenset({"primary", "supplement"})
SUPPLEMENT_PROVIDERS = frozenset({"wikipedia", "douban"})


def identity(ref: dict[str, Any]) -> tuple[str, str, str]:
    return (str(ref.get("provider", "")), str(ref.get("external_id", "")),
            str(ref.get("scope", "")))


def binding_id(ref: dict[str, Any]) -> str:
    if "binding_id" in ref:
        return ref["binding_id"]
    encoded = json.dumps(identity(ref), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "sb_" + hashlib.sha256(encoded).hexdigest()[:24]


def normalized_refs(record: dict[str, Any]) -> list[dict[str, Any]]:
    refs = record.get("source_refs", [])
    if not isinstance(refs, list):
        return []
    result = []
    for raw in refs:
        if not isinstance(raw, dict):
            continue
        ref = deepcopy(raw)
        ref.setdefault("binding_id", binding_id(ref))
        ref.setdefault("enabled", True)
        ref.setdefault("role", "supplement" if ref.get("provider") in SUPPLEMENT_PROVIDERS else "primary")
        result.append(ref)
    return result


def _targets(refs: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Collapse refresh choices without changing or validating stored bindings."""
    result = {}
    for ref in refs:
        provider = ref.get("provider")
        if not isinstance(provider, str) or not provider:
            continue
        role = ref.get("role", "supplement" if provider in SUPPLEMENT_PROVIDERS else "primary")
        if provider == "douban":
            role = "supplement"
        role = role if isinstance(role, str) and role in ROLES else "primary"
        if provider not in result or role == "primary":
            result[provider] = {"provider": provider, "role": role}
    return list(result.values())


def source_policy(record: dict[str, Any]) -> dict[str, Any]:
    refs = normalized_refs(record)
    config = record.get("source_policy_config")
    if isinstance(config, dict) and isinstance(config.get("targets"), list):
        targets = _targets([item for item in config["targets"] if isinstance(item, dict)])
        custom = config.get("custom") is not False
        reason = "custom" if custom else "saved_default"
        # Explicit bindings remain authoritative even for older/partial configs.
        known = {item["provider"] for item in targets}
        targets.extend(item for item in _targets(refs) if item["provider"] not in known)
    elif refs:
        targets, custom, reason = _targets(refs), True, "existing_bindings"
    else:
        attributes = record.get("attributes", [])
        values = {item.get("type"): item.get("data") for item in attributes
                  if isinstance(item, dict) and isinstance(item.get("type"), str)} if isinstance(attributes, list) else {}
        collection = values.get("collection-type", {})
        domain = collection.get("domain", "") if isinstance(collection, dict) else ""
        country = values.get("country", "")
        custom = False
        if domain == "animation":
            targets, reason = [{"provider": "bangumi", "role": "primary"}], "animation"
        elif domain == "television" and country == "korea":
            targets = [{"provider": "tmdb", "role": "primary"},
                       {"provider": "wikipedia", "role": "supplement"}]
            reason = "korean_television"
        else:
            targets, reason = [{"provider": "bangumi", "role": "primary"}], "default"
    bound = {ref.get("provider") for ref in refs if isinstance(ref.get("provider"), str)}
    return {"primary_provider": next((item["provider"] for item in targets if item["role"] == "primary"), None),
            "supplement_providers": [item["provider"] for item in targets if item["role"] == "supplement"],
            "reason": reason, "custom": custom,
            # Disabled bindings are intentional, not missing provider choices.
            "missing_providers": [item["provider"] for item in targets if item["provider"] not in bound],
            "targets": deepcopy(targets)}
