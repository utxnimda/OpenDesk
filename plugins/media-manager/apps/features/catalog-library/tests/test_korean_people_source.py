from copy import deepcopy
import unittest
from unittest.mock import patch

from catalog_library import korean_people_source as source
from catalog_library import wikipedia_source as wiki


def response(pageid=200, title="찬란한 너의 계절에", item="Q123456", text="== 등장 인물 ==\n* 이성경 : 송하란 역", **fields):
    return {"query": {"pages": [{"pageid": pageid, "ns": 0, "title": title,
        "pageprops": {"wikibase_item": item},
        "revisions": [{"revid": 900, "slots": {"main": {"content": text}}}], **fields}]}}


def chinese_article():
    return {"pageid": 100, "title": "在你燦爛的季節", "revid": 900,
            "raw": response(pageid=100, title="在你燦爛的季節")}


def discovery(**fields):
    return response(pageid=100, title="在你燦爛的季節", langlinks=[{"lang": "ko", "title": "찬란한 너의 계절에"}], **fields)


class KoreanPeopleSourceTest(unittest.TestCase):
    def test_exact_link_and_shared_wikidata_item_keep_complete_offline_evidence(self):
        zh, links, ko = chinese_article(), discovery(), response()
        before = deepcopy(zh)
        with patch.object(wiki, "_request_json", side_effect=[links, ko]) as request:
            result = source.fetch_for_work(zh)
        self.assertEqual(zh, before)
        self.assertEqual(len(result), 1)
        article = result[0]
        self.assertEqual(article["language"], "ko")
        self.assertEqual(article["raw"], ko)
        self.assertEqual(article["work_identity"]["language_link_response"], links)
        self.assertEqual(article["work_identity"]["wikidata_item"], "Q123456")
        self.assertEqual(article["work_identity"]["chinese_pageid"], 100)
        self.assertEqual(article["work_identity"]["chinese_revid"], 900)
        self.assertEqual(article["revision_url"], "https://ko.wikipedia.org/w/index.php?oldid=900")
        self.assertTrue(article["url"].startswith("https://ko.wikipedia.org/wiki/"))
        self.assertEqual(article["license"], "CC BY-SA 4.0")
        self.assertIn("贡献者", article["attribution"])
        self.assertEqual(request.call_args_list[0].args[0]["pageids"], 100)
        self.assertEqual(request.call_args_list[0].kwargs["language"], "zh")
        self.assertEqual(request.call_args_list[1].args[0]["titles"], "찬란한 너의 계절에")
        self.assertEqual(request.call_args_list[1].kwargs["language"], "ko")
        self.assertEqual(request.call_args_list[0].kwargs["deadline"], request.call_args_list[1].kwargs["deadline"])
        self.assertTrue(all("list" not in call.args[0] for call in request.call_args_list))
        article["raw"]["query"]["pages"][0]["title"] = "modified local copy"
        self.assertEqual(ko["query"]["pages"][0]["title"], "찬란한 너의 계절에")

    def test_missing_language_link_does_not_search_or_request_korean(self):
        links = discovery()
        links["query"]["pages"][0].pop("langlinks")
        warnings = []
        with patch.object(wiki, "_request_json", return_value=links) as request:
            self.assertEqual(source.fetch_for_work(chinese_article(), warnings=warnings), [])
        request.assert_called_once()
        self.assertTrue(any("没有韩文语言链接" in warning for warning in warnings))

    def test_legacy_language_link_star_title_is_supported(self):
        links = discovery()
        links["query"]["pages"][0]["langlinks"] = [{"lang": "ko", "*": "찬란한 너의 계절에"}]
        with patch.object(wiki, "_request_json", side_effect=[links, response()]):
            self.assertEqual(len(source.fetch_for_work(chinese_article())), 1)

    def test_link_url_is_not_fetched_and_cannot_change_fixed_origin(self):
        links = discovery()
        links["query"]["pages"][0]["langlinks"][0]["url"] = "https://attacker.invalid/steal"
        with patch.object(wiki, "_request_json", side_effect=[links, response()]) as request:
            result = source.fetch_for_work(chinese_article())
        self.assertEqual(len(result), 1)
        self.assertNotIn("url", request.call_args_list[1].args[0])
        self.assertEqual(request.call_args_list[1].kwargs["language"], "ko")

    def test_missing_conflicting_or_ambiguous_identity_is_nonfatal(self):
        for changed in ({"pageprops": {}}, {"pageprops": {"wikibase_item": "Q999"}},
                        {"pageprops": {"wikibase_item": "Q123456", "disambiguation": ""}},
                        {"ns": 1}, {"missing": True}, {"revisions": []}):
            raw = response(**changed)
            warnings = []
            with self.subTest(changed=changed), patch.object(wiki, "_request_json", side_effect=[discovery(), raw]):
                self.assertEqual(source.fetch_for_work(chinese_article(), warnings=warnings), [])
                self.assertTrue(warnings)

    def test_discovery_must_preserve_bound_pageid_and_single_korean_link(self):
        variants = [response(pageid=101), discovery(pageprops={}), discovery(pageprops={"wikibase_item": "invalid"})]
        for link_value in ([{"lang": "en", "title": "English"}], [{"lang": "ko", "title": "A"}, {"lang": "ko", "title": "B"}], None):
            variant = discovery()
            variant["query"]["pages"][0]["langlinks"] = link_value
            variants.append(variant)
        for value in variants:
            with self.subTest(value=value), patch.object(wiki, "_request_json", return_value=value) as request:
                self.assertEqual(source.fetch_for_work(chinese_article()), [])
                request.assert_called_once()

    def test_unsafe_titles_are_rejected_before_korean_request(self):
        for title in ("https://evil.test", "A|B", "../A#B", "Template:A", "A\\B", "A\x00B", "A" * 301, ""):
            links = discovery()
            links["query"]["pages"][0]["langlinks"][0]["title"] = title
            with self.subTest(title=title), patch.object(wiki, "_request_json", return_value=links) as request:
                self.assertEqual(source.fetch_for_work(chinese_article()), [])
                request.assert_called_once()

    def test_redirect_must_be_explicit_and_shared_identity_is_still_required(self):
        target = response(title="정식 작품 이름")
        with patch.object(wiki, "_request_json", side_effect=[discovery(), target]):
            self.assertEqual(source.fetch_for_work(chinese_article()), [])
        target["query"]["redirects"] = [{"from": "찬란한 너의 계절에", "to": "정식 작품 이름"}]
        with patch.object(wiki, "_request_json", side_effect=[discovery(), target]):
            self.assertEqual(source.fetch_for_work(chinese_article())[0]["title"], "정식 작품 이름")
        target["query"]["pages"][0]["pageprops"]["wikibase_item"] = "Q999"
        with patch.object(wiki, "_request_json", side_effect=[discovery(), target]):
            self.assertEqual(source.fetch_for_work(chinese_article()), [])

    def test_nonfatal_network_failure_and_expired_deadline(self):
        warnings = []
        with patch.object(wiki, "_request_json", side_effect=ValueError("network timed out")) as request:
            self.assertEqual(source.fetch_for_work(chinese_article(), warnings=warnings), [])
            request.assert_called_once()
        self.assertTrue(any("network timed out" in warning for warning in warnings))
        with patch.object(wiki, "_request_json") as request:
            self.assertEqual(source.fetch_for_work(chinese_article(), deadline=0), [])
            request.assert_not_called()

    def test_bad_bound_article_never_starts_network(self):
        variants = [None, {}, {**chinese_article(), "raw": None}, {**chinese_article(), "pageid": 999},
                    {**chinese_article(), "revid": 999}, {**chinese_article(), "language": "ko"}]
        for value in variants:
            with self.subTest(value=value), patch.object(wiki, "_request_json") as request:
                self.assertEqual(source.fetch_for_work(value), [])
                request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
