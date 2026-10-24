"""HTTP boundaries for multi-source operations; no real DB or network access."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from catalog_library import service, source_sync, web
from work_catalog_yaml.jp_tv.browse_app import build_jp_tv_browse_app
from work_catalog_yaml.jp_tv.browse_settings import JpTvBrowseSettings
from test_playback_web import request


class SourceSyncWebTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.db = self.base / "db"
        self.db.mkdir()
        self.settings = JpTvBrowseSettings(version=1, filesystem_root=self.db,
            resolved_default_readable=str(self.db / "fixture.yaml"), resolved_catalog_yaml_paths=(),
            enum_options={}, enum_labels={}, enum_section_labels={}, app_features=())
        settings_patch = patch.object(web, "get_resolved_browse_settings", return_value=(self.settings, None))
        self.settings_lookup = settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.handlers = {}
        for route, owner, name in (("sync-preview", source_sync, "preview"),
                                   ("sync-apply", source_sync, "apply"),
                                   ("binding-update", service, "binding_update")):
            mock = patch.object(owner, name, side_effect=AssertionError("Unexpected service call"))
            self.handlers[route] = mock.start()
            self.addCleanup(mock.stop)
        self.app = build_jp_tv_browse_app(operation_log_root=self.base / "logs")
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)
        self.assertEqual(list(self.db.iterdir()), [])
        self.temporary.cleanup()

    async def request(self, route, body=None, **kwargs):
        return await request(self.app, route, body, base_path="/api/catalog-library/provider/", **kwargs)

    async def test_registered_routes_preserve_payload_queue_and_operation_log(self):
        for route, mock in self.handlers.items():
            with self.subTest(route=route):
                calls = []

                def handler(body, *, settings):
                    calls.append((body, settings, threading.current_thread().name))
                    return {"ok": True, "sources": []}

                mock.side_effect = handler
                body = {"ref": {"fixture": True}, "binding_ids": ["sb_fixture"]}
                operation_id = str(uuid4())
                status, content, headers = await self.request(route, body,
                    headers=[(b"x-nimda-operation-id", operation_id.encode())])
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(content), {"ok": True, "sources": []})
                self.assertEqual(calls[0][:2], (body, self.settings))
                self.assertTrue(calls[0][2].startswith("nimda-provider" if route == "sync-preview" else "nimda-disk"))
                self.assertIn(b"no-store", headers[b"cache-control"])
                operation = self.app.state.operation_registry.snapshot(operation_id)
                self.assertEqual(operation["status"], "succeeded")
                self.assertTrue(operation["log"]["available"])

    async def test_invalid_body_origin_and_method_fail_before_service(self):
        for route in self.handlers:
            for raw in (b"[]", b"null", b"{broken"):
                status, _, _ = await self.request(route, raw=raw)
                self.assertEqual(status, 400)
            status, _, _ = await self.request(route, {}, headers=[(b"origin", b"https://foreign.invalid")])
            self.assertEqual(status, 403)
            for method in ("GET", "HEAD"):
                status, _, _ = await self.request(route, {}, method=method)
                self.assertEqual(status, 405)
        for mock in self.handlers.values():
            mock.assert_not_called()
        self.settings_lookup.assert_not_called()

    async def test_partial_failure_is_warning_and_details_are_searchable(self):
        warning = "中文维基百科 / sb_wiki / 作品 12：来源读取超时，保留已有数据"
        handler = self.handlers["sync-preview"]
        handler.side_effect = None
        handler.return_value = {"can_apply": True, "warnings": [warning],
            "sources": [{"binding_id": "sb_wiki", "status": "error", "error": warning}]}
        operation_id = str(uuid4())
        status, content, _ = await self.request("sync-preview", {},
            headers=[(b"x-nimda-operation-id", operation_id.encode())])
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(content)["can_apply"])
        registry = self.app.state.operation_registry
        self.assertEqual(registry.snapshot(operation_id)["status"], "warning")
        self.assertIn(warning, registry.log_store.read(operation_id)["content"])

    async def test_stale_apply_reports_failure_without_echoing_preview_token(self):
        self.handlers["sync-apply"].side_effect = ValueError("作品已变化，请重新预览")
        operation_id = str(uuid4())
        status, content, _ = await self.request("sync-apply", {"preview_token": "private-fixture-token"},
            headers=[(b"x-nimda-operation-id", operation_id.encode())])
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(content)["ok"])
        registry = self.app.state.operation_registry
        self.assertEqual(registry.snapshot(operation_id)["status"], "failed")
        log = registry.log_store.read(operation_id)["content"]
        self.assertIn("作品已变化", log)
        self.assertNotIn("private-fixture-token", log)


if __name__ == "__main__":
    unittest.main()
