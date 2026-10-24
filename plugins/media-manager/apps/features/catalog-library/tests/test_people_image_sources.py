from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from catalog_library import assets, people_assets, people_image_sources as source, providers


BG_IMAGE = "https://lain.bgm.tv/pic/crt/m/12/34/1234_Abc.jpg"
WM_IMAGE = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Actor.jpg"
WM_THUMB = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Actor.jpg/800px-Actor.jpg"


def bitmap():
    stream = io.BytesIO()
    with Image.new("RGB", (3, 4), (70, 50, 20)) as image:
        image.save(stream, format="PNG")
    return stream.getvalue()


def page(row):
    return {"query": {"pages": [row]}}


def file_response(**changes):
    value = {"pageid": 22, "ns": 6, "title": "File:Actor.jpg", "imageinfo": [{
        "url": WM_IMAGE, "thumburl": WM_THUMB, "mime": "image/jpeg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Actor.jpg",
        "extmetadata": {"Artist": {"value": '<a href="https://example.invalid">Photo &amp; Artist</a>'},
                        "LicenseShortName": {"value": "CC BY-SA 4.0"},
                        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"},
                        "ImageDescription": {"value": "<div>Role photo<br>portrait</div><script>evil()</script>"}}}]}
    value.update(changes)
    return page(value)


class PeopleImageSourcesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "people"
        self.data = bitmap()

    def fetch(self, provider="commons", value="File:Actor.jpg", *, entity_type="character", responses=None, **kwargs):
        responses = responses if responses is not None else [file_response()]
        with patch.object(source, "_request_json", side_effect=responses) as request, \
             patch.object(assets, "download_image", return_value=(self.data, "image/png")) as download:
            result = source.fetch_image(provider, value, entity_type=entity_type, people_root=self.root, **kwargs)
        return result, request, download

    def test_wikimedia_allowlist_accepts_real_original_and_thumbnail_shapes(self):
        for value in (WM_IMAGE, WM_THUMB, WM_IMAGE.replace("commons", "zh"),
                      WM_THUMB.replace("upload.wikimedia.org", "thumb.wikimedia.org"),
                      WM_THUMB.replace("Actor.jpg", "Actor.svg").replace("800px-Actor.svg", "800px-Actor.svg.png"),
                      WM_IMAGE.replace("Actor.jpg", "%E8%A7%92%E8%89%B2.jpg"), WM_IMAGE.replace(".org/", ".org:443/")):
            with self.subTest(value=value):
                self.assertEqual(source.validate_wikimedia_url(value), value)

    def test_unsafe_wikimedia_addresses_rejected_before_download(self):
        for value in (WM_IMAGE.replace("https:", "http:"), WM_IMAGE.replace("upload.wikimedia.org", "127.0.0.1"),
                      WM_IMAGE.replace("upload.wikimedia.org", "upload.wikimedia.org.evil.invalid"),
                      WM_IMAGE.replace("upload.wikimedia.org", "upload.wikimedia.org."),
                      WM_IMAGE.replace(".org/", ".org:8080/"), WM_IMAGE.replace("://", "://user:pass@"),
                      WM_IMAGE + "?url=https://127.0.0.1", WM_IMAGE + "#x", WM_IMAGE.replace("Actor.jpg", "../../Actor.jpg"),
                      WM_IMAGE.replace("Actor.jpg", "%2fsecret.jpg"), WM_IMAGE.replace("Actor.jpg", "%252fsecret.jpg"),
                      WM_IMAGE.replace("Actor.jpg", "bad%xx.jpg"), WM_IMAGE.replace("Actor.jpg", "file.svg"),
                      WM_IMAGE.replace("Actor.jpg", "file.pdf"), WM_IMAGE.replace("/a/ab/", "/b/ab/"),
                      WM_IMAGE.replace("commons", "en"), WM_IMAGE.replace("upload.wikimedia.org", "thumb.wikimedia.org"),
                      WM_THUMB.replace("upload.wikimedia.org", "thumb.wikimedia.org.evil.invalid"), "file:///C:/secret.png"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                source.validate_wikimedia_url(value)

    def test_bangumi_character_and_person_download_explicit_endpoint(self):
        for entity_type, value in (("character", "https://bangumi.tv/character/123"), ("person", "123")):
            with self.subTest(entity_type=entity_type), \
                 patch.object(providers, "_request_json", return_value={"id": 123, "name": "Name", "images": {"medium": BG_IMAGE}}) as request, \
                 patch.object(assets, "download_image", return_value=(self.data, "image/png")):
                result = source.fetch_image("bangumi", value, entity_type=entity_type, people_root=self.root)
                self.assertEqual(request.call_args.args[0], "/v0/" + ("characters" if entity_type == "character" else "persons") + "/123")
                self.assertEqual(result["avatar"]["source_url"], BG_IMAGE)
                self.assertEqual(result["evidence"]["entity_type"], entity_type)
                self.assertNotIn("source_refs", result)
        self.assertFalse((self.root / "db").exists())

    def test_bangumi_rejects_wrong_kind_id_and_host_before_request(self):
        for value in ("https://bangumi.tv/person/123", "https://evil.invalid/character/123", "0", True,
                      "https://bangumi.tv/character/123?next=1", "http://bangumi.tv/character/123"):
            with self.subTest(value=value), patch.object(providers, "_request_json") as request, self.assertRaises(ValueError):
                source.fetch_image("bangumi", value, entity_type="character", people_root=self.root)
            request.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_bangumi_mismatched_result_missing_image_and_bad_cdn_fail(self):
        for raw in ({"id": 456, "images": {"medium": BG_IMAGE}}, {"id": 123, "images": {}},
                    {"id": 123, "images": {"medium": "https://evil.invalid/picture.jpg"}}):
            with self.subTest(raw=raw), patch.object(providers, "_request_json", return_value=raw), \
                 patch.object(assets, "download_image") as download, self.assertRaises(ValueError):
                source.fetch_image("bangumi", "123", entity_type="character", people_root=self.root)
            download.assert_not_called()

    def test_wikipedia_article_uses_pageimage_then_file_metadata(self):
        article = page({"pageid": 123, "ns": 0, "title": "角色", "pageimage": "Actor.jpg"})
        result, request, _ = self.fetch("wikipedia", "https://zh.wikipedia.org/zh-hans/%E8%A7%92%E8%89%B2", responses=[article, file_response()])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[1]["titles"], "角色")
        self.assertEqual(request.call_args_list[1].args[1]["titles"], "File:Actor.jpg")
        self.assertEqual(result["evidence"]["external_id"], "123")
        self.assertEqual(result["evidence"]["title"], "角色")
        self.assertEqual(result["evidence"]["file_title"], "File:Actor.jpg")
        self.assertIn("不证明", result["warnings"][0])

    def test_wikipedia_numeric_file_and_commons_links_are_supported(self):
        cases = (("wikipedia", "123", {"pageid": 123, "ns": 6, "title": "File:Actor.jpg"}),
                 ("wikipedia", "文件:Actor.jpg", None),
                 ("commons", "https://commons.wikimedia.org/wiki/File:Actor.jpg", None),
                 ("commons", "https://commons.wikimedia.org/w/index.php?title=File%3AActor.jpg", None))
        for provider, value, initial in cases:
            with self.subTest(value=value):
                responses = [page(initial), file_response()] if initial else [file_response()]
                result, _, _ = self.fetch(provider, value, responses=responses)
                self.assertEqual(result["avatar"]["source_url"], WM_THUMB)

    def test_numeric_article_link_stays_title_not_different_page_id(self):
        for value in ("https://zh.wikipedia.org/wiki/1984", "https://zh.wikipedia.org/w/index.php?title=1984"):
            with self.subTest(value=value):
                self.assertEqual(source._wiki_target("wikipedia", value), {"titles": "1984"})
        self.assertEqual(source._wiki_target("wikipedia", "1984"), {"pageids": "1984"})
        self.assertEqual(source._wiki_target("wikipedia", "https://zh.wikipedia.org/w/index.php?curid=1984"), {"pageids": "1984"})
        with self.assertRaises(ValueError):
            source._wiki_target("wikipedia", "https://zh.wikipedia.org/w/index.php?curid=other")

    def test_shared_commons_file_without_local_pageid_is_allowed(self):
        result, _, _ = self.fetch("wikipedia", "File:Actor.jpg", responses=[file_response(pageid=-1, missing=True)])
        self.assertEqual(result["evidence"]["page_url"], "https://commons.wikimedia.org/wiki/File:Actor.jpg")
        self.assertEqual(result["evidence"]["external_id"], "File:Actor.jpg")
        self.assertEqual(result["avatar"]["source_url"], WM_THUMB)

    def test_wiki_source_rejects_cross_platform_arbitrary_url_multiple_titles(self):
        for provider, value in (("commons", "Actor"), ("commons", "123"), ("wikipedia", "One|Two"),
                                ("wikipedia", "https://commons.wikimedia.org/wiki/File:Actor.jpg"),
                                ("commons", "https://upload.wikimedia.org/wikipedia/commons/a/ab/Actor.jpg"),
                                ("wikipedia", "https://zh.wikipedia.org/w/api.php?titles=Actor"),
                                ("wikipedia", "https://zh.wikipedia.org/w/index.php?title=Actor&title=Other"),
                                ("wikipedia", "https://zh.wikipedia.org/wiki/Actor?redirect=https://127.0.0.1")):
            with self.subTest(value=value), patch.object(source, "_request_json") as request, self.assertRaises(ValueError):
                source.fetch_image(provider, value, entity_type="character", people_root=self.root)
            request.assert_not_called()

    def test_metadata_is_plain_text_and_official_description_url_only(self):
        raw = file_response()
        raw["query"]["pages"][0]["imageinfo"][0]["descriptionurl"] = "https://evil.invalid/tracking"
        result, _, _ = self.fetch(responses=[raw])
        evidence = result["evidence"]
        self.assertEqual(evidence["author"], "Photo & Artist")
        self.assertEqual(evidence["description"], "Role photo portrait")
        self.assertEqual(evidence["license"], "CC BY-SA 4.0")
        self.assertEqual(evidence["description_url"], "https://commons.wikimedia.org/wiki/File:Actor.jpg")
        self.assertNotIn("<", json.dumps(evidence))

    def test_missing_license_keeps_candidate_and_warns(self):
        raw = file_response()
        raw["query"]["pages"][0]["imageinfo"][0]["extmetadata"] = {}
        result, _, _ = self.fetch(responses=[raw])
        self.assertEqual(result["evidence"]["license"], "")
        self.assertTrue(any("许可" in row for row in result["warnings"]))

    def test_missing_article_picture_and_malformed_results_do_not_download(self):
        for raw in (page({"pageid": 12, "ns": 0, "title": "角色"}), page({"ns": 0, "title": "角色", "missing": True}),
                    {"query": {"pages": []}}, {"query": {"pages": [{}, {}]}}):
            with self.subTest(raw=raw), patch.object(source, "_request_json", return_value=raw), \
                 patch.object(assets, "download_image") as download, self.assertRaises(ValueError):
                source.fetch_image("wikipedia", "角色", entity_type="character", people_root=self.root)
            download.assert_not_called()

    def test_imageinfo_external_or_original_svg_url_is_never_downloaded(self):
        for value in ("https://evil.invalid/x.jpg", WM_IMAGE.replace(".jpg", ".svg")):
            raw = file_response()
            raw["query"]["pages"][0]["imageinfo"][0]["thumburl"] = value
            with self.subTest(value=value), patch.object(source, "_request_json", return_value=raw), \
                 patch.object(assets, "download_image") as download, self.assertRaises(ValueError):
                source.fetch_image("commons", "File:Actor.jpg", entity_type="person", people_root=self.root)
            download.assert_not_called()

    def test_real_mediawiki_thumbnail_host_and_analytics_tuple_are_normalized(self):
        clean = "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ac/Shin_Hye-sun_in_April_2024.png/960px-Shin_Hye-sun_in_April_2024.png"
        raw = file_response()
        raw["query"]["pages"][0]["imageinfo"][0]["thumburl"] = clean + "?utm_source=zh.wikipedia.org&utm_campaign=imageinfo&utm_content=thumbnail"
        result, _, download = self.fetch("wikipedia", "File:Actor.jpg", responses=[raw])
        self.assertEqual(result["avatar"]["source_url"], clean)
        self.assertEqual(result["evidence"]["source_url"], clean)
        self.assertEqual(download.call_args.args[0], clean)
        self.assertEqual(source.validate_source_url(clean), clean)
        # This API-only cleanup cannot turn arbitrary user queries into allowed URLs.
        with self.assertRaises(ValueError):
            source.validate_source_url(clean + "?utm_source=zh.wikipedia.org&utm_campaign=imageinfo&utm_content=thumbnail")

    def test_original_api_analytics_tuple_can_be_removed_without_thumbnail(self):
        raw = file_response()
        info = raw["query"]["pages"][0]["imageinfo"][0]
        info.pop("thumburl")
        info["url"] = WM_IMAGE + "?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original"
        result, _, download = self.fetch(responses=[raw])
        self.assertEqual(result["avatar"]["source_url"], WM_IMAGE)
        self.assertEqual(download.call_args.args[0], WM_IMAGE)

    def test_unexpected_api_image_queries_are_rejected_before_download(self):
        allowed = "utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=thumbnail"
        for query in (allowed + "&next=https://127.0.0.1", allowed + "&utm_source=commons.wikimedia.org",
                      allowed.replace("imageinfo", "other"), allowed.replace("thumbnail", "original"),
                      allowed.replace("commons.wikimedia.org", "evil.invalid"), "utm_campaign=imageinfo", "download=1"):
            raw = file_response()
            raw["query"]["pages"][0]["imageinfo"][0]["thumburl"] = WM_THUMB + "?" + query
            with self.subTest(query=query), patch.object(source, "_request_json", return_value=raw), \
                 patch.object(assets, "download_image") as download, self.assertRaises(ValueError):
                source.fetch_image("commons", "File:Actor.jpg", entity_type="person", people_root=self.root)
            download.assert_not_called()

    def test_local_cache_content_hash_and_no_entity_writes(self):
        result, _, _ = self.fetch()
        digest = hashlib.sha256(self.data).hexdigest()
        self.assertEqual(result["avatar"]["asset_id"], digest + ".png")
        self.assertEqual((self.root / "assets" / (digest + ".png")).read_bytes(), self.data)
        self.assertEqual([p.name for p in self.root.iterdir()], ["assets"])

    def test_previous_valid_local_image_is_reused_and_not_mutated(self):
        result, _, _ = self.fetch()
        previous = deepcopy(result["avatar"])
        again, _, download = self.fetch(previous=previous)
        download.assert_not_called()
        self.assertEqual(again["avatar"], previous)
        self.assertIsNot(again["avatar"], previous)

    def test_invalid_image_bytes_never_stored_and_previous_unchanged(self):
        previous = {"asset_id": "a" * 64 + ".png", "source_url": WM_IMAGE}
        old = deepcopy(previous)
        with patch.object(source, "_request_json", return_value=file_response()), \
             patch.object(assets, "download_image", return_value=(b"<svg></svg>", "image/png")), self.assertRaises(ValueError):
            source.fetch_image("commons", "File:Actor.jpg", entity_type="character", people_root=self.root, previous=previous)
        self.assertEqual(previous, old)
        self.assertFalse(self.root.exists())

    def test_linked_people_root_and_expired_deadline_fail_before_network(self):
        with patch.object(assets, "_is_link", side_effect=lambda p: p == self.root), \
             patch.object(source, "_request_json") as request, self.assertRaises(ValueError):
            source.fetch_image("commons", "File:Actor.jpg", entity_type="character", people_root=self.root)
        request.assert_not_called()
        with patch.object(source.time, "monotonic", return_value=100), \
             patch.object(source, "_request_json") as request, self.assertRaises(ValueError):
            source.fetch_image("commons", "File:Actor.jpg", entity_type="character", people_root=self.root, deadline=99)
        request.assert_not_called()

    def test_api_transport_fixed_origin_bounded_proxy_and_no_redirects(self):
        content = json.dumps(file_response()).encode()
        response = io.BytesIO(content)
        response.headers = {"Content-Type": "application/json", "Content-Length": str(len(content))}
        with patch.object(source, "build_opener") as opener:
            opener.return_value.open.return_value = response
            raw = source._request_json("commons", {"titles": "File:Actor.jpg", "prop": "imageinfo"}, deadline=10**12)
            self.assertEqual(raw, file_response())
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(urlsplit(request.full_url).netloc, "commons.wikimedia.org")
            self.assertEqual(parse_qs(urlsplit(request.full_url).query)["action"], ["query"])
            self.assertEqual(request.get_header("Accept-encoding"), "identity")
            handler = opener.call_args.args[0]
            self.assertIsInstance(handler, assets._NoRedirects)
            with self.assertRaises(ValueError):
                handler.redirect_request(None, None, 302, "Found", {}, "https://127.0.0.1")

    def test_api_failures_redact_network_details_and_reject_gzip_size_json(self):
        with patch.object(source, "build_opener") as opener, self.assertRaises(ValueError) as error:
            opener.return_value.open.side_effect = URLError("secret proxy user password")
            source._request_json("commons", {}, deadline=10**12)
        self.assertNotIn("secret", str(error.exception))
        for content, headers in ((b"{}", {"Content-Encoding": "gzip"}),
                                 (b"{}", {"Content-Length": str(providers.MAX_RESPONSE_BYTES + 1)}),
                                 (b"[]", {}), (b'{"error":{"info":"secret"}}', {}),
                                 (b"invalid", {}), (b"{}", {"Content-Length": "3"})):
            response = io.BytesIO(content)
            response.headers = headers
            with self.subTest(headers=headers, content=content), patch.object(source, "build_opener") as opener, self.assertRaises(ValueError):
                opener.return_value.open.return_value = response
                source._request_json("commons", {}, deadline=10**12)


if __name__ == "__main__":
    unittest.main()
