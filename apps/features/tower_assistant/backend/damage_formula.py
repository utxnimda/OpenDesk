"""Explain one observed, audited main-damage expression without running game code."""
from __future__ import annotations

import math
from typing import Any

EXPRESSIONS = frozenset((5579, 5584))
TERM_DEFINITIONS = (
    ("attack", "面板攻击", "multiplier"),
    ("skill", "技能系数", "multiplier"),
    ("critical", "本次暴击系数", "multiplier"),
    ("normalBonus", "普技增伤", "additive"),
    ("attackTypeBonus", "攻类增伤", "additive"),
    ("elementBonus", "元素增伤", "additive"),
    ("enemyTypeBonus", "敌类增伤", "additive"),
    ("armorBonus", "护甲增伤", "additive"),
    ("defenseRate", "目标防御率", "defense-rate"),
    ("armorCounter", "护甲克制倍率", "multiplier"),
    ("elementCounter", "五行克制倍率", "multiplier"),
    ("finalBonus", "最终增伤倍率", "multiplier"),
    ("profession", "职业类型倍率", "multiplier"),
    ("skillElement", "技能元素倍率", "multiplier"),
)
FORMULA_TEXT = (
    "向下取整[面板攻击 × 技能系数 × 本次暴击系数 × "
    "(1 + 普技增伤 + 攻类增伤 + 元素增伤 + 敌类增伤 + 护甲增伤) × "
    "(1 − 目标防御率) × 护甲克制倍率 × 五行克制倍率 × 最终增伤倍率 × 职业类型倍率 × 技能元素倍率]"
)
LIMITATION = (
    "只改变本次命中的一个输入并重新取整；乘数提高 1%，加算增伤提高 1 个百分点，"
    "目标防御率降低 1 个百分点。各项投入成本不同，不能当作同成本装备排名；"
    "不包含攻速、冷却、暴击概率或持续伤害，也不预测其他命中的目标与状态变化。"
)


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and -1e100 <= value <= 1e100 and math.isfinite(value)


def validate_formula(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"expression", "inputs", "result"}:
        raise ValueError("伤害公式快照字段无效。")
    if type(value["expression"]) is not int or value["expression"] not in EXPRESSIONS:
        raise ValueError("伤害公式版本未经核验。")
    inputs = value["inputs"]
    if not isinstance(inputs, list) or len(inputs) != 14 or not all(_finite(v) for v in inputs):
        raise ValueError("伤害公式需要 14 个有限数值输入。")
    if not _finite(value["result"]) or value["result"] < 0:
        raise ValueError("伤害公式结果无效。")
    normalized = {"expression": value["expression"], "inputs": [float(v) for v in inputs], "result": float(value["result"])}
    # Reject arithmetic overflow before storing an event, not on a later UI request.
    calculate(normalized["inputs"])
    return normalized


def calculate(values: list[float]) -> int:
    # Match JavaScript's left-associative IEEE-754 operation order exactly.
    additive = 1.0
    for value in values[3:8]:
        additive += value
    result = values[0] * values[1] * values[2] * additive * (1.0 - values[8])
    for value in values[9:14]:
        result *= value
    if not math.isfinite(result) or not 0 <= result <= 1e100:
        raise ValueError("伤害公式计算超出支持范围。")
    return math.floor(result)


def explain_formula(value: dict[str, Any], amount: float) -> dict[str, Any]:
    formula = validate_formula(value)
    inputs = formula["inputs"]
    calculated = calculate(inputs)
    matches = calculated == formula["result"] == amount
    sensitivity = []
    if matches:
        for index, (key, label, mode) in enumerate(TERM_DEFINITIONS):
            changed = inputs.copy()
            if mode == "additive":
                changed[index] += 0.01
                change_label = "+1 个百分点"
            elif mode == "defense-rate":
                changed[index] -= 0.01
                change_label = "目标防御率 −1 个百分点"
            else:
                changed[index] *= 1.01
                change_label = "+1%（相对当前值）"
            try:
                adjusted = calculate(changed)
            except ValueError:
                # An unsupported hypothetical value is not a game observation.
                continue
            delta = adjusted - calculated
            sensitivity.append({"id": key, "label": label, "changeLabel": change_label,
                                "baseline": calculated, "adjusted": adjusted, "delta": delta,
                                "gainPercent": delta / calculated * 100 if calculated else None})
        sensitivity.sort(key=lambda row: (-row["delta"], row["id"]))
    return {**formula, "calculated": calculated, "matches": matches, "text": FORMULA_TEXT,
            "terms": [{"id": key, "label": label, "value": inputs[index], "mode": mode}
                      for index, (key, label, mode) in enumerate(TERM_DEFINITIONS)],
            "sensitivity": sensitivity, "limitation": LIMITATION}
