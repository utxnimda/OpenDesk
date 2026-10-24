from copy import deepcopy
import re
import unittest
from unittest.mock import patch
from catalog_library import wikipedia_people as supplement, wikipedia_source as wiki


def payload(title, text, pageid=123, **extra):
    return {"query": {"pages": [{"pageid": pageid, "ns": 0, "title": title,
        "revisions": [{"revid": pageid + 1000, "slots": {"main": {"content": text}}}], **extra}]}}


def table(*rows):
    return '{| class="wikitable"\n! 演員 !! 角色 !! 介紹 !! 台灣配音\n|-\n' + '\n|-\n'.join(rows) + '\n|}'


class WikipediaPeopleTest(unittest.TestCase):
    def setUp(self):
        conversion = patch.object(wiki, "simplified_display", return_value=({}, None))
        self.display_conversion = conversion.start()
        self.addCleanup(conversion.stop)
        korean = patch.object(supplement.korean_people_source, "fetch_for_work", return_value=[])
        self.korean_fetch = korean.start()
        self.addCleanup(korean.stop)
        self.record = {"source_refs": [{"provider": "wikipedia", "external_id": "123", "enabled": True}]}
        self.snapshot = {"schema_version": 1, "provider": "tmdb", "kind": "people", "subject_id": 8,
            "season_number": 1, "original_language": "ko", "warnings": [],
            "raw": {"fallback": {"1": {"id": 1, "name": "Ha Seok-Jin", "original_name": "Ha Seok-Jin"}},
                    "details": {"1": {"id": 1, "name": "Ha Seok-Jin", "also_known_as": ["하석진"]}},
                    "relations": [{"person_id": 1, "category": "cast", "value": "Lee Jae-In", "credit_id": "c1", "eps": "16 集"}],
                    "unavailable_lists": []}}
        self.work = payload("作品", table('| [[河錫辰]] || 李宰仁<ref>官方角色名稱</ref> ||align=left| 男主角 || [[配音員]]'))
        self.actor = payload("河錫辰", '{{藝人|姓名=河錫辰|外文名=하석진|外文=ko|羅馬拼音=Ha Seok-Jin}}', 456,
                             varianttitles={"zh-cn": "河锡辰", "zh-hant": "河錫辰"})
        self.calls = []

    def request(self, query, **kwargs):
        self.calls.append((deepcopy(query), kwargs))
        if query.get("pageids") in (123, 124):
            value = deepcopy(self.work)
            value["query"]["pages"][0]["pageid"] = query["pageids"]
            return value
        if query.get("titles") == self.actor["query"]["pages"][0]["title"]:
            return deepcopy(self.actor)
        raise AssertionError(query)

    def enrich(self):
        with patch.object(wiki, "_request_json", side_effect=self.request):
            return supplement.enrich(self.snapshot, self.record)

    def test_exact_link_native_name_chinese_role_and_complete_provenance(self):
        original = deepcopy(self.snapshot["raw"])
        self.enrich()
        wiki_data = self.snapshot["raw"]["wikipedia_people"]
        person = wiki_data["people"]["1"]
        self.assertEqual(person["name_cn"], "河錫辰")
        self.assertEqual(person["name"], "하석진")
        self.assertEqual(person["name_source"], "native_name")
        self.assertEqual(person["native_name_evidence"], {"field": "外文名", "language": "ko", "value": "하석진"})
        self.assertEqual(person["field_sources"]["localized.name.ko"], {
            "provider": "wikipedia", "external_id": "456", "source_url": wiki.article_url("河錫辰")})
        self.assertIn("河锡辰", person["aliases"])
        relation = wiki_data["relations"][0]
        self.assertEqual(relation["character_name_cn"], "李宰仁")
        self.assertEqual(relation["original_value"], "Lee Jae-In")
        self.assertEqual(relation["credit_id"], "c1")
        self.assertNotIn("character_name", relation)
        self.assertEqual(wiki_data["work_pages"][0]["raw"], self.work)
        self.assertEqual(wiki_data["person_pages"][0]["raw"], self.actor)
        self.assertEqual(relation["source"]["license"], "CC BY-SA 4.0")
        self.assertEqual(relation["source"]["attribution"], "中文维基百科贡献者")
        self.assertEqual(relation["source"]["revision_url"], "https://zh.wikipedia.org/w/index.php?oldid=1123")
        self.assertEqual({key: value for key, value in self.snapshot["raw"].items() if key != "wikipedia_people"}, original)
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(call[1]["deadline"] for call in self.calls))
        self.assertNotIn("search", repr(self.calls))

    def test_no_bound_or_disabled_work_makes_no_request(self):
        for refs in ([], [{"provider": "wikipedia", "external_id": "123", "enabled": False}],
                     [{"provider": "tmdb", "external_id": "123"}]):
            with self.subTest(refs=refs), patch.object(wiki, "_request_json", side_effect=AssertionError("no network")):
                supplement.enrich(deepcopy(self.snapshot), {"source_refs": refs})

    def test_explicit_korean_birth_name_with_romanization_spacing_variant(self):
        self.snapshot["raw"]["fallback"]["1"].update(name="Lee Hae-in", original_name="Lee Hae-in")
        self.snapshot["raw"]["details"]["1"].update(name="Lee Hae-in", also_known_as=["이해인"])
        self.work = payload("作品", table('| [[李海印]] || 李秀晶 || 介紹 || 無'))
        self.actor = payload("李海印", '{{藝人|姓名=李海印|本名=이해인|外文名=|外文=ko|羅馬拼音=Lee Hae In}}', 456)
        self.enrich()
        person = self.snapshot["raw"]["wikipedia_people"]["people"]["1"]
        self.assertEqual(person["name"], "이해인")
        self.assertEqual(person["native_name_evidence"]["field"], "本名")
        # An unrelated official English/stage name is not replaced by the
        # Korean birth name, even for this already matched actor.
        self.snapshot["raw"]["fallback"]["1"]["original_name"] = "David"
        self.enrich()
        self.assertNotIn("name", self.snapshot["raw"]["wikipedia_people"]["people"].get("1", {}))

    def test_existing_chinese_is_not_replaced(self):
        self.snapshot["raw"]["fallback"]["1"]["name"] = "河锡辰"
        self.snapshot["raw"]["relations"][0]["value_cn"] = "李在仁"
        self.enrich()
        result = self.snapshot["raw"]["wikipedia_people"]
        self.assertNotIn("name_cn", result["people"].get("1", {}))
        self.assertEqual(result["relations"], [])

    def test_same_actor_multiple_roles_cannot_be_guessed(self):
        self.work = payload("作品", table('| [[河錫辰]] || 李宰仁 || 介紹 || 無', '| [[河錫辰]] || 李在英 || 介紹 || 無'))
        self.enrich()
        self.assertEqual(self.snapshot["raw"]["wikipedia_people"]["relations"], [])
        self.assertTrue(any("不唯一" in warning for warning in self.snapshot["warnings"]))

    def test_multiple_tmdb_roles_need_explicit_role_identity(self):
        self.work = payload("作品", table('| [[河錫辰]] || 李宰仁<br>{{lang|ko|이재인}} || 介紹 || 無'))
        self.snapshot["raw"]["relations"] = [
            {"person_id": 1, "category": "cast", "value": "이재인", "credit_id": "c1"},
            {"person_id": 1, "category": "cast", "value": "Other Character", "credit_id": "c2"}]
        self.enrich()
        proposals = self.snapshot["raw"]["wikipedia_people"]["relations"]
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["credit_id"], "c1")
        self.assertEqual(proposals[0]["character_name"], "이재인")

    def test_multiple_work_pages_do_not_assign_same_actor_role(self):
        self.record["source_refs"].append({"provider": "wikipedia", "external_id": "124"})
        self.enrich()
        self.assertEqual(self.snapshot["raw"]["wikipedia_people"]["relations"], [])

    def test_ambiguous_person_identity_does_not_bind(self):
        self.snapshot["raw"]["fallback"]["2"] = {"id": 2, "name": "Ha Seok-Jin"}
        self.snapshot["raw"]["details"]["2"] = {"id": 2, "also_known_as": ["하석진"]}
        self.enrich()
        data = self.snapshot["raw"]["wikipedia_people"]
        self.assertEqual((data["people"], data["relations"]), ({}, []))
        self.assertTrue(any("多个 TMDB 人物" in warning for warning in self.snapshot["warnings"]))

    def test_link_ko_is_explicit_evidence_but_never_searched(self):
        self.snapshot["raw"]["details"]["1"]["also_known_as"] = ["임도윤"]
        self.work = payload("作品", table('| {{link-ko|林度允|임도윤}} || 鄭玄真 || 介紹 || 無'))
        self.enrich()
        data = self.snapshot["raw"]["wikipedia_people"]
        self.assertEqual(data["people"]["1"]["name_cn"], "林度允")
        self.assertNotIn("name", data["people"]["1"])
        self.assertEqual(data["relations"][0]["character_name_cn"], "鄭玄真")
        self.assertEqual(len(self.calls), 1)

    def test_dubbing_columns_never_identify_cast(self):
        self.work = payload("作品", table('| 不同演員 || 李宰仁 || 介紹 || [[河錫辰]]'))
        self.enrich()
        self.assertEqual(self.snapshot["raw"]["wikipedia_people"]["relations"], [])
        self.assertEqual(len(self.calls), 1)

    def test_western_official_name_not_replaced_with_korean_alias(self):
        self.snapshot["raw"]["fallback"]["1"]["original_name"] = "Official English Name"
        self.actor["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"] = '{{藝人|姓名=河錫辰|別名=하석진}}'
        self.enrich()
        self.assertNotIn("name", self.snapshot["raw"]["wikipedia_people"]["people"]["1"])

    def test_foreign_script_not_inferred_from_work_country(self):
        self.actor["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"] = '{{藝人|姓名=河錫辰|外文名=하석진|外文=en|羅馬拼音=Ha Seok-Jin}}'
        self.enrich()
        self.assertNotIn("name", self.snapshot["raw"]["wikipedia_people"]["people"]["1"])

    def test_network_failure_and_deadline_are_nonfatal(self):
        before = deepcopy(self.snapshot["raw"])
        with patch.object(wiki, "_request_json", side_effect=ValueError("代理失效")):
            supplement.enrich(self.snapshot, self.record)
        self.assertEqual(self.snapshot["raw"], before)
        self.assertTrue(any("代理失效" in warning for warning in self.snapshot["warnings"]))
        with patch.object(wiki, "_request_json", side_effect=AssertionError("expired")):
            supplement.enrich(self.snapshot, self.record, deadline=0)
        self.assertTrue(any("总时限" in warning for warning in self.snapshot["warnings"]))

    def test_disambiguation_invalid_revision_and_unsafe_person_title_rejected(self):
        for change in ({"pageprops": {"disambiguation": ""}}, {"revisions": []}):
            with self.subTest(change=change), patch.object(wiki, "_request_json", return_value=payload("作品", "text", **change)):
                result = supplement.enrich(deepcopy(self.snapshot), self.record)
                self.assertNotIn("wikipedia_people", result["raw"])
                self.assertTrue(result["warnings"])
        with patch.object(wiki, "_request_json", side_effect=AssertionError("unsafe title")):
            for title in ("https://evil.example/a", "User:Foo", "A|B", "A#B", "A\x00B"):
                with self.subTest(title=title), self.assertRaises(ValueError):
                    supplement._article(title=title, deadline=1)

    def test_table_parser_handles_line_headers_and_rejects_spans_nested_tables(self):
        text = '{|\n! 演員\n! 角色\n! 介紹\n|-\n| [[河錫辰]]\n| 李宰仁\n| 介紹\n|}'
        self.assertEqual(len(supplement._cast_rows(text)), 1)
        for text in (table('| rowspan="2" | [[河錫辰]] || 李宰仁 || 說明 || 無'),
                     table('| [[河錫辰]] || 李宰仁<br>其他角色 || 說明 || 無'),
                     '{|\n! 演員 !! 角色\n|-\n| [[河錫辰]] || 李宰仁\n{|\n| nested\n|}\n|}'):
            with self.subTest(text=text):
                self.assertEqual(supplement._cast_rows(text), [])

    def test_real_article_bold_ordinary_cells_as_headers(self):
        text = '''{| class="wikitable" style="text-align:center"
| width="12%" | ''' + "'''演員'''" + '''
| width="12%" | ''' + "'''角色'''" + '''
| ''' + "'''介紹'''" + '''
| width="12%" | ''' + "'''台灣配音'''" + '''
|-
| [[河錫辰]] || 李宰仁<ref>官方漢名</ref> ||align=left| 介紹 || 配音員
|}'''
        self.work = payload("作品", text)
        self.enrich()
        result = self.snapshot["raw"]["wikipedia_people"]
        self.assertEqual(result["relations"][0]["character_name_cn"], "李宰仁")

    def test_chinese_person_name_from_exact_link_page_if_cell_is_romanized(self):
        self.work = payload("作品", table('| [[河錫辰|Ha Seok-Jin]] || 李宰仁 || 介紹 || 無'))
        self.enrich()
        result = self.snapshot["raw"]["wikipedia_people"]
        self.assertEqual(result["people"]["1"]["name_cn"], "河錫辰")

    def test_unparseable_actor_table_warns_without_silent_failure(self):
        self.work = payload("作品", "沒有表格")
        self.enrich()
        self.assertTrue(any("没有可安全解析" in warning for warning in self.snapshot["warnings"]))

    def test_unknown_template_content_is_not_identity_evidence(self):
        self.assertEqual(supplement._names("{{unknown|[[河錫辰]]}}"), {"names": [], "native_names": [], "links": []})

    def test_two_actor_links_require_both_to_resolve_to_same_person(self):
        self.work = payload("作品", table('| [[河錫辰]] || 李宰仁 || 介紹 || 無',
                                            '| [[另一同名演員|Ha Seok-Jin]] || 李宰仁 || 介紹 || 無'))
        normal_request = self.request
        def request(query, **kwargs):
            if query.get("titles") == "另一同名演員":
                raise ValueError("演員頁連線失敗")
            return normal_request(query, **kwargs)
        with patch.object(wiki, "_request_json", side_effect=request):
            supplement.enrich(self.snapshot, self.record)
        result = self.snapshot["raw"]["wikipedia_people"]
        self.assertEqual(result["people"], {})
        self.assertEqual(result["relations"], [])
        self.assertTrue(any("同名演员链接身份不唯一" in warning for warning in self.snapshot["warnings"]))

    def test_localized_credit_name_is_known_even_when_person_detail_fails(self):
        self.snapshot["raw"]["details"]["1"] = None
        self.snapshot["raw"]["fallback_localized"] = {"1": {"name": "河錫辰"}}
        self.snapshot["original_language"] = "en"
        self.enrich()
        data = self.snapshot["raw"]["wikipedia_people"]
        self.assertEqual(data["relations"][0]["character_name_cn"], "李宰仁")
        self.assertEqual(data["people"], {})
        self.assertEqual(len(self.calls), 1)

    def test_bounded_person_fetches_and_snapshot_size(self):
        with patch.object(supplement, "MAX_PERSON_PAGES", 0):
            self.enrich()
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(any("数量或时间" in warning for warning in self.snapshot["warnings"]))
        with patch.object(supplement, "MAX_SUPPLEMENT_BYTES", 1):
            result = self.enrich()
        self.assertTrue(any("安全大小" in warning for warning in result["warnings"]))

    def korean_article(self, text):
        raw = payload("작품", text, 900)
        return {"pageid": 900, "title": "작품", "revid": 1900, "language": "ko", "raw": raw,
                "url": "https://ko.wikipedia.org/wiki/작품", "revision_url": "https://ko.wikipedia.org/w/index.php?oldid=1900",
                "license": "CC BY-SA 4.0", "license_url": wiki.LICENSE_URL, "attribution": "韩文维基百科贡献者"}

    def independent(self, *, original_language="ko", source_snapshot=False):
        with patch.object(wiki, "_request_json", side_effect=self.request):
            return supplement.fetch_people_snapshot(123, original_language=original_language,
                source_snapshot={"raw": deepcopy(self.work)} if source_snapshot else None)

    def test_independent_wiki_import_retains_simplified_role_and_verified_korean_original(self):
        self.work = payload("作品", table('| [[河錫辰]] || 張秀賢 || 介紹 || 無'))
        korean = self.korean_article("== 등장 인물 ==\n* [[다른 배우]] : 다른 역\n* [[하석진]] : 장수현 역\n")
        self.korean_fetch.return_value = [korean]
        self.display_conversion.return_value = ({"張秀賢": "张秀贤"}, {"variant": "zh-hans"})
        original = deepcopy(self.work)
        result = self.independent(source_snapshot=True)
        relation = result["raw"]["relations"][0]
        self.assertEqual((relation["character_name_cn"], relation["character_name"]), ("张秀贤", "장수현"))
        self.assertEqual(relation["evidence"]["role_cell"], "張秀賢")
        self.assertEqual(relation["evidence"]["translations"][0]["source"]["language"], "ko")
        self.assertEqual(result["raw"]["korean_work_articles"], [korean])
        self.assertEqual(result["raw"]["work_article"]["raw"], original)
        self.assertEqual(self.korean_fetch.call_args.args[0]["pageid"], 123)
        self.assertEqual(self.korean_fetch.call_args.args[0]["revid"], 1123)
        self.assertFalse(any(call[0].get("titles") == "하석진" for call in self.calls))

    def test_role_languages_are_resolved_before_actor_profile_consumes_the_deadline(self):
        self.work = payload("作品", table('| [[河錫辰]] || 張秀賢 || 介紹 || 無'))
        self.korean_fetch.return_value = [self.korean_article("== 등장 인물 ==\n* [[하석진]] : 장수현 역\n")]
        clock = [0]
        def convert(values, **_options):
            self.assertLess(clock[0], 30, "角色简体转换必须先于可选人物详情")
            return {"張秀賢": "张秀贤"}, {"variant": "zh-hans"}
        self.display_conversion.side_effect = convert
        def profile(_title, **_options):
            clock[0] = 31
            return {"external_id": "456", "name": "하석진", "aliases": ["河錫辰", "하석진"]}
        with patch.object(supplement.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(supplement, "fetch_person_profile", side_effect=profile):
            result = self.independent()
        relation = result["raw"]["relations"][0]
        self.assertEqual((relation["character_name_cn"], relation["character_name"]), ("张秀贤", "장수현"))
        self.korean_fetch.assert_called_once()

    def test_tmdb_role_simplification_survives_later_actor_timeout(self):
        self.work = payload("作品", table('| [[河錫辰]] || 張秀賢 || 介紹 || 無'))
        clock = [0]
        original = self.request
        def request(query, **options):
            result = original(query, **options)
            if query.get("titles"):
                clock[0] = 31
            return result
        def convert(values, **_options):
            return ({"張秀賢": "张秀贤"}, {"variant": "zh-hans"}) if clock[0] < 30 else ({}, None)
        self.display_conversion.side_effect = convert
        with patch.object(supplement.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(wiki, "_request_json", side_effect=request):
            supplement.enrich(self.snapshot, self.record)
        self.assertEqual(self.snapshot["raw"]["wikipedia_people"]["relations"][0]["character_name_cn"], "张秀贤")
        self.assertIn("role_display_conversion", self.snapshot["raw"]["wikipedia_people"])

    def test_independent_native_role_keeps_explicit_english_official_name(self):
        self.korean_fetch.return_value = [self.korean_article("== 등장 인물 ==\n* [[하석진]] : 벤(Ben) 역\n")]
        result = self.independent()
        self.assertEqual(result["raw"]["relations"][0]["character_name"], "Ben")

    def test_independent_roles_never_join_by_position_or_guess_multiple_roles(self):
        self.korean_fetch.return_value = [self.korean_article("== 등장 인물 ==\n* [[다른 배우]] : 다른 역\n")]
        self.assertEqual(self.independent()["raw"]["relations"][0]["character_name"], "李宰仁")
        self.korean_fetch.return_value = [self.korean_article("== 등장 인물 ==\n* [[하석진]] : 이재인 역\n* [[하석진]] : 다른 역\n")]
        result = self.independent()
        self.assertEqual(result["raw"]["relations"][0]["character_name"], "李宰仁")
        self.assertTrue(any("不唯一" in value for value in result["warnings"]))
        self.work = payload("作品", table('| [[河錫辰]] || 李宰仁 || 介紹 || 無', '| [[河錫辰]] || 另一角色 || 介紹 || 無'))
        self.korean_fetch.return_value = [self.korean_article("== 등장 인물 ==\n* [[하석진]] : 이재인 역\n")]
        result = self.independent()
        self.assertEqual([row["character_name"] for row in result["raw"]["relations"]], ["李宰仁", "另一角色"])

    def test_independent_role_keeps_original_from_chinese_cast_table(self):
        self.work = payload("作品", table('| [[河錫辰]] || 李宰仁<br>{{lang|ko|이재인}} || 介紹 || 無'))
        self.korean_fetch.return_value = [self.korean_article("== 등장 인물 ==\n* [[하석진]] : 다른 역\n")]
        result = self.independent()
        self.assertEqual(result["raw"]["relations"][0]["character_name"], "이재인")

    def test_independent_work_requires_korean_original_language_not_just_korean_actor(self):
        self.korean_fetch.return_value = [self.korean_article("== 등장 인물 ==\n* [[하석진]] : 이재인 역\n")]
        self.independent(original_language="ja")
        self.independent(original_language=None)
        self.korean_fetch.assert_not_called()
        self.work["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"] = "{{Infobox television|language=韓語}}\n" + table('| [[河錫辰]] || 李宰仁 || 介紹 || 無')
        result = self.independent(original_language=None)
        self.assertEqual(result["raw"]["relations"][0]["character_name"], "이재인")

    def test_independent_optional_korean_failure_keeps_profiles_and_chinese_roles(self):
        self.korean_fetch.side_effect = ValueError("Synthetic language-link failure")
        result = self.independent()
        self.assertEqual(len(result["raw"]["profiles"]), 1)
        self.assertEqual(result["raw"]["relations"][0]["character_name_cn"], "李宰仁")
        self.assertEqual(result["raw"]["korean_work_articles"], [])
        self.assertTrue(any("Synthetic" in value for value in result["warnings"]))

    def test_independent_native_actor_alias_collision_does_not_choose_a_profile(self):
        self.work = payload("作品", table('| [[河錫辰]] || 李宰仁 || 介紹 || 無', '| [[其他演员]] || 另一角色 || 介紹 || 無'))
        self.korean_fetch.return_value = [self.korean_article("== 등장 인물 ==\n* [[하석진]] : 이재인 역\n")]
        def profile(title, **_options):
            return {"external_id": "1" if title == "河錫辰" else "2", "name": title, "aliases": ["하석진"]}
        with patch.object(supplement, "fetch_person_profile", side_effect=profile):
            result = self.independent()
        self.assertEqual([row["character_name"] for row in result["raw"]["relations"]], ["李宰仁", "另一角色"])
        self.assertTrue(any("身份不唯一" in value for value in result["warnings"]))

    def test_list_cast_joins_verified_korean_role_by_actor_not_position(self):
        self.work = payload("作品", "== 演員陣容 ==\n* [[河錫辰]]飾演李宰仁\n")
        self.korean_fetch.return_value = [self.korean_article("== 등장 인물 ==\n* [[다른 배우]] : 다른 역\n* [[하석진]] : 이재인 역 - 인물 소개\n")]
        self.enrich()
        data = self.snapshot["raw"]["wikipedia_people"]
        self.assertEqual(len(data["relations"]), 1)
        relation = data["relations"][0]
        self.assertEqual((relation["character_name_cn"], relation["character_name"]), ("李宰仁", "이재인"))
        self.assertEqual({item["source"]["language"] for item in relation["evidence"]["translations"]}, {"zh", "ko"})
        self.assertEqual(len(data["work_pages"]), 2)
        self.assertFalse(any(call[0].get("titles") == "하석진" for call in self.calls))

    def test_existing_chinese_still_receives_missing_korean_role(self):
        self.snapshot["raw"]["relations"][0]["value_cn"] = "李在仁"
        self.korean_fetch.return_value = [self.korean_article("== 등장 인물 ==\n* [[하석진]] : 이재인 역\n")]
        self.enrich()
        relation = self.snapshot["raw"]["wikipedia_people"]["relations"][0]
        self.assertEqual(relation["character_name_cn"], "")
        self.assertEqual(relation["character_name"], "이재인")

    def test_adult_child_are_not_mixed(self):
        self.work = payload("作品", "== 演員與角色 ==\n* [[河錫辰]]（少年：李童星）飾演李宰仁\n")
        self.snapshot["raw"]["fallback"]["2"] = {"id": 2, "name": "李童星", "original_name": "Child Actor"}
        self.snapshot["raw"]["relations"].append({"person_id": 2, "category": "cast", "value": "Lee Jae-In (young)", "credit_id": "c2"})
        self.enrich()
        relations = self.snapshot["raw"]["wikipedia_people"]["relations"]
        self.assertEqual({entry["credit_id"]: entry["character_name_cn"] for entry in relations}, {"c1": "李宰仁", "c2": "李宰仁（少年）"})
        self.assertNotIn("qualifier", relations[0])
        self.assertEqual(relations[1]["qualifier"]["value"], "teen")
        self.assertEqual(relations[0]["role_group"], relations[1]["role_group"])

    def test_multiname_role_is_retained_as_unresolved_with_source_and_not_applied(self):
        self.work = payload("作品", table('| [[河錫辰]] || 李宰仁／高垠山3972 || 說明 || 無'))
        self.enrich()
        data = self.snapshot["raw"]["wikipedia_people"]
        self.assertEqual(data['relations'], [])
        self.assertEqual(data['unresolved'][0]['role_name_candidates'], ['李宰仁', '高垠山3972'])
        self.assertEqual(data['unresolved'][0]['source']['pageid'], 123)

    def test_conflicting_korean_roles_do_not_choose_one(self):
        self.korean_fetch.return_value = [self.korean_article("== 등장 인물 ==\n* [[하석진]] : 이재인 역\n* [[하석진]] : 다른 역\n")]
        self.enrich()
        relations = self.snapshot["raw"]["wikipedia_people"]["relations"]
        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0]["character_name_cn"], "李宰仁")
        self.assertNotIn("character_name", relations[0])
        self.assertTrue(any("不唯一" in warning for warning in self.snapshot["warnings"]))

    def test_korean_evidence_size_failure_does_not_discard_chinese(self):
        self.korean_fetch.return_value = [self.korean_article("== 등장 인물 ==\n* [[하석진]] : 이재인 역\n" + "x" * 12000)]
        with patch.object(supplement, "MAX_SUPPLEMENT_BYTES", 10000):
            self.enrich()
        data = self.snapshot["raw"]["wikipedia_people"]
        self.assertEqual(data["relations"][0]["character_name_cn"], "李宰仁")
        self.assertEqual(len(data["work_pages"]), 1)
        self.assertTrue(any("中文资料仍继续" in warning for warning in self.snapshot["warnings"]))

    def test_explicit_english_original_role_is_preserved(self):
        self.korean_fetch.return_value = [self.korean_article("== 등장 인물 ==\n* [[하석진]] : 벤(Ben) 역\n")]
        self.enrich()
        self.assertEqual(self.snapshot["raw"]["wikipedia_people"]["relations"][0]["character_name"], "Ben")

    def test_chinese_parser_limit_does_not_block_verified_korean(self):
        self.work = payload("作品", "== 演員陣容 ==\n" + "* [[河錫辰]]飾演李宰仁\n" * 601)
        self.korean_fetch.return_value = [self.korean_article("== 등장 인물 ==\n* [[하석진]] : 이재인 역\n")]
        self.enrich()
        data = self.snapshot["raw"]["wikipedia_people"]
        self.assertEqual(len(data["work_pages"]), 2)
        self.assertEqual(data["relations"][0]["character_name"], "이재인")
        self.assertTrue(any("解析失败" in warning for warning in self.snapshot["warnings"]))

    def test_simplified_display_changes_only_proposed_names_and_roles_not_raw_or_native(self):
        self.work = payload("作品", table('| [[河錫辰]] || 張秀賢 || 介紹 || 無'))
        self.display_conversion.return_value = ({"河錫辰": "河锡辰", "張秀賢": "张秀贤"}, {"variant": "zh-hans", "raw": {"parse": {"text": "converted"}}})
        self.enrich()
        data = self.snapshot["raw"]["wikipedia_people"]
        self.assertEqual(data["people"]["1"]["name_cn"], "河锡辰")
        self.assertEqual(data["people"]["1"]["name"], "하석진")
        self.assertIn("河錫辰", data["people"]["1"]["aliases"])
        self.assertEqual(data["relations"][0]["character_name_cn"], "张秀贤")
        self.assertEqual(data["relations"][0]["evidence"]["role_cell"], "張秀賢")
        self.assertEqual(data["work_pages"][0]["raw"], self.work)
        self.assertEqual(data["person_pages"][0]["title"], "河錫辰")
        self.assertEqual(data["display_conversion"]["variant"], "zh-hans")


