from __future__ import annotations

from copy import deepcopy
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from PIL import Image

from catalog_library import assets, people_assets
from people_catalog.model import source_identity, validate_avatar_reference, validate_entity


SOURCE = "https://lain.bgm.tv/pic/crt/m/12/34/12_Abc.jpg?r=1720910081"


def image_bytes():
    stream = io.BytesIO()
    with Image.new("RGB", (3, 4), (45, 75, 120)) as image:
        image.save(stream, format="PNG")
    return stream.getvalue()


class PeopleAssetsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "people"
        self.data = image_bytes()
        self.raw = {"id": 123, "images": {"medium": SOURCE}}

    def acquire(self, **kwargs):
        with patch.object(assets, "download_image", return_value=(self.data, "image/png")):
            return people_assets.acquire_avatar(self.raw, user_agent="test", root=self.root, **kwargs)

    def test_people_urls_allow_only_portrait_cdn_and_revision_query(self):
        for value in (SOURCE, SOURCE.split("?")[0], SOURCE.replace("/pic/", "/r/240/pic/"),
                      SOURCE.replace("/pic/", "/r/240x300/pic/"), SOURCE.replace("://lain.bgm.tv/", "://lain.bgm.tv:443/")):
            with self.subTest(value=value):
                self.assertEqual(people_assets.validate_source_url(value), value)
        for value in (SOURCE.replace("https", "http"), SOURCE.replace("lain.bgm.tv", "127.0.0.1"),
                      SOURCE.replace("lain.bgm.tv", "evil.lain.bgm.tv"), SOURCE.replace("lain.bgm.tv", "lain.bgm.tv."),
                      SOURCE.replace("lain.bgm.tv", "lain.bgm.tv:8080"), SOURCE.replace("https://", "https://user:pass@"),
                      SOURCE.replace("/pic/", "/../pic/"), SOURCE.replace("/pic/", "/%70ic/"),
                      SOURCE.replace("/crt/", "/cover/"), SOURCE.replace("/crt/", "/user/"), SOURCE + "#profile",
                      SOURCE + "&next=http://127.0.0.1", SOURCE + "&r=99", SOURCE.replace("?r=", "?url="),
                      SOURCE.replace("?r=1720910081", "?r=abc"), "file:///tmp/picture.png", "data:image/png,x", " " + SOURCE):
            with self.subTest(value=value), patch.object(assets, "build_opener") as opener:
                with self.assertRaises(ValueError):
                    people_assets.acquire_avatar({"images": {"medium": value}}, user_agent="test", root=self.root)
                opener.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_source_selection_supports_absent_images_and_bounded_fallback(self):
        for raw in ({}, {"images": None}, {"images": {}}, {"images": {"medium": "", "large": None}}, None):
            self.assertIsNone(people_assets.select_source_url(raw))
        for raw in ({"images": []}, {"images": "bad"}, {"images": {"medium": 1}}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                people_assets.select_source_url(raw)
        self.assertEqual(people_assets.select_source_url({"images": {"large": SOURCE}}), SOURCE)
        self.assertEqual(people_assets.select_source_url({"images": {"medium": SOURCE, "large": "invalid"}}), SOURCE)

    def test_avatar_store_is_local_content_addressed_and_deduplicated(self):
        first = self.acquire()
        digest = hashlib.sha256(self.data).hexdigest()
        self.assertEqual(first, {"asset_id": digest + ".png", "source_url": SOURCE})
        self.assertEqual((self.root / "assets" / first["asset_id"]).read_bytes(), self.data)
        self.assertFalse((self.root / "db").exists())
        with patch.object(assets, "atomic_write_bytes", side_effect=AssertionError("must reuse")):
            self.assertEqual(self.acquire(), first)
        self.assertEqual(len(list((self.root / "assets").iterdir())), 1)

    def test_unchanged_previous_image_does_not_download_again(self):
        previous = self.acquire()
        with patch.object(assets, "download_image", side_effect=AssertionError("no network")):
            result = people_assets.acquire_avatar(self.raw, user_agent="test", root=self.root, previous=previous)
        self.assertEqual(result, previous)
        self.assertIsNot(result, previous)

    def test_changed_revision_or_missing_file_reacquires_without_overwriting_old(self):
        previous = self.acquire()
        alternate = {"images": {"medium": SOURCE.replace("1720910081", "1720910082")}}
        with patch.object(assets, "download_image", return_value=(self.data, "image/png")) as download:
            result = people_assets.acquire_avatar(alternate, user_agent="test", root=self.root, previous=previous)
        self.assertEqual(result["asset_id"], previous["asset_id"])
        self.assertNotEqual(result["source_url"], previous["source_url"])
        download.assert_called_once()
        missing = {"asset_id": "a" * 64 + ".png", "source_url": SOURCE}
        self.assertEqual(self.acquire(previous=missing), previous)

    def test_optional_failure_never_changes_previous_avatar_or_creates_files(self):
        previous = {"asset_id": "a" * 64 + ".png", "source_url": SOURCE.replace("1720910081", "1")}
        original = deepcopy(previous)
        with patch.object(assets, "build_opener") as opener, self.assertRaisesRegex(ValueError, "人物图片连接失败") as failed:
            opener.return_value.open.side_effect = URLError("proxy credentials or private address")
            people_assets.acquire_avatar(self.raw, user_agent="test", root=self.root, previous=previous)
        self.assertNotIn("credentials", str(failed.exception))
        self.assertNotIn("private", str(failed.exception))
        self.assertEqual(previous, original)
        self.assertFalse(self.root.exists())

    def test_avatar_uses_shared_download_validation_and_timeout(self):
        response = io.BytesIO(self.data)
        response.headers = {"Content-Type": "image/png", "Content-Length": str(len(self.data))}
        with patch.object(assets, "build_opener") as opener:
            opener.return_value.open.return_value = response
            result = people_assets.acquire_avatar(self.raw, user_agent="avatar-test", root=self.root)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, SOURCE)
            self.assertEqual(request.get_header("User-agent"), "avatar-test")
            self.assertEqual(request.get_header("Accept-encoding"), "identity")
            self.assertIsInstance(opener.call_args.args[0], assets._NoRedirects)
        self.assertTrue(result["asset_id"].endswith(".png"))
        with patch.object(assets.time, "monotonic", return_value=100), patch.object(assets, "build_opener") as opener:
            with self.assertRaisesRegex(ValueError, "人物图片下载超过总时限"):
                people_assets.acquire_avatar(self.raw, user_agent="test", root=self.root, deadline=99)
            opener.return_value.open.assert_not_called()

    def test_malformed_oversized_and_mime_mismatch_are_not_stored(self):
        for data, mime in ((b"<svg></svg>", "image/svg+xml"), (self.data[:40], "image/png"), (self.data, "image/jpeg")):
            with self.subTest(mime=mime, size=len(data)), patch.object(assets, "download_image", return_value=(data, mime)):
                with self.assertRaises(ValueError):
                    people_assets.acquire_avatar(self.raw, user_agent="test", root=self.root)
        with patch.object(assets, "MAX_PIXELS", 1), self.assertRaisesRegex(ValueError, "尺寸"):
            self.acquire()
        with patch.object(assets, "MAX_IMAGE_BYTES", 10), self.assertRaisesRegex(ValueError, "大小"):
            self.acquire()
        self.assertFalse(self.root.exists())

    def test_local_reads_are_offline_and_reject_path_hash_or_link_tampering(self):
        avatar = self.acquire()
        with patch.object(assets, "build_opener", side_effect=AssertionError("offline")):
            self.assertEqual(people_assets.read_avatar(avatar["asset_id"], root=self.root), (self.data, "image/png"))
        for filename in ("../secret", "a" * 64 + ".svg", "a" * 64 + ".png:secret", "A" * 64 + ".png"):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                people_assets.read_avatar(filename, root=self.root)
        path = self.root / "assets" / avatar["asset_id"]
        with patch.object(assets, "_is_link", side_effect=lambda candidate: candidate == path), self.assertRaises(ValueError):
            people_assets.read_avatar(avatar["asset_id"], root=self.root)
        path.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "校验失败"):
            people_assets.read_avatar(avatar["asset_id"], root=self.root)
        with self.assertRaisesRegex(ValueError, "校验失败"):
            self.acquire(previous=avatar)
        self.assertEqual(path.read_bytes(), b"corrupt")

    def test_linked_asset_roots_fail_before_any_download(self):
        with patch.object(assets, "_is_link", side_effect=lambda candidate: candidate == self.root), \
             patch.object(assets, "download_image") as download:
            with self.assertRaisesRegex(ValueError, "联接"):
                people_assets.acquire_avatar(self.raw, user_agent="test", root=self.root)
            download.assert_not_called()

    def test_missing_local_file_never_creates_directories(self):
        with self.assertRaises(ValueError):
            people_assets.read_avatar("a" * 64 + ".png", root=self.root)
        self.assertFalse(self.root.exists())

    def test_reference_validation_and_browser_urls_never_use_remote_sources(self):
        avatar = {"asset_id": "a" * 64 + ".png", "source_url": SOURCE}
        self.assertEqual(validate_avatar_reference(avatar), avatar)
        self.assertEqual(people_assets.avatar_url(avatar), "/api/catalog-library/people/assets/" + avatar["asset_id"])
        self.assertEqual(validate_avatar_reference({}), {})
        self.assertEqual(people_assets.avatar_url({}), "")
        for invalid in (None, SOURCE, {"url": SOURCE}, {**avatar, "extra": True},
                        {**avatar, "asset_id": SOURCE}, {**avatar, "asset_id": "../x.png"},
                        {**avatar, "source_url": "http://lain.bgm.tv/x"}, {**avatar, "source_url": "https://user:secret@lain.bgm.tv/x"}):
            with self.subTest(invalid=invalid):
                self.assertEqual(people_assets.avatar_url(invalid), "")
                with self.assertRaises(ValueError):
                    validate_avatar_reference(invalid)
        person = {"id": source_identity("bangumi", "character", 1), "schema_version": 1,
                  "type": "character", "name": "角色", "avatar": avatar}
        self.assertEqual(validate_entity(person)["avatar"], avatar)
        person["avatar"] = {"asset_id": "../x.png", "source_url": SOURCE}
        with self.assertRaises(ValueError):
            validate_entity(person)


if __name__ == "__main__":
    unittest.main()
