"""Per-language profile consent stays independent from identity and work links."""
from copy import deepcopy
import unittest

from catalog_library import people_import
from people_catalog import localization
from people_catalog.model import source_identity
import test_people as fixtures


class PeopleLocalizedImportTest(unittest.TestCase):
    setUp = fixtures.CatalogPeopleTest.setUp
    save_entity = fixtures.CatalogPeopleTest.save_entity

    def snapshot(self, localized, *, existing=None):
        identity = source_identity("tmdb", "person", "301")
        source = {"provider": "tmdb", "entity_type": "person", "external_id": "301"}
        candidate = {"id": identity, "type": "person", "kind": "individual", "name": "유인식",
                     "taxonomy": {"category": "person", "reality": "real", "subtype": "individual"},
                     "localized": deepcopy(localized), "source": source}
        if existing is not None:
            self.save_entity({"id": identity, "schema_version": 1, "type": "person", "kind": "individual",
                              "name": "유인식", "source_refs": [source], **deepcopy(existing)})
        return {"provider": "tmdb", "subject_id": 100, "import_entries": [{"external_id": "person:301",
                "candidate": candidate, "relations": [{"id": "director", "kind": "work", "role": "导演",
                    "required_entity_keys": [], "ref": {"id": identity, "type": "person", "role": "导演",
                                                        "source": {"provider": "tmdb", "subject_id": "100"}}}]}]}

    def preview(self, snapshot):
        return people_import.preview(snapshot, "localized-preview", {"people_refs": []}, self.repo)[0]

    def choice(self, preview):
        row = preview["entries"][0]
        return {"external_id": row["external_id"], "action": row["action"], "target_id": row["target_id"],
                "fields": list(row["selected_fields"]), "relations": list(row["selected_relations"]), "overrides": {}}

    def test_different_languages_fill_independently_without_legacy_duplicate_diffs(self):
        existing = {"localized": {"name": {"ko": "유인식"}, "summary": {"ko": "한국의 감독"}, "aliases": {"ko": ["유 감독"]}}}
        incoming = {"name": {"ko": "유인식", "zh-Hans": "刘仁植", "en": "Yoo In-sik"},
                    "summary": {"zh-Hans": "韩国导演"}, "aliases": {"zh-Hans": ["刘仁植导演"], "en": ["Yu In-sik"]}}
        snapshot = self.snapshot(incoming, existing=existing)
        before = self.repo.read(snapshot["import_entries"][0]["candidate"]["id"])
        preview = self.preview(snapshot)
        row = preview["entries"][0]
        diff = {field["field"]: field for field in row["targets"][0]["diff"]}
        self.assertFalse({"name", "name_cn", "summary", "aliases"} & diff.keys())
        self.assertTrue(all(not field["requires_confirmation"] for field in diff.values()))
        self.assertEqual(diff["localized.name.ko"]["merge_action"], "same")
        self.assertEqual(diff["localized.summary.ko"]["merge_action"], "keep")
        self.assertEqual(set(row["selected_fields"]), {"localized.name.zh-Hans", "localized.name.en", "localized.summary.zh-Hans",
                                                     "localized.aliases.zh-Hans", "localized.aliases.en"})
        self.assertEqual(row["selected_relations"], ["director"])
        work, pending, _ = people_import.compose(snapshot, [self.choice(preview)], {"people_refs": []}, self.repo)
        updated = pending[row["target_id"]]
        self.assertEqual(localization.project(updated)["summary"], {"ko": "한국의 감독", "zh-Hans": "韩国导演"})
        self.assertEqual(updated["name"], before["name"])
        self.assertEqual(updated["field_sources"]["localized.name.en"]["provider"], "tmdb")
        self.assertEqual(updated["source_values"]["tmdb"]["localized.name.en"], "Yoo In-sik")
        self.assertEqual(self.repo.read(row["target_id"]), before)
        self.assertEqual(work["people_refs"][0]["role"], "导演")

    def test_same_language_conflicts_are_unselected_and_only_chosen_slots_change(self):
        snapshot = self.snapshot({"name": {"zh-Hans": "新中文名"}, "summary": {"zh-Hans": "新简介"},
                                  "aliases": {"zh-Hans": ["新别名"]}},
                                 existing={"localized": {"name": {"zh-Hans": "旧中文名", "en": "Old English"},
                                     "summary": {"zh-Hans": "旧简介"}, "aliases": {"zh-Hans": ["旧别名"]}}})
        preview = self.preview(snapshot)
        row = preview["entries"][0]
        fields = {"localized.name.zh-Hans", "localized.summary.zh-Hans"}
        conflicts = {field["field"] for field in row["targets"][0]["diff"] if field["requires_confirmation"]}
        self.assertEqual(conflicts, set())
        self.assertEqual(fields & set(row["selected_fields"]), {"localized.name.zh-Hans"})
        choice = self.choice(preview)
        choice["fields"] = ["localized.name.zh-Hans"]
        _, pending, _ = people_import.compose(snapshot, [choice], {"people_refs": []}, self.repo)
        values = localization.project(pending[row["target_id"]])
        self.assertEqual(values["name"]["zh-Hans"], "旧中文名")
        self.assertEqual(values["name"]["en"], "Old English")
        self.assertEqual(values["summary"]["zh-Hans"], "旧简介")
        self.assertEqual(values["aliases"]["zh-Hans"], ["旧别名", "新中文名"])
        self.assertEqual(pending[row["target_id"]]["profile_variants"]["name"]["zh-Hans"]["tmdb"]["value"], "新中文名")

    def test_new_multilingual_card_defaults_required_name_and_valid_slots_without_writes(self):
        snapshot = self.snapshot({"name": {"ko": "유인식", "zh-Hans": "刘仁植"}, "summary": {"zh-Hans": "韩国导演"}})
        preview = self.preview(snapshot)
        row = preview["entries"][0]
        self.assertEqual(row["action"], "create")
        self.assertEqual(row["creation"]["required_fields"], ["localized.name.ko"])
        self.assertTrue(set(row["creation"]["required_fields"]) <= set(row["selected_fields"]))
        _, pending, _ = people_import.compose(snapshot, [self.choice(preview)], {"people_refs": []}, self.repo)
        entity = pending[row["candidate"]["id"]]
        self.assertEqual(entity["localized"]["name"], {"ko": "유인식", "zh-Hans": "刘仁植"})
        self.assertEqual(entity["name"], "유인식")
        self.assertEqual(entity["name_cn"], "刘仁植")
        self.assertFalse((self.people_root / "db").exists())
        choice = self.choice(preview)
        choice["fields"].remove("localized.name.ko")
        with self.assertRaisesRegex(ValueError, "非空名称"):
            people_import.compose(snapshot, [choice], {"people_refs": []}, self.repo)

    def test_aliases_default_to_lossless_union_and_preserve_other_languages(self):
        snapshot = self.snapshot({"name": {"ko": "유인식"}, "aliases": {"zh-Hans": ["新别名"]}},
                                 existing={"localized": {"aliases": {"zh-Hans": ["旧别名", "旧别名二"],
                                                                     "en": ["English alias"]}}})
        preview = self.preview(snapshot)
        choice = self.choice(preview)
        self.assertIn("localized.aliases.zh-Hans", choice["fields"])
        _, pending, _ = people_import.compose(snapshot, [choice], {"people_refs": []}, self.repo)
        self.assertEqual(localization.project(pending[preview["entries"][0]["target_id"]])["aliases"],
                         {"zh-Hans": ["旧别名", "旧别名二", "新别名"], "en": ["English alias"]})
        row = next(row for row in preview["entries"][0]["targets"][0]["diff"]
                   if row["field"] == "localized.aliases.zh-Hans")
        self.assertEqual(row["merged_value"], ["旧别名", "旧别名二", "新别名"])
        self.assertFalse(row["requires_confirmation"])

    def test_explicit_manual_alias_override_can_still_replace_one_language(self):
        snapshot = self.snapshot({"aliases": {"zh-Hans": ["新别名"]}},
                                 existing={"localized": {"aliases": {"zh-Hans": ["旧别名"], "en": ["Other"]}}})
        preview = self.preview(snapshot)
        choice = self.choice(preview)
        choice.update(fields=["localized.aliases.zh-Hans"], overrides={"localized.aliases.zh-Hans": ["手填别名"]})
        _, pending, _ = people_import.compose(snapshot, [choice], {"people_refs": []}, self.repo)
        self.assertEqual(localization.project(pending[preview["entries"][0]["target_id"]])["aliases"],
                         {"zh-Hans": ["手填别名"], "en": ["Other"]})

    def test_explicit_empty_slot_and_cancelled_fills_preserve_consent(self):
        snapshot = self.snapshot({"name": {"ko": "유인식", "en": "New English"},
                                  "summary": {"ko": "한국의 감독"}}, existing={"summary": "한국의 감독"})
        preview = self.preview(snapshot)
        choice = self.choice(preview)
        choice.update(fields=["localized.summary.ko"], overrides={"localized.summary.ko": ""}, relations=[])
        _, pending, _ = people_import.compose(snapshot, [choice], {"people_refs": []}, self.repo)
        updated = pending[preview["entries"][0]["target_id"]]
        self.assertEqual(localization.project(updated)["summary"]["ko"], "")
        self.assertNotIn("en", localization.project(updated)["name"])
        self.assertTrue(updated["field_sources"]["localized.summary.ko"]["manually_edited"])
        self.assertEqual(updated["name"], "유인식")

    def test_clearing_the_last_name_is_rejected_without_writing(self):
        snapshot = self.snapshot({"name": {"ko": "유인식"}}, existing={})
        preview = self.preview(snapshot)
        choice = self.choice(preview)
        choice.update(fields=["localized.name.ko"], overrides={"localized.name.ko": ""}, relations=[])
        before = self.repo.read(preview["entries"][0]["target_id"])
        with self.assertRaises(ValueError):
            people_import.compose(snapshot, [choice], {"people_refs": []}, self.repo)
        self.assertEqual(self.repo.read(before["id"]), before)

    def test_language_paths_and_value_types_are_validated_before_composition(self):
        snapshot = self.snapshot({"name": {"ko": "유인식"}, "aliases": {"en": ["Alias"]}}, existing={})
        preview = self.preview(snapshot)
        for field, value in (("localized.name.fr", "name"), ("localized.name.ko", ["wrong type"]),
                             ("localized.aliases.en", "not a list"), ("localized.name.ko.extra", "name")):
            with self.subTest(field=field):
                choice = self.choice(preview)
                choice.update(fields=[field], overrides={field: value})
                with self.assertRaises(ValueError):
                    people_import.compose(snapshot, [choice], {"people_refs": []}, self.repo)


if __name__ == "__main__":
    unittest.main()
