from copy import deepcopy
import unittest

from catalog_library.chapter_numbers import chapter_numbers, normalize_number, project_chapters


class ChapterNumbersTest(unittest.TestCase):
    def test_source_ep_and_sort_remain_independent(self):
        raw = [{"id": str(index), "type": 0, "ep": index, "sort": index + 13}
               for index in range(1, 14)]
        original = deepcopy(raw)
        chapters, numbering = project_chapters(raw)
        self.assertEqual(numbering, {"has_continuous": True, "local_start": "1", "continuous_start": "14"})
        self.assertEqual([row["local_number"] for row in chapters], [str(index) for index in range(1, 14)])
        self.assertEqual([row["continuous_number"] for row in chapters], [str(index) for index in range(14, 27)])
        self.assertEqual(raw, original)
        chapters[0]["ep"] = 999
        self.assertEqual(raw, original)

    def test_identical_main_numbers_and_specials_do_not_enable_extra_column(self):
        raw = [{"ep": 1, "sort": 1}, {"ep": 2, "sort": 2},
               {"type": 1, "ep": 0, "sort": 1}, {"type": 2, "ep": 5, "sort": 99}]
        chapters, numbering = project_chapters(raw)
        self.assertFalse(numbering["has_continuous"])
        self.assertEqual(chapters[2]["local_number"], "1")
        self.assertEqual(chapters[2]["continuous_number"], "")
        self.assertEqual(chapters[3]["continuous_number"], "")

    def test_legacy_priority_is_preserved_without_invented_matches(self):
        self.assertEqual(chapter_numbers({"number": "03", "ep": 2, "sort": 15}, 0)["local_match_number"], "3")
        for raw in ({"ep": 0, "sort": 14}, {"sort": 14}):
            numbers = chapter_numbers(raw, 0)
            self.assertEqual(numbers["local_number"], "14")
            self.assertFalse(numbers["has_continuous_number"])
        missing = chapter_numbers({"name": "Unnumbered"}, 4)
        self.assertEqual(missing["local_number"], "5")
        self.assertEqual(missing["local_match_number"], "")
        self.assertEqual(missing["continuous_match_number"], "")

    def test_partial_and_fractional_sequences_are_not_rebased(self):
        raw = [{"ep": 4, "sort": 17}, {"ep": 4.5, "sort": 17.5}, {"ep": 6, "sort": 20}]
        chapters, numbering = project_chapters(raw)
        self.assertEqual(numbering["local_start"], "4")
        self.assertEqual(numbering["continuous_start"], "17")
        self.assertEqual([row["continuous_number"] for row in chapters], ["17", "17.5", "20"])

    def test_invalid_source_numbers_and_types_are_not_continuation_numbers(self):
        for value in (None, True, [], {}, "", "-1", -1, "1e2", float("inf"), float("nan"), "100001", "1.12345"):
            with self.subTest(value=value):
                self.assertEqual(normalize_number(value), "")
                self.assertFalse(chapter_numbers({"ep": 1, "sort": value}, 0)["has_continuous_number"])
        self.assertEqual(normalize_number("0001.500"), "1.5")
        for kind in (True, False, "0", None, -1, 1):
            with self.subTest(kind=kind):
                self.assertEqual(chapter_numbers({"ep": 1, "sort": 14, "type": kind}, 0)["continuous_number"], "")
        self.assertEqual(chapter_numbers({"ep": 1, "sort": 0}, 0)["continuous_number"], "")


if __name__ == "__main__":
    unittest.main()
