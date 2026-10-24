from copy import deepcopy
import unittest

from catalog_library import people
from catalog_library.people_field_merge import adopted_chinese_mirror
from people_catalog.model import source_identity


class LegacyPeopleMergeTest(unittest.TestCase):
    def test_chinese_mirror_requires_the_actual_selected_language_value(self):
        updated = {"name": "김부장", "localized": {"name": {"ko": "김부장", "und": "金部長"}}}
        source = {"name_cn": "金部長"}
        self.assertEqual(adopted_chinese_mirror(updated, source, ["localized.name.und"]), "金部長")
        self.assertEqual(adopted_chinese_mirror(updated, source, []), "")
        self.assertEqual(adopted_chinese_mirror(updated, source, ["localized.name.ko"]), "")
        self.assertEqual(adopted_chinese_mirror({**updated, "name_cn": "本地姓名"}, source, ["localized.name.und"]), "")
        self.assertEqual(adopted_chinese_mirror(updated, {"name_cn": "未采用"}, ["localized.name.und"]), "")
        self.assertNotIn("zh-Hans", updated["localized"]["name"])

    def test_legacy_builder_uses_additive_aliases_keyed_details_and_empty_fills(self):
        identity = source_identity("tmdb", "person", "123")
        old = {"schema_version": 1, "id": identity, "type": "person", "kind": "individual",
               "name": "Person", "name_cn": "", "summary": "", "aliases": ["A1", "A2"],
               "infobox": [{"key": "旧资料", "value": "保留"}, {"key": "冲突", "value": "原值"},
                           {"key": "身高", "value": ""}], "career": [], "gender": "", "birth_date": {}, "avatar": {},
               "localized": {"name": {"en": "Person"}, "aliases": {"en": ["A1", "A2"]}, "summary": {"zh-Hans": ""}},
               "source_values": {"tmdb": {"summary": "Previously cleared"}}}
        incoming = {**deepcopy(old), "aliases": ["A1", "A3"], "summary": "简介",
                    "infobox": [{"key": "新资料", "value": "新增"}, {"key": "冲突", "value": "新值"},
                                {"key": "身高", "value": "180"}],
                    "localized": {"name": {"en": "Person"}, "aliases": {"en": ["A1", "A3"]}, "summary": {"zh-Hans": "简介"}}}
        baseline = deepcopy(old)
        result = people._merge_entity(old, incoming, source={"provider": "tmdb", "entity_type": "person", "external_id": "123"})
        self.assertEqual(old, baseline)
        self.assertEqual(result["aliases"], ["A1", "A2", "A3"])
        self.assertEqual(result["localized"]["aliases"]["en"], ["A1", "A2", "A3"])
        self.assertEqual(result["summary"], "简介")
        self.assertEqual(result["localized"]["summary"]["zh-Hans"], "简介")
        self.assertEqual({row["key"]: row["value"] for row in result["infobox"]}, {
            "旧资料": "保留", "新资料": "新增", "冲突": "原值", "身高": "180"})


if __name__ == "__main__":
    unittest.main()
