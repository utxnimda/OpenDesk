"""The shared import adapters use the ordinary origin guard and task queues."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from catalog_library import people_import, people_retry, web
from work_catalog_yaml.jp_tv.browse_app import build_jp_tv_browse_app
from test_playback_web import request


class PeopleImportWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = build_jp_tv_browse_app(operation_log_root=Path(self.temp.name) / "logs")
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)

    async def test_routes_use_signed_payload_and_correct_queue(self):
        for route, name, queue in (("selection-target", "target_preview", "nimda-disk"),
                                    ("wikipedia-preview", "wikipedia_preview", "nimda-provider")):
            body = {"snapshot_id": "snapshot", "preview_token": "signed", "external_id": "person:42", "target_id": "existing"} if route == "selection-target" else {"ref": {"index_in_file": 0}, "pages": [{"page": "Example", "role": "演员"}]}
            calls = []

            def handler(value, *, settings):
                calls.append((value, settings, threading.current_thread().name))
                return {"result": "preview-only"}

            with patch.object(people_import, name, side_effect=handler), patch.object(web, "get_resolved_browse_settings", return_value=("settings", None)):
                status, content, headers = await request(self.app, route, body, base_path="/api/catalog-library/people/")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(content), {"ok": True, "result": "preview-only"})
            self.assertEqual(calls[0][:2], (body, "settings"))
            self.assertTrue(calls[0][2].startswith(queue), calls)
            self.assertIn(b"no-store", headers[b"cache-control"])

    async def test_cross_origin_or_invalid_json_never_reach_import(self):
        for route, name in (("selection-target", "target_preview"), ("wikipedia-preview", "wikipedia_preview")):
            with patch.object(people_import, name) as handler, patch.object(web, "get_resolved_browse_settings") as settings:
                status, _, _ = await request(self.app, route, {}, base_path="/api/catalog-library/people/", headers=[(b"origin", b"https://untrusted.invalid")])
                self.assertEqual(status, 403)
                status, _, _ = await request(self.app, route, raw=b"[]", base_path="/api/catalog-library/people/")
                self.assertEqual(status, 400)
                handler.assert_not_called()
                settings.assert_not_called()

    async def test_errors_report_failed_stage(self):
        with patch.object(people_import, "target_preview", side_effect=ValueError("人物库已变化，请重新预览")), patch.object(web, "get_resolved_browse_settings", return_value=("settings", None)):
            status, content, _ = await request(self.app, "selection-target", {}, base_path="/api/catalog-library/people/")
        self.assertEqual(status, 400)
        self.assertIn("人物库已变化", json.loads(content)["error"])

    async def test_retry_uses_network_queue_and_origin_guard(self):
        calls = []
        def retry(value, *, settings):
            calls.append((value, threading.current_thread().name))
            return {"preview_patch": {"preview_token": "renewed"}}
        body = {"ref": {}, "snapshot_id": "child", "preview_token": "parent", "failure_ids": ["person-detail:1"]}
        with patch.object(people_retry, "retry", side_effect=retry), patch.object(web, "get_resolved_browse_settings", return_value=("settings", None)):
            status, content, _ = await request(self.app, "retry", body, base_path="/api/catalog-library/people/")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(content)["preview_patch"]["preview_token"], "renewed")
            self.assertEqual(calls[0][0], body)
            self.assertTrue(calls[0][1].startswith("nimda-provider"))
            status, _, _ = await request(self.app, "retry", body, base_path="/api/catalog-library/people/",
                headers=[(b"origin", b"https://untrusted.invalid")])
            self.assertEqual(status, 403)
            self.assertEqual(len(calls), 1)
