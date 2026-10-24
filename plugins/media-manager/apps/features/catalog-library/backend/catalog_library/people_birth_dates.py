"""Pure, conservative normalization of Bangumi's two birthday representations.

``normalize_birth_date(raw)`` returns ``birth_date``, ``available``, ``replace``,
``clear_fields`` and diagnostic ``issues``. The date uses the existing local
``year / mon / day`` keys. Missing components remain unknown; no Jan 1 default
is ever invented. Issues carry bounded raw evidence for the operation log.

``merge_birth_date(previous, result)`` preserves previously imported components
when a source component is unavailable. Explicit contradictions/calendar errors
clear only the affected fields. ``replace`` is reserved for a recognizable
upstream year-only corruption, where preserving the old "day" is unsafe.
Callers must separately protect manually edited dates and require confirmation
before persisting a repair. Neither function performs I/O or mutates its input.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import re
import unicodedata

_FIELDS = {"year": ("birth_year", 9999), "mon": ("birth_mon", 12), "day": ("birth_day", 31)}
_BIRTH_KEYS = {"生日", "出生日期", "生年月日", "誕生日", "birthdate", "birthday", "dateofbirth"}
_PATTERNS = (
    re.compile(r"(?P<year>[0-9]{4})年(?P<mon>[0-9]{1,2})月(?P<day>[0-9]{1,2})[日号]?"),
    re.compile(r"(?P<year>[0-9]{4})(?P<sep>[-/.])(?P<mon>[0-9]{1,2})(?P=sep)(?P<day>[0-9]{1,2})"),
    re.compile(r"(?P<year>[0-9]{4})年(?P<mon>[0-9]{1,2})月"),
    re.compile(r"(?P<year>[0-9]{4})-(?P<mon>[0-9]{1,2})"),
    re.compile(r"(?:(?:\?\?\?\?|XXXX|xxxx)-|--)(?P<mon>[0-9]{2})-(?P<day>[0-9]{2})"),
    re.compile(r"(?P<mon>[0-9]{1,2})月(?P<day>[0-9]{1,2})[日号]?"),
    re.compile(r"(?P<year>[0-9]{4})年?"),
)


def _evidence(value):
    """Never copy arbitrary nested source graphs into diagnostics."""
    if value is None or type(value) in (int, bool):
        return value
    if isinstance(value, str):
        return value[:300]
    return f"<{type(value).__name__}>"


def _issue(field, reason, raw, *, recovered=False):
    return {"field": field, "reason": reason, "raw": _evidence(raw),
            "recovered": recovered, "severity": "info" if recovered else "warning"}


def _calendar_error(parts):
    if "mon" not in parts or "day" not in parts:
        return False
    try:
        # An unknown year may be a leap year; a known non-leap year may not.
        date(parts.get("year", 2000), parts["mon"], parts["day"])
    except ValueError:
        return True
    return False


def _parse_text(value):
    if not isinstance(value, str) or len(value) > 300:
        return None, set()
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
    for pattern in _PATTERNS:
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        parts = {key: int(raw) for key, raw in match.groupdict().items() if key in _FIELDS}
        invalid = {key for key, number in parts.items() if not 1 <= number <= _FIELDS[key][1]}
        valid = {key: number for key, number in parts.items() if key not in invalid}
        if _calendar_error(valid):
            invalid.add("day")
            valid.pop("day", None)
        return valid, invalid
    return None, set()


def _infobox_dates(infobox):
    """Read the API's scalar and list-of-{k,v} forms without guessing prose."""
    values = []
    if not isinstance(infobox, list):
        return values
    for row in infobox[:200]:
        if not isinstance(row, Mapping) or not isinstance(row.get("key"), str):
            continue
        key = re.sub(r"\s+", "", unicodedata.normalize("NFKC", row["key"])).lower()
        if key not in _BIRTH_KEYS:
            continue
        entries = row.get("value")
        entries = entries[:200] if isinstance(entries, list) else [entries]
        for entry in entries:
            value = entry.get("v") if isinstance(entry, Mapping) and "v" in entry else entry
            if value is None or value == "":
                continue
            if not any(type(value) is type(old) and value == old for old in values):
                values.append(value)
        if len(values) >= 200:
            return values[:200]
    return values


