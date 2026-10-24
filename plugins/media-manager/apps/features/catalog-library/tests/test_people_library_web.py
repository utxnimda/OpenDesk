"""Independent people/work clear route origin safety and queue composition."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from catalog_library import character_merge, people_library, role_catalog, work_details, web
from work_catalog_yaml.jp_tv.browse_app import build_jp_tv_browse_app
from work_catalog_yaml.jp_tv.browse_settings import JpTvBrowseSettings
from test_playback_web import request


class PeopleLibraryWebTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.db = self.base / "db"
        self.db.mkdir()
        self.settings = JpTvBrowseSettings(version=1, filesystem_root=self.db,
            resolved_default_readable=str(self.db / "fixture.yaml"), resolved_catalog_yaml_paths=(),
            enum_options={}, enum_labels={}, enum_section_labels={}, app_features=())
        setting = patch.object(web, "get_resolved_browse_settings", return_value=(self.settings, None))
        self.settings_lookup = setting.start()
        self.addCleanup(setting.stop)
        self.handlers = {}
        for action in ("browse", "detail", "edit_preview", "edit_apply", "clear_preview", "clear_apply", "source_preview", "source_apply", "image_preview", "image_apply", "image_candidates"):
            handler = patch.object(people_library, action, side_effect=AssertionError("Unexpected service access"))
            self.handlers["people-library/" + action.replace("_", "-")] = handler.start()
            self.addCleanup(handler.stop)
        for action in ("preview", "apply"):
            handler = patch.object(work_details, action, side_effect=AssertionError("Unexpected clear access"))
            self.handlers["work-details/clear-" + action] = handler.start()
            self.addCleanup(handler.stop)
        for module, prefix, actions in ((character_merge, "characters/merge/", ("candidates", "preview", "apply")),
                                        (role_catalog, "characters/materialize/", ("preview", "apply"))):
            for action in actions:
                handler = patch.object(module, action, side_effect=AssertionError("Unexpected role access"))
                self.handlers[prefix + action] = handler.start()
                self.addCleanup(handler.stop)
        self.app = build_jp_tv_browse_app(operation_log_root=self.base / "logs")
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)
        self.assertEqual(list(self.db.iterdir()), [])
        self.temporary.cleanup()

    async def request(self, action, body=None, **kwargs):
        return await request(self.app, action, body, base_path="/api/catalog-library/", **kwargs)

    async def test_all_ten_routes_preserve_payload_and_only_source_preview_uses_network_queue(self):
        for action, mock in self.handlers.items():
            with self.subTest(action=action):
                calls = []

                def handler(body, *, settings):
                    calls.append((body, settings, threading.current_thread().name))
                    return {"ok": True, "fixture": True}

                mock.side_effect = handler
                body = {"person_id": "person_fixture", "version": "private-version", "preview_token": "private-token",
                    "selected_fields": ["name_cn"], "groups": ["description"]}
                operation_id = str(uuid4())
                status, content, headers = await self.request(action, body,
                    headers=[(b"x-nimda-operation-id", operation_id.encode())])
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(content), {"ok": True, "fixture": True})
                self.assertEqual(calls[0][:2], (body, self.settings))
                expected_queue = "nimda-provider" if action in {"people-library/source-preview", "people-library/image-preview", "people-library/image-candidates"} else "nimda-disk"
                self.assertTrue(calls[0][2].startswith(expected_queue))
                self.assertIn(b"no-store", headers[b"cache-control"])
                registry = self.app.state.operation_registry
                self.assertEqual(registry.snapshot(operation_id)["status"], "succeeded")
                log = registry.log_store.read(operation_id)["content"]
                self.assertNotIn("private-token", log)
                self.assertNotIn("private-version", log)

    async def test_invalid_origin_json_and_method_never_reach_services(self):
        for action in self.handlers:
            for raw in (b"[]", b"null", b"{broken"):
                status, _, _ = await self.request(action, raw=raw)
                self.assertEqual(status, 400)
            status, _, _ = await self.request(action, {}, headers=[(b"origin", b"https://foreign.invalid")])
            self.assertEqual(status, 403)
            for method in ("GET", "HEAD"):
                status, _, _ = await self.request(action, {}, method=method)
                self.assertEqual(status, 405)
        for mock in self.handlers.values():
            mock.assert_not_called()
        self.settings_lookup.assert_not_called()

    async def test_people_catalog_frontend_mount_exists(self):
        self.assertTrue(any(getattr(route, "path", "") == "/features/people-catalog" for route in self.app.routes))

    async def test_image_preview_rejects_oversized_body_before_service_or_decoding(self):
        from catalog_library.people_images import MAX_REQUEST_BYTES
        status, content, _ = await self.request("people-library/image-preview", raw=b'{"image_base64":"' + b'a' * MAX_REQUEST_BYTES + b'"}')
        self.assertEqual(status, 400)
        self.assertIn("安全大小", json.loads(content)["error"])
        self.handlers["people-library/image-preview"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