class WikipediaPersonProfileTest(unittest.TestCase):
    def setUp(self):
        self.raw = payload("河錫辰", '{{藝人|姓名=河錫辰|外文名=하석진|外文=ko|羅馬拼音=Ha Seok-Jin|occupation=演員}}', 456,
                           varianttitles={"zh-hans": "河锡辰", "zh-hant": "河錫辰"}, extract="韓國演員。")

    def test_explicit_profile_keeps_identity_korean_original_and_separate_conversion_evidence(self):
        calls = []
        def request(query, **kwargs):
            calls.append((query, kwargs))
            if kwargs.get("action") == "parse":
                rendered = re.sub(r'(<div id="nimda-(hans|hant)-[0-9]+">)(.*?)(</div>)',
                    lambda row: row[1] + (row[3]
                        .replace("韩国", "韓國").replace("演员", "演員").replace("河锡辰", "河錫辰") if row[2] == "hant" else
                        row[3].replace("韓國", "韩国").replace("演員", "演员").replace("河錫辰", "河锡辰")) + row[4],
                    query["text"], flags=re.S)
                return {"parse": {"text": rendered}}
            return deepcopy(self.raw)
        with patch.object(wiki, "_request_json", side_effect=request):
            result = supplement.fetch_person_profile("https://zh.wikipedia.org/zh-hans/河錫辰")
        self.assertEqual(calls[0][0]["titles"], "河錫辰")
        self.assertEqual((result["provider"], result["external_id"], result["name"], result["name_cn"]), ("wikipedia", "456", "하석진", "河锡辰"))
        self.assertEqual(result["summary"], "韩国演员。")
        self.assertEqual(result["localized"]["summary"], {"zh-Hant": "韓國演員。", "zh-Hans": "韩国演员。"})
        self.assertEqual(result["infobox"], [{"key": "职业", "value": "演员"}])
        self.assertEqual(result["raw"], self.raw)
        self.assertEqual(result["source_metadata"]["page_title"], "河錫辰")
        self.assertEqual(result["source_metadata"]["revision_id"], 1456)
        self.assertEqual(result["source_url"], wiki.article_url("河錫辰"))
        self.assertEqual(len(calls), 3)
        self.assertTrue(all("search" not in query for query, _ in calls))

    def test_profile_conversion_failure_retains_original_and_other_fields(self):
        def request(query, **kwargs):
            if kwargs.get("action") == "parse":
                raise ValueError("conversion unavailable")
            return deepcopy(self.raw)
        with patch.object(wiki, "_request_json", side_effect=request):
            result = supplement.fetch_person_profile(456)
        self.assertEqual(result["summary"], "韓國演員。")
        self.assertEqual(result["name"], "하석진")
        self.assertTrue(result["warnings"])
        self.assertNotIn("zh-Hans", result["localized"]["summary"])
        self.assertNotIn("display_variant", result["source_metadata"])

    def test_cached_ji_sung_stage_name_precedes_bilingual_birth_name_per_atom(self):
        self.raw = payload('池晟', '{{藝人|姓名=池晟|本名=郭太根（{{lang|ko|곽태근}}）|外文名=지성|外文=ko|羅馬拼音=Ji Sung|英文名=Justin}}', 695335,
                           varianttitles={'zh-hans': '池晟', 'zh-hant': '池晟'})
        with patch.object(wiki, 'simplified_display', return_value=({}, None)):
            result = supplement._profile_from_article(supplement._article_response(self.raw), deadline=float('inf'))
        self.assertEqual(result['name'], '지성')
        self.assertEqual(result['localized']['name']['ko'], '지성')
        self.assertIn('곽태근', result['localized']['aliases']['ko'])
        self.assertNotIn('郭太根', result['localized']['aliases']['ko'])
        self.assertIn('郭太根', result['aliases'])
        self.assertEqual(result['localized']['name']['en'], 'Justin')

    def test_cached_hwang_hee_infobox_beats_disambiguated_article_variants(self):
        self.raw = payload('黃熙 (演員)', '{{藝人|姓名=黃熙|本名=金智秀（김지수）|外文名=황희|外文=ko|羅馬拼音=Hwang Hee}}', 7633581,
                           varianttitles={'zh-hans': '黄熙 (演员)', 'zh-hant': '黃熙 (演員)'})
        with patch.object(wiki, 'simplified_display', return_value=({'黃熙': '黄熙'}, {'original_languages': {'黃熙': 'zh-Hant'}})):
            result = supplement._profile_from_article(supplement._article_response(self.raw), deadline=float('inf'))
        self.assertEqual((result['name'], result['name_cn']), ('황희', '黄熙'))
        self.assertEqual(result['localized']['name']['ko'], '황희')
        self.assertEqual(result['localized']['name']['zh-Hans'], '黄熙')
        self.assertEqual(result['localized']['name']['zh-Hant'], '黃熙')
        self.assertFalse(any('演員' in name or '演员' in name for name in result['localized']['name'].values()))
        self.assertNotIn('金智秀', result['localized']['aliases']['ko'])
        self.assertEqual(result['source_metadata']['page_title'], '黃熙 (演員)')

    def test_old_supplement_names_are_reparsed_offline_only_after_exact_actor_binding(self):
        raw = payload('黃熙 (演員)', '{{藝人|姓名=黃熙|本名=金智秀（김지수）|外文名=황희|外文=ko}}', 7633581,
                      varianttitles={'zh-hans': '黄熙 (演员)'})
        source = {'actor_matches': [{'tmdb_person_id': '2506031', 'wikipedia_pageid': '7633581'}],
                  'person_pages': [supplement._article_response(raw)],
                  'people': {'2506031': {'localized': {'name': {'ko': '金智秀'}}}},
                  'display_conversion': {'variant': 'zh-hans', 'input': ['黃熙'],
                      'original_languages': {'黃熙': 'zh-Hant'},
                      'raw': {'parse': {'text': '<div id="nimda-hans-0">黄熙</div>'}}}}
        original = deepcopy(source)
        with patch.object(wiki, '_request_json', side_effect=AssertionError('no network')):
            result = supplement.reparse_person_name_evidence(source, '2506031')
        self.assertEqual(result['localized']['name'], {'ko': '황희', 'zh-Hant': '黃熙', 'zh-Hans': '黄熙'})
        self.assertEqual(result['name_cn'], '黄熙')
        self.assertEqual(result['field_sources']['localized.name.ko']['external_id'], '7633581')
        self.assertEqual(source, original)
        source['actor_matches'].append({'tmdb_person_id': '2506031', 'wikipedia_pageid': '123'})
        self.assertIsNone(supplement.reparse_person_name_evidence(source, '2506031'))

    def test_unknown_work_and_untrusted_urls_cannot_become_person_profiles(self):
        with patch.object(wiki, "_request_json", return_value=payload("作品", "{{Infobox television|name=作品}}")):
            with self.assertRaisesRegex(ValueError, "人物信息框"):
                supplement.fetch_person_profile(123)
        with patch.object(wiki, "_request_json") as request:
            for value in ("https://evil.example/wiki/Person", "File:Person", None, True):
                with self.assertRaises(ValueError):
                    supplement.fetch_person_profile(value)
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
