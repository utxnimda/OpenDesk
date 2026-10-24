"""Small source-grounded examples; fixtures never contain complete articles."""
import unittest
from unittest.mock import patch

from catalog_library import wikipedia_cast as cast


def table(*rows):
    return '{| class="wikitable"\n! 演員 !! 角色 !! 介紹 !! 台灣配音\n|-\n' + '\n|-\n'.join(rows) + '\n|}'


class NameEvidenceTest(unittest.TestCase):
    def test_known_link_and_language_templates(self):
        result = cast.names("[[河錫辰]]<br>{{lang|ko|하석진}}")
        self.assertEqual(result, {"names": ["河錫辰", "하석진"], "native_names": ["하석진"], "links": ["河錫辰"]})
        for template in ("lk", "link-ko"):
            result = cast.names("{{" + template + "|吳大錫 (演員)|오대석 (배우)|吳大錫}}")
            self.assertEqual(result["names"], ["吳大錫", "오대석"])
            self.assertEqual(result["native_names"], ["오대석"])
            self.assertEqual(result["links"], [])

    def test_chinese_parenthetical_korean_name_is_explicit_alias(self):
        result = cast.names("金奎元（김규원）")
        self.assertEqual(result["names"], ["金奎元", "김규원"])
        self.assertEqual(result["native_names"], ["김규원"])
        self.assertEqual(result["links"], [])
        self.assertEqual(cast.names("納吉‧費拉斯（나지 피라스）")["names"], ["納吉‧費拉斯", "나지 피라스"])

    def test_explicit_role_alias_and_disambiguated_article_link(self):
        self.assertEqual(cast.names("金娜娜（金娜鳳）")["names"], ["金娜娜", "金娜鳳"])
        self.assertEqual(cast.names("[[李宗賢 (演員)|李宗賢]]")["links"], ["李宗賢 (演員)"])

    def test_unknown_templates_multiple_people_and_unsafe_links_are_not_evidence(self):
        for raw in ("{{unknown|[[河錫辰]]}}", "[[A]]和[[B]]", "[[A]]／[[B]]", "黃金[[尋回犬]]",
                    "[[User:演員]]", "[[https://evil.example/a]]", "演員（少年：另一演員）", "{{lk|A}}"):
            with self.subTest(raw=raw):
                self.assertEqual(cast.names(raw)["names"], [])

    def test_names_are_plain_evidence_not_guessed_translations(self):
        self.assertEqual(cast.names("Official English Name")["names"], ["Official English Name"])
        self.assertEqual(cast.names("Official English Name")["native_names"], [])
        self.assertEqual(cast.names("{{lang|en|Official English Name}}")["native_names"], [])


