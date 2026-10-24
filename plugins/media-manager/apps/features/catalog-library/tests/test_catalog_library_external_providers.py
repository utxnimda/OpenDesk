"""Provider adapters cannot bypass local preview/confirmation or expose tokens."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from catalog_library import providers, tmdb_source, wikipedia_source
from collection_detail.catalog_edit_service import catalog_records
from work_catalog_yaml.yaml_io import dump_yaml_string
import test_catalog_library_providers as fixtures


class TmdbSourceTests(unittest.TestCase):
    def setUp(self):
        self.series = {"id": 50, "original_name": "사랑의 불시착", "name": "爱的迫降", "original_language": "ko",
                       "first_air_date": "2019-12-14", "overview": "系列简介", "poster_path": "/Series.jpg"}
        self.season = {"id": 501, "season_number": 1, "name": "第 1 季", "air_date": "2019-12-14", "overview": "本季简介",
                       "poster_path": "/Season.jpg", "episodes": [
                           {"id": 511, "show_id": 50, "season_number": 1, "episode_number": 1, "name": "第一集", "air_date": "2019-12-14", "runtime": 70},
                           {"id": 512, "show_id": 50, "season_number": 1, "episode_number": 2, "name": "第二集", "air_date": "2019-12-15", "runtime": None}]}
        self.original = deepcopy(self.season)
        self.original["episodes"][0]["name"] = "제1회"
        self.original["episodes"][1]["name"] = "제2회"

    def test_original_titles_chinese_hover_and_runtime_are_local_candidates(self):
        with patch.object(tmdb_source, "_request_json", side_effect=deepcopy([self.series, self.season, self.original])) as request:
            snapshot = tmdb_source.fetch_snapshot(50)
        self.assertEqual(request.call_args_list[2].kwargs["query"]["language"], "ko")
        self.assertEqual(snapshot["source_url"], "https://www.themoviedb.org/tv/50/season/1")
        candidates = snapshot["candidates"]
        self.assertEqual(candidates["name"], "사랑의 불시착")
        self.assertEqual(candidates["metadata.aliases"], ["爱的迫降"])
        self.assertEqual(candidates["air_date_start"], "20191214")
        self.assertEqual(candidates["metadata.summary"], "本季简介")
        episode = candidates["metadata.episodes"][0]
        self.assertEqual((episode["title"], episode["name_cn"]), ("제1회", "第一集"))
        self.assertEqual(episode["duration_seconds"], 4200)
        self.assertNotIn("duration_seconds", candidates["metadata.episodes"][1])
        self.assertNotIn("air_date_end", candidates)
        self.assertEqual(snapshot["raw"]["original_season"], self.original)

    def test_original_failure_is_warning_and_does_not_block_chinese(self):
        with patch.object(tmdb_source, "_request_json", side_effect=[self.series, self.season, ValueError("原文暂缺")]):
            snapshot = tmdb_source.fetch_snapshot(50)
        self.assertEqual(snapshot["candidates"]["metadata.episodes"][0]["title"], "第一集")
        self.assertTrue(any("原文章节读取失败" in row for row in snapshot["warnings"]))

    def test_episode_identity_mismatch_rejects_without_partial_import(self):
        for broken in ({**self.season, "season_number": 2},
                       {**self.season, "episodes": [self.season["episodes"][0]] * 2},
                       {**self.season, "episodes": [{**self.season["episodes"][0], "show_id": 51}]},
                       {**self.season, "episodes": [{**self.season["episodes"][0], "episode_number": True}]}):
            with self.subTest(broken=broken), patch.object(tmdb_source, "_request_json", side_effect=[self.series, broken]), self.assertRaises(ValueError):
                tmdb_source.fetch_snapshot(50)

    def test_invalid_season_rejected_before_network(self):
        for number in (-1, True, 1000, "1", None):
            with self.subTest(number=number), patch.object(tmdb_source, "_request_json") as request, self.assertRaises(ValueError):
                tmdb_source.fetch_snapshot(50, season_number=number)
            request.assert_not_called()

    def test_season_date_missing_not_replaced_with_series_date(self):
        with patch.object(tmdb_source, "_request_json", side_effect=[self.series, {**self.season, "air_date": None}, self.original]):
            snapshot = tmdb_source.fetch_snapshot(50)
        self.assertEqual(snapshot["candidates"]["air_date_start"], "")
        self.assertTrue(any("没有用整个系列" in row for row in snapshot["warnings"]))

    def test_cover_only_skips_original_title_request(self):
        with patch.object(tmdb_source, "_request_json", side_effect=[self.series, self.season]) as request:
            snapshot = tmdb_source.fetch_snapshot(50, cover_only=True)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(snapshot["candidates"]["metadata.episodes"], [])

    def test_cover_only_ignores_unrelated_malformed_episode_fields(self):
        with patch.object(tmdb_source, "_request_json", side_effect=[self.series, {**self.season, "episodes": "bad"}]):
            snapshot = tmdb_source.fetch_snapshot(50, cover_only=True)
        self.assertEqual(snapshot["poster_path"], "/Season.jpg")

    def test_changed_original_episode_number_falls_back_to_consistent_chinese(self):
        self.original["episodes"][0]["episode_number"] = 3
        with patch.object(tmdb_source, "_request_json", side_effect=[self.series, self.season, self.original]):
            snapshot = tmdb_source.fetch_snapshot(50)
        self.assertEqual(snapshot["candidates"]["metadata.episodes"][0]["ep"], 1)
        self.assertEqual(snapshot["candidates"]["metadata.episodes"][0]["title"], "第一集")
        self.assertTrue(any("集数不一致" in row for row in snapshot["warnings"]))

    def test_cover_strict_host_and_no_token_forwarding(self):
        good = "https://image.tmdb.org/t/p/w500/Season.jpg"
        self.assertEqual(tmdb_source.validate_cover_url(good), good)
        for url in (good + "?secret=token", good.replace("image.tmdb.org", "image.tmdb.org.evil.test"),
                    good.replace("https:", "http:"), good.replace("/Season.jpg", "/../Season.jpg"),
                    good.replace("image.tmdb.org", "user:secret@image.tmdb.org")):
            with self.subTest(url=url), self.assertRaises(ValueError):
                tmdb_source.validate_cover_url(url)
        with patch.object(tmdb_source.assets, "download_image", return_value=(b"safe", "image/jpeg")) as fetch, \
             patch.object(tmdb_source.assets, "store_image", return_value={"sha256": "a" * 64}):
            cover = tmdb_source.acquire_cover({"poster_path": "/Season.jpg", "subject_id": 50}, user_agent="nimda")
        self.assertEqual(fetch.call_args.args[0], good)
        self.assertEqual(set(fetch.call_args.kwargs), {"user_agent", "validate_url", "deadline", "label"})
        self.assertEqual(cover["source_url"], good)

    def test_search_returns_original_and_chinese_and_public_link(self):
        with patch.object(tmdb_source, "_request_json", return_value={"results": [self.series], "total_results": 1}) as request:
            result = providers.search_provider({"provider": "tmdb", "query": "爱的迫降"})
        self.assertEqual(result["results"][0]["source_url"], "https://www.themoviedb.org/tv/50")
        self.assertEqual(request.call_args.kwargs["query"]["language"], "zh-CN")


class TmdbCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "providers.local.yaml"
        self.environ = patch.dict(os.environ, {}, clear=True)
        self.environ.start()
        self.addCleanup(self.environ.stop)

    def test_environment_precedes_private_config(self):
        self.file.write_text("tmdb:\n  read_access_token: local-secret\n", encoding="utf-8")
        self.assertEqual(tmdb_source._token(self.file), "local-secret")
        with patch.dict(os.environ, {tmdb_source.ENV_TOKEN: "environment-secret"}):
            self.assertEqual(tmdb_source._token(self.file), "environment-secret")

    def test_missing_and_malformed_config_expose_no_secret(self):
        with self.assertRaisesRegex(ValueError, "尚未配置"):
            tmdb_source._token(self.file)
        for contents in ("tmdb: [secret-value-with-bad-yaml", "tmdb: secret-value", "tmdb:\n  read_access_token: 123\n"):
            self.file.write_text(contents, encoding="utf-8")
            with self.subTest(contents=contents), self.assertRaises(ValueError) as error:
                tmdb_source._token(self.file)
            self.assertNotIn("secret-value", str(error.exception))

    def test_token_sent_only_in_header_not_url_or_error(self):
        secret = "private-read-token"
        error = HTTPError("https://api.themoviedb.org/3/search/tv", 401, secret, {}, io.BytesIO(secret.encode()))
        with patch.dict(os.environ, {tmdb_source.ENV_TOKEN: secret}), patch.object(tmdb_source, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(ValueError) as caught:
                tmdb_source._request_json("/search/tv", query={"query": "x"})
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer " + secret)
        self.assertNotIn(secret, request.full_url)
        self.assertNotIn(secret, str(caught.exception))

    def test_network_diagnostic_does_not_echo_arbitrary_exception(self):
        with patch.dict(os.environ, {tmdb_source.ENV_TOKEN: "token"}), patch.object(tmdb_source, "build_opener") as opener, \
             patch.object(providers, "_wait_retry"):
            opener.return_value.open.side_effect = URLError("secret token in server exception")
            with self.assertRaises(ValueError) as error:
                tmdb_source._request_json("/search/tv")
        self.assertNotIn("secret", str(error.exception))

    def test_redirect_and_unapproved_path_rejected(self):
        with self.assertRaisesRegex(ValueError, "重定向"):
            tmdb_source._NoRedirects().redirect_request(None, None, 302, None, {}, "https://evil.test")
        with patch.object(tmdb_source, "_token") as token:
            for path in ("https://evil.test/", "/account/1", "/tv/1?api_key=secret", "/tv/1/season/1/../../account"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    tmdb_source._request_json(path)
            token.assert_not_called()


class ExternalProviderPipelineTests(unittest.TestCase):
    ref = fixtures.ProviderImportTest.ref

    def setUp(self):
        fixtures.ProviderImportTest.setUp(self)
        self.file.write_text(dump_yaml_string([{**self.record, "metadata": {**self.record["metadata"],
            "aliases": ["手写别名"], "episodes": [{"id": "manual:1", "title": "保留章节"}]}}]), encoding="utf-8")
        self.tmdb = {"schema_version": 1, "provider": "tmdb", "subject_id": 50, "season_number": 2,
            "fetched_at": "2026-10-06T12:00:00+00:00", "source_url": "https://www.themoviedb.org/tv/50/season/2",
            "subject": {"id": 50, "name": "원작", "name_cn": "中文", "source_url": "https://www.themoviedb.org/tv/50/season/2"},
            "source_metadata": {"media_type": "tv", "season_number": 2}, "raw": {"series": {"id": 50}},
            "candidates": {"name": "원작", "metadata.aliases": ["中文"], "metadata.summary": "TMDB 简介",
                "air_date_start": "20200201", "metadata.episodes": [{"id": "tmdb:511", "source_id": 511, "type": 0,
                     "ep": 1, "sort": 1, "title": "제1회", "name_cn": "第一集"}], "metadata.cover": None}, "warnings": []}
        self.wiki = {"schema_version": 1, "provider": "wikipedia", "subject_id": 82,
            "fetched_at": "2026-10-06T12:00:00+00:00", "source_url": "https://zh.wikipedia.org/wiki/Test",
            "subject": {"id": 82, "name": "中文条目", "name_cn": "中文条目", "source_url": "https://zh.wikipedia.org/wiki/Test"},
            "source_metadata": {"page_title": "Test", "revision_id": 99, "license": "CC BY-SA 4.0"}, "raw": {"pageid": 82},
            "candidates": {"name": "中文条目", "metadata.aliases": ["维基别名"], "metadata.summary": "中文简介",
                           "air_date_end": "20201011"}, "warnings": []}

    def preview(self, provider="tmdb"):
        snapshot = self.tmdb if provider == "tmdb" else self.wiki
        adapter = tmdb_source if provider == "tmdb" else wikipedia_source
        body = {"provider": provider, "ref": self.ref(), "subject_id": snapshot["subject_id"]}
        if provider == "tmdb":
            body["season_number"] = 2
        with patch.object(adapter, "fetch_snapshot", return_value=deepcopy(snapshot)), \
             patch.object(tmdb_source, "acquire_cover", side_effect=ValueError("无封面")):
            return providers.preview_provider_import(body, settings=self.settings, source_root=self.sources)

    def apply(self, preview, fields, **overrides):
        body = {"provider": preview["provider"], "ref": preview["ref"], "snapshot_id": preview["snapshot_id"],
                "preview_token": preview["preview_token"], "selected_fields": fields, **overrides}
        return providers.apply_provider_import(body, settings=self.settings, source_root=self.sources)

    def test_preview_no_db_write_and_apply_uses_cache_only_common_writer(self):
        before = self.file.read_bytes()
        preview = self.preview()
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(preview["source_url"], self.tmdb["source_url"])
        self.assertEqual(preview["automatic_aliases"], ["원작"])
        self.assertEqual(self.people_preview_patch.target.preview_for_import.call_count, 1)
        self.assertEqual(self.people_preview_patch.target.preview_for_import.call_args.kwargs["provider"], "tmdb")
        with patch.object(tmdb_source, "fetch_snapshot", side_effect=AssertionError("No network while applying")):
            result = self.apply(preview, ["metadata.summary"])
        self.assertTrue(result["db_committed"])
        record = catalog_records(self.settings)[0]["record"]
        self.assertEqual(record["metadata"]["summary"], "TMDB 简介")
        self.assertEqual(record["metadata"]["aliases"], ["手写别名", "원작"])

        self.assertEqual(record["metadata"]["episodes"], [{"id": "manual:1", "title": "保留章节"}])
        self.assertEqual(record["source_refs"][0]["scope"], "season:2")
        self.assertEqual(record["source_refs"][0]["season_number"], 2)
        self.assertEqual(record["source_refs"][0]["source_url"], self.tmdb["source_url"])

    def test_people_preview_reuses_original_language_from_fetched_series(self):
        self.tmdb.setdefault("raw", {})["series"] = {"id": 50, "original_language": "ko"}
        self.preview()
        self.assertEqual(self.people_preview_patch.target.preview_for_import.call_args.kwargs["original_language"], "ko")

    def test_provider_token_cannot_be_swapped(self):
        preview = self.preview()
        with self.assertRaisesRegex(ValueError, "来源与预览来源不一致"):
            self.apply(preview, [], provider="bangumi")

    def test_tampered_snapshot_rejected(self):
        preview = self.preview()
        path = self.sources / (preview["snapshot_id"] + ".json")
        path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "校验失败"):
            self.apply(preview, [])

    def test_wikipedia_selected_end_date_and_alias_preserve_other_source(self):
        self.apply(self.preview(), [])
        preview = self.preview("wikipedia")
        fields = {row["field"]: row for row in preview["diff"]}
        self.assertTrue(fields["air_date_end"]["selectable"])
        self.assertFalse(fields["metadata.episodes"]["selectable"])
        self.apply(preview, ["air_date_end", "metadata.summary"])
        record = catalog_records(self.settings)[0]["record"]
        self.assertEqual(providers._field(record, "air_date_end"), "20201011")
        self.assertEqual(record["metadata"]["summary"], "中文简介")
        self.assertEqual([row["provider"] for row in record["source_refs"]], ["tmdb", "wikipedia"])
        self.assertEqual(record["source_refs"][1]["revision_id"], 99)
        self.assertIn("中文条目", record["metadata"]["aliases"])
        self.assertEqual(len(record["metadata"]["episodes"]), 1)

    def test_missing_end_date_cannot_be_selected_for_bangumi_or_tmdb(self):
        preview = self.preview()
        with self.assertRaisesRegex(ValueError, "为空或无效"):
            self.apply(preview, ["air_date_end"])

    def test_refresh_two_distinct_seasons_preserves_both_source_bindings(self):
        self.apply(self.preview(), [])
        self.tmdb["season_number"] = 3
        self.tmdb["source_metadata"]["season_number"] = 3
        self.tmdb["source_url"] = "https://www.themoviedb.org/tv/50/season/3"
        with patch.object(tmdb_source, "fetch_snapshot", return_value=deepcopy(self.tmdb)), \
             patch.object(tmdb_source, "acquire_cover", side_effect=ValueError("无封面")):
            preview = providers.preview_provider_import({"provider": "tmdb", "ref": self.ref(), "subject_id": 50, "season_number": 3},
                settings=self.settings, source_root=self.sources)
        self.apply(preview, [])
        refs = catalog_records(self.settings)[0]["record"]["source_refs"]
        self.assertEqual([row["scope"] for row in refs], ["season:2", "season:3"])

    def test_wikipedia_redirect_binds_canonical_id(self):
        with patch.object(wikipedia_source, "fetch_snapshot", return_value=deepcopy(self.wiki)):
            preview = providers.preview_provider_import({"provider": "wikipedia", "ref": self.ref(), "subject_id": 81},
                settings=self.settings, source_root=self.sources)
        self.assertEqual(preview["subject_id"], 82)
        self.apply(preview, [])
        self.assertEqual(catalog_records(self.settings)[0]["record"]["source_refs"][0]["external_id"], "82")


if __name__ == "__main__":
    unittest.main()
