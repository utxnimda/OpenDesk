"""Recompute observed work expressions using their original evaluation order."""
from __future__ import annotations

import math
from typing import Any

# Each index belongs to a complete audited rule profile, checked again by the
# capture reader. Expressions 5621..5623 share arithmetic, not work categories.
WALL_EXPRESSIONS = frozenset((5615, 5620))
NORMAL_EXPRESSIONS = frozenset((5616, 5617, 5618, 5621, 5622, 5623))
EXPRESSIONS = WALL_EXPRESSIONS | NORMAL_EXPRESSIONS
NORMAL_TERMS = (
    ("collectionRate", "采集率"), ("panelWork", "面板工作量"), ("critical", "本次暴击系数"),
    ("majorBonus", "大类增产"), ("minorBonus", "小类增产"), ("qiBonus", "灵气加成"),
    ("profession", "职业类型倍率"), ("production", "生产类型倍率"),
    ("technology", "科技加成倍率"), ("final", "最终增产倍率"),
)
WALL_TERMS = (
    *NORMAL_TERMS[:5], ("final", "最终增产（加算）"), ("qiBonus", "灵气加成"),
    ("profession", "职业类型倍率"), ("production", "生产类型倍率"),
    ("technology", "科技加成倍率"), ("repair", "城墙修复加成"),
)


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and -1e100 <= value <= 1e100 and math.isfinite(value)


def calculate(value: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    inputs = value["inputs"]
    base = inputs[0] * inputs[1] * inputs[2]
    additive = 1.0 + inputs[3] + inputs[4]
    if value["expression"] in WALL_EXPRESSIONS:
        additive += inputs[5]
        product = base * additive * (1.0 + inputs[6]) * inputs[7] * inputs[8] * inputs[9]
    else:
        product = base * additive * (1.0 + inputs[5]) * inputs[6] * inputs[7] * inputs[8] * inputs[9]
    if not math.isfinite(product) or abs(product) > 1e100:
        raise ValueError("工作公式计算超出支持范围。")
    # JavaScript's Math.ceil/floor return IEEE-754 numbers, not arbitrary-
    # precision integers. Preserve that before the following addition.
    rounded = float(math.ceil(product))
    flat = float(math.floor(value["flatBonus"]))
    steps = [{"label": "基础乘积", "value": base}, {"label": "增产乘算结果", "value": product},
             {"label": "向上取整", "value": rounded}, {"label": "固定增加量向下取整", "value": flat}]
    if value["expression"] in WALL_EXPRESSIONS:
        repaired = rounded * ((100.0 + inputs[10]) / 100.0)
        value_before_rounding = repaired + flat
        if not math.isfinite(value_before_rounding) or abs(value_before_rounding) > 1e100:
            raise ValueError("工作公式计算超出支持范围。")
        calculated = float(math.ceil(value_before_rounding))
        steps.append({"label": "修墙倍率调整后", "value": repaired})
    else:
        calculated = rounded + flat
    if not 0 <= calculated <= 1e100:
        raise ValueError("工作公式结果超出支持范围。")
    steps.append({"label": "本次实际工作输出", "value": calculated})
    return calculated, steps


def validate_formula(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"expression", "inputs", "flatBonus", "result"}:
        raise ValueError("工作公式快照字段无效。")
    if type(value["expression"]) is not int or value["expression"] not in EXPRESSIONS:
        raise ValueError("工作公式版本未经核验。")
    count = 11 if value["expression"] in WALL_EXPRESSIONS else 10
    if not isinstance(value["inputs"], list) or len(value["inputs"]) != count or not all(_finite(v) for v in value["inputs"]):
        raise ValueError(f"工作公式需要 {count} 个有限数值输入。")
    if not _finite(value["flatBonus"]) or not _finite(value["result"]) or value["result"] < 0:
        raise ValueError("工作公式固定增加量或结果无效。")
    normalized = {"expression": value["expression"], "inputs": [float(v) for v in value["inputs"]],
                  "flatBonus": float(value["flatBonus"]), "result": float(value["result"])}
    calculate(normalized)
    return normalized


def explain_formula(value: dict[str, Any], amount: float) -> dict[str, Any]:
    formula = validate_formula(value)
    wall = formula["expression"] in WALL_EXPRESSIONS
    calculated, steps = calculate(formula)
    definitions = WALL_TERMS if wall else NORMAL_TERMS
    text = ("向上取整[向上取整[采集率 × 面板工作量 × 本次暴击系数 × "
            "(1 + 大类增产 + 小类增产 + 最终增产) × (1 + 灵气加成) × 职业类型倍率 × 生产类型倍率 × 科技加成倍率] "
            "× ((100 + 城墙修复加成) ÷ 100) + 向下取整[固定增加量]]") if wall else (
            "向上取整[采集率 × 面板工作量 × 本次暴击系数 × (1 + 大类增产 + 小类增产) × "
            "(1 + 灵气加成) × 职业类型倍率 × 生产类型倍率 × 科技加成倍率 × 最终增产倍率] + 向下取整[固定增加量]")
    return {**formula, "calculated": calculated, "matches": calculated == formula["result"] == amount,
            "text": text, "terms": [{"id": key, "label": label, "value": formula["inputs"][index], "mode": "input"}
                                     for index, (key, label) in enumerate(definitions)] +
                                    [{"id": "flatBonus", "label": "固定增加量", "value": formula["flatBonus"], "mode": "input"}],
            "steps": steps, "limitation": "来自本次实际工作事件的输入快照；保留超出任务剩余需求的完整工作量。普通工作与修墙的最终增产和取整顺序不同，不按产物数量反推。"}
