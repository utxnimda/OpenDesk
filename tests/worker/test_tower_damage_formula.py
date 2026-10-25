import json
import unittest

from apps.features.tower_assistant.backend.damage_formula import explain_formula, validate_formula
from apps.features.tower_assistant.backend.output_statistics import OutputStatistics, StatisticsError


def formula():
    return {"expression": 5579, "inputs": [1000, 2, 1.5, .5, 0, 0, 0, 0, .25, 1, 1, 1, 1, 1], "result": 3375}


def damage(sequence=1, **extras):
    return {"schemaVersion": 1, "sequence": sequence, "kind": "damage", "personId": "person:7",
            "personName": "测试人物", "category": "普通攻击", "amount": 3375, "effectiveAmount": 100,
            "formulaCoverage": "main-product", "formula": formula(), **extras}


class FormulaTests(unittest.TestCase):
    def test_reindexed_audited_formula_preserves_expression_identity_and_semantics(self):
        raw = {**formula(), "expression": 5584}
        result = explain_formula(raw, 3375)
        self.assertEqual(result["expression"], 5584)
        self.assertTrue(result["matches"])
        self.assertEqual(result["sensitivity"], explain_formula(formula(), 3375)["sensitivity"])

    def test_observed_inputs_explain_all_multipliers_and_real_truncation(self):
        result = explain_formula(formula(), 3375)
        self.assertTrue(result["matches"])
        self.assertEqual(result["calculated"], 3375)
        self.assertEqual(len(result["terms"]), 14)
        gains = {row["id"]: row for row in result["sensitivity"]}
        self.assertEqual(gains["attack"]["adjusted"], 3408)
        self.assertEqual(gains["normalBonus"]["adjusted"], 3397)
        self.assertEqual(gains["defenseRate"]["adjusted"], 3420)
        self.assertIn("成本", result["limitation"])

    def test_mismatch_keeps_evidence_but_never_offers_false_sensitivity(self):
        for raw, amount in [({**formula(), "result": 3374}, 3374), (formula(), 3000)]:
            result = explain_formula(raw, amount)
            self.assertFalse(result["matches"])
            self.assertEqual(result["sensitivity"], [])

    def test_zero_baseline_has_no_infinite_percentage(self):
        raw = formula()
        raw["inputs"][0] = 0
        raw["result"] = 0
        result = explain_formula(raw, 0)
        self.assertTrue(all(row["gainPercent"] is None for row in result["sensitivity"]))

    def test_untrusted_or_overflowing_formulas_are_rejected(self):
        bad = [None, {**formula(), "expression": 5617}, {**formula(), "inputs": [1] * 13},
               {**formula(), "inputs": [10**400] * 14}, {**formula(), "inputs": [1e100] * 14},
               {**formula(), "inputs": [float("nan")] * 14}, {**formula(), "result": True},
               {**formula(), "evaluate": "arbitrary script"}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_formula(value)

    def test_explanation_belongs_to_retained_event_and_recording_session(self):
        stats = OutputStatistics(detail_limit=1)
        stats.start("stream", 0)
        first_session = stats.snapshot()["sessionId"]
        stats.accept("stream", [damage()])
        detail = stats.explain(1)
        self.assertEqual(detail["sessionId"], first_session)
        self.assertTrue(detail["formula"]["matches"])
        row = stats.snapshot()["events"][0]
        self.assertEqual(row["overflowAmount"], 3275)
        self.assertNotIn("sensitivity", row["formula"])
        stats.accept("stream", [damage(2)])
        with self.assertRaises(StatisticsError):
            stats.explain(1)
        stats.stop()
        stats.start("stream", 2)
        self.assertNotEqual(stats.snapshot()["sessionId"], first_session)

    def test_special_damage_has_no_fabricated_main_formula(self):
        row = damage(formulaCoverage="stored-buff-value-only")
        del row["formula"]
        stats = OutputStatistics()
        stats.start("stream", 0)
        stats.accept("stream", [row])
        self.assertIsNone(stats.explain(1)["formula"])
        self.assertIn("持续伤害", stats.explain(1)["message"])

    def test_bounded_snapshot_fits_actual_worker_response_limit(self):
        stats = OutputStatistics()
        stats.start("stream", 0)
        rows = [damage(index, personId=str(index), personName="人" * 100, category="攻" * 100,
                       targetName="敌" * 100) for index in range(1, 513)]
        stats.accept("stream", rows)
        response = json.dumps(stats.snapshot(), ensure_ascii=False).encode("utf-8")
        self.assertLess(len(response), 2 * 1024 * 1024 - 32 * 1024)


if __name__ == "__main__":
    unittest.main()
