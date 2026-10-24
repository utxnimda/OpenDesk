"""Source-specific profile evidence and user-controlled display preferences.

The caller owns source identity confirmation and persistence. All operations here
are in-memory; localized remains the compatible displayed profile projection.
"""
from copy import deepcopy
import json

from . import localization

KINDS = ("name", "summary")
PROVIDERS = ("wikipedia", "tmdb", "bangumi", "local")
MAX_BYTES = 768 * 1024


def _provider(key, row):
    return row.get("provider", key.split(":", 1)[0])


def _keys(versions, provider):
    return [key for key, row in versions.items() if _provider(key, row) == provider]


def _upsert(versions, provider, row, *, reject_conflict=False):
    match = next((key for key, old in versions.items() if _provider(key, old) == provider
                  and old["external_id"] == row["external_id"]), None)
    if match is not None:
        if reject_conflict and versions[match]["value"] != row["value"]:
            raise ValueError("人物同一来源身份的版本内容冲突；请先核对版本后再合并资料卡")
        versions[match] = ({**deepcopy(row), **versions[match]} if reject_conflict else deepcopy(row)) | {"provider": provider}
        return match
    key = provider if provider not in versions else provider + ":" + row["external_id"]
    versions[key] = {**deepcopy(row), "provider": provider}
    return key


def validate_variants(value):
    if not isinstance(value, dict) or set(value) - set(KINDS):
        raise ValueError("人物来源版本只能包含 name、summary")
    result = {}
    for kind, languages in value.items():
        if not isinstance(languages, dict) or set(languages) - set(localization.LANGUAGES):
            raise ValueError("人物来源版本语种无效")
        result[kind] = {}
        for lang, sources in languages.items():
            if not isinstance(sources, dict) or len(sources) > 100:
                raise ValueError("人物来源版本平台无效")
            result[kind][lang] = {}
            for key, row in sources.items():
                if not isinstance(key, str) or not isinstance(row, dict) or set(row) - {"value", "external_id", "source_url", "provider"}:
                    raise ValueError("人物来源版本必须包含资料值与明确来源身份")
                text = row.get("value")
                if (not isinstance(text, str) or not text.strip() or len(text) > (1000 if kind == "name" else 100000)
                        or "\x00" in text or not isinstance(row.get("external_id"), str)
                        or not row["external_id"] or len(row["external_id"]) > 200):
                    raise ValueError("人物来源版本内容或身份无效")
                if "source_url" in row and (not isinstance(row["source_url"], str) or len(row["source_url"]) > 4000):
                    raise ValueError("人物来源版本网址无效")
                provider = _provider(key, row)
                if provider not in PROVIDERS or key not in {provider, provider + ":" + row["external_id"]}:
                    raise ValueError("人物来源版本键与平台身份不一致")
                if any(_provider(old_key, old) == provider and old["external_id"] == row["external_id"]
                       for old_key, old in result[kind][lang].items()):
                    raise ValueError("人物同一来源身份的版本不能重复")
                result[kind][lang][key] = {**deepcopy(row), "provider": provider}
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
        raise ValueError("人物来源版本超过大小限制")
    return result


def validate_preferences(value, variants=None):
    if not isinstance(value, dict) or set(value) - set(KINDS):
        raise ValueError("人物显示偏好只能包含 name、summary")
    for kind, languages in value.items():
        if not isinstance(languages, dict) or set(languages) - set(localization.LANGUAGES):
            raise ValueError("人物显示偏好语种无效")
        for lang, preference in languages.items():
            if preference == {"locked": True} and type(preference.get("locked")) is bool:
                continue
            if (not isinstance(preference, dict) or set(preference) != {"source"}
                    or not isinstance(preference["source"], str) or preference["source"].split(":", 1)[0] not in PROVIDERS):
                raise ValueError("人物显示偏好必须为既有来源或手工锁定")
            if variants is not None and preference["source"] not in variants.get(kind, {}).get(lang, {}):
                raise ValueError("只能选择当前人物该语种已保存的来源版本")
    return deepcopy(value)


def validate_exports(entity):
    variants = validate_variants(entity.get("profile_variants", {}))
    validate_preferences(entity.get("profile_preferences", {}), variants)


def slot(entity, field):
    if localization.is_field(field):
        _, kind, lang = field.split(".")
        return (kind, lang) if kind in KINDS else None
    kind = "name" if field in {"name", "name_cn"} else "summary" if field == "summary" else ""
    if not kind:
        return None
    value = entity.get(field)
    if not isinstance(value, str) or not value.strip():
        return None
    matching = [lang for lang, text in localization.project(entity)[kind].items() if text == value]
    return (kind, matching[0]) if len(matching) == 1 else None


