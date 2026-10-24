"""Multilingual provider evidence from existing in-memory responses only."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from catalog_library import people, tmdb_people, wikipedia_people, wikipedia_source
from people_catalog import localization


class ProviderLocalizationTest(unittest.TestCase):
    def test_bangumi_keeps_japanese_name_chinese_and_explicit_english_evidence(self):
        entity = people._profile("person", {"id": 1, "type": 1, "name": "花澤香菜", "name_cn": "花泽香菜",
            "summary": "日本の声優", "infobox": [{"key": "英文名", "value": "Kana Hanazawa"},
                                                    {"key": "别名", "value": [{"k": "纯假名", "v": "はなざわ かな"}]}]})
        value = entity["localized"]
        self.assertEqual(value["name"]["ja"], "花澤香菜")
        self.assertEqual(value["name"]["zh-Hans"], "花泽香菜")
        self.assertEqual(value["name"]["en"], "Kana Hanazawa")
        self.assertEqual(value["summary"]["ja"], "日本の声優")
        self.assertIn("はなざわ かな", value["aliases"]["ja"])

    def test_tmdb_retains_native_aliases_without_guessing_latin_is_english(self):
        snapshot = {"subject_id": 100, "season_number": 1, "original_language": "ko", "raw": {}}
        entity = tmdb_people._profile({"id": 1, "name": "河锡辰", "biography": "演员简介",
            "also_known_as": ["하석진", "Ha Seok-Jin"]}, {"id": 1, "original_name": "Ha Seok-Jin"},
            None, [], snapshot, report=False)
        value = entity["localized"]
        self.assertEqual(value["name"]["und-Latn"], "Ha Seok-Jin")
        self.assertEqual(value["name"]["ko"], "하석진")
        self.assertEqual(value["name"]["zh-Hans"], "河锡辰")
        self.assertEqual(value["summary"]["zh-Hans"], "演员简介")
        self.assertNotIn("en", value["name"])
        self.assertNotIn("ko-Latn", value["name"])

    def test_same_language_existing_values_survive_and_empty_slots_are_filled_on_import(self):
        incoming = people._profile("person", {"id": 1, "type": 1, "name": "새 이름", "name_cn": "新名字"})
        source = {"provider": "bangumi", "entity_type": "person", "external_id": "1"}
        previous = people._merge_entity(None, incoming, source=source)
        previous["localized"]["name"]["ko"] = "사용자 이름"
        previous["localized"]["name"]["zh-Hans"] = ""
        updated = people._merge_entity(previous, incoming, source=source)
        self.assertEqual(updated["localized"]["name"]["ko"], "사용자 이름")
        self.assertEqual(updated["localized"]["name"]["zh-Hans"], "新名字")

    def test_wiki_failed_conversion_and_legacy_name_cn_do_not_duplicate_traditional(self):
        article = self.article()
        article["raw"]["query"]["pages"][0]["varianttitles"] = {"zh-hant": "河錫辰"}
        with patch.object(wikipedia_people, "_article", return_value=article), patch.object(
                wikipedia_source, "simplified_display", return_value=({}, None)):
            profile = wikipedia_people.fetch_person_profile(42)
        self.assertEqual(profile["name_cn"], "河錫辰")
        projected = localization.project(profile)
        self.assertEqual(projected["name"]["zh-Hant"], "河錫辰")
        self.assertNotIn("zh-Hans", projected["name"])
        self.assertNotIn("zh-Hans", projected["summary"])

    def article(self):
        text = "{{藝人|姓名=河錫辰|外文名=하석진|外文=ko|羅馬拼音=Ha Seok-Jin|英文名=Henry}}"
        raw = {"query": {"pages": [{"pageid": 42, "ns": 0, "title": "河錫辰", "extract": "原始简介",
            "varianttitles": {"zh-cn": "河锡辰", "zh-hant": "河錫辰"},
            "revisions": [{"revid": 43, "slots": {"main": {"content": text}}}]}]}}
        return wikipedia_people._article_response(raw)

    def test_wiki_retains_variants_native_name_and_explicit_romanization(self):
        article = self.article()
        parsed = wikipedia_people._person_names(article)["localized"]
        self.assertEqual(parsed["name"]["zh-Hans"], "河锡辰")
        self.assertEqual(parsed["name"]["zh-Hant"], "河錫辰")
        self.assertEqual(parsed["name"]["ko"], "하석진")
        self.assertEqual(parsed["name"]["ko-Latn"], "Ha Seok-Jin")
        self.assertEqual(parsed["name"]["en"], "Henry")
        with patch.object(wikipedia_people, "_article", return_value=article), patch.object(
                wikipedia_source, "simplified_display", return_value=({"原始简介": "简体简介"}, {"fixture": True})):
            profile = wikipedia_people.fetch_person_profile(42)
        self.assertEqual(profile["localized"]["summary"]["zh-Hans"], "简体简介")
        self.assertEqual(profile["localized"]["summary"]["und"], "原始简介")
        self.assertEqual(profile["localized"]["name"]["ko-Latn"], "Ha Seok-Jin")

    def test_work_people_projection_exposes_localized_without_mutating_profiles(self):
        entity = people._profile("person", {"id": 1, "type": 1, "name": "배우", "name_cn": "演员"})
        before = deepcopy(entity)
        result = people.project({"people_refs": [{"id": entity["id"], "type": "person", "role": "导演"}]}, entities=[entity])
        self.assertEqual(result["persons"][0]["localized"]["name"]["ko"], "배우")
        self.assertEqual(entity, before)


if __name__ == "__main__":
    unittest.main()
