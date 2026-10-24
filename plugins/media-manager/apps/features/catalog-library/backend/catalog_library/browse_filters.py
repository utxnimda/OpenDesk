"""Pure, local catalog facets and predicates shared by the library browse view.

Resource filters deliberately match one release row. A work with a VCB BDRip
and another group's WEBRip must not satisfy VCB + WEBRip together.
"""
from __future__ import annotations

from collections import Counter
import unicodedata

from work_catalog_yaml.media_groups import normalize_press_group

FILTER_FIELDS = ("country", "release_type", "press_group", "press_format")
FACET_FIELDS = {"country": "countries", "release_type": "release_types",
                "press_group": "press_groups", "press_format": "press_formats"}
EMPTY_VALUE = "__none__"
_LABELS = {"country": "国家", "release_type": "媒体类型", "press_group": "压制组", "press_format": "压制格式"}


def _value(field, raw):
    if not isinstance(raw, str):
        return ""
    value = unicodedata.normalize("NFKC", raw).strip()
    return normalize_press_group(value) if field == "press_group" else value


def _key(field, value):
    return _value(field, value).casefold()


def parse_filters(body):
    """Distinguish all (empty input) from an explicitly empty release field."""
    result = {}
    for field in FILTER_FIELDS:
        value = body.get(field)
        if value is None or value == "":
            continue
        if not isinstance(value, str) or len(value) > 256:
            raise ValueError(f"{_LABELS[field]}筛选必须为最多 256 字符的枚举值")
        if not value.strip():
            continue
        result[field] = "" if value == EMPTY_VALUE else _key(field, value)
    return result


def matches_filters(item, selected):
    for field in ("country", "release_type"):
        if field in selected and _key(field, item.get(field)) != selected[field]:
            return False
    resource_fields = [field for field in ("press_group", "press_format") if field in selected]
    if not resource_fields:
        return True
    return any(all(_key(field, row.get(field)) == selected[field] for field in resource_fields)
               for row in item.get("press", []) if isinstance(row, dict))


def filter_facets(records, settings):
    """Use canonical enums, retaining searchable legacy values without DB edits.

    Counts are unique works, not release rows. This function never resolves or
    scans media paths and never mutates the cached authoritative records.
    """
    counts = {field: Counter() for field in FILTER_FIELDS}
    observed = {field: {} for field in FILTER_FIELDS}
    for item in records:
        for field in FILTER_FIELDS:
            values = ([item.get(field)] if field in {"country", "release_type"} else
                      [row.get(field) for row in item.get("press", []) if isinstance(row, dict)])
            keys = set()
            for raw in values:
                value = _value(field, raw)
                key = value.casefold()
                observed[field].setdefault(key, value)
                keys.add(key)
            counts[field].update(keys)
    facets = {}
    for field in FILTER_FIELDS:
        labels = {_key(field, value): label for value, label in settings.enum_labels.get(field, {}).items()}
        canonical = {}
        for raw in settings.enum_options.get(field, ()):
            value = _value(field, raw)
            if value:
                canonical.setdefault(value.casefold(), value)
        for key, value in sorted(observed[field].items()):
            if key:
                canonical.setdefault(key, value)
        rows = [{"value": value, "label": labels.get(key) or value, "count": counts[field][key]}
                for key, value in canonical.items()]
        if counts[field][""]:
            rows.append({"value": EMPTY_VALUE, "label": "----（未填写）", "count": counts[field][""]})
        facets[FACET_FIELDS[field]] = rows
    return facets
