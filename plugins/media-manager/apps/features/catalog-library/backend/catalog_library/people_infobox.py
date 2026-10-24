"""Key-level source supplementation without changing the stored infobox schema.

Field paths encode keys, never list positions: adding another source's key does
not shift a pending choice. Manual whole-infobox edits deliberately bypass this
adapter. Unknown legacy rows remain visible as one separate JSON-valued field.
"""
import base64
from copy import deepcopy
import json
import unicodedata

PREFIX = "infobox."
LEGACY_FIELD = PREFIX + "~legacy"
ROWS_PREFIX = PREFIX + "~rows."


def _key(row):
    value = row.get("key") if isinstance(row, dict) else None
    return unicodedata.normalize("NFC", value.strip()) if isinstance(value, str) else ""


def field_path(key, *, complete_rows=False):
    key = unicodedata.normalize("NFC", key.strip()) if isinstance(key, str) else ""
    if not key or "\x00" in key:
        raise ValueError("详细资料字段名无效")
    prefix = ROWS_PREFIX if complete_rows else PREFIX
    return prefix + base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii").rstrip("=")


def field_key(path):
    if not isinstance(path, str) or not path.startswith(PREFIX) or path == LEGACY_FIELD:
        raise ValueError("详细资料字段路径无效")
    complete_rows = path.startswith(ROWS_PREFIX)
    encoded = path[len(ROWS_PREFIX if complete_rows else PREFIX):]
    try:
        key = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True).decode("utf-8")
    except (ValueError, UnicodeError):
        raise ValueError("详细资料字段路径无效") from None
    if field_path(key, complete_rows=complete_rows) != path:
        raise ValueError("详细资料字段路径不是规范编码")
    return key


def is_field(path):
    if path == LEGACY_FIELD:
        return True
    try:
        field_key(path)
        return True
    except (ValueError, TypeError):
        return False


def field_paths(*entities):
    keys, grouped = [], {}
    for entity in entities:
        for row in (entity or {}).get("infobox", []):
            key = _key(row)
            if key not in keys:
                keys.append(key)
            grouped.setdefault(key, []).append(row)
    result = []
    for key in keys:
        if not key:
            result.append(LEGACY_FIELD)
            continue
        metadata = [{name: value for name, value in row.items() if name not in {"key", "value"}}
                    for row in grouped[key]]
        variants = {json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) for value in metadata}
        # A key is not a sufficient identity when language, source or other
        # metadata differs. Compare complete records rather than flattening
        # them and silently attaching a new value to the first row's metadata.
        complete_rows = any(metadata) and len(variants) > 1
        result.append(field_path(key, complete_rows=complete_rows))
    return tuple(result)


def field_value(entity, path):
    rows = (entity or {}).get("infobox", [])
    if path == LEGACY_FIELD:
        return deepcopy([row for row in rows if not _key(row)])
    key = field_key(path)
    if path.startswith(ROWS_PREFIX):
        return deepcopy([row for row in rows if _key(row) == key])
    values = []
    for row in rows:
        if _key(row) == key and row.get("value") not in values:
            values.append(deepcopy(row.get("value")))
    return values[0] if len(values) == 1 else values if values else None


def set_field(entity, path, value):
    rows = deepcopy(entity.get("infobox", []))
    if path == LEGACY_FIELD:
        if not isinstance(value, list) or any(not isinstance(row, dict) or _key(row) for row in value):
            raise ValueError("未命名详细资料必须为不含有效 key 的对象数组")
        entity["infobox"] = [row for row in rows if _key(row)] + deepcopy(value)
        return
    key = field_key(path)
    matches = [index for index, row in enumerate(rows) if _key(row) == key]
    if path.startswith(ROWS_PREFIX):
        if not isinstance(value, list) or any(not isinstance(row, dict) or _key(row) != key for row in value):
            raise ValueError("完整详细资料必须为当前 key 的原始条目数组")
        first = matches[0] if matches else len(rows)
        entity["infobox"] = rows[:first] + deepcopy(value) + [row for row in rows[first:] if _key(row) != key]
        return
    if field_path(key, complete_rows=True) in field_paths(entity):
        raise ValueError("详细资料含不同扩展资料；请重新预览并确认完整条目，不能按单个值覆盖")
    if matches:
        rows[matches[0]]["value"] = deepcopy(value)
        rows = [row for index, row in enumerate(rows) if index not in matches[1:]]
    else:
        rows.append({"key": key, "value": deepcopy(value)})
    entity["infobox"] = rows


def label(path):
    result = "详细资料 · " + ("未命名资料" if path == LEGACY_FIELD else field_key(path))
    return result + "（完整条目，含扩展资料）" if path.startswith(ROWS_PREFIX) else result


def is_alias_field(path):
    return is_field(path) and path != LEGACY_FIELD and not path.startswith(ROWS_PREFIX) and field_key(path).casefold() in {
        "别名", "別名", "又名", "alias", "aliases", "also known as"}


def _has_alias_content(value):
    """Ignore empty placeholders without flattening structured alias evidence."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_alias_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_alias_content(item) for item in value)
    return True


def merge_alias_values(before, after):
    """Retain structured alias evidence as well as plain Bangumi alias strings."""
    result, seen = [], set()
    for value in (before, after):
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not _has_alias_content(item):
                continue
            canonical = unicodedata.normalize("NFC", item.strip()) if isinstance(item, str) else item
            key = json.dumps(canonical, ensure_ascii=False, sort_keys=True, allow_nan=False)
            if key not in seen:
                seen.add(key)
                result.append(deepcopy(item))
    original = before if isinstance(before, list) else [before]
    return deepcopy(before) if result == original else result


def edit_type(before, after):
    populated = [value for value in (before, after) if value is not None]
    if populated and all(isinstance(value, str) for value in populated):
        return "textarea" if any("\n" in value for value in populated) else "text"
    return "json"


def source_changes(changes, target=None):
    """Split only source changes; the caller retains original edit semantics."""
    result = {}
    for field, value in changes.items():
        if field != "infobox":
            result[field] = deepcopy(value)
            continue
        entity = {"infobox": value}
        keys = {_key(row) for row in value}
        result.update({path: field_value(entity, path) for path in field_paths(entity, target)
                       if ("" if path == LEGACY_FIELD else field_key(path)) in keys})
    return result
