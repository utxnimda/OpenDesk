from __future__ import annotations

from copy import deepcopy
import io
import json
import re
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from catalog_library import wikipedia_source as wiki


def page_payload(**overrides):
    page = {"pageid": 123, "ns": 0, "title": "1%的可能性 (2016年電視劇)",
            "fullurl": "https://attacker.example/not-used", "extract": "一部韩国电视剧。",
            "varianttitles": {"zh-cn": "1%的可能性 (2016年电视剧)", "zh-tw": "1%的可能性 (2016年電視劇)"},
            "revisions": [{"revid": 987, "timestamp": "2026-10-06T00:00:00Z", "slots": {"main": {"content": """
{{Other|ignore=1}}
{{Infobox television
| show_name = 1%的可能性
| show_name_2 = {{lang|ko|1%의 어떤 것}}
| first_aired = {{start date|2016|10|5}}
| last_aired = 2016年11月24日<ref>source</ref>
| director = [[人物|导演]]
}}
"""}}}]}
    page.update(overrides)
    return {"query": {"pages": [page]}}


class FakeResponse(io.BytesIO):
    def __init__(self, value, headers=None):
        super().__init__(value if isinstance(value, bytes) else json.dumps(value).encode())
        self.headers = headers or {}


class WikipediaSearchTest(unittest.TestCase):
    def setUp(self):
        conversion = patch.object(wiki, "simplified_display", return_value=({}, None))
        conversion.start()
        self.addCleanup(conversion.stop)

    def test_search_uses_fixed_api_and_returns_encoded_entry_url(self):
        data = {"query": {"search": [{"pageid": 123, "ns": 0, "title": "1%的可能性 (2016年電視劇)"}],
                          "searchinfo": {"totalhits": 42}}}
        with patch.object(wiki, "_request_json", return_value=data) as request:
            result = wiki.search("1%的可能性", offset=20)
        self.assertEqual(result["total"], 42)
        self.assertEqual(result["offset"], 20)
        self.assertEqual(request.call_args.args[0]["srnamespace"], "0")
        self.assertEqual(request.call_args.args[0]["sroffset"], 20)
        self.assertIn("/wiki/1%25", result["results"][0]["source_url"])
        self.assertNotIn("%2525", result["results"][0]["source_url"])

    def test_url_input_decodes_once_and_resolves_redirects_via_api(self):
        url = wiki.article_url("1%的可能性 (2016年電視劇)") + "#演员"
        with patch.object(wiki, "_request_json", return_value=page_payload()) as request:
            result = wiki.search(url)
        params = request.call_args.args[0]
        self.assertEqual(params["titles"], "1%的可能性 (2016年電視劇)")
        self.assertEqual(params["redirects"], "1")
        self.assertEqual(result["results"][0]["id"], 123)

    def test_numeric_page_id_search(self):
        with patch.object(wiki, "_request_json", return_value=page_payload()) as request:
            wiki.search("123")
        self.assertEqual(request.call_args.args[0]["pageids"], "123")

    def test_arbitrary_hosts_paths_credentials_and_title_batches_rejected(self):
        values = ["https://evil.example/wiki/a", "http://zh.wikipedia.org/wiki/a",
                  "https://zh.wikipedia.org@evil.example/wiki/a", "https://zh.wikipedia.org:443/wiki/a",
                  "https://zh.wikipedia.org/w/api.php", "https://zh.wikipedia.org/wiki/A?redirect=evil",
                  "https://zh.wikipedia.org/wiki/A%7CB", "https://zh.wikipedia.org/wiki/A%00B",
                  "https://zh.wikipedia.org/wiki/%FF", "//zh.wikipedia.org/wiki/A", "file:///a", "\x00A"]
        with patch.object(wiki, "_request_json", side_effect=AssertionError("must not request")):
            for value in values:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    wiki.search(value)

    def test_invalid_response_entries_and_offsets_rejected(self):
        for offset in (-1, True, 1001):
            with self.assertRaises(ValueError):
                wiki.search("剧集", offset=offset)
        for row in ({"pageid": 1, "ns": 1, "title": "讨论"}, {"pageid": True, "ns": 0, "title": "x"}, None):
            with patch.object(wiki, "_request_json", return_value={"query": {"search": [row]}}):
                with self.assertRaises(ValueError):
                    wiki.search("剧集")