def normalize_birth_date(raw, *, infobox=None):
    """Normalize a single raw person/character detail; see module contract.

    Supplying ``infobox`` lets the importer reuse its separately validated box.
    If omitted (or ``None``), the raw object's infobox is used. Ordinary invalid
    scalar fields remain unavailable so refreshes can preserve older good data.
    Reliable conflicting source fields and impossible calendar days are explicit
    clears. Unrecognized prose is retained only as a warning, never parsed by
    extracting arbitrary digit runs.
    """
    if not isinstance(raw, Mapping):
        raise TypeError("人物生日来源必须为对象")
    parts, invalid, issues = {}, {}, []
    for key, (field, maximum) in _FIELDS.items():
        value = raw.get(field)
        if value is None or type(value) is int and value == 0:
            continue
        if type(value) is int and 1 <= value <= maximum:
            parts[key] = value
        else:
            invalid[key] = value

    boxes = _infobox_dates(raw.get("infobox") if infobox is None else infobox)
    candidates, box_invalid = [], set()
    for value in boxes:
        parsed, failures = _parse_text(value)
        if parsed is None:
            issues.append(_issue("infobox.生日", "生日原文格式无法可靠识别；保留原文，不推断年月日", value))
            continue
        if failures:
            box_invalid.update(failures)
            issues.append(_issue("infobox.生日", "生日原文包含无效的 " + "/".join(sorted(failures)) + "；相应分量置空", value))
        candidates.append(parsed)

    box_values = {key: {row[key] for row in candidates if key in row} for key in _FIELDS}
    conflicts = {key for key, values in box_values.items() if len(values) > 1}
    box_parts = {key: next(iter(values)) for key, values in box_values.items() if len(values) == 1}
    clear_fields = set(conflicts)
    for key in sorted(conflicts):
        issues.append(_issue("infobox.生日", f"多条生日原文中的 {key} 不一致；该分量置空",
                             " / ".join(str(_evidence(value)) for value in boxes)))

    # Actual upstream defect: 1961年 -> year=null, mon=19, day=1 (or
    # 1980年 -> mon=19, day=null). A simple range filter would retain that
    # erroneous 1 as a valid day. Replace the entire source-derived date here.
    year = box_parts.get("year")
    year_only = bool(candidates) and all(set(row) == {"year"} for row in candidates)
    poison = (year_only and not conflicts and not box_invalid and year is not None
              and type(raw.get("birth_mon")) is int and raw["birth_mon"] > 12
              and raw["birth_mon"] == int(f"{year:04d}"[:2])
              and (raw.get("birth_year") is None or type(raw.get("birth_year")) is int
                   and raw["birth_year"] in (0, year))
              and (raw.get("birth_day") is None or type(raw.get("birth_day")) is int
                   and raw["birth_day"] in (0, year % 10)))
    if poison:
        evidence = f"生日原文={boxes!r}; birth_year={raw.get('birth_year')!r}; birth_mon={raw.get('birth_mon')!r}; birth_day={raw.get('birth_day')!r}"
        issues.append(_issue("birth_date", f"来源将仅年份生日误拆为月份/日期，已按原文恢复为 {year} 年；不保留误拆日期", evidence, recovered=True))
        return {"birth_date": {"year": year}, "replace": True, "clear_fields": ["mon", "day"],
                "available": True, "issues": issues}

    for key, number in box_parts.items():
        if key in parts and parts[key] != number:
            clear_fields.add(key)
            issues.append(_issue(_FIELDS[key][0], f"结构化生日值 {parts[key]} 与生日原文值 {number} 不一致；该分量置空",
                                 " / ".join(str(_evidence(value)) for value in boxes)))
        elif key not in clear_fields:
            parts[key] = number
    for key, value in invalid.items():
        recovered = key in box_parts and key not in clear_fields
        reason = (f"结构化字段值 {_evidence(value)!r} 无效，已由生日原文恢复为 {box_parts[key]}"
                  if recovered else f"字段值 {_evidence(value)!r} 无效，应为 1～{_FIELDS[key][1]} 的整数；本次不提供该分量")
        issues.append(_issue(_FIELDS[key][0], reason, value, recovered=recovered))
    # A malformed textual component cannot make an otherwise valid structured
    # component invalid. If there is no valid structured fallback, mark the
    # explicit source error so an old impossible component cannot reappear.
    clear_fields.update(key for key in box_invalid if key not in parts)
    for key in clear_fields:
        parts.pop(key, None)
    if _calendar_error(parts):
        issues.append(_issue("birth_day", "生日组合不是有效的公历日期；日期分量置空",
                             f"year={parts.get('year')}, mon={parts['mon']}, day={parts['day']}"))
        parts.pop("day")
        clear_fields.add("day")
    return {"birth_date": parts, "replace": False, "clear_fields": sorted(clear_fields),
            "available": bool(parts or clear_fields), "issues": issues}


def merge_birth_date(previous, result):
    """Apply availability/clearing semantics to a previously imported date.

    This function deliberately does not decide whether a local value is manual.
    A final calendar guard handles dates which become impossible only after
    filling missing source components from the previous import.
    """
    merged = {} if result.get("replace") else {
        key: value for key, value in (previous.items() if isinstance(previous, Mapping) else ())
        if key in _FIELDS and type(value) is int and 1 <= value <= _FIELDS[key][1]
    }
    for key in result.get("clear_fields", []):
        merged.pop(key, None)
    merged.update(result["birth_date"])
    if _calendar_error(merged):
        merged.pop("day", None)
    return merged
