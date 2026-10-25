"""Work arithmetic preserves event inputs, distinct branches and rounding."""
import unittest

from apps.features.tower_assistant.backend.work_formula import calculate, explain_formula, validate_formula
from apps.features.tower_assistant.backend.output_statistics import OutputStatistics, StatisticsError


def formula(expression=5621, **changes):
    inputs = [1, 10.25, 1, 0, 0, 0, 1, 1, 1, 1]
    result = 13
    if expression in (5615, 5620):
        inputs = [1, 10.25, 1, 0, 0, 0, 0, 1, 1, 1, 10]
        result = 15
    return {"expression": expression, "inputs": inputs, "flatBonus": 2.9, "result": result, **changes}


class WorkFormulaTests(unittest.TestCase):
    def test_normal_and_wall_work_have_different_rounding_boundaries(self):
        for expression in (5616, 5617, 5618, 5621, 5622, 5623):
            with self.subTest(expression=expression):
                detail = explain_formula(formula(expression), 13)
                self.assertTrue(detail["matches"])
                self.assertEqual(detail["calculated"], 13)
                self.assertEqual(len(detail["terms"]), 11)
        for expression in (5615, 5620):
            detail = explain_formula(formula(expression), 15)
            self.assertTrue(detail["matches"])
            self.assertEqual(len(detail["terms"]), 12)

    def test_final_bonus_is_multiplier_for_normal_and_additive_for_wall(self):
        ordinary = formula(inputs=[1, 100, 1, .2, .3, .5, 2, 3, 1, 4], flatBonus=0, result=5400)
        wall = formula(5620, inputs=[1, 100, 1, .2, .3, .5, .5, 2, 3, 1, 10], flatBonus=0, result=1981)
        self.assertTrue(explain_formula(ordinary, 5400)["matches"])
        self.assertTrue(explain_formula(wall, 1981)["matches"])
        # The native float product 1800 * 1.1 is slightly above 1980. Do not
        # round to decimal first or rearrange the repair multiplication.
        self.assertEqual(calculate(wall)[0], 1981)

    def test_negative_flat_bonus_is_floored_and_mismatch_preserves_evidence(self):
        raw = formula(flatBonus=-1.1, result=9)
        detail = explain_formula(raw, 9)
        self.assertTrue(detail["matches"])
        self.assertEqual(detail["flatBonus"], -1.1)
        mismatch = explain_formula(raw, 8)
        self.assertFalse(mismatch["matches"])
        self.assertEqual(mismatch["result"], 9)

    def test_large_values_keep_javascript_number_addition_after_rounding(self):
        value = formula(inputs=[1, 2**53, 1, 0, 0, 0, 1, 1, 1, 1], flatBonus=1, result=2**53)
        self.assertTrue(explain_formula(value, 2**53)["matches"])

    def test_unknown_shape_nonfinite_and_overflowing_values_are_rejected(self):
        invalid = [formula(expression=1), formula(expression=True), formula(inputs=[1]),
                   formula(flatBonus=float("nan")), formula(result=-1),
                   formula(inputs=[True] * 10), formula(inputs=[1e100] * 10),
                   {**formula(), "execute": "not allowed"}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_formula(value)

    def test_work_event_explanation_is_a_deep_copy_bound_to_the_recording(self):
        stats = OutputStatistics(detail_limit=1)
        stats.start("stream", 0)
        row = {"schemaVersion": 1, "sequence": 1, "kind": "work", "personId": "1",
               "personName": "工匠", "category": "锻造", "amount": 13, "workFormula": formula(),
               "person": {"profession": "匠人", "appearance": "A1", "iconKey": "person:A1"}}
        stats.accept("stream", [row])
        session = stats.snapshot()["sessionId"]
        detail = stats.explain(1, session)
        self.assertTrue(detail["workFormula"]["matches"])
        self.assertEqual(detail["event"]["person"]["profession"], "匠人")
        detail["event"]["person"]["profession"] = "caller mutation"
        self.assertEqual(stats.explain(1, session)["event"]["person"]["profession"], "匠人")
        stats.accept("stream", [{**row, "sequence": 2}])
        with self.assertRaisesRegex(StatisticsError, "已不在当前保留"):
            stats.explain(1, session)
        self.assertEqual(detail["workFormula"]["calculated"], 13)
        stats.stop()
        stats.start("stream", 0)
        stats.accept("stream", [row])
        with self.assertRaisesRegex(StatisticsError, "计时会话已改变"):
            stats.explain(1, session)


if __name__ == "__main__":
    unittest.main()