class WikipediaSnapshotTest(unittest.TestCase):
    def fetch(self, payload=None):
        with patch.object(wiki, "_request_json", return_value=deepcopy(payload or page_payload())), patch.object(wiki, "simplified_display", return_value=({}, None)):
            return wiki.fetch_snapshot(123)

    def test_exact_dates_names_summary_revision_and_raw_snapshot(self):
        raw = page_payload()
        result = self.fetch(raw)
        self.assertEqual(result["provider"], "wikipedia")
        self.assertEqual(result["subject_id"], 123)
        self.assertEqual(result["raw"], raw)
        self.assertEqual(result["candidates"]["name"], "1%的可能性")
        self.assertEqual(result["candidates"]["air_date_start"], "20161005")
        self.assertEqual(result["candidates"]["air_date_end"], "20161124")
        self.assertIn("1%의 어떤 것", result["candidates"]["metadata.aliases"])
        self.assertEqual(result["source_metadata"]["revision_id"], 987)
        self.assertEqual(result["source_metadata"]["revision_url"], "https://zh.wikipedia.org/w/index.php?oldid=987")
        self.assertEqual(result["source_metadata"]["license"], "CC BY-SA 4.0")
        self.assertNotIn("attacker", result["source_url"])
        self.assertNotIn("metadata.episodes", result["candidates"])
        self.assertNotIn("metadata.cover", result["candidates"])

    def test_optional_missing_and_invalid_data_does_not_propose_erasure(self):
        raw = page_payload(extract={"invalid": True}, varianttitles=["invalid"])
        raw["query"]["pages"][0]["revisions"][0]["slots"] = "invalid"
        result = self.fetch(raw)
        self.assertEqual(result["candidates"]["name"], raw["query"]["pages"][0]["title"])
        self.assertNotIn("metadata.summary", result["candidates"])
        self.assertNotIn("air_date_start", result["candidates"])
        self.assertEqual(len(result["warnings"]), 2)

    def test_multiple_broadcasts_and_unparsed_dates_not_guessed(self):
        raw = page_payload()
        raw["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"] = """
{{Infobox television
| first_aired = {{start date|2016|9|30}}（网络）<br>{{start date|2016|10|5}}（电视）
| last_aired = {{end date|2016}}
}}
"""
        result = self.fetch(raw)
        self.assertNotIn("air_date_start", result["candidates"])
        self.assertNotIn("air_date_end", result["candidates"])
        self.assertEqual(len(result["warnings"]), 2)

    def test_conflicting_duplicate_fields_rejected_even_if_individually_valid(self):
        warnings = []
        result = wiki._candidates({"title": "剧集", "extract": "简介"},
            "{{Infobox television|first_aired=2016-01-01|first_airdate=2016-02-01}}", warnings)
        self.assertNotIn("air_date_start", result)
        self.assertEqual(len(warnings), 1)

    def test_impossible_and_reverse_dates_rejected(self):
        self.assertEqual(wiki._date_value("2025-02-29"), "")
        self.assertEqual(wiki._date_value("{{start date|2024|2|29|df=yes}}"), "20240229")
        self.assertEqual(wiki._date_value("{{end date|2016|11|24|}}"), "20161124")
        self.assertEqual(wiki._date_value("2024年2月"), "")
        self.assertEqual(wiki._date_value("2024-01-01至2024-01-02"), "")
        warnings = []
        result = wiki._candidates({"title": "剧集", "extract": "简介"},
            "{{Infobox television|first_aired=2016-12-01|last_aired=2016-02-01}}", warnings)
        self.assertNotIn("air_date_start", result)
        self.assertNotIn("air_date_end", result)

    def test_live_article_original_name_and_multiple_broadcast_endings(self):
        warnings = []
        result = wiki._candidates({"title": "1%的可能性 (2016年電視劇)", "extract": "简介"}, """
{{Infobox television
| show_name = 1%的可能性
| original_name = {{lang|ko|1%의 어떤 것}}
| first_aired = {{Start date|2016|9|30}}
| last_aired = {{End date|2016||}}（玉米應用程式）<br />{{Start date|2016|10|5}}－{{End date|2016|11|24|}}（DramaX）
}}
""", warnings)
        self.assertIn("1%의 어떤 것", result["metadata.aliases"])
        self.assertEqual(result["air_date_start"], "20160930")
        self.assertNotIn("air_date_end", result)
        self.assertEqual(len(warnings), 1)

    def test_nested_markup_and_unknown_templates_are_not_guessed(self):
        fields = wiki._infobox("{{Hatnote|xx}}{{Infobox television|show_name=[[作品|作品名]]|show_name_2={{lang|ko|이름}}}}")
        self.assertEqual(fields["show_name"], ["[[作品|作品名]]"])
        self.assertEqual(wiki._plain_name(fields["show_name"][0]), "作品名")
        self.assertEqual(wiki._plain_name("{{unknown|not reliable}}"), "")
        self.assertEqual(wiki._infobox("{{" * 101 + "bad"), {})

    def test_missing_nonarticle_disambiguation_and_bad_revision_rejected(self):
        for changes in ({"missing": True}, {"ns": 6}, {"pageprops": {"disambiguation": ""}}, {"revisions": []}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.fetch(page_payload(**changes))

    def test_cover_refresh_not_supported(self):
        with patch.object(wiki, "_request_json", side_effect=AssertionError("no fetch")):
            with self.assertRaisesRegex(ValueError, "文字补充"):
                wiki.fetch_snapshot(123, cover_only=True)


class WikipediaNetworkTest(unittest.TestCase):
    def test_only_explicit_zh_and_ko_api_origins_are_allowed(self):
        for language, host in (("zh", "zh.wikipedia.org"), ("ko", "ko.wikipedia.org")):
            with self.subTest(language=language), patch.object(wiki, "build_opener") as build:
                build.return_value.open.return_value = FakeResponse({"query": {}})
                wiki._request_json({"titles": "확인된 작품"}, language=language)
                request = build.return_value.open.call_args.args[0]
                self.assertEqual(urlsplit(request.full_url).netloc, host)
                self.assertEqual(urlsplit(request.full_url).path, "/w/api.php")
                self.assertEqual(request.get_method(), "GET")
                self.assertIsInstance(build.call_args.args[0], wiki._NoRedirects)
        with patch.object(wiki, "build_opener") as build:
            for language in ("en", "zh-CN", "ko.wikipedia.org", "https://evil.test", None, []):
                with self.subTest(language=language), self.assertRaises(ValueError):
                    wiki._request_json({}, language=language)
            build.assert_not_called()

    def test_fixed_get_with_useragent_and_default_proxy_aware_opener(self):
        with patch.object(wiki, "build_opener") as build:
            build.return_value.open.return_value = FakeResponse({"query": {}})
            self.assertEqual(wiki._request_json({"action": "delete", "titles": "A"}), {"query": {}})
        request = build.return_value.open.call_args.args[0]
        parsed = urlsplit(request.full_url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "zh.wikipedia.org")
        self.assertEqual(parsed.path, "/w/api.php")
        self.assertEqual(parse_qs(parsed.query)["action"], ["query"])
        self.assertIn("Nimda", request.get_header("User-agent"))
        self.assertIsInstance(build.call_args.args[0], wiki._NoRedirects)

    def test_redirect_rejected(self):
        with self.assertRaisesRegex(ValueError, "重定向"):
            wiki._NoRedirects().redirect_request(None, None, 302, "Found", {}, "https://evil.example")

    def test_malformed_oversized_truncated_json_and_api_errors(self):
        cases = [FakeResponse(b"not JSON"), FakeResponse(b'{"value": NaN}'), FakeResponse([]),
                 FakeResponse({"error": {"code": "maxlag"}}),
                 FakeResponse({}, {"Content-Length": str(wiki.MAX_RESPONSE_BYTES + 1)}),
                 FakeResponse({}, {"Content-Length": "200"}),
                 FakeResponse(b"x" * (wiki.MAX_RESPONSE_BYTES + 1))]
        for response in cases:
            with patch.object(wiki, "build_opener") as build:
                build.return_value.open.return_value = response
                with self.assertRaises(ValueError):
                    wiki._request_json({"titles": "A"})

    def test_timeout_and_http_errors_safe_messages(self):
        with patch.object(wiki, "build_opener") as build:
            build.return_value.open.side_effect = URLError("proxy down")
            with self.assertRaisesRegex(ValueError, "代理"):
                wiki._request_json({"titles": "A"})
            build.return_value.open.side_effect = HTTPError(wiki.API_ROOT, 429, "rate limit", {}, None)
            with self.assertRaisesRegex(ValueError, "HTTP 429"):
                wiki._request_json({"titles": "A"})
        with self.assertRaisesRegex(ValueError, "总时限"):
            wiki._request_json({"titles": "A"}, deadline=0)


class WikipediaSimplifiedDisplayTest(unittest.TestCase):
    def parse_response(self, query, **kwargs):
        self.assertEqual(kwargs.get("action"), "parse")
        # Fixture models the server conversion; production never uses a local
        # character replacement table.
        rendered = re.sub(r'(<div id="nimda-(hans|hant)-[0-9]+">)(.*?)(</div>)',
            lambda row: row[1] + (row[3]
                .replace("电视剧", "電視劇").replace("河锡辰", "河錫辰").replace("韩国", "韓國") if row[2] == "hant" else
                row[3].replace("電視劇", "电视剧").replace("河錫辰", "河锡辰").replace("韓國", "韩国")) + row[4],
            query["text"], flags=re.S)
        return {"parse": {"text": '<div lang="zh-Hans">' + rendered + '</div>'}}

    def test_conversion_is_inert_batched_and_retains_korean_and_punctuation(self):
        original = "電視劇 & [[link]] {{template}} <script>\n하석진 %"
        with patch.object(wiki, "_request_json", side_effect=self.parse_response) as request:
            mapping, evidence = wiki.simplified_display([original, original, "하석진"], deadline=500)
        self.assertEqual(mapping[original], original.replace("電視劇", "电视剧"))
        self.assertEqual(len(request.call_args_list), 2)
        submitted = request.call_args_list[0].args[0]["text"]
        self.assertNotIn("[[", submitted)
        self.assertNotIn("{{", submitted)
        self.assertNotIn("<script>", submitted)
        self.assertEqual(evidence["input"], [original])
        self.assertEqual(evidence["variant"], "zh-hans")
        self.assertIn("raw", evidence)
        self.assertEqual(evidence["original_languages"][original], "zh-Hant")

    def test_original_script_is_derived_from_two_actual_conversions(self):
        with patch.object(wiki, "_request_json", side_effect=self.parse_response):
            mapping, evidence = wiki.simplified_display(["韓國電視劇", "韩国电视剧", "韓國电视剧"])
        self.assertEqual(evidence["original_languages"], {
            "韓國電視劇": "zh-Hant", "韩国电视剧": "zh-Hans", "韓國电视剧": "und"})
        localized = {}
        wiki.add_localized_display(localized, "summary", "韓國電視劇", mapping, evidence)
        self.assertEqual(localized["summary"], {"zh-Hant": "韓國電視劇", "zh-Hans": "韩国电视剧"})

    def test_failed_conversion_never_labels_raw_text_as_simplified(self):
        localized = {}
        wiki.add_localized_display(localized, "name", "河錫辰", {}, None, {"zh-hant": "河錫辰"})
        self.assertEqual(localized["name"], {"zh-Hant": "河錫辰"})
        self.assertEqual(wiki.display_language("韓國演員", {}), "")

    def test_traditional_verification_failure_does_not_discard_simplified(self):
        def request(query, **kwargs):
            if kwargs.get("variant") == "zh-hant":
                raise ValueError("timeout")
            return self.parse_response(query, **kwargs)
        warnings = []
        with patch.object(wiki, "_request_json", side_effect=request):
            mapping, evidence = wiki.simplified_display(["韓國"], warnings=warnings)
        self.assertEqual(mapping, {"韓國": "韩国"})
        self.assertEqual(evidence["original_languages"], {"韓國": "und"})
        self.assertTrue(warnings)

    def test_missing_fields_duplicate_fields_markup_and_unavailable_conversion_fail_open(self):
        bad = [None, "", '<div id="nimda-hans-0"><img src="remote"/></div>',
               '<div id="nimda-hans-0">字</div><div id="nimda-hans-0">字</div>']
        for html in bad:
            warnings = []
            with patch.object(wiki, "_request_json", return_value={"parse": {"text": html}}):
                self.assertEqual(wiki.simplified_display(["電視劇"], warnings=warnings), ({}, None))
            self.assertTrue(warnings)
        with patch.object(wiki, "_request_json", side_effect=ValueError("offline")):
            self.assertEqual(wiki.simplified_display(["電視劇"]), ({}, None))
        with patch.object(wiki, "_request_json") as request:
            self.assertEqual(wiki.simplified_display(["한글", "ASCII"]), ({}, None))
            self.assertEqual(wiki.simplified_display(["字" * 120001]), ({}, None))
            request.assert_not_called()

    def test_transport_selects_content_variant_and_only_readonly_parse_uses_post(self):
        with patch.object(wiki, "build_opener") as build:
            build.return_value.open.side_effect = [FakeResponse({}), FakeResponse({}), FakeResponse({})]
            wiki._request_json({"titles": "電視劇"})
            query_request = build.return_value.open.call_args.args[0]
            params = parse_qs(urlsplit(query_request.full_url).query)
            self.assertEqual(params["variant"], ["zh-hans"])
            self.assertEqual(params["uselang"], ["zh-hans"])
            wiki._request_json({"text": "電視劇", "contentmodel": "wikitext"}, action="parse")
            parse_request = build.return_value.open.call_args.args[0]
            self.assertEqual(parse_request.full_url, wiki.API_ROOT)
            self.assertEqual(parse_request.get_method(), "POST")
            self.assertEqual(parse_qs(parse_request.data.decode())["action"], ["parse"])
            wiki._request_json({"titles": "한글"}, language="ko")
            self.assertNotIn("variant", parse_qs(urlsplit(build.return_value.open.call_args.args[0].full_url).query))
            with self.assertRaises(ValueError):
                wiki._request_json({}, action="edit")

    def test_traditional_verification_transport_uses_explicit_safe_variant(self):
        with patch.object(wiki, "build_opener") as build:
            build.return_value.open.return_value = FakeResponse({})
            wiki._request_json({"text": "演员"}, action="parse", variant="zh-hant")
            params = parse_qs(build.return_value.open.call_args.args[0].data.decode())
            self.assertEqual(params["variant"], ["zh-hant"])
            self.assertEqual(params["uselang"], ["zh-hant"])
        for variant in ("zh", "en", "https://evil.test", [], None):
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                wiki._request_json({}, action="parse", variant=variant)

    def test_safe_simplified_urls_resolve_original_title_without_rebinding_identity(self):
        for url in ("https://zh.wikipedia.org/zh-hans/1%25的可能性", "https://zh.wikipedia.org/wiki/1%25的可能性?variant=zh-hans#角色"):
            self.assertEqual(wiki._search_target(url), ("titles", "1%的可能性"))
        for url in ("https://evil.test/zh-hans/A", "https://zh.wikipedia.org/zh-hans/A?variant=zh-hant", "https://zh.wikipedia.org/wiki/A?variant=zh-hans&redirect=evil", "https://zh.wikipedia.org/wiki/A?variant=zh-hans&variant=zh-hans"):
            with self.assertRaises(ValueError):
                wiki._search_target(url)

    def test_search_display_is_simplified_but_title_and_link_are_canonical(self):
        def request(query, **kwargs):
            if kwargs.get("action") == "parse":
                return self.parse_response(query, **kwargs)
            return {"query": {"search": [{"pageid": 123, "ns": 0, "title": "電視劇"}], "searchinfo": {"totalhits": 1}}}
        with patch.object(wiki, "_request_json", side_effect=request):
            result = wiki.search("電視劇")
        row = result["results"][0]
        self.assertEqual((row["id"], row["name"], row["name_cn"], row["title"]), (123, "电视剧", "电视剧", "電視劇"))
        self.assertEqual(row["source_url"], wiki.article_url("電視劇"))

    def test_work_values_are_simplified_and_raw_revision_remains_exact(self):
        raw = page_payload(title="電視劇", extract="韓國電視劇。")
        raw["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"] = "{{Infobox television|show_name=電視劇|show_name_2={{lang|ko|한국 드라마}}}}"
        def request(query, **kwargs):
            return self.parse_response(query, **kwargs) if kwargs.get("action") == "parse" else deepcopy(raw)
        with patch.object(wiki, "_request_json", side_effect=request):
            result = wiki.fetch_snapshot(123)
        self.assertEqual(result["candidates"]["name"], "电视剧")
        self.assertEqual(result["candidates"]["metadata.summary"], "韩国电视剧。")
        self.assertIn("한국 드라마", result["candidates"]["metadata.aliases"])
        self.assertEqual(result["raw"], raw)
        self.assertEqual(result["source_metadata"]["page_title"], "電視劇")
        self.assertEqual(result["source_metadata"]["revision_id"], 987)
        self.assertEqual(result["source_url"], wiki.article_url("電視劇"))


if __name__ == "__main__":
    unittest.main()
