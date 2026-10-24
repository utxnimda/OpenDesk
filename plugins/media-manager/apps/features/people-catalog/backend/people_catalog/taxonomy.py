"""Provider-neutral entity classification, independent of legacy source types.

``type`` and ``kind`` remain source identity/compatibility fields. Reading an
old record never writes a migration: explicit local taxonomy wins, otherwise
only documented legacy kinds are mapped. An unclassified ``person`` is not
assumed to be a human, since provider person endpoints also include companies.
"""
from copy import deepcopy

CATEGORIES = {"person": "人物", "organization": "组织", "object": "其他实体", "unknown": "待分类"}
REALITIES = {"real": "现实", "fictional": "虚构", "unknown": "待确认"}
SUBTYPES = {
    "person": {"individual": "个人", "character": "角色", "unknown": "待确认"},
    "organization": {"corporation": "公司", "studio": "工作室", "association": "团体／协会", "organization": "组织", "unknown": "待确认"},
    "object": {"mechanic": "机体", "ship": "舰船", "object": "其他实体", "unknown": "待确认"},
    "unknown": {"unknown": "待确认"},
}
_LEGACY = {
    ("person", "individual"): ("person", "real", "individual"),
    ("person", "corporation"): ("organization", "real", "corporation"),
    ("person", "association"): ("organization", "real", "association"),
    ("character", "character"): ("person", "fictional", "character"),
    ("character", "organization"): ("organization", "fictional", "organization"),
    ("character", "mechanic"): ("object", "fictional", "mechanic"),
    ("character", "ship"): ("object", "fictional", "ship"),
}


def validate(value):
    if not isinstance(value, dict) or set(value) != {"category", "reality", "subtype"}:
        raise ValueError("实体分类必须包含 category、reality、subtype 三个枚举字段")
    category, reality, subtype = (value[key] for key in ("category", "reality", "subtype"))
    if (not isinstance(category, str) or category not in CATEGORIES or not isinstance(reality, str)
            or reality not in REALITIES or not isinstance(subtype, str) or subtype not in SUBTYPES[category]):
        raise ValueError("实体分类、现实性或细分類别组合无效")
    return deepcopy(value)


def project(entity):
    """Return an independent, validated classification without changing entity."""
    if entity is None:
        return {"category": "unknown", "reality": "unknown", "subtype": "unknown"}
    if "taxonomy" in entity:
        return validate(entity["taxonomy"])
    values = _LEGACY.get((entity.get("type"), entity.get("kind")), ("unknown", "unknown", "unknown"))
    return dict(zip(("category", "reality", "subtype"), values))


def label(entity):
    value = project(entity)
    category, reality, subtype = (value[key] for key in ("category", "reality", "subtype"))
    if category == "unknown":
        return "待分类"
    if category == "person":
        return {"real": "现实人物", "fictional": "虚拟角色", "unknown": "人物 · 现实性待确认"}[reality]
    return " · ".join([CATEGORIES[category], SUBTYPES[category][subtype], REALITIES[reality]])


def is_real_person(entity):
    value = project(entity)
    return value["category"] == "person" and value["reality"] == "real"


def is_fictional_person(entity):
    value = project(entity)
    return value["category"] == "person" and value["reality"] == "fictional"


def options():
    def rows(values):
        return [{"value": value, "label": label} for value, label in values.items()]
    return {"categories": rows(CATEGORIES), "realities": rows(REALITIES),
            "subtypes": {category: rows(values) for category, values in SUBTYPES.items()}}
