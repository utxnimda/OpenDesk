"""Explicit portrayal stages; age labels alone never establish role identity."""
from copy import deepcopy
import re

STAGES = {"child", "teen", "young", "adult", "elder"}
_LABELS = {
    "child": "child", "childhood": "child", "童年": "child", "幼年": "child", "儿童": "child", "兒童": "child", "아역": "child", "어린 시절": "child", "어린": "child",
    "teen": "teen", "teenage": "teen", "teenager": "teen", "少年": "teen", "청소년": "teen",
    "young": "young", "younger": "young", "青年": "young", "젊은": "young",
    "adult": "adult", "成年": "adult", "성인": "adult",
    "elder": "elder", "elderly": "elder", "old": "elder", "老年": "elder", "노년": "elder",
}
_SUFFIX = re.compile(r"[（(]\s*(" + "|".join(re.escape(key) for key in sorted(_LABELS, key=len, reverse=True)) + r")\s*[)）]\s*$", re.I)


def validate_qualifier(value):
    if (not isinstance(value, dict) or set(value) - {"kind", "value", "label", "language", "explicit"}
            or value.get("kind") != "age" or not isinstance(value.get("value"), str)
            or value.get("value") not in STAGES or value.get("explicit") is not True):
        raise ValueError("饰演阶段必须为明确来源的年龄限定信息")
    for key, maximum in (("label", 80), ("language", 20)):
        text = value.get(key, "")
        if not isinstance(text, str) or len(text) > maximum or any(ord(char) < 32 for char in text):
            raise ValueError("饰演阶段标签或语言无效")
    return deepcopy(value)


def validate_role_group(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value):
        raise ValueError("同角色来源分组证据无效")
    return value


def parse_qualifier(value):
    match = _SUFFIX.search(value) if isinstance(value, str) else None
    if not match:
        return None
    label = match[1]
    language = "ko" if re.search(r"[\uac00-\ud7af]", label) else "zh" if re.search(r"[\u3400-\u9fff]", label) else "en"
    return {"kind": "age", "value": _LABELS[label.casefold()], "label": label, "language": language, "explicit": True}


def qualifier_for(value):
    if value.get("qualifier") is not None:
        return validate_qualifier(value["qualifier"])
    return parse_qualifier(value.get("original_name") or value.get("source_values", {}).get("character_name") or value.get("character_name", ""))


def stage_for(value):
    return (qualifier_for(value) or {}).get("value", "")


def compatible_stage(left, right):
    first, second = stage_for(left), stage_for(right)
    return first == second or {first, second} in ({"young", "child"}, {"young", "teen"})


def conflicting_stage(left, right):
    """Only two explicitly incompatible stages are a relationship conflict."""
    return bool(stage_for(left) and stage_for(right) and not compatible_stage(left, right))


def base_name(value, qualifier):
    """Display without a redundant age suffix, never a grouping/matching key."""
    if not qualifier or not isinstance(value, str):
        return value
    found = parse_qualifier(value)
    return _SUFFIX.sub("", value).strip() if found and compatible_stage({"qualifier": found}, {"qualifier": qualifier}) else value
