from __future__ import annotations

from copy import deepcopy
import unittest

from catalog_library.people_birth_dates import merge_birth_date, normalize_birth_date


def detail(birthday=None, **fields):
    if birthday is not None:
        fields["infobox"] = [{"key": "生日", "value": birthday}]
    return fields


class PeopleBirthDateTest(unittest.TestCase):
    def test_absent_source_does_not_supply_or_replace_a_date(self):
        for raw in ({}, {"birth_year": None, "birth_mon": 0, "birth_day": None}, {"infobox": []}):
            with self.subTest(raw=raw):
                result = normalize_birth_date(raw)
                self.assertEqual(result["birth_date"], {})
                self.assertFalse(result["available"])
                self.assertFalse(result["replace"])
                self.assertEqual(result["issues"], [])
                self.assertEqual(merge_birth_date({"year": 1980, "mon": 3, "day": 8}, result),
                                 {"year": 1980, "mon": 3, "day": 8})

    def test_normal_structured_date(self):
        result = normalize_birth_date(detail(birth_year=1961, birth_mon=12, birth_day=18))
        self.assertEqual(result["birth_date"], {"year": 1961, "mon": 12, "day": 18})
        self.assertTrue(result["available"])
        self.assertEqual(result["issues"], [])

    def test_independent_scalar_failures_preserve_existing_components(self):
        result = normalize_birth_date(detail(birth_year=None, birth_mon=4, birth_day="invalid"))
        self.assertEqual(result["birth_date"], {"mon": 4})
        self.assertFalse(result["replace"])
        self.assertEqual(result["clear_fields"], [])
        self.assertEqual(merge_birth_date({"year": 1980, "mon": 3, "day": 8}, result),
                         {"year": 1980, "mon": 4, "day": 8})
        issue = result["issues"][0]
        self.assertEqual(issue["field"], "birth_day")
        self.assertEqual(issue["raw"], "invalid")
        self.assertEqual(issue["severity"], "warning")
        self.assertFalse(issue["recovered"])

    def test_scalar_type_and_range_checks_reject_booleans_and_coercion(self):
        for value in (True, False, "12", 12.0, [], {}, -1, 13):
            with self.subTest(value=value):
                result = normalize_birth_date(detail(birth_mon=value))
                self.assertEqual(result["birth_date"], {})
                self.assertFalse(result["available"])
                self.assertEqual(len(result["issues"]), 1)
                self.assertEqual(result["issues"][0]["field"], "birth_mon")

    def test_year_only_never_invents_month_or_day(self):
        for birthday in ("1961年", "1961", "１９６１年", " 1961 年 "):
            with self.subTest(birthday=birthday):
                result = normalize_birth_date(detail(birthday))
                self.assertEqual(result["birth_date"], {"year": 1961})
                self.assertFalse(result["replace"])
                self.assertEqual(result["issues"], [])

    def test_year_only_available_source_can_preserve_older_valid_details(self):
        result = normalize_birth_date(detail("1961年", birth_year=1961))
        self.assertEqual(merge_birth_date({"year": 1961, "mon": 12, "day": 18}, result),
                         {"year": 1961, "mon": 12, "day": 18})

    def test_actual_year_only_upstream_poison_is_replaced_not_merged(self):
        for year, day in ((1961, 1), (1966, 6), (1980, None), (1931, 1), (2004, 4)):
            with self.subTest(year=year):
                raw = detail(f"{year}年", birth_year=None, birth_mon=int(str(year)[:2]), birth_day=day)
                result = normalize_birth_date(raw)
                self.assertEqual(result["birth_date"], {"year": year})
                self.assertTrue(result["replace"])
                self.assertTrue(result["available"])
                self.assertEqual(merge_birth_date({"day": day} if day else {}, result), {"year": year})
                self.assertEqual(result["issues"][0]["severity"], "info")
                self.assertTrue(result["issues"][0]["recovered"])
                self.assertIn(f"{year}年", result["issues"][0]["raw"])
                self.assertIn("birth_mon", result["issues"][0]["raw"])
                self.assertFalse(any(issue["severity"] == "warning" for issue in result["issues"]))

    def test_zero_upstream_components_count_as_unavailable_in_known_corruption(self):
        result = normalize_birth_date(detail("1980年", birth_year=0, birth_mon=19, birth_day=0))
        self.assertTrue(result["replace"])
        self.assertEqual(result["birth_date"], {"year": 1980})

    def test_known_corruption_signature_is_not_applied_to_unrelated_invalid_month(self):
        for raw in (detail("1961年", birth_mon="19", birth_day=1),
                    detail("1961年", birth_mon=20, birth_day=1),
                    detail("1961年", birth_mon=19, birth_day=8),
                    detail("1961年", birth_mon=19, birth_day=1, birth_year=1962)):
            with self.subTest(raw=raw):
                result = normalize_birth_date(raw)
                self.assertFalse(result["replace"])
                self.assertTrue(any(issue["severity"] == "warning" for issue in result["issues"]))

    def test_full_text_date_forms(self):
        for birthday in ("1961年12月18日", "1961年12月18号", "1961年12月18", "1961-12-18", "1961/12/18", "1961.12.18"):
            with self.subTest(birthday=birthday):
                result = normalize_birth_date(detail(birthday))
                self.assertEqual(result["birth_date"], {"year": 1961, "mon": 12, "day": 18})
                self.assertEqual(result["issues"], [])

    def test_year_month_precision(self):
        for birthday in ("1961年12月", "1961-12"):
            with self.subTest(birthday=birthday):
                self.assertEqual(normalize_birth_date(detail(birthday))["birth_date"], {"year": 1961, "mon": 12})

    def test_month_day_without_year_does_not_invent_a_year(self):
        result = normalize_birth_date(detail("1月21日"))
        self.assertEqual(result["birth_date"], {"mon": 1, "day": 21})
        self.assertEqual(result["issues"], [])

    def test_explicit_unknown_year_placeholders_preserve_partial_precision(self):
        for birthday in ("????-07-08", "XXXX-07-08", "--07-08"):
            with self.subTest(birthday=birthday):
                result = normalize_birth_date(detail(birthday))
                self.assertEqual(result["birth_date"], {"mon": 7, "day": 8})
                self.assertEqual(result["issues"], [])

    def test_calendar_checks_structured_and_text_dates(self):
        for raw in (detail(birth_year=2023, birth_mon=2, birth_day=29),
                    detail("2023年2月29日"), detail(birth_mon=4, birth_day=31), detail("4月31日")):
            with self.subTest(raw=raw):
                result = normalize_birth_date(raw)
                self.assertNotIn("day", result["birth_date"])
                self.assertIn("day", result["clear_fields"])
                self.assertTrue(result["issues"])
                self.assertNotIn("day", merge_birth_date({"day": 29}, result))

    def test_leap_day_with_unknown_or_leap_year_is_valid(self):
        for raw in (detail("2月29日"), detail("2024年2月29日"), detail(birth_mon=2, birth_day=29),
                    detail(birth_year=2000, birth_mon=2, birth_day=29)):
            with self.subTest(raw=raw):
                result = normalize_birth_date(raw)
                self.assertEqual(result["birth_date"]["day"], 29)
                self.assertEqual(result["issues"], [])

    def test_merge_cannot_form_invalid_date_from_individually_valid_components(self):
        result = normalize_birth_date(detail(birth_mon=2))
        self.assertEqual(merge_birth_date({"year": 2023, "mon": 1, "day": 31}, result), {"year": 2023, "mon": 2})
        leap = normalize_birth_date(detail("2月29日"))
        self.assertEqual(merge_birth_date({"year": 2023}, leap), {"year": 2023, "mon": 2})

    def test_bad_structured_fields_recover_from_valid_text_with_info_diagnostic(self):
        result = normalize_birth_date(detail("1961年12月18日", birth_year="wrong", birth_mon=19, birth_day=18))
        self.assertEqual(result["birth_date"], {"year": 1961, "mon": 12, "day": 18})
        self.assertEqual(len(result["issues"]), 2)
        self.assertTrue(all(issue["recovered"] and issue["severity"] == "info" for issue in result["issues"]))

    def test_conflicting_valid_structured_and_text_components_are_explicitly_cleared(self):
        result = normalize_birth_date(detail("1961年12月18日", birth_year=1962, birth_mon=11, birth_day=18))
        self.assertEqual(result["birth_date"], {"day": 18})
        self.assertEqual(result["clear_fields"], ["mon", "year"])
        self.assertEqual(merge_birth_date({"year": 1962, "mon": 11, "day": 18}, result), {"day": 18})
        self.assertTrue(all(issue["severity"] == "warning" for issue in result["issues"]))

    def test_conflict_can_explicitly_clear_an_entire_date(self):
        result = normalize_birth_date(detail("1961年", birth_year=1962))
        self.assertEqual(result["birth_date"], {})
        self.assertTrue(result["available"])
        self.assertEqual(merge_birth_date({"year": 1962}, result), {})

    def test_bangumi_list_values_and_repeated_values(self):
        result = normalize_birth_date(detail([{"k": "公历", "v": "1961年12月18日"}, {"v": "1961年12月18日"}]))
        self.assertEqual(result["birth_date"], {"year": 1961, "mon": 12, "day": 18})
        self.assertEqual(result["issues"], [])

    def test_consistent_partial_text_values_can_complement_each_other(self):
        result = normalize_birth_date(detail([{"v": "1961年"}, {"v": "12月18日"}]))
        self.assertEqual(result["birth_date"], {"year": 1961, "mon": 12, "day": 18})

    def test_conflicting_text_values_do_not_choose_first(self):
        result = normalize_birth_date(detail([{"v": "1961年12月18日"}, {"v": "1962年12月18日"}], birth_year=1961))
        self.assertEqual(result["birth_date"], {"mon": 12, "day": 18})
        self.assertIn("year", result["clear_fields"])
        self.assertEqual(merge_birth_date({"year": 1961}, result), {"mon": 12, "day": 18})

    def test_unrecognized_dates_are_not_guessed_from_digit_runs(self):
        for birthday in ("1961年（推定）", "12/18/1961", "1961-12/18", "1961~1962年", "1961年12月18日（旧历）",
                         "昭和36年12月18日", "不明", "19", 1961, {"year": 1961}):
            with self.subTest(birthday=birthday):
                result = normalize_birth_date(detail(birthday))
                self.assertEqual(result["birth_date"], {})
                self.assertFalse(result["replace"])
                self.assertFalse(result["available"])
                self.assertTrue(result["issues"])

    def test_unrecognized_text_does_not_discard_valid_structured_data(self):
        result = normalize_birth_date(detail("1961年（推定）", birth_year=1961, birth_mon=12))
        self.assertEqual(result["birth_date"], {"year": 1961, "mon": 12})

    def test_text_range_error_leaves_only_valid_components(self):
        result = normalize_birth_date(detail("1961年19月18日"))
        self.assertEqual(result["birth_date"], {"year": 1961, "day": 18})
        self.assertIn("mon", result["clear_fields"])

    def test_invalid_text_component_can_use_valid_structured_fallback(self):
        result = normalize_birth_date(detail("1961年19月18日", birth_mon=12))
        self.assertEqual(result["birth_date"], {"year": 1961, "mon": 12, "day": 18})
        self.assertEqual(result["clear_fields"], [])

    def test_infobox_keys_do_not_match_unrelated_dates(self):
        raw = {"infobox": [{"key": "出道日期", "value": "1961年12月18日"}, {"key": "死亡日期", "value": "2000年1月1日"}]}
        self.assertEqual(normalize_birth_date(raw)["birth_date"], {})
        for key in ("出生日期", "生年月日", "誕生日", "Date Of Birth"):
            with self.subTest(key=key):
                self.assertEqual(normalize_birth_date({"infobox": [{"key": key, "value": "1961年"}]})["birth_date"], {"year": 1961})

    def test_validated_infobox_override_is_respected(self):
        raw = detail("1961年")
        self.assertEqual(normalize_birth_date(raw, infobox=[])["birth_date"], {})

    def test_empty_and_malformed_infobox_containers_are_nonblocking(self):
        for box in (None, "broken", {}, [None, 42, {"key": None}], [{"key": "生日", "value": []}], [{"key": "生日"}]):
            with self.subTest(box=box):
                self.assertEqual(normalize_birth_date({"infobox": box})["birth_date"], {})

    def test_diagnostics_are_bounded_and_do_not_copy_arbitrary_graphs(self):
        result = normalize_birth_date(detail("a" * 2000, birth_day={"nested": "b" * 2000}))
        self.assertTrue(all(len(str(issue["raw"])) <= 300 for issue in result["issues"]))
        self.assertEqual(next(issue for issue in result["issues"] if issue["field"] == "birth_day")["raw"], "<dict>")

    def test_input_objects_are_not_mutated(self):
        raw = detail("1961年", birth_mon=19, birth_day=1)
        before = deepcopy(raw)
        previous = {"day": 1}
        result = normalize_birth_date(raw)
        result_before = deepcopy(result)
        merge_birth_date(previous, result)
        self.assertEqual(raw, before)
        self.assertEqual(result, result_before)
        self.assertEqual(previous, {"day": 1})

    def test_previous_values_are_sanitized_when_merging(self):
        result = normalize_birth_date({})
        self.assertEqual(merge_birth_date({"year": True, "mon": 19, "day": 1, "extra": 2}, result), {"day": 1})
        self.assertEqual(merge_birth_date(None, result), {})

    def test_non_mapping_source_is_rejected_as_programming_error(self):
        for raw in (None, [], "text"):
            with self.subTest(raw=raw), self.assertRaises(TypeError):
                normalize_birth_date(raw)


if __name__ == "__main__":
    unittest.main()