class CastListTest(unittest.TestCase):
    def test_apartment_cast_fragment_splits_explicit_young_actor(self):
        # Cached 公寓黑風暴: only the necessary cast lines are reproduced.
        rows = cast.cast_rows("""== 演員與角色 ==
=== 主要人物 ===
* [[池晟]]（少年：[[李宗賢 (演員)|李宗賢]]）飾演朴海江<ref>source</ref>
: 綠洲幫前任老大。
* [[河潤景]]飾演姜夏莉
""")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["actor"]["names"], ["池晟"])
        self.assertEqual(rows[0]["character_name_cn"], "朴海江")
        self.assertNotIn("qualifier", rows[0])
        self.assertEqual(rows[0]["role_group"], rows[1]["role_group"])
        self.assertEqual(rows[1]["actor"]["names"], ["李宗賢"])
        self.assertEqual(rows[1]["character_name_cn"], "朴海江（少年）")
        self.assertEqual(rows[1]["role"]["names"], ["朴海江"])
        self.assertEqual(rows[1]["qualifier"], {"kind": "age", "value": "teen", "label": "少年", "language": "zh", "explicit": True})
        self.assertEqual(rows[2]["character_name_cn"], "姜夏莉")

    def test_apartment_explicit_role_real_name_and_parenthetical_actor(self):
        rows = cast.cast_rows("""== 演員與角色 ==
=== 其他 ===
* [[金元海]]飾演蜥蜴
: 本名黃仁植，前公寓管理所所長。
* 金奎元（김규원）飾演大寶
: 海江組織年紀最小的成員。
* {{lk|吳大錫 (演員)|오대석 (배우)|吳大錫}}飾演宋友植
""")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["role"]["names"], ["蜥蜴", "黃仁植"])
        self.assertEqual(rows[0]["role_alias_evidence"][0]["value"], "黃仁植")
        self.assertEqual(rows[1]["actor"]["native_names"], ["김규원"])
        self.assertEqual(rows[2]["actor"]["names"], ["吳大錫", "오대석"])

    def test_cached_judge_multiple_explicit_age_actors_share_one_source_role_group(self):
        rows = cast.cast_rows('== 演員 ==\n* [[池晟]]（少年：[[尹在贊]]）（童年：[[徐宇真]]）飾演李漢英')
        self.assertEqual(len(rows), 3)
        self.assertEqual([row.get('qualifier', {}).get('value') for row in rows], [None, 'teen', 'child'])
        self.assertEqual(len({row['role_group'] for row in rows}), 1)
        self.assertEqual([row['actor']['names'][0] for row in rows], ['池晟', '尹在贊', '徐宇真'])

    def test_multiname_child_actor_stays_unresolved_and_never_guesses_correspondence(self):
        unresolved = []
        rows, warnings = cast.parse('== 演員 ==\n* [[甲]]（童年：[[乙]]／[[丙]]）飾演角色甲', unresolved=unresolved)
        self.assertEqual(rows, [])
        self.assertEqual(len(unresolved), 1)
        self.assertTrue(warnings)
        self.assertNotIn('role_group', unresolved[0])

    def test_shining_season_fragment_keeps_evidenced_character_alias(self):
        # Cached 在你燦爛的季節: parenthesis is explicit character-name evidence.
        rows = cast.cast_rows("""== 演員陣容 ==
=== 主要人物 ===
* [[李聖經]]飾演宋嘏爛
=== 其他人物 ===
* [[李美淑]]飾演金娜娜（金娜鳳）
* {{lk|權赫 (1989年)|권혁 (1989년)|權赫}}飾演延太錫
* 納吉‧費拉斯（나지 피라스）飾演班
""")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1]["character_name_cn"], "金娜娜")
        self.assertEqual(rows[1]["role"]["names"], ["金娜娜", "金娜鳳"])
        self.assertEqual(rows[2]["actor"]["native_names"], ["권혁"])
        self.assertEqual(rows[3]["actor"]["names"], ["納吉‧費拉斯", "나지 피라스"])

    def test_lists_are_restricted_to_cast_sections_not_body_references_or_dubbing(self):
        text = """* [[甲]]飾演角色甲
== 劇情概要 ==
* [[乙]]飾演角色乙
== 演員陣容 ==
=== 主要人物 ===
* [[丙]]飾演角色丙
=== 配音員 ===
* [[丁]]飾演角色丁
== 外部連結 ==
* [[戊]]飾演角色戊
"""
        self.assertEqual([row["actor"]["names"][0] for row in cast.cast_rows(text)], ["丙"])

    def test_unsupported_complex_list_lines_warn_and_do_not_guess(self):
        text = """== 演員與角色 ==
* [[李玄均]]
: 首爾警察廳搜查部長。
* [[甲]]和[[乙]]飾演角色
* [[丙]]飾演角色一／角色二
* [[丁]]飾演角色甲，並協助角色乙。
* 黃金[[尋回犬]]飾演朴天才
"""
        rows, warnings = cast.parse(text)
        self.assertEqual(rows, [])
        self.assertEqual(len(warnings), 5)

    def test_role_alias_is_only_explicit_and_stays_with_adjacent_cast_item(self):
        text = """== 演員陣容 ==
* [[甲]]飾演角色甲
: 父親本名張三，背景資料。
* [[乙]]飾演角色乙
: 本名李四，其他資料。
* [[丙]]
: 本名王五，未知角色。
"""
        rows = cast.cast_rows(text)
        self.assertEqual(rows[0]["role"]["names"], ["角色甲"])
        self.assertEqual(rows[1]["role"]["names"], ["角色乙", "李四"])

    def test_korean_explicit_actor_role_markers_and_description(self):
        rows = cast.cast_rows("""== 등장인물 ==
=== 주요 인물 ===
* [[지성]] : 박해강 역 - 과거 조직의 우두머리
* [[하윤경]] : 강하리 역
== 줄거리 ==
* [[다른 배우]] : 다른 인물 역 - ignore
""", language="ko")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["actor"]["names"], ["지성"])
        self.assertEqual(rows[0]["character_name"], "박해강")
        self.assertEqual(rows[0]["character_name_cn"], "")

    def test_korean_no_colon_or_no_role_marker_is_rejected(self):
        rows, warnings = cast.parse("""== 등장인물 ==
* [[지성]] 박해강 역
* [[하윤경]] : 강하리 소개
* [[다른 배우]] : A/B 역 - invalid
""", language="ko")
        self.assertEqual(rows, [])
        self.assertEqual(len(warnings), 3)

    def test_explicit_korean_young_actor_preserves_qualifier(self):
        rows = cast.cast_rows("== 등장인물 ==\n* [[지성]] (아역: [[이종현 (배우)|이종현]]) : 박해강 역 - 설명", language="ko")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["qualifier"]["value"], "child")
        self.assertEqual(rows[1]["character_name"], "박해강")

    def test_parenthetical_english_original_is_not_replaced_with_korean_transliteration(self):
        for name in ("벤(Ben)", "벤 (Ben)"):
            rows = cast.cast_rows("== 등장인물 ==\n* [[나지 피라스]] : " + name + " 역 - 설명", language="ko")
            self.assertEqual(rows[0]["character_name"], "Ben")
            self.assertEqual(rows[0]["role"]["names"], ["벤", "Ben"])
            self.assertEqual(rows[0]["role"]["original_name"], "Ben")
            self.assertEqual(rows[0]["role"]["original_name_language"], "en")
            self.assertTrue(rows[0]["role"]["original_name_evidence"]["explicit"])

    def test_bullet_without_space_and_list_like_table_description_are_safe(self):
        text = "== 演員 ==\n*[[甲]]飾演角色甲\n{|\n|\n* [[乙]]飾演虛構角色\n|}"
        self.assertEqual([row["actor"]["names"][0] for row in cast.cast_rows(text)], ["甲"])


