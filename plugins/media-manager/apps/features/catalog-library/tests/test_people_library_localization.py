"""Multilingual edits are previewed and only touch temporary person fixtures."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from catalog_library import people_library as library, wikipedia_people
from people_catalog import localization
import test_people_library as fixtures


class LocalizedPeopleLibraryTest(unittest.TestCase):
    setUp = fixtures.PeopleLibraryTest.setUp
    save_entity = fixtures.PeopleLibraryTest.save_entity
    detail = fixtures.PeopleLibraryTest.detail
    preview = fixtures.PeopleLibraryTest.preview
    apply = fixtures.PeopleLibraryTest.apply
    bytes = fixtures.PeopleLibraryTest.bytes

    def multilingual(self):
        self.person["localized"] = {
            "name": {"zh-Hans": "旧中文名", "ko": "배우", "en": "Official Name"},
            "summary": {"zh-Hans": "本地中文简介", "ko": "한국어 소개"},
            "aliases": {"ko-Latn": ["Baeu"], "zh-Hans": ["旧别名"]},
        }
        self.save_entity(self.person)

    def test_language_edit_is_offline_versioned_and_preserves_other_languages_and_links(self):
        self.multilingual()
        before = self.bytes()
        fields = {field["field"]: field for field in self.detail()["edit_fields"]}
        self.assertFalse(set(fields) & {"name", "name_cn", "summary", "aliases"})
        self.assertEqual(fields["localized.name.ko"]["value"], "배우")
        self.assertEqual(fields["localized.aliases.ko-Latn"]["type"], "list")
        preview = self.preview(changes={"localized.name.ko": "새 배우", "localized.summary.en": "An English introduction"})
        self.assertEqual(self.bytes(), before)
        self.apply(preview, ["localized.name.ko"])
        saved = self.repo.read(self.identity)
        self.assertEqual(saved["localized"]["name"]["ko"], "새 배우")
        self.assertEqual(saved["localized"]["name"]["zh-Hans"], "旧中文名")
        self.assertEqual(localization.project(saved)["summary"], localization.project(self.person)["summary"])
        self.assertEqual(saved["source_refs"], self.person["source_refs"])
        self.assertEqual(self.source.read_bytes(), before[0])
        self.assertEqual(saved["field_sources"]["localized.name.ko"]["provider"], "manual")

    def test_source_defaults_only_fill_missing_language_and_keep_conflicts_unselected(self):
        self.multilingual()
        before = self.bytes()
        profile = {"provider": "wikipedia", "external_id": "77", "fetched_at": "2026-10-09T00:00:00Z",
            "name": self.person["name"], "name_cn": self.person["name_cn"], "aliases": [], "summary": "",
            "source_url": "https://zh.wikipedia.org/wiki/Actor", "warnings": [],
            "localized": {"name": {"zh-Hans": "新中文名", "ko": "배우", "en": "Different English"},
                          "summary": {"en": "English biography"}, "aliases": {"ja": ["別の呼び名"]}}}
        with patch.object(wikipedia_people, "fetch_person_profile", return_value=profile):
            preview = self.preview("source", provider="wikipedia", external_id="77")
        rows = {row["field"]: row for row in preview["diff"]}
        self.assertEqual(rows["localized.name.ko"]["merge_action"], "same")
        self.assertFalse(rows["localized.name.ko"]["selected"])
        self.assertEqual(rows["localized.name.zh-Hans"]["merge_action"], "merge")
        self.assertTrue(rows["localized.name.zh-Hans"]["selected"])
        self.assertEqual(rows["localized.name.en"]["merge_action"], "conflict")
        self.assertFalse(rows["localized.name.en"]["selected"])
        self.assertTrue(rows["localized.summary.en"]["selected"])
        self.assertTrue(rows["localized.aliases.ja"]["selected"])
        self.assertFalse(set(rows) & {"name", "name_cn", "summary", "aliases"})
        self.assertEqual(self.bytes(), before)
        self.apply(preview)
        saved = self.repo.read(self.identity)
        expected_names = {**localization.project(self.person)["name"], "zh-Hans": "新中文名"}
        self.assertEqual(localization.project(saved)["name"], expected_names)
        self.assertIn(localization.project(self.person)["name"]["zh-Hans"], saved["aliases"])
        self.assertEqual(saved["localized"]["summary"]["en"], "English biography")
        self.assertEqual(saved["localized"]["summary"]["ko"], "한국어 소개")
        self.assertEqual(self.source.read_bytes(), before[0])

    def test_explicit_clear_does_not_resurrect_legacy_summary_or_clear_names(self):
        self.multilingual()
        preview = self.preview("clear")
        self.assertFalse({row["field"] for row in preview["diff"]} & {"name_cn", "summary", "aliases"})
        self.assertFalse(any(field.startswith("localized.name.") for field in [row["field"] for row in preview["diff"]]))
        chosen = [row["field"] for row in preview["diff"]
                  if row["selectable"] and (row["field"] == "summary" or row["field"].startswith("localized.summary."))]
        self.apply(preview, chosen)
        saved = self.repo.read(self.identity)
        self.assertFalse(any(localization.project(saved)["summary"].values()))
        self.assertEqual(localization.project(saved)["name"], localization.project(self.person)["name"])

    def test_source_alias_union_matches_preview_and_does_not_erase_other_languages(self):
        self.multilingual()
        profile = {"provider": "wikipedia", "external_id": "77", "fetched_at": "2026-10-11T00:00:00Z",
            "name": self.person["name"], "source_url": "https://zh.wikipedia.org/wiki/Actor", "warnings": [],
            "localized": {"aliases": {"zh-Hans": ["旧别名", "新别名"], "ko-Latn": ["Baeu", "Actor"]}}}
        with patch.object(wikipedia_people, "fetch_person_profile", return_value=profile):
            preview = self.preview("source", provider="wikipedia", external_id="77")
        rows = {row["field"]: row for row in preview["diff"]}
        self.assertEqual(rows["localized.aliases.zh-Hans"]["merge_action"], "merge")
        self.assertEqual(rows["localized.aliases.zh-Hans"]["merged_value"], ["旧别名", "新别名"])
        self.assertFalse(rows["localized.aliases.zh-Hans"]["requires_confirmation"])
        self.apply(preview)
        saved = self.repo.read(self.identity)
        self.assertEqual(saved["localized"]["aliases"]["zh-Hans"], ["旧别名", "新别名"])
        self.assertEqual(saved["localized"]["aliases"]["ko-Latn"], ["Baeu", "Actor"])
        self.assertEqual(localization.project(saved)["summary"], localization.project(self.person)["summary"])

    def test_clearing_one_language_does_not_reclassify_its_legacy_mirror(self):
        self.person.update(name="Official Name", summary="English biography", aliases=["Baeu"],
            localized={"name": {"en": "Official Name", "zh-Hans": "中文名"},
                       "summary": {"en": "English biography"}, "aliases": {"ko-Latn": ["Baeu"]}})
        self.save_entity(self.person)
        self.apply(self.preview(changes={"localized.summary.en": "", "localized.aliases.ko-Latn": []}))
        result = self.detail()["person"]
        self.assertFalse(any(result["localized"]["summary"].values()))
        self.assertFalse(any(result["localized"]["aliases"].values()))
        self.assertNotIn("und-Latn", result["localized"]["summary"])
        self.assertEqual(result["name"], "Official Name")

    def test_multilingual_card_and_search_use_local_storage(self):
        self.multilingual()
        for query in ("배우", "Baeu", "Official Name"):
            with patch("socket.socket.connect", side_effect=AssertionError("browse must stay local")):
                result = library.browse({"query": query}, **self.kw)
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["items"][0]["localized"]["name"]["ko"], "배우")

    def test_dynamic_paths_and_values_are_strictly_validated(self):
        before = self.bytes()
        for changes in ({"localized.name.xx": "X"}, {"localized.name.ko.extra": "X"},
                        {"localized": {"name": {"ko": "X"}}}, {"localized.name.ko": []},
                        {"localized.aliases.ko": "not a list"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.preview(changes=deepcopy(changes))
        self.assertEqual(self.bytes(), before)


if __name__ == "__main__":
    unittest.main()
