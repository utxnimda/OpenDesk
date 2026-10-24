"""Shared multilingual source policy stays independent of persistence."""
from copy import deepcopy
import unittest

from people_catalog import localization, profile_variants as variants


class ProfileVariantsTest(unittest.TestCase):
    def person(self, name="TMDB名字", summary="TMDB简介"):
        row = {"name": "한국 이름", "name_cn": name, "summary": summary,
               "localized": {"name": {"ko": "한국 이름", "zh-Hans": name}, "summary": {"zh-Hans": summary}}}
        variants.capture(row, row, {"provider": "tmdb", "external_id": "10"}, seed_current=False)
        variants.refresh_projection_provenance(row)
        return row

    def incoming(self, name="维基名字", summary="维基简介"):
        return {"name": name, "summary": summary, "localized": {"name": {"zh-Hans": name}, "summary": {"zh-Hans": summary}}}

    def adopt(self, person, incoming, source):
        for kind in ("name", "summary"):
            path = f"localized.{kind}.zh-Hans"
            decision = variants.decision(person, incoming, path, source)
            if decision["default_selected"]:
                localization.set_field(person, path, decision["merged_value"])
        variants.capture(person, incoming, source)
        variants.collect_aliases(person)
        variants.refresh_projection_provenance(person)

    def test_wiki_priority_is_stable_across_later_tmdb_refresh_and_other_names_are_aliases(self):
        person = self.person()
        self.adopt(person, self.incoming(), {"provider": "wikipedia", "external_id": "20"})
        self.adopt(person, self.incoming("TMDB新名字", "TMDB新简介"), {"provider": "tmdb", "external_id": "10"})
        self.assertEqual(person["localized"]["name"]["zh-Hans"], "维基名字")
        self.assertEqual(person["localized"]["summary"]["zh-Hans"], "维基简介")
        self.assertIn("TMDB新名字", person["localized"]["aliases"]["zh-Hans"])
        self.assertEqual(person["field_sources"]["localized.name.zh-Hans"]["provider"], "wikipedia")
        self.assertEqual(person["profile_variants"]["summary"]["zh-Hans"]["tmdb"]["value"], "TMDB新简介")

    def test_manual_source_switch_and_auto_do_not_lose_versions(self):
        person = self.person()
        self.adopt(person, self.incoming(), {"provider": "wikipedia", "external_id": "20"})
        variants.apply_preferences(person, {"summary": {"zh-Hans": {"source": "tmdb"}}})
        self.assertEqual(person["localized"]["summary"]["zh-Hans"], "TMDB简介")
        versions = deepcopy(person["profile_variants"])
        variants.apply_preferences(person, {})
        self.assertEqual(person["localized"]["summary"]["zh-Hans"], "维基简介")
        self.assertEqual(person["profile_variants"], versions)
        with self.assertRaises(ValueError):
            variants.apply_preferences(person, {"summary": {"zh-Hans": {"source": "bangumi"}}})

    def test_manual_name_lock_keeps_display_but_collects_wiki_name_as_alias(self):
        person = self.person()
        localization.set_field(person, "localized.name.zh-Hans", "手填名字")
        variants.mark_manual(person, "localized.name.zh-Hans")
        self.adopt(person, self.incoming(), {"provider": "wikipedia", "external_id": "20"})
        self.assertEqual(person["localized"]["name"]["zh-Hans"], "手填名字")
        self.assertIn("维基名字", person["localized"]["aliases"]["zh-Hans"])

    def test_ordinary_clear_removes_versions_and_allows_refill_but_explicit_empty_lock_does_not(self):
        person = self.person()
        previous = deepcopy(person)
        localization.set_field(person, "localized.summary.zh-Hans", "")
        variants.mark_manual(person, "localized.summary.zh-Hans", previous)
        self.assertNotIn("zh-Hans", person["profile_variants"]["summary"])
        source = {"provider": "wikipedia", "external_id": "20"}
        decision = variants.decision(person, self.incoming(), "localized.summary.zh-Hans", source)
        self.assertTrue(decision["default_selected"])
        variants.apply_preferences(person, {"summary": {"zh-Hans": {"locked": True}}})
        decision = variants.decision(person, self.incoming(), "localized.summary.zh-Hans", source)
        self.assertFalse(decision["default_selected"])
        self.assertEqual(decision["merged_value"], "")

    def test_enriched_field_evidence_is_not_misattributed_to_tmdb(self):
        candidate = self.incoming()
        candidate["field_sources"] = {"localized.name.zh-Hans": {
            "provider": "wikipedia", "external_id": "20", "source_url": "https://zh.wikipedia.org/wiki/Actor"}}
        person = self.person()
        variants.capture(person, candidate, {"provider": "tmdb", "external_id": "10"})
        names = person["profile_variants"]["name"]["zh-Hans"]
        self.assertEqual(names["tmdb"]["value"], "TMDB名字")
        self.assertEqual(names["wikipedia"]["value"], "维基名字")

    def test_card_merge_unions_providers_preserves_target_preference_and_rejects_conflicting_same_provider(self):
        target, donor = self.person(), self.incoming()
        variants.capture(donor, donor, {"provider": "wikipedia", "external_id": "20"}, seed_current=False)
        target["profile_preferences"] = {"summary": {"zh-Hans": {"source": "tmdb"}}}
        variants.merge_exports(target, donor)
        self.assertEqual(set(target["profile_variants"]["summary"]["zh-Hans"]), {"tmdb", "wikipedia"})
        self.assertEqual(target["profile_preferences"]["summary"]["zh-Hans"], {"source": "tmdb"})
        conflicting = deepcopy(target)
        conflicting["profile_variants"]["summary"]["zh-Hans"]["wikipedia"]["value"] = "Other"
        with self.assertRaisesRegex(ValueError, "来源身份的版本内容冲突"):
            variants.merge_exports(target, conflicting)

    def test_same_provider_multiple_id_merge_retains_target_choice_and_stable_refresh_keys(self):
        target, donor = self.person(), self.incoming("另一个来源名", "另一个来源简介")
        variants.capture(donor, donor, {"provider": "tmdb", "external_id": "30"}, seed_current=False)
        target["profile_preferences"] = {"summary": {"zh-Hans": {"source": "tmdb"}}}
        variants.merge_exports(target, donor)
        self.assertEqual(set(target["profile_variants"]["summary"]["zh-Hans"]), {"tmdb", "tmdb:30"})
        self.assertEqual(target["profile_preferences"]["summary"]["zh-Hans"], {"source": "tmdb"})
        variants.capture(target, self.incoming("更新来源名", "更新来源简介"), {"provider": "tmdb", "external_id": "30"})
        versions = target["profile_variants"]["summary"]["zh-Hans"]
        self.assertEqual(versions["tmdb"]["external_id"], "10")
        self.assertEqual(versions["tmdb:30"]["value"], "更新来源简介")
        variants.apply_preferences(target, {"summary": {"zh-Hans": {"source": "tmdb:30"}}})
        self.assertEqual(target["localized"]["summary"]["zh-Hans"], "更新来源简介")

    def test_legacy_manual_evidence_projects_lock_readonly_and_auto_unlocks(self):
        person = self.person("手填名字")
        person["field_sources"]["localized.name.zh-Hans"] = {"provider": "manual", "action": "edit"}
        original = deepcopy(person)
        self.assertEqual(variants.effective_preferences(person), {"name": {"zh-Hans": {"locked": True}}})
        self.assertEqual(person, original)
        self.adopt(person, self.incoming(), {"provider": "wikipedia", "external_id": "20"})
        self.adopt(person, self.incoming("维基新名字"), {"provider": "wikipedia", "external_id": "20"})
        self.assertEqual(person["localized"]["name"]["zh-Hans"], "手填名字")
        # Simulate legacy storage with no persisted preference yet.
        person.pop("profile_preferences")
        variants.apply_preferences(person, {})
        self.assertEqual(person["localized"]["name"]["zh-Hans"], "维基新名字")
        self.assertEqual(person["field_sources"]["localized.name.zh-Hans"]["provider"], "wikipedia")

    def test_summary_preference_does_not_touch_unchanged_name_preferences_or_legacy_mirrors(self):
        person = self.person()
        self.adopt(person, self.incoming(), {"provider": "wikipedia", "external_id": "20"})
        preferences = {"name": {"zh-Hans": {"source": "tmdb"}}, "summary": {"zh-Hans": {"source": "tmdb"}}}
        variants.apply_preferences(person, preferences)
        person["name_cn"] = "旧版独立手工中文名"
        before = deepcopy(person)
        preferences["summary"] = {"zh-Hans": {"source": "wikipedia"}}
        variants.apply_preferences(person, preferences)
        self.assertEqual(person["name_cn"], before["name_cn"])
        self.assertEqual(person["name"], before["name"])
        self.assertEqual(person["localized"]["name"], before["localized"]["name"])
        self.assertEqual(person["localized"]["summary"]["zh-Hans"], "维基简介")

    def test_both_chinese_variant_choices_keep_simplified_and_traditional_slots_separate(self):
        person = self.person("简体名")
        person["localized"]["name"]["zh-Hant"] = "繁體名"
        incoming = {"name": "한국 이름", "localized": {"name": {"zh-Hans": "新简体名", "zh-Hant": "新繁體名"}}}
        variants.capture(person, incoming, {"provider": "wikipedia", "external_id": "20"})
        variants.apply_preferences(person, {"name": {lang: {"source": "wikipedia"} for lang in ("zh-Hant", "zh-Hans")}})
        self.assertEqual(person["localized"]["name"]["zh-Hans"], "新简体名")
        self.assertEqual(person["localized"]["name"]["zh-Hant"], "新繁體名")
        self.assertEqual(person["name_cn"], "新简体名")

    def test_card_merge_does_not_treat_distinct_manual_card_versions_as_one_external_identity(self):
        target, donor = self.person("目标手填"), self.person("捐入手填")
        target["id"], donor["id"] = "target-card", "donor-card"
        for row in (target, donor):
            row["profile_variants"] = {}
            variants.mark_manual(row, "localized.name.zh-Hans")
        variants.merge_exports(target, donor)
        names = target["profile_variants"]["name"]["zh-Hans"]
        self.assertEqual(names["local"]["value"], "目标手填")
        self.assertEqual(names["local:donor-card"]["value"], "捐入手填")
        self.assertEqual(target["profile_preferences"]["name"]["zh-Hans"], {"locked": True})

    def test_legacy_provider_tagged_manual_edit_is_seeded_as_local_never_forged_as_tmdb(self):
        person = self.person("手工名字", "手工简介")
        person.pop("profile_variants")
        person["source_values"] = {"tmdb": {"name_cn": "旧TMDB名字", "summary": "旧TMDB简介"}}
        for kind in ("name", "summary"):
            person["field_sources"][f"localized.{kind}.zh-Hans"] = {
                "provider": "tmdb", "external_id": "10", "manually_edited": True}
        variants.capture(person, self.incoming(), {"provider": "wikipedia", "external_id": "20"})
        for kind, expected in (("name", "手工名字"), ("summary", "手工简介")):
            evidence = person["profile_variants"][kind]["zh-Hans"]
            self.assertEqual(evidence["local"]["value"], expected)
            self.assertNotIn("tmdb", evidence)
            self.assertEqual(person["profile_preferences"][kind]["zh-Hans"], {"locked": True})
        self.assertEqual(person["source_values"]["tmdb"]["name_cn"], "旧TMDB名字")


if __name__ == "__main__":
    unittest.main()
