"""Confirmed source versions and profile preference previews use temp data."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from catalog_library import people, people_library, wikipedia_people
from people_catalog import localization, profile_variants
import test_people_library as fixtures


class PeopleProfileVariantsTest(unittest.TestCase):
    setUp = fixtures.PeopleLibraryTest.setUp
    save_entity = fixtures.PeopleLibraryTest.save_entity
    detail = fixtures.PeopleLibraryTest.detail
    preview = fixtures.PeopleLibraryTest.preview
    apply = fixtures.PeopleLibraryTest.apply
    bytes = fixtures.PeopleLibraryTest.bytes

    def wiki(self):
        return {"provider": "wikipedia", "external_id": "77", "fetched_at": "2026-10-11T00:00:00Z",
                "source_url": "https://zh.wikipedia.org/wiki/Actor", "name": "Original Name", "warnings": [],
                "localized": {"name": {"zh-Hans": "维基名字"}, "summary": {"zh-Hans": "维基简介"}}}

    def source_preview(self):
        with patch.object(wikipedia_people, "fetch_person_profile", return_value=self.wiki()):
            return self.preview("source", provider="wikipedia", external_id="77")

    def locked(self):
        self.person["localized"] = {"name": {"en": "Original Name", "zh-Hans": "手工名字"},
                                    "summary": {"zh-Hans": "手工简介"}}
        self.person["field_sources"] = {f"localized.{kind}.zh-Hans": {"provider": "manual", "action": "edit"}
                                        for kind in ("name", "summary")}
        self.save_entity(self.person)

    def test_save_only_source_candidates_does_not_change_locked_display_and_identical_repeat_is_noop(self):
        self.locked()
        before = self.bytes()
        preview = self.source_preview()
        self.assertTrue(preview["profile_versions_changed"])
        self.assertEqual(self.bytes(), before)
        result = self.apply(preview, [])
        self.assertTrue(result["people_updated"])
        saved = self.repo.read(self.identity)
        self.assertEqual(saved["localized"]["name"]["zh-Hans"], "手工名字")
        self.assertEqual(saved["localized"]["summary"]["zh-Hans"], "手工简介")
        self.assertEqual(saved["profile_variants"]["summary"]["zh-Hans"]["wikipedia"]["value"], "维基简介")
        self.assertIn("维基名字", saved["aliases"])
        before_repeat = self.bytes()
        repeated = self.source_preview()
        self.assertFalse(repeated["profile_versions_changed"])
        self.assertFalse(self.apply(repeated, [])["people_updated"])
        self.assertEqual(self.bytes(), before_repeat)

    def test_legacy_manual_lock_is_readonly_projected_then_auto_previews_actual_text_and_unlocks(self):
        self.locked()
        evidence = deepcopy(self.person)
        profile_variants.capture(evidence, self.wiki(), {"provider": "wikipedia", "external_id": "77"})
        evidence.pop("profile_preferences", None)
        self.save_entity(evidence)
        before = self.bytes()
        detail = self.detail()
        self.assertTrue(detail["person"]["profile_preferences"]["name"]["zh-Hans"]["locked"])
        self.assertEqual(self.bytes(), before)
        preview = self.preview(changes={"profile_preferences": {}})
        row = next(row for row in preview["diff"] if row["field"] == "profile_preferences")
        display = {item["field"]: item for item in row["display_diff"]}
        self.assertEqual(display["localized.name.zh-Hans"]["before"], "手工名字")
        self.assertEqual(display["localized.name.zh-Hans"]["after"], "维基名字")
        self.assertEqual(self.bytes(), before)
        self.apply(preview)
        saved = self.repo.read(self.identity)
        self.assertEqual(saved["localized"]["name"]["zh-Hans"], "维基名字")
        self.assertEqual(saved["field_sources"]["localized.name.zh-Hans"]["provider"], "wikipedia")

    def test_source_preference_manual_edit_and_clear_share_existing_apply_and_allow_refill(self):
        self.apply(self.source_preview())
        self.apply(self.preview(changes={"localized.summary.zh-Hans": "手工更新简介"}))
        saved = self.repo.read(self.identity)
        self.assertEqual(saved["profile_preferences"]["summary"]["zh-Hans"], {"locked": True})
        self.apply(self.preview(changes={"localized.summary.zh-Hans": ""}))
        saved = self.repo.read(self.identity)
        self.assertNotIn("zh-Hans", saved["profile_variants"]["summary"])
        self.assertNotIn("zh-Hans", saved["profile_preferences"]["summary"])
        preview = self.source_preview()
        self.assertIn("localized.summary.zh-Hans", preview["selected_fields"])
        self.apply(preview)
        self.assertEqual(self.repo.read(self.identity)["localized"]["summary"]["zh-Hans"], "维基简介")

    def test_legacy_entity_merge_never_replaces_manual_provenance_on_repeat(self):
        self.locked()
        incoming = {key: deepcopy(self.person.get(key, "")) for key in people._PROFILE_FIELDS}
        incoming.update(id=self.identity, type="person", name_cn="维基名字", summary="维基简介",
                        localized={"name": {"zh-Hans": "维基名字"}, "summary": {"zh-Hans": "维基简介"}},
                        field_sources={"localized.name.zh-Hans": {"provider": "wikipedia", "external_id": "77"}})
        source = {"provider": "wikipedia", "entity_type": "person", "external_id": "77"}
        merged = people._merge_entity(self.person, incoming, source=source)
        merged = people._merge_entity(merged, incoming, source=source)
        self.assertEqual(localization.project(merged)["name"]["zh-Hans"], "手工名字")
        self.assertEqual(merged["field_sources"]["localized.name.zh-Hans"]["provider"], "manual")

    def test_explicit_refresh_of_already_merged_same_provider_identity_is_allowed_not_new_identity(self):
        self.person["source_refs"].append({"provider": "bangumi", "entity_type": "person", "external_id": "12"})
        self.save_entity(self.person)
        self.assertEqual(people_library._source_identity(self.person, "bangumi", "11", self.repo), "11")
        self.assertEqual(people_library._source_identity(self.person, "bangumi", "12", self.repo), "12")
        with self.assertRaisesRegex(ValueError, "不同的同平台身份"):
            people_library._source_identity(self.person, "bangumi", "13", self.repo)
        with self.assertRaisesRegex(ValueError, "明确选择"):
            people_library._source_identity(self.person, "bangumi", None, self.repo)


if __name__ == "__main__":
    unittest.main()
