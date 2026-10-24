"""Whole-field supplementation never turns missing evidence into a deletion."""
from copy import deepcopy
import unittest

from catalog_library.people_field_merge import field_merge, has_value
from catalog_library import people_import


class PeopleFieldMergeTest(unittest.TestCase):
    def test_one_sided_fields_keep_evidence_and_only_fill_is_selected(self):
        for field, value, empty in (("summary", "Profile", "  "), ("aliases", ["Alias"], []),
                                    ("birth_date", {"month": 2}, {}), ("gender", 2, 0)):
            with self.subTest(field=field):
                fill = field_merge(field, empty, value)
                self.assertEqual(fill["merge_action"], "fill")
                self.assertTrue(fill["default_selected"])
                self.assertFalse(fill["requires_confirmation"])
                self.assertEqual(fill["merged_value"], value)
                keep = field_merge(field, value, empty)
                self.assertEqual(keep["merge_action"], "keep")
                self.assertFalse(keep["default_selected"])
                self.assertFalse(keep["changed"])
                self.assertEqual(keep["merged_value"], value)

    def test_equal_nonempty_fields_need_no_selection(self):
        for field, value in (("summary", "Profile"), ("aliases", ["Alias"]), ("birth_date", {"year": 2000})):
            row = field_merge(field, value, deepcopy(value))
            self.assertEqual(row["merge_action"], "same")
            self.assertFalse(row["changed"])
            self.assertFalse(row["requires_confirmation"])
            self.assertFalse(row["default_selected"])

    def test_non_alias_lists_and_mappings_are_atomic_conflicts(self):
        for field, before, after in (("summary", "Old", "New"), ("career", ["actor"], ["director"]),
                                     ("birth_date", {"year": 2000}, {"month": 2})):
            row = field_merge(field, before, after)
            self.assertEqual(row["merge_action"], "conflict")
            self.assertTrue(row["requires_confirmation"])
            self.assertFalse(row["default_selected"])
            self.assertEqual(row["merged_value"], after)
            if isinstance(after, (dict, list)):
                self.assertIsNot(row["merged_value"], after)

    def test_aliases_are_safe_stable_union_in_legacy_and_language_fields(self):
        for field in ("aliases", "localized.aliases.zh-Hans", "localized.aliases.ko"):
            with self.subTest(field=field):
                row = field_merge(field, ["Old", "Both"], ["Both", "New"])
                self.assertEqual(row["merge_action"], "merge")
                self.assertEqual(row["merged_value"], ["Old", "Both", "New"])
                self.assertTrue(row["default_selected"])
                self.assertTrue(row["changed"])
                self.assertFalse(row["requires_confirmation"])
                unchanged = field_merge(field, ["Old", "Both"], ["Both"])
                self.assertEqual(unchanged["merge_action"], "same")
                self.assertFalse(unchanged["changed"])
                fill = field_merge(field, [], ["New", "New", " New "])
                self.assertEqual(fill["merged_value"], ["New"])

    def test_blank_unknown_and_null_values_are_not_evidence(self):
        unknown = {"category": "unknown", "reality": "unknown", "subtype": "unknown"}
        for field, value in (("summary", None), ("summary", " \t\n"), ("aliases", []), ("aliases", [" ", ""]),
                             ("birth_date", {}), ("birth_date", {"year": None}),
                             ("gender", 0), ("gender", "0"), ("gender", "unknown"), ("taxonomy", unknown)):
            self.assertFalse(has_value(field, value), (field, value))
            self.assertEqual(field_merge(field, None, value)["merge_action"], "empty")
        row = next(row for row in people_import._diff({"taxonomy": unknown}, {}, creating=True) if row["field"] == "taxonomy")
        self.assertFalse(row["selected"])
        self.assertTrue(row["must_override"])
        self.assertTrue(has_value("infobox", [{"value": 0}]))

    def test_known_taxonomy_never_reclassified_but_unresolved_taxonomy_fills(self):
        entity = {"type": "person", "kind": "individual"}
        candidate = {**entity, "taxonomy": {"category": "person", "reality": "real", "subtype": "individual"}}
        row = next(row for row in people_import._diff(candidate, entity) if row["field"] == "taxonomy")
        self.assertEqual(row["merge_action"], "same")
        self.assertFalse(row["selected"])
        self.assertFalse(row["requires_confirmation"])
        self.assertTrue(row["must_override"])
        row = next(row for row in people_import._diff(candidate, {"type": "person"}) if row["field"] == "taxonomy")
        self.assertTrue(row["selected"])
        self.assertFalse(row["must_override"])
        row = next(row for row in people_import._diff(candidate, {"type": "person", "field_sources": {
            "taxonomy": {"provider": "manual"}}}) if row["field"] == "taxonomy")
        self.assertFalse(row["selected"])
        self.assertTrue(row["must_override"])


if __name__ == "__main__":
    unittest.main()
