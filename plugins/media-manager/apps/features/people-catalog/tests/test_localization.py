"""Pure multilingual schema/projection tests; no database, network or UI."""
from copy import deepcopy
import unittest

from people_catalog import localization as local
from people_catalog.model import source_identity, validate_entity


class LocalizationTest(unittest.TestCase):
    def entity(self, **changes):
        return {"schema_version": 1, "id": source_identity("manual", "person", "language"),
                "type": "person", "name": "Legacy Name", **changes}

    def test_schema_one_accepts_legacy_and_optional_multilingual_slots(self):
        self.assertNotIn("localized", validate_entity(self.entity()))
        entity = self.entity(localized={"name": {"ko": "김배우", "zh-Hans": "金演员"},
                                       "summary": {"en": "Biography"}, "aliases": {"ja": ["キム"]}})
        self.assertEqual(validate_entity(entity), entity)

    def test_strict_languages_types_duplicates_and_bounds(self):
        for value in ({"other": {}}, {"name": {"fr": "Nom"}}, {"name": {"ko": []}},
                      {"aliases": {"ko": "김"}}, {"aliases": {"ko": ["김", "김"]}},
                      {"name": {"ko": "x" * 1001}}, {"summary": {"en": "x" * 100001}},
                      {"aliases": {"en": [str(i) for i in range(201)]}}, {"name": {"en": "bad\x00name"}},
                      {"summary": {"ko": "김" * 100000, "ja": "あ" * 100000}}):
            with self.subTest(value=str(value)[:80]), self.assertRaises(ValueError):
                validate_entity(self.entity(localized=value))

    def test_latin_is_not_english_or_korean_romanization_without_evidence(self):
        entity = self.entity(name="Ha Seok-Jin", aliases=["Kim", "김배우", "かな", "漢字"])
        result = local.project(entity)
        self.assertEqual(result["name"], {"und-Latn": "Ha Seok-Jin"})
        self.assertEqual(result["aliases"]["und-Latn"], ["Kim"])
        self.assertEqual(result["aliases"]["ko"], ["김배우"])
        self.assertEqual(result["aliases"]["ja"], ["かな"])
        self.assertEqual(result["aliases"]["und"], ["漢字"])
        for hint in ("ko", "ja", "zh-CN"):
            self.assertEqual(local.infer_language("Kim", hint=hint), "und-Latn")
        self.assertEqual(local.infer_language("Kim", hint="en"), "en")
        self.assertEqual(local.infer_language("Kim", hint="ko-Latn"), "ko-Latn")

    def test_han_name_needs_chinese_field_or_language_source_evidence(self):
        self.assertEqual(local.project(self.entity(name="花澤香菜"))["name"], {"und": "花澤香菜"})
        self.assertEqual(local.project(self.entity(name="花澤香菜", source_refs=[{"provider": "bangumi"}]))["name"], {"ja": "花澤香菜"})
        self.assertEqual(local.project(self.entity(name="花泽香菜", name_cn="花泽香菜"))["name"], {"zh-Hans": "花泽香菜"})

    def test_native_name_inside_chinese_biography_does_not_change_language(self):
        biography = "苏志燮（소지섭），韩国著名演员，出生于首尔，曾主演多部电视剧。"
        self.assertEqual(local.infer_language(biography, hint="zh-CN"), "zh-Hans")
        self.assertEqual(local.infer_language(biography, hint="zh-Hant"), "zh-Hant")
        self.assertEqual(local.infer_language(biography), "und")
        self.assertEqual(local.infer_language("소지섭（金某），한국의 배우이다", hint="zh-CN"), "ko")
        self.assertEqual(local.infer_language("김부장", hint="zh-CN"), "ko")
        self.assertEqual(local.infer_language("山田（やまだ），日本著名演员，在东京出生。", hint="zh-CN"), "zh-Hans")
        self.assertEqual(local.infer_language("山田は日本の俳優です", hint="ja"), "ja")
        self.assertEqual(local.infer_language("日本の声優"), "ja")
        values = {}
        local.add_evidence(values, "summary", biography, hint="zh-Hans")
        local.add_evidence(values, "summary", "한국의 배우이다", hint="ko")
        self.assertEqual(values["summary"], {"zh-Hans": biography, "ko": "한국의 배우이다"})

    def test_generic_chinese_language_does_not_claim_simplified(self):
        self.assertEqual(local.language("zh"), "")
        self.assertEqual(local.infer_language("韓國演員", hint="zh"), "und")

    def test_traditional_or_unknown_name_cn_never_gains_simplified_slot(self):
        for language in ("zh-Hant", "und"):
            entity = self.entity(name="河錫辰", name_cn="河錫辰", localized={"name": {language: "河錫辰"}})
            before = deepcopy(entity)
            self.assertEqual(local.project(entity)["name"], {language: "河錫辰"})
            self.assertEqual(entity, before)

    def test_name_cn_explicit_traditional_provenance_survives_legacy_projection(self):
        entity = self.entity(name="배우", name_cn="河錫辰", field_sources={"name_cn": {"language": "zh-TW"}})
        self.assertEqual(local.project(entity)["name"], {"ko": "배우", "zh-Hant": "河錫辰"})

    def test_unverified_chinese_remains_visible_without_fabricating_script(self):
        entity = self.entity(name="배우", name_cn="河錫辰", localized={"name": {"ko": "배우", "und": "河錫辰"}})
        self.assertEqual(local.compatibility(entity)["name_cn"], "河錫辰")
        self.assertNotIn("zh-Hans", local.project(entity)["name"])

    def test_alias_union_preserves_order_variants_and_checks_bounds(self):
        before = ["金部长", "Kim", "Cafe\u0301"]
        after = [" 金部长 ", "Kim", "Caf\u00e9", "김부장", ""]
        self.assertEqual(local.merge_aliases(before, after), ["金部长", "Kim", "Cafe\u0301", "김부장"])
        self.assertEqual(before, ["金部长", "Kim", "Cafe\u0301"])
        self.assertEqual(local.merge_aliases(None, ["Alias"]), ["Alias"])
        with self.assertRaises(ValueError):
            local.merge_aliases([str(index) for index in range(200)], ["Overflow"])
        with self.assertRaises(ValueError):
            local.merge_aliases(["Name"], "Not a list")

    def test_explicit_empty_slots_suppress_legacy_and_do_not_mutate_on_read(self):
        entity = self.entity(name="김배우", name_cn="金演员", summary="한국어 소개", aliases=["김", "Kim"])
        local.set_field(entity, "localized.name.ko", "")
        local.set_field(entity, "localized.summary.ko", "")
        local.set_field(entity, "localized.aliases.ko", [])
        before = deepcopy(entity)
        result = local.project(entity)
        self.assertEqual(result["name"]["ko"], "")
        self.assertEqual(result["summary"]["ko"], "")
        self.assertEqual(result["aliases"]["ko"], [])
        self.assertEqual(result["aliases"]["und-Latn"], ["Kim"])
        self.assertEqual(entity, before)

    def test_legacy_repeated_aliases_and_nonobject_field_sources_remain_readable(self):
        entity = self.entity(aliases=["Alias", "Alias"], field_sources="old-format")
        self.assertEqual(validate_entity(entity), entity)
        self.assertEqual(local.project(entity)["aliases"]["und-Latn"], ["Alias"])

    def test_clear_explicit_latin_slots_does_not_resurrect_legacy_as_unknown_latin(self):
        entity = self.entity(name="Actor", name_cn="演员", summary="Biography", aliases=["Kim", "Unclassified"],
                             localized={"name": {"en": "Actor"}, "summary": {"en": "Biography"},
                                        "aliases": {"ko-Latn": ["Kim"]}})
        local.set_field(entity, "localized.name.en", "")
        local.set_field(entity, "localized.summary.en", "")
        local.set_field(entity, "localized.aliases.ko-Latn", [])
        result = local.project(entity)
        self.assertEqual(entity["name"], "演员")
        self.assertEqual(entity["summary"], "")
        self.assertEqual(entity["aliases"], ["Unclassified"])
        self.assertEqual(result["name"], {"en": "", "zh-Hans": "演员"})
        self.assertEqual(result["summary"], {"en": ""})
        self.assertEqual(result["aliases"], {"ko-Latn": [], "und-Latn": ["Unclassified"]})

    def test_slot_edit_only_updates_proven_mirrors_and_preserves_other_languages(self):
        entity = self.entity(name="김배우", name_cn="中文名", summary="Manual summary", aliases=["Kim", "Other"],
                             localized={"name": {"ko": "김배우", "en": "Actor"},
                                        "summary": {"en": "Biography"}, "aliases": {"ko-Latn": ["Kim"], "en": ["Kim"]}})
        local.set_field(entity, "localized.name.en", "Updated actor")
        local.set_field(entity, "localized.summary.en", "Updated biography")
        local.set_field(entity, "localized.aliases.ko-Latn", ["Gim"])
        self.assertEqual((entity["name"], entity["name_cn"], entity["summary"]), ("김배우", "中文名", "Manual summary"))
        self.assertEqual(entity["aliases"], ["Kim", "Other", "Gim"])
        self.assertEqual(local.project(entity)["aliases"]["en"], ["Kim"])
        local.set_field(entity, "localized.name.ko", "김새이름")
        self.assertEqual(entity["name"], "김새이름")

    def test_last_name_clear_and_invalid_value_fail_atomically(self):
        entity = self.entity(name="Actor", localized={"name": {"en": "Actor"}})
        before = deepcopy(entity)
        for value in ("", " " * 3, "x" * 1001):
            with self.subTest(value=value[:20]), self.assertRaises(ValueError):
                local.set_field(entity, "localized.name.en", value)
            self.assertEqual(entity, before)
        # A differently spelled stale mirror in the same evidenced language
        # is not permission to leave every displayable name slot empty.
        entity = self.entity(name="Old Actor", source={"language": "en"}, localized={"name": {"en": "Actor"}})
        before = deepcopy(entity)
        with self.assertRaises(ValueError):
            local.set_field(entity, "localized.name.en", "")
        self.assertEqual(entity, before)

    def test_dynamic_paths_are_ordered_bounded_and_kind_aware(self):
        entity = self.entity(localized={"aliases": {"ko": ["김"]}, "name": {"ko": "김배우"}})
        paths = local.field_paths(entity, self.entity(localized={"summary": {"ja": "紹介"}}))
        self.assertIn("localized.name.ko", paths)
        self.assertIn("localized.summary.ja", paths)
        self.assertIn("localized.aliases.ko", paths)
        self.assertEqual(local.field_value(entity, "localized.aliases.ko"), ["김"])
        self.assertEqual(local.value(entity, "localized.aliases.en"), [])
        for path in ("localized.name.fr", "localized.aliases.ko.extra", "localized.name.__dict__", "name.ko", None):
            self.assertFalse(local.is_field(path))
            with self.assertRaises(ValueError):
                local.set_field(entity, path, "bad")

    def test_explicit_evidence_and_other_language_values_are_not_overwritten(self):
        result = {"name": {"ko": "김배우"}}
        local.add_evidence(result, "name", "다른 이름")
        local.add_evidence(result, "name", "金演员", hint="zh-CN")
        self.assertEqual(result["name"], {"ko": "김배우", "zh-Hans": "金演员"})
        self.assertEqual(result["aliases"]["ko"], ["다른 이름"])

    def test_compatibility_mirrors_confirmed_language_without_losing_aliases(self):
        entity = self.entity(name="김배우", name_cn="旧中文", aliases=["Old Alias"],
                             localized={"name": {"ko": "김새이름", "zh-Hans": "新中文"}, "aliases": {"ko": ["김"]}})
        before = deepcopy(entity)
        result = local.compatibility(entity)
        self.assertEqual((result["name"], result["name_cn"]), ("김새이름", "新中文"))
        self.assertEqual(result["aliases"], ["Old Alias", "김"])
        self.assertEqual(entity, before)


if __name__ == "__main__":
    unittest.main()
