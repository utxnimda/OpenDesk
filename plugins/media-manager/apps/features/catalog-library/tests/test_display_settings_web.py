import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from catalog_library import display_settings, service, web
from work_catalog_yaml.jp_tv.browse_app import build_jp_tv_browse_app
from work_catalog_yaml.jp_tv.browse_settings import JpTvBrowseSettings
from test_playback_web import request


class DisplaySettingsWebTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.db = self.base / "db"
        self.db.mkdir()
        self.settings = JpTvBrowseSettings(version=1, filesystem_root=self.db, resolved_default_readable=None,
            resolved_catalog_yaml_paths=(), enum_options={}, enum_labels={}, enum_section_labels={}, app_features=())
        self.patchers = [patch.object(web, "get_resolved_browse_settings", return_value=(self.settings, None)),
                         patch.object(service, "display_settings_payload"), patch.object(display_settings, "save")]
        self.mocks = [patcher.start() for patcher in self.patchers]
        self.app = build_jp_tv_browse_app(operation_log_root=self.base / "logs")
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.assertEqual(list(self.db.iterdir()), [])
        self.temporary.cleanup()

    async def test_display_routes_use_shared_disk_queue_and_forward_exact_body(self):
        for route, handler in (("display-settings", self.mocks[1]), ("display-settings/save", self.mocks[2])):
            with self.subTest(route=route):
                calls = []
                def perform(body, *, settings):
                    calls.append((body, settings, threading.current_thread().name))
                    return {"revision": "fixture"}
                handler.side_effect = perform
                body = {"revision": "old", "config": {"fixture": True}}
                status, content, headers = await request(self.app, route, body, base_path="/api/catalog-library/")
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(content), {"ok": True, "revision": "fixture"})
                self.assertEqual(calls[0][:2], (body, self.settings))
                self.assertTrue(calls[0][2].startswith("nimda-disk"))
                self.assertIn(b"no-store", headers[b"cache-control"])

    async def test_validation_failure_is_reported_without_touching_catalog(self):
        self.mocks[2].side_effect = ValueError("显示配置已变化")
        status, content, _ = await request(self.app, "display-settings/save", {}, base_path="/api/catalog-library/")
        self.assertEqual(status, 400)
        self.assertIn("显示配置已变化", json.loads(content)["error"])
