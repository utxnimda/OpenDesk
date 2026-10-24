from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from catalog_library import playback, web
from work_catalog_yaml.jp_tv.browse_app import build_jp_tv_browse_app
from work_catalog_yaml.jp_tv.browse_settings import JpTvBrowseSettings


_ROUTES = {"scan": "scan", "save": "save", "open": "open_chapter", "select": "select"}


async def request(app, action, body=None, *, raw=None, headers=(), method="POST",
                  base_path="/api/catalog-library/playback/"):
    """Dispatch ASGI directly without a listener, browser, or native player."""
    messages = []
    sent = False
    payload = raw if raw is not None else json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
    path = base_path.rstrip("/") + "/" + action

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)

    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
               "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "", "query_string": b"",
               "server": ("127.0.0.1", 8765), "client": ("127.0.0.1", 12345),
               "headers": [(b"host", b"127.0.0.1:8765"), (b"content-type", b"application/json"), *headers]}, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    content = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], content, dict(start["headers"])


class PlaybackWebTest(unittest.IsolatedAsyncioTestCase):
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
        self.original_handlers = {action: getattr(playback, name) for action, name in _ROUTES.items()}
        self.handlers = {}
        for action, name in _ROUTES.items():
            replacement = patch.object(playback, name, side_effect=AssertionError("Unexpected playback handler: " + action))
            self.handlers[action] = replacement.start()
            self.addCleanup(replacement.stop)
        # Invalid requests must fail before disk access. Success adapters are
        # mocked above, and these guards prevent accidental DB/player calls.
        self.guards = []
        for owner, name in ((playback.service, "detail"), (playback.service, "apply_edits"),
                            (playback.media_files, "scan_media_files"), (playback.media_files, "open_media_file"),
                            (playback.media_files, "probe_media_duration")):
            replacement = patch.object(owner, name, side_effect=AssertionError("No real DB/media access: " + name))
            self.guards.append(replacement.start())
            self.addCleanup(replacement.stop)
        self.app = build_jp_tv_browse_app(operation_log_root=self.base / "logs")
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.ref = {"yaml_source_rel": "fixture.yaml", "index_in_file": 0,
                    "source_sha256": "a" * 64, "record_sha256": "b" * 64}

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)
        self.assertEqual(list(self.db.iterdir()), [], "HTTP adapter tests never create or modify a catalog DB")
        self.temporary.cleanup()

    def assert_untouched(self):
        for handler in self.handlers.values():
            handler.assert_not_called()
        self.settings_lookup.assert_not_called()
        for guard in self.guards:
            guard.assert_not_called()

    async def test_all_four_post_routes_delegate_unchanged_and_record_success(self):
        bodies = {
            "scan": {"ref": self.ref, "resource_index": 1, "numbering_mode": "local"},
            "save": {"ref": self.ref, "resource_index": 1, "revision": "c" * 64,
                     "assignments": [{"chapter_key": "id:bangumi:35", "relative_path": "_Disc/01.mkv"}]},
            "open": {"ref": self.ref, "resource_index": 1, "revision": "c" * 64,
                     "chapter_key": "id:bangumi:35", "relative_path": "_Disc/01.mkv"},
            "select": {"ref": self.ref, "resource_index": 1, "numbering_mode": "continuous"},
        }
        for action, body in bodies.items():
            with self.subTest(action=action):
                for handler in self.handlers.values():
                    handler.reset_mock()
                handler = self.handlers[action]
                handler.side_effect = None
                result = {"ok": True, "action": action, "fixture": {"keep": [1, "章节"]}}
                handler.return_value = result
                operation_id = str(uuid4())
                status, content, headers = await request(self.app, action, body,
                    headers=[(b"origin", b"http://127.0.0.1:8765"), (b"x-nimda-operation-id", operation_id.encode())])
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(content), result)
                self.assertIn(b"application/json", headers[b"content-type"])
                self.assertIn(b"no-store", headers[b"cache-control"])
                self.assertEqual(headers[b"x-nimda-operation-id"].decode(), operation_id)
                handler.assert_called_once_with(body, settings=self.settings)
                for other_action, other in self.handlers.items():
                    if other_action != action:
                        other.assert_not_called()
                operation = self.app.state.operation_registry.snapshot(operation_id)
                self.assertEqual(operation["status"], "succeeded")
                self.assertTrue(operation["log"]["available"])
        for guard in self.guards:
            guard.assert_not_called()

    async def test_all_routes_reject_invalid_json_or_non_object_bodies_before_delegation(self):
        for action in _ROUTES:
            for raw in (b"{broken", b"[]", b"null", b'"text"', b"true", b"0", b"\xff"):
                with self.subTest(action=action, raw=raw):
                    status, content, headers = await request(self.app, action, raw=raw)
                    self.assertEqual(status, 400)
                    result = json.loads(content)
                    self.assertFalse(result["ok"])
                    self.assertIn("JSON", result["error"])
                    operation = self.app.state.operation_registry.snapshot(headers[b"x-nimda-operation-id"].decode())
                    self.assertEqual(operation["status"], "failed")
        self.assert_untouched()

    async def test_all_routes_require_a_ref_before_attempting_db_or_media_access(self):
        for action in _ROUTES:
            self.handlers[action].side_effect = self.original_handlers[action]
            for body in ({}, {"ref": None}, {"ref": []}, {"ref": "arbitrary-path", "resource_index": 0}):
                with self.subTest(action=action, body=body):
                    status, content, _headers = await request(self.app, action, body)
                    self.assertEqual(status, 400)
                    result = json.loads(content)
                    self.assertFalse(result["ok"])
                    self.assertIn("ref", result["error"])
        for guard in self.guards:
            guard.assert_not_called()

    async def test_invalid_resource_indexes_return_400_without_scanning_or_opening(self):
        detail = {"resources": [{"press_path": "Fixture_BDRip"}], "record": {}, "ref": self.ref}
        with patch.object(playback.service, "detail", return_value=detail) as lookup:
            for action in _ROUTES:
                self.handlers[action].side_effect = self.original_handlers[action]
                for index in (None, True, -1, 1, "0", [], {}):
                    with self.subTest(action=action, resource_index=index):
                        status, content, _headers = await request(self.app, action, {"ref": self.ref, "resource_index": index})
                        self.assertEqual(status, 400)
                        result = json.loads(content)
                        self.assertFalse(result["ok"])
                        self.assertIn("压制资源", result["error"])
            self.assertEqual(lookup.call_count, len(_ROUTES) * 7)
        for guard in self.guards:
            guard.assert_not_called()

    async def test_service_validation_and_io_failures_return_json_and_failed_operations(self):
        for action in _ROUTES:
            for error, expected in ((ValueError("invalid playback input fixture"), 400),
                                    (OSError("unreadable media fixture"), 500),
                                    (RuntimeError("player unavailable fixture"), 500)):
                with self.subTest(action=action, error=error):
                    self.handlers[action].side_effect = error
                    operation_id = str(uuid4())
                    status, content, headers = await request(self.app, action, {"ref": self.ref},
                        headers=[(b"x-nimda-operation-id", operation_id.encode())])
                    self.assertEqual(status, expected)
                    result = json.loads(content)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["error"], str(error))
                    self.assertEqual(headers[b"x-nimda-operation-id"].decode(), operation_id)
                    operation = self.app.state.operation_registry.snapshot(operation_id)
                    self.assertEqual(operation["status"], "failed")
                    self.assertIn(str(error), json.dumps(operation))
        for guard in self.guards:
            guard.assert_not_called()

    async def test_wrong_origin_and_cross_site_requests_cannot_reach_playback(self):
        for action in _ROUTES:
            for headers in ([(b"origin", b"https://foreign.invalid")],
                            [(b"origin", b"http://127.0.0.1:9999")],
                            [(b"origin", b"http://127.0.0.1:8765"), (b"sec-fetch-site", b"cross-site")]):
                with self.subTest(action=action, headers=headers):
                    status, content, _headers = await request(self.app, action, {"ref": self.ref}, headers=headers)
                    self.assertEqual(status, 403)
                    self.assertFalse(json.loads(content)["ok"])
        self.assert_untouched()

    async def test_get_and_head_cannot_scan_save_open_or_probe_media(self):
        for action in _ROUTES:
            for method in ("GET", "HEAD"):
                with self.subTest(action=action, method=method):
                    status, _content, headers = await request(self.app, action, {"ref": self.ref}, method=method)
                    self.assertEqual(status, 405)
                    self.assertEqual(headers[b"allow"], b"POST")
        self.assert_untouched()

    async def test_independent_duration_endpoint_is_removed(self):
        status, _content, _headers = await request(self.app, "durations", {"ref": self.ref, "paths": ["01.mkv"]})
        self.assertEqual(status, 404)
        self.assert_untouched()


if __name__ == "__main__":
    unittest.main()
