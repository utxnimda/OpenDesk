from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from catalog_library import assets, people_assets, providers, web
from work_catalog_yaml.jp_tv.browse_app import build_jp_tv_browse_app
from test_people_assets import image_bytes
from test_playback_web import request


class PeopleAssetsWebTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "people"
        self.data = image_bytes()
        info = assets.store_image(self.data, "image/png", root=self.root / "assets")
        self.filename = info["sha256"] + ".png"
        self.path = self.root / "assets" / self.filename
        root_patch = patch.object(people_assets, "feature_data_root", return_value=self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.guards = []
        for owner, name in ((assets, "build_opener"), (providers, "_request_json"),
                            (people_assets, "acquire_avatar"), (web, "get_resolved_browse_settings")):
            guard = patch.object(owner, name, side_effect=AssertionError("asset GET must stay offline and read-only"))
            self.guards.append(guard.start())
            self.addCleanup(guard.stop)
        self.app = build_jp_tv_browse_app(operation_log_root=self.base / "logs")
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)
        for guard in self.guards:
            guard.assert_not_called()
        self.assertFalse((self.root / "db").exists())
        self.temp.cleanup()

    async def get(self, filename=None, *, method="GET", headers=()):
        return await request(self.app, self.filename if filename is None else filename,
                             base_path="/api/catalog-library/people/assets", method=method, headers=headers)

    async def test_actual_route_get_head_etag_and_security_headers(self):
        status, content, headers = await self.get()
        self.assertEqual(status, 200)
        self.assertEqual(content, self.data)
        self.assertEqual(headers[b"content-type"], b"image/png")
        self.assertEqual(headers[b"content-length"], str(len(self.data)).encode())
        self.assertEqual(headers[b"x-content-type-options"], b"nosniff")
        self.assertIn(b"immutable", headers[b"cache-control"])
        self.assertIn(b"sandbox", headers[b"content-security-policy"])
        self.assertEqual(headers[b"etag"], ('"' + self.filename.split(".")[0] + '"').encode())
        status, content, head_headers = await self.get(method="HEAD")
        self.assertEqual((status, content), (200, b""))
        self.assertEqual(head_headers[b"etag"], headers[b"etag"])
        self.assertEqual(head_headers[b"content-length"], headers[b"content-length"])
        status, content, cached_headers = await self.get(headers=[(b"if-none-match", headers[b"etag"])])
        self.assertEqual((status, content), (304, b""))
        self.assertEqual(cached_headers[b"etag"], headers[b"etag"])
        self.assertEqual(self.path.read_bytes(), self.data)

    async def test_route_runs_local_validation_in_disk_queue(self):
        reader = people_assets.read_avatar
        workers = []

        def read(filename):
            workers.append(threading.current_thread().name)
            return reader(filename)

        with patch.object(people_assets, "read_avatar", side_effect=read):
            status, content, _headers = await self.get()
        self.assertEqual((status, content), (200, self.data))
        self.assertTrue(workers[0].startswith("nimda-disk"))

    async def test_invalid_names_missing_and_corrupted_content_are_uncacheable_404(self):
        for name in ("invalid.png", "a" * 64 + ".png", "a" * 64 + ".svg", "A" * 64 + ".png", "a" * 64 + ".png:stream"):
            with self.subTest(name=name):
                status, content, headers = await self.get(name)
                self.assertEqual((status, content), (404, b""))
                self.assertEqual(headers[b"cache-control"], b"no-store")
        _status, _content, valid_headers = await self.get()
        self.path.write_bytes(b"corrupt")
        status, content, headers = await self.get(headers=[(b"if-none-match", valid_headers[b"etag"])])
        self.assertEqual((status, content), (404, b""))
        self.assertEqual(headers[b"cache-control"], b"no-store")
        self.assertEqual(self.path.read_bytes(), b"corrupt")

    async def test_route_rejects_writes_and_path_traversal(self):
        status, _content, headers = await self.get(method="POST")
        self.assertEqual(status, 405)
        self.assertNotIn(b"POST", headers[b"allow"])
        for name in ("../outside.png", "nested/" + self.filename, "%2e%2e%2foutside.png"):
            with self.subTest(name=name):
                status, _content, _headers = await self.get(name)
                self.assertEqual(status, 404)
        self.assertEqual(self.path.read_bytes(), self.data)


if __name__ == "__main__":
    unittest.main()
