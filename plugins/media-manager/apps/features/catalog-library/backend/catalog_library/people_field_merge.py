"""Provider-neutral, non-mutating decisions for whole person profile fields.

Localized profile paths are compared one language at a time by the caller.
Aliases are additive evidence and merge without confirmation. Other conflicting
non-empty profile fields remain explicit choices, never silent replacements.
"""
from copy import deepcopy

from people_catalog.localization import is_field, merge_aliases, field_value
from . import people_infobox
from people_catalog import profile_variants


def profile_field_merge(field, before, after, entity, candidate, source):
    """Combine ordinary additive fields with shared source display policy."""
    return profile_variants.decision(entity, candidate, field, source) or field_merge(field, before, after)


def adopted_chinese_mirror(entity, candidate, selected_fields):
    """Retain a selected Chinese display value without inventing its variant."""
    if entity.get("name_cn") or not isinstance(candidate, dict):
        return ""
    value = candidate.get("name_cn")
    if not isinstance(value, str) or not value.strip():
        return ""
    return value if any(is_field(field) and field.startswith("localized.name.")
                        and field_value(entity, field) == value for field in selected_fields) else ""


def is_alias_field(field):
    return field == "aliases" or is_field(field) and field.startswith("localized.aliases.") or people_infobox.is_alias_field(field)


def has_value(field, value):
    """Whether a value is actual profile evidence, not an empty/unknown marker."""
    if value is None:
        return False
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return False
    if field == "gender" and (value == 0 or isinstance(value, str) and value.casefold() in {"0", "unknown", "未知"}):
        return False
    if field == "taxonomy":
        return isinstance(value, dict) and value.get("category") not in {None, "", "unknown"}
    if isinstance(value, dict):
        return any(has_value("", item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(has_value("", item) for item in value)
    # Numeric zero can be real information inside an otherwise valid field;
    # only the gender-specific zero above denotes an unknown enum value.
    return True


def field_merge(field, before, after):
    """Describe the safe default and the precise value an adoption would write."""
    existing, incoming = has_value(field, before), has_value(field, after)
    if is_alias_field(field) and incoming:
        merged = (people_infobox.merge_alias_values(before, after) if people_infobox.is_alias_field(field)
                  else merge_aliases(before, after))
        action = "fill" if not existing else "same" if merged == before else "merge"
        return {"merge_action": action, "requires_confirmation": False,
                "default_selected": action in {"fill", "merge"}, "changed": action in {"fill", "merge"},
                "merged_value": merged}
    action = ("empty" if not existing and not incoming else "keep" if not incoming else
              "fill" if not existing else "same" if before == after else "conflict")
    return {"merge_action": action, "requires_confirmation": action == "conflict",
            "default_selected": action == "fill", "changed": action in {"fill", "conflict"},
            "merged_value": deepcopy(after if action in {"fill", "conflict"} else before)}