def _source(entity, kind, lang, fallback):
    evidence = entity.get("field_sources", {})
    row = evidence.get(f"localized.{kind}.{lang}", evidence.get(kind, {})) if isinstance(evidence, dict) else {}
    if isinstance(row, dict) and (row.get("provider") == "manual" or row.get("manually_edited") is True):
        return None
    row = row if isinstance(row, dict) and row.get("provider") in PROVIDERS else fallback
    if not isinstance(row, dict) or row.get("provider") not in PROVIDERS or not row.get("external_id"):
        return None
    result = {"provider": row["provider"], "external_id": str(row["external_id"])}
    if row.get("source_url"):
        result["source_url"] = row["source_url"]
    return result


def _manual(entity, kind, lang):
    preference = entity.get("profile_preferences", {}).get(kind, {}).get(lang)
    if preference is not None:
        return preference.get("locked") is True
    evidence = entity.get("field_sources", {})
    evidence = evidence if isinstance(evidence, dict) else {}
    row = evidence.get(f"localized.{kind}.{lang}", evidence.get(kind, {}))
    return bool(localization.field_value(entity, f"localized.{kind}.{lang}")) and isinstance(row, dict) and (
        row.get("provider") == "manual" or row.get("manually_edited") is True)


def effective_preferences(entity):
    """Read-only compatibility projection of legacy manual provenance."""
    result = deepcopy(entity.get("profile_preferences", {}))
    for kind, languages in localization.project(entity).items():
        if kind not in KINDS:
            continue
        for lang in languages:
            if _manual(entity, kind, lang):
                result.setdefault(kind, {})[lang] = {"locked": True}
    return result


def capture(entity, candidate, source, *, seed_current=True):
    """Save proven source values only after the caller confirms the identity."""
    variants = validate_variants(entity.get("profile_variants", {}))
    effective = effective_preferences(entity)
    if effective:
        entity["profile_preferences"] = effective
    current = localization.project(entity)
    for kind in KINDS:
        for lang, value in (current[kind].items() if seed_current else ()):
            if not value.strip() or variants.get(kind, {}).get(lang):
                continue
            origin = _source(entity, kind, lang, None) or {"provider": "local", "external_id": "local"}
            provider = origin.pop("provider")
            _upsert(variants.setdefault(kind, {}).setdefault(lang, {}), provider, {"value": value, **origin})
    # A normalized/enriched candidate may already retain multiple independent
    # source versions. They stay attached to their actual providers.
    inherited = validate_variants(candidate.get("profile_variants", {}))
    for kind, languages in inherited.items():
        for lang, versions in languages.items():
            for key, row in versions.items():
                _upsert(variants.setdefault(kind, {}).setdefault(lang, {}), _provider(key, row), row)
    proposed = localization.project(candidate)
    for kind in KINDS:
        unavailable = candidate.get("_unavailable_fields", [])
        if kind in unavailable and (kind != "name" or "name_cn" in unavailable):
            continue
        for lang, value in proposed[kind].items():
            if not value.strip():
                continue
            origin = _source(candidate, kind, lang, source)
            if not origin:
                continue
            provider = origin.pop("provider")
            _upsert(variants.setdefault(kind, {}).setdefault(lang, {}), provider, {"value": value, **origin})
    if variants:
        entity["profile_variants"] = validate_variants(variants)
    return entity


def _choice(entity, kind, lang):
    current = localization.field_value(entity, f"localized.{kind}.{lang}")
    if _manual(entity, kind, lang):
        return current
    versions = entity.get("profile_variants", {}).get(kind, {}).get(lang, {})
    preference = entity.get("profile_preferences", {}).get(kind, {}).get(lang, {})
    chosen = preference.get("source")
    if chosen:
        return versions.get(chosen, {}).get("value", current)
    if not _keys(versions, "wikipedia") and current:
        origin = _source(entity, kind, lang, None)
        matched = next((row for key, row in versions.items() if origin and _provider(key, row) == origin["provider"]
                        and row["external_id"] == origin["external_id"]), None)
        if matched:
            return matched["value"]
        return current
    return next((versions[key]["value"] for provider in PROVIDERS for key in _keys(versions, provider)), current)


def decision(entity, candidate, field, source):
    """Default source policy for Chinese names and multilingual biographies."""
    target = slot(candidate, field) or slot(entity, field)
    if target is None:
        return None
    kind, lang = target
    unavailable = candidate.get("_unavailable_fields", [])
    if kind in unavailable and (kind != "name" or "name_cn" in unavailable):
        return None
    if kind == "name" and lang not in {"zh-Hans", "zh-Hant"}:
        return None
    projected = deepcopy(entity)
    capture(projected, candidate, source)
    before = localization.field_value(entity, f"localized.{kind}.{lang}")
    proposed = localization.field_value(candidate, f"localized.{kind}.{lang}")
    if not proposed:
        return None
    locked = _manual(entity, kind, lang)
    merged = before if locked else _choice(projected, kind, lang)
    changed = before != merged
    # A non-displayed translated name is still additive alias evidence.
    additive = kind == "name" and proposed != before and not locked
    action = "keep" if locked else "fill" if not before and changed else "merge" if changed or additive else "same"
    return {"merge_action": action, "requires_confirmation": False, "default_selected": changed or additive,
            "changed": changed or additive, "merged_value": merged, "profile_variant": True,
            "profile_locked": locked, "reason": "手工固定的资料保持不变" if locked else "保留各来源版本，中文名称与简介默认优先维基百科"}


