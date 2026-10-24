from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from PIL import Image

from catalog_library import assets, people_assets, people_image_candidates as source, tmdb_source


def image_bytes():
    stream = io.BytesIO()
    with Image.new("RGB", (5, 3), (20, 50, 80)) as picture:
        picture.save(stream, format="PNG")
    return stream.getvalue()


def image_row(index=1, **changes):
    return {"file_path": f"/Still_{index}.jpg", "width": 1920, "height": 1080, **changes}


class PeopleImageCandidatesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "people"
        self.data = image_bytes()
        self.target = source.parse_target("259837")

    def fetch(self, raw=None, *, target=None, **kwargs):
        raw = raw if raw is not None else {"id": 259837, "backdrops": [image_row()]}
        with patch.object(tmdb_source, "_request_json", return_value=raw) as request, \
             patch.object(assets, "download_image", return_value=(self.data, "image/png")) as download:
            result = source.fetch_candidates(target or self.target, people_root=self.root, **kwargs)
        return result, request, download

    def test_targets_cover_tv_movie_and_episode_without_request(self):
        self.assertEqual(self.target["api_path"], "/tv/259837/images")
        self.assertEqual(self.target["image_kind"], "backdrop")
        for value in ("https://www.themoviedb.org/movie/12", "https://themoviedb.org/movie/12-a-film/"):
            result = source.parse_target(value)
            self.assertEqual(result["media_type"], "movie")
            self.assertEqual(result["source_url"], "https://www.themoviedb.org/movie/12")
        from_link = source.parse_target("https://www.themoviedb.org/tv/259837-a-show/season/0/episode/14")
        from_fields = source.parse_target(259837, season_number="0", episode_number="14")
        self.assertEqual(from_link, from_fields)
        self.assertEqual(from_fields["api_path"], "/tv/259837/season/0/episode/14/images")
        self.assertEqual(from_fields["image_kind"], "still")

    def test_invalid_targets_do_not_network(self):
        values = (True, 0, -1, 1.5, {}, "0001", "10000000000", "../1", "https://evil.invalid/tv/12",
                  "https://www.themoviedb.org.evil.invalid/tv/12", "https://user:pass@www.themoviedb.org/tv/12",
                  "https://www.themoviedb.org:8080/tv/12", "http://www.themoviedb.org/tv/12",
                  "https://www.themoviedb.org/tv/12?token=private", "https://www.themoviedb.org/tv/12#x",
                  "https://www.themoviedb.org/person/12", "https://www.themoviedb.org/tv/12/images",
                  "https://www.themoviedb.org/tv/12/season/1", "https://www.themoviedb.org/tv/%31%32",
                  "https://www.themoviedb.org/tv/12/../13", "https://www.themoviedb.org/tv/12\n")
        with patch.object(tmdb_source, "_request_json") as request:
            for value in values:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    source.parse_target(value)
            for kwargs in ({"media_type": []}, {"media_type": "person"}, {"season_number": True},
                           {"episode_number": 1}, {"season_number": 1}, {"season_number": 1, "episode_number": 0},
                           {"season_number": 1000, "episode_number": 1},
                           {"media_type": "movie", "season_number": 1, "episode_number": 1}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    source.parse_target(12, **kwargs)
            with self.assertRaisesRegex(ValueError, "不一致"):
                source.parse_target("https://www.themoviedb.org/tv/12/season/1/episode/2", season_number=2)
        request.assert_not_called()

    def test_api_path_allowlist_adds_only_still_endpoints(self):
        allowed = ("/search/tv", "/person/12", "/tv/12", "/tv/12/season/0", "/tv/12/season/1/aggregate_credits",
                   "/tv/12/season/1/episode/14/credits", "/tv/12/images", "/movie/12/images",
                   "/tv/12/season/0/episode/14/images")
        rejected = ("/movie/12", "/person/12/images", "/tv/12/season/1/images", "/movie/12/credits",
                    "/tv/0/images", "/tv/12/images?api_key=x", "/tv/12/images/extra", "/tv/12/season/1/episode/0/images",
                    "/tv/12/season/1/episode/14", "/tv/12/season/1000/episode/1/images", "/tv/12/../13/images",
                    "https://evil.invalid/tv/12/images", "/tv/12/images\n")
        for value in allowed:
            with self.subTest(value=value):
                self.assertIsNotNone(tmdb_source._PATH.fullmatch(value))
        for value in rejected:
            with self.subTest(value=value), patch.object(tmdb_source, "_token") as token, self.assertRaises(ValueError):
                tmdb_source._request_json(value)
            token.assert_not_called()

    def test_request_uses_server_token_and_no_secret_enters_response(self):
        raw = {"id": 259837, "backdrops": []}
        response = io.BytesIO(json.dumps(raw).encode())
        response.headers = {"Content-Type": "application/json"}
        with patch.object(tmdb_source, "_token", return_value="test-only-secret"), patch.object(tmdb_source, "build_opener") as opener:
            opener.return_value.open.return_value = response
            result = source.fetch_candidates(self.target, people_root=self.root)
            request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.themoviedb.org/3/tv/259837/images")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-only-secret")
        self.assertNotIn("test-only-secret", json.dumps(result))
        self.assertFalse(self.root.exists())

    def test_backdrops_only_local_thumbnails_no_posters_profiles_or_original(self):
        raw = {"id": 259837, "backdrops": [image_row()], "posters": [image_row(2)],
               "profiles": [image_row(3)], "logos": [image_row(4)], "stills": [image_row(5)]}
        original = deepcopy(raw)
        result, request, download = self.fetch(raw)
        self.assertEqual(raw, original)
        request.assert_called_once()
        row = result["candidates"][0]
        self.assertEqual(row["kind"], "backdrop")
        self.assertTrue(row["selectable"])
        self.assertTrue(row["thumbnail_url"].startswith("/api/catalog-library/people/assets/"))
        self.assertEqual(download.call_args.args[0], "https://image.tmdb.org/t/p/w500/Still_1.jpg")
        self.assertIs(download.call_args.kwargs["validate_url"], tmdb_source.validate_cover_url)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertFalse((self.root / "db").exists())
        self.assertEqual(len(list(self.root.rglob("*.*"))), 1)

    def test_movie_and_episode_use_distinct_id_scopes(self):
        movie = source.parse_target(12, media_type="movie")
        result, request, _ = self.fetch({"id": 12, "backdrops": [image_row()]}, target=movie)
        self.assertEqual(request.call_args.args[0], "/movie/12/images")
        episode = source.parse_target(259837, season_number=1, episode_number=14)
        other, request, _ = self.fetch({"id": 987654, "stills": [image_row()], "backdrops": [image_row(5)]}, target=episode)
        self.assertEqual(request.call_args.args[0], "/tv/259837/season/1/episode/14/images")
        self.assertEqual(other["candidates"][0]["kind"], "still")
        self.assertNotEqual(result["candidates"][0]["id"], other["candidates"][0]["id"])

    def test_limits_deduplicate_and_only_download_visible_page(self):
        raw = {"id": 259837, "backdrops": [image_row()] + [image_row(i) for i in range(1, 301)]}
        result, _, download = self.fetch(raw, page=2)
        self.assertEqual(result["total"], 30)
        self.assertTrue(result["limited"])
        self.assertTrue(result["has_more"])
        self.assertEqual(download.call_count, 8)
        self.assertEqual([row["file_path"] for row in result["candidates"]], [f"/Still_{i}.jpg" for i in range(9, 17)])
        final, _, download = self.fetch(raw, page=4)
        self.assertEqual(download.call_count, 6)
        self.assertFalse(final["has_more"])
        for page in (0, -1, 5, True, "1"):
            with self.subTest(page=page), patch.object(tmdb_source, "_request_json") as request, self.assertRaises(ValueError):
                source.fetch_candidates(self.target, page=page, people_root=self.root)
            request.assert_not_called()

    def test_invalid_entries_skipped_without_remote_path_leak(self):
        raw = {"id": 259837, "backdrops": [None, image_row(file_path="https://private.invalid/token.jpg"),
               image_row(file_path="/../escape.jpg"), image_row(width=True), image_row(height=0), image_row(9)]}
        result, _, download = self.fetch(raw)
        self.assertEqual(result["total"], 1)
        self.assertEqual(download.call_count, 1)
        self.assertIn("5", result["warnings"][0])
        self.assertNotIn("private.invalid", json.dumps(result))

    def test_invalid_response_target_and_identity_stop_before_image_download(self):
        for raw in (None, [], {}, {"id": 259837, "backdrops": {}}, {"id": True, "backdrops": []}, {"id": 12, "backdrops": []}):
            with self.subTest(raw=raw), patch.object(tmdb_source, "_request_json", return_value=raw), \
                 patch.object(assets, "download_image") as download, self.assertRaises(ValueError):
                source.fetch_candidates(self.target, people_root=self.root)
            download.assert_not_called()
        for key, value in (("source_url", "https://evil.invalid/"), ("api_path", "/person/12/images"), ("image_kind", "portrait")):
            target = {**self.target, key: value}
            with self.subTest(key=key), patch.object(tmdb_source, "_request_json") as request, self.assertRaises(ValueError):
                source.fetch_candidates(target, people_root=self.root)
            request.assert_not_called()

    def test_one_failed_thumbnail_preserves_other_candidates_and_redacts_transport(self):
        def download(url, **kwargs):
            if url.endswith("Still_1.jpg"):
                raise URLError("https://user:private@proxy.invalid:999")
            return self.data, "image/png"
        with patch.object(tmdb_source, "_request_json", return_value={"id": 259837, "backdrops": [image_row(), image_row(2)]}), \
             patch.object(assets, "download_image", side_effect=download):
            result = source.fetch_candidates(self.target, people_root=self.root)
        failed, good = result["candidates"]
        self.assertFalse(failed["selectable"])
        self.assertEqual(failed["thumbnail_url"], "")
        self.assertTrue(good["selectable"])
        self.assertIn("/Still_1.jpg", result["warnings"][0])
        self.assertNotIn("private", json.dumps(result))

    def test_selected_candidate_reuses_local_w500_without_applying_any_record(self):
        result, _, _ = self.fetch()
        row = result["candidates"][0]
        with patch.object(assets, "download_image", side_effect=AssertionError("cached")), \
             patch.object(tmdb_source, "_request_json", side_effect=AssertionError("no metadata refetch")):
            selected = source.download_candidate(self.target, row, people_root=self.root)
        self.assertEqual(selected["avatar"], row["thumbnail"])
        self.assertEqual(selected["evidence"]["external_id"], "tv:259837")
        self.assertTrue(all(isinstance(value, str) for value in selected["evidence"].values()))
        self.assertNotIn("source_refs", selected)
        self.assertFalse((self.root / "db").exists())

    def test_selected_candidate_cross_target_or_arbitrary_path_is_rejected(self):
        row = source._candidate(self.target, image_row())
        cases = ({**row, "id": "f" * 64}, {**row, "kind": "portrait"},
                 {**row, "file_path": "https://image.tmdb.org/t/p/original/a.jpg"})
        for candidate in cases:
            with self.subTest(candidate=candidate), patch.object(assets, "download_image") as download, self.assertRaises(ValueError):
                source.download_candidate(self.target, candidate, people_root=self.root)
            download.assert_not_called()
        with patch.object(assets, "download_image") as download, self.assertRaises(ValueError):
            source.download_candidate(source.parse_target(12), row, people_root=self.root)
        download.assert_not_called()

    def test_missing_thumbnail_reacquires_selected_only_and_preserves_provenance(self):
        target = source.parse_target(259837, season_number=1, episode_number=14)
        row = source._candidate(target, image_row())
        row["thumbnail"] = {"asset_id": "a" * 64 + ".png", "source_url": tmdb_source._image_url(row["file_path"])}
        with patch.object(assets, "download_image", return_value=(self.data, "image/png")) as download:
            selected = source.download_candidate(target, row, people_root=self.root)
        download.assert_called_once()
        self.assertEqual(selected["evidence"]["season_number"], "1")
        self.assertEqual(selected["evidence"]["episode_number"], "14")
        self.assertEqual(selected["evidence"]["image_kind"], "still")
        self.assertTrue(selected["evidence"]["page_url"].endswith("/season/1/episode/14"))

    def test_malformed_image_is_not_selectable_or_stored(self):
        with patch.object(tmdb_source, "_request_json", return_value={"id": 259837, "backdrops": [image_row()]}), \
             patch.object(assets, "download_image", return_value=(b"<not-an-image>", "image/png")):
            result = source.fetch_candidates(self.target, people_root=self.root)
        self.assertFalse(result["candidates"][0]["selectable"])
        self.assertTrue(result["warnings"])
        self.assertFalse(list(self.root.rglob("*.png")))


if __name__ == "__main__":
    unittest.main()
