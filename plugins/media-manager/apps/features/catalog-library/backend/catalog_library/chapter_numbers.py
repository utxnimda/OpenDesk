"""Pure chapter numbering shared by library views and local media matching.

Bangumi's ``ep`` is local to a work while ``sort`` may continue a previous
work. Keep both source values intact; never infer a matching number from a
row position or an assumed season offset.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re


NUMBERING_MODES = frozenset({"local", "continuous"})


def normalize_number(value) -> str:
    if type(value) not in {str, int, float}:
        return ""
    raw = str(value)
    if len(raw) > 18 or re.fullmatch(r"\d{1,6}(?:\.\d{1,4})?", raw) is None:
        return ""
    try:
        number = Decimal(raw)
        if not number.is_finite() or number < 0 or number > 100000:
            return ""
        return format(number.normalize(), "f")
    except (InvalidOperation, ValueError):
        return ""


def chapter_numbers(chapter: dict, index: int) -> dict:
    """Return display numbers and source-backed match numbers separately."""
    local = next((number for field in ("number", "ep", "sort")
                  if (number := normalize_number(chapter.get(field))) and (field != "ep" or number != "0")), "")
    kind = chapter.get("type", 0)
    continuous = normalize_number(chapter.get("sort")) if type(kind) is int and kind == 0 else ""
    if continuous == "0":
        continuous = ""
    return {"local_number": local or str(index + 1),
            "continuous_number": continuous,
            "has_continuous_number": bool(local and local != "0" and continuous and continuous != local),
            "local_match_number": local,
            "continuous_match_number": continuous}


def project_chapters(chapters: list[dict]) -> tuple[list[dict], dict]:
    """Enrich a read-only view without adding derived fields to source data."""
    result, pairs = [], []
    for index, chapter in enumerate(chapters):
        numbers = chapter_numbers(chapter, index)
        result.append({**deepcopy(chapter), **{key: numbers[key] for key in
                       ("local_number", "continuous_number", "has_continuous_number")}})
        if numbers["local_match_number"] not in {"", "0"} and numbers["continuous_match_number"]:
            pairs.append(numbers)
    has_continuous = any(pair["has_continuous_number"] for pair in pairs)
    first = pairs[0] if has_continuous else {}
    return result, {"has_continuous": has_continuous,
                    "local_start": first.get("local_number", ""),
                    "continuous_start": first.get("continuous_number", "")}