def _mirrors(entity):
    values = localization.compatibility(entity)
    entity["aliases"] = deepcopy(values["aliases"])


def collect_aliases(entity):
    localized = localization.project(entity)
    for lang, versions in entity.get("profile_variants", {}).get("name", {}).items():
        if lang not in {"zh-Hans", "zh-Hant"}:
            continue
        display = localized["name"].get(lang, "")
        alternatives = [row["value"] for row in versions.values() if row["value"] != display]
        if alternatives:
            path = "localized.aliases." + lang
            localization.set_field(entity, path, localization.merge_aliases(localized["aliases"].get(lang, []), alternatives))
    _mirrors(entity)


def refresh_projection_provenance(entity):
    """Displayed evidence follows the displayed version, not the last fetch."""
    localized = localization.project(entity)
    for kind in KINDS:
        for lang, versions in entity.get("profile_variants", {}).get(kind, {}).items():
            if _manual(entity, kind, lang):
                continue
            value = localized[kind].get(lang)
            matches = {key: row for key, row in versions.items() if row["value"] == value}
            if not matches:
                continue
            preferred = entity.get("profile_preferences", {}).get(kind, {}).get(lang, {}).get("source")
            origin = _source(entity, kind, lang, None)
            origin_key = next((key for key, row in matches.items() if origin and _provider(key, row) == origin["provider"]
                               and row["external_id"] == origin["external_id"]), None)
            selected = preferred if preferred in matches else origin_key or next(
                key for provider in PROVIDERS for key in _keys(matches, provider))
            entity.setdefault("field_sources", {})[f"localized.{kind}.{lang}"] = {
                "provider": _provider(selected, matches[selected]),
                **{key: deepcopy(item) for key, item in matches[selected].items() if key != "value"}}
    return entity


def mark_manual(entity, field, previous=None):
    target = slot(entity, field) or slot(previous or {}, field)
    if target:
        kind, lang = target
        value = localization.field_value(entity, f"localized.{kind}.{lang}")
        if value:
            entity.setdefault("profile_preferences", {}).setdefault(kind, {})[lang] = {"locked": True}
            entity.setdefault("profile_variants", {}).setdefault(kind, {}).setdefault(lang, {})["local"] = {
                "value": value, "external_id": "local"}
        else:
            for key in ("profile_preferences", "profile_variants"):
                entity.get(key, {}).get(kind, {}).pop(lang, None)


def apply_preferences(entity, preferences):
    variants = validate_variants(entity.get("profile_variants", {}))
    previous = effective_preferences(entity)
    preferences = validate_preferences(preferences, variants)
    entity["profile_preferences"] = preferences
    for kind in KINDS:
        for lang in localization.LANGUAGES:
            if previous.get(kind, {}).get(lang) == preferences.get(kind, {}).get(lang):
                continue
            if preferences.get(kind, {}).get(lang, {}).get("locked"):
                continue
            # An explicit source/auto choice intentionally unlocks earlier
            # manually edited values while preserving all their evidence.
            evidence = entity.get("field_sources", {})
            if isinstance(evidence, dict):
                evidence.pop(f"localized.{kind}.{lang}", None)
                evidence.pop(kind, None)
            value = _choice(entity, kind, lang)
            if value:
                localization.set_field(entity, f"localized.{kind}.{lang}", value)
    collect_aliases(entity)
    refresh_projection_provenance(entity)
    return entity


def merge_exports(target, donor):
    """Merge explicit same-entity cards without dropping a source version."""
    variants = validate_variants(target.get("profile_variants", {}))
    incoming = validate_variants(donor.get("profile_variants", {}))
    for kind, languages in incoming.items():
        for lang, sources in languages.items():
            current = variants.setdefault(kind, {}).setdefault(lang, {})
            for key, row in sources.items():
                if (_provider(key, row) == "local" and row["external_id"] == "local" and donor.get("id")
                        and donor.get("id") != target.get("id")):
                    # "local" identifies one card's historic manual evidence,
                    # not a shared external person identity across all cards.
                    row = {**row, "external_id": str(donor["id"])}
                _upsert(current, _provider(key, row), row, reject_conflict=True)
    if variants:
        target["profile_variants"] = validate_variants(variants)
    validate_exports(target)
    return target