class CastTableTest(unittest.TestCase):
    def test_cached_superpower_small_child_actor_and_numeric_names_are_preserved(self):
        text = table('| [[崔允智]]<br>{{small|（童年：[[崔素律]]）}} || 石皓蘭2972 || 說明 || 無',
                     '| [[裴奈拏]] || 金八昊4885 || 說明 || 無',
                     '| [[徐宇真]] || 永遠之子1730 || 說明 || 無')
        rows, warnings = cast.parse(text)
        self.assertEqual(warnings, [])
        self.assertEqual(len(rows), 4)
        self.assertEqual([row['actor']['names'][0] for row in rows[:2]], ['崔允智', '崔素律'])
        self.assertEqual(rows[1]['qualifier']['value'], 'child')
        self.assertNotIn('qualifier', rows[0])
        self.assertEqual(rows[0]['role_group'], rows[1]['role_group'])
        self.assertEqual(rows[0]['role_group'], cast.parse(text)[0][0]['role_group'])
        self.assertEqual(rows[2]['character_name_cn'], '金八昊4885')
        self.assertEqual(rows[3]['character_name_cn'], '永遠之子1730')
        self.assertNotIn('role_group', rows[2])

    def test_unmarked_student_primary_is_not_adult_and_explicit_adult_branch_is_retained(self):
        rows = cast.cast_rows(table('| [[學生演員]]<br>{{small|（童年：[[童年演員]]）}} || 學生角色 || 說明 || 無',
                                    '| [[少年演員]]（成年：[[成年演員]]） || 另一角色 || 說明 || 無'))
        self.assertNotIn('qualifier', rows[0])
        self.assertEqual(rows[1]['qualifier']['value'], 'child')
        self.assertEqual(rows[0]['role_group'], rows[1]['role_group'])
        self.assertNotIn('qualifier', rows[2])
        self.assertEqual(rows[3]['qualifier']['value'], 'adult')
        self.assertEqual(rows[2]['role_group'], rows[3]['role_group'])

    def test_cached_superpower_slash_role_is_structured_unresolved_not_alias_or_split(self):
        unresolved = []
        rows, warnings = cast.parse(table('| [[車銀優]] || 李雲情／高垠山3972 || 說明 || 無'), unresolved=unresolved)
        self.assertEqual(rows, [])
        self.assertEqual(len(warnings), 1)
        self.assertEqual(unresolved[0]['actor']['links'], ['車銀優'])
        self.assertEqual(unresolved[0]['role_raw'], '李雲情／高垠山3972')
        self.assertEqual(unresolved[0]['role_name_candidates'], ['李雲情', '高垠山3972'])
        self.assertNotIn('role_group', unresolved[0])

    def test_known_korean_actress_disambiguator_does_not_drop_actor(self):
        rows = cast.cast_rows(table('| {{Lk|鄭妍|정연 (여배우)}} || 文美熙 || 說明 || 無'))
        self.assertEqual(rows[0]['actor']['native_names'], ['정연'])
        self.assertEqual(rows[0]['actor']['links'], [])

    def test_existing_table_columns_and_explicit_language(self):
        rows = cast.cast_rows(table('| [[河錫辰]] || 李宰仁<br>{{lang|ko|이재인}} ||align=left| 說明 || [[配音員]]'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["actor"]["links"], ["河錫辰"])
        self.assertEqual(rows[0]["character_name_cn"], "李宰仁")
        self.assertEqual(rows[0]["character_name"], "이재인")

    def test_bold_ordinary_cells_are_supported_headers(self):
        text = "{|\n| width=\"12%\" | '''演員'''\n| '''角色'''\n| '''介紹'''\n|-\n| [[河錫辰]] || 李宰仁 ||align=left| 說明\n|}"
        self.assertEqual(cast.cast_rows(text)[0]["character_name_cn"], "李宰仁")

    def test_korean_actor_and_character_headers(self):
        rows = cast.cast_rows("{|\n! 배우 !! 배역 !! 설명\n|-\n| [[지성]] || 박해강 || 설명\n|}", language="ko")
        self.assertEqual(rows[0]["character_name"], "박해강")

    def test_multirole_cells_rowspans_and_unknown_templates_are_not_guessed(self):
        for raw in (table('| [[河錫辰]] || 李宰仁<br>另一角色 || 說明 || 無'),
                    table('| rowspan="2" | [[河錫辰]] || 李宰仁 || 說明 || 無'),
                    table('| {{unknown|[[河錫辰]]}} || 李宰仁 || 說明 || 無')):
            with self.subTest(raw=raw):
                self.assertEqual(cast.cast_rows(raw), [])
                self.assertTrue(cast.diagnostics(raw))

    def test_text_rows_tables_and_language_are_bounded(self):
        with self.assertRaises(ValueError):
            cast.cast_rows("x", language="en")
        with patch.object(cast, "MAX_TEXT_LENGTH", 1), self.assertRaises(ValueError):
            cast.cast_rows("xx")
        with patch.object(cast, "MAX_ROWS", 0), self.assertRaises(ValueError):
            cast.cast_rows("== 演員 ==\n* [[甲]]飾演角色")
        with patch.object(cast, "MAX_TABLES", 0), self.assertRaises(ValueError):
            cast.cast_rows(table('| [[甲]] || 角色 || 說明 || 無'))

    def test_unrelated_ratings_table_spans_do_not_emit_cast_warnings(self):
        text = '{|\n! 集數 !! 日期\n|-\n| rowspan="2" | 1 || 2026年\n|}'
        self.assertEqual(cast.parse(text), ([], []))


if __name__ == "__main__":
    unittest.main()
