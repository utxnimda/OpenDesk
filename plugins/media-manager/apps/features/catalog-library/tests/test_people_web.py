from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from catalog_library import people, web
from work_catalog_yaml.jp_tv.browse_app import build_jp_tv_browse_app
from work_catalog_yaml.jp_tv.browse_settings import JpTvBrowseSettings
from test_playback_web import request


_ROUTES = ("preview", "apply")
_BASE_PATH = "/api/catalog-library/people/"


class PeopleWebTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.db = self.base / "db"
        self.db.mkdir()
        self.people_root = self.base / "people"
        self.settings = JpTvBrowseSettings(version=1, filesystem_root=self.db,
            resolved_default_readable=str(self.db / "fixture.yaml"), resolved_catalog_yaml_paths=(),
            enum_options={}, enum_labels={}, enum_section_labels={}, app_features=())
        self.settings_lookup = self.start_patch(web, "get_resolved_browse_settings", return_value=(self.settings, None))
        self.original_handlers = {action: getattr(people, action) for action in _ROUTES}
        self.handlers = {action: self.start_patch(people, action,
            side_effect=AssertionError("Unexpected people handler: " + action)) for action in _ROUTES}
        # Adapters never access real people/catalog files or the network. Tests
        # that restore service validation must fail before these boundaries.
        self.guards = [self.start_patch(owner, name,
            side_effect=AssertionError("No DB/network access: " + name)) for owner, name in (
                (people.service, "apply_edits"), (people.providers, "catalog_records"),
                (people.providers, "_request_json"), (people.providers, "_save_snapshot"),
                (people.providers, "_read_snapshot"))]
        self.repository_factory = self.start_patch(people, "PeopleRepository")
        self.repository = self.repository_factory.return_value
        self.repository.root = self.people_root
        for name in ("read", "revision", "apply_batch"):
            guard = getattr(self.repository, name)
            guard.side_effect = AssertionError("No people DB access: " + name)
            self.guards.append(guard)
        self.app = build_jp_tv_browse_app(operation_log_root=self.base / "logs")
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.ref = {"yaml_source_rel": "fixture.yaml", "index_in_file": 0,
                    "source_sha256": "a" * 64, "record_sha256": "b" * 64}

    def start_patch(self, owner, name, **kwargs):
        replacement = patch.object(owner, name, **kwargs)
        result = replacement.start()
        self.addCleanup(replacement.stop)
        return result

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)
        self.assertEqual(list(self.db.iterdir()), [], "HTTP adapter tests must not modify the catalog")
        self.assertFalse(self.people_root.exists(), "HTTP adapter tests must not create a people DB")
        self.temporary.cleanup()

    async def request(self, action, body=None, **kwargs):
        return await request(self.app, action, body, base_path=_BASE_PATH, **kwargs)

    def assert_no_io(self):
        for guard in self.guards:
            guard.assert_not_called()

    def assert_not_delegated(self):
        for handler in self.handlers.values():
            handler.assert_not_called()
        self.settings_lookup.assert_not_called()
        self.repository_factory.assert_not_called()
        self.assert_no_io()

    async def test_post_routes_keep_payloads_and_use_network_preview_disk_apply_queues(self):
        bodies = {"preview": {"ref": self.ref, "subject_id": 12},
                  "apply": {"ref": self.ref, "subject_id": 12,
                            "snapshot_id": "fixture-snapshot", "preview_token": "fixture-token"}}
        for action, body in bodies.items():
            with self.subTest(action=action):
                received = []
                result = {"ok": True, "people": {"characters": [{"name": "角色", "actors": [{"name": "声优"}]}]},
                          "warnings": [], "action": action}

                def handler(value, *, settings):
                    received.append((value, settings, threading.current_thread().name))
                    return result

                self.handlers[action].side_effect = handler
                operation_id = str(uuid4())
                status, content, headers = await self.request(action, body, headers=[
                    (b"origin", b"http://127.0.0.1:8765"), (b"x-nimda-operation-id", operation_id.encode())])
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(content), result)
                self.assertEqual(received[0][:2], (body, self.settings))
                self.assertTrue(received[0][2].startswith("nimda-provider" if action == "preview" else "nimda-disk"))
                self.assertIn(b"application/json", headers[b"content-type"])
                self.assertIn(b"no-store", headers[b"cache-control"])
                self.assertEqual(headers[b"x-nimda-operation-id"].decode(), operation_id)
                operation = self.app.state.operation_registry.snapshot(operation_id)
                self.assertEqual(operation["status"], "succeeded")
                self.assertTrue(operation["log"]["available"])
                self.assertEqual(operation["title"], _BASE_PATH + action)
        self.assert_no_io()

    async def test_waiting_provider_preview_does_not_block_the_disk_queue(self):
        started, release = threading.Event(), threading.Event()

        def preview(_body, *, settings):
            started.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release the preview")
            return {"ok": True, "preview": True}

        self.handlers["preview"].side_effect = preview
        self.handlers["apply"].side_effect = None
        self.handlers["apply"].return_value = {"ok": True, "applied": True}
        pending = asyncio.create_task(self.request("preview", {"ref": self.ref}))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            status, content, _headers = await asyncio.wait_for(self.request("apply", {"ref": self.ref}), timeout=2)
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(content)["applied"])
        finally:
            release.set()
            status, _content, _headers = await pending
        self.assertEqual(status, 200)
        self.assert_no_io()

    async def test_partial_people_result_is_http_success_with_searchable_warning(self):
        warning = "Bangumi 作品 4035 / person:4 / birth_mon：字段值 19 无效；未获取的字段留空，已有资料保留"
        for action in _ROUTES:
            self.handlers[action].side_effect = None
            self.handlers[action].return_value = {"ok": True, "warnings": [warning], "warning_count": 1,
                "people": {"characters": [], "persons": [{"name": "人物", "birth_date": {}}], "warnings": []}}
            operation_id = str(uuid4())
            status, content, _headers = await self.request(action, {"ref": self.ref},
                headers=[(b"x-nimda-operation-id", operation_id.encode())])
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(content)["warning_count"], 1)
            self.assertEqual(json.loads(content)["warnings"], [warning])
            registry = self.app.state.operation_registry
            self.assertEqual(registry.snapshot(operation_id)["status"], "warning")
            log = registry.log_store.read(operation_id)["content"]
            self.assertIn(warning, log)
            self.assertIn("[WARN]", log)
        self.assert_no_io()

    async def test_invalid_json_and_non_object_bodies_fail_before_delegation(self):
        for action in _ROUTES:
            for raw in (b"{broken", b"[]", b"null", b'"text"', b"true", b"0", b"\xff"):
                with self.subTest(action=action, raw=raw):
                    status, content, headers = await self.request(action, raw=raw)
                    self.assertEqual(status, 400)
                    result = json.loads(content)
                    self.assertFalse(result["ok"])
                    self.assertIn("JSON", result["error"])
                    operation = self.app.state.operation_registry.snapshot(headers[b"x-nimda-operation-id"].decode())
                    self.assertEqual(operation["status"], "failed")
        self.assert_not_delegated()

    async def test_wrong_origin_and_cross_site_requests_cannot_fetch_or_write_people(self):
        for action in _ROUTES:
            for headers in ([(b"origin", b"https://foreign.invalid")],
                            [(b"origin", b"http://127.0.0.1:9999")],
                            [(b"origin", b"http://127.0.0.1:8765"), (b"sec-fetch-site", b"cross-site")]):
                with self.subTest(action=action, headers=headers):
                    status, content, _headers = await self.request(action, {"ref": self.ref}, headers=headers)
                    self.assertEqual(status, 403)
                    self.assertFalse(json.loads(content)["ok"])
        self.assert_not_delegated()

    async def test_get_and_head_do_not_fetch_snapshots_or_write_people(self):
        for action in _ROUTES:
            for method in ("GET", "HEAD"):
                with self.subTest(action=action, method=method):
                    status, _content, headers = await self.request(action, {"ref": self.ref}, method=method)
                    self.assertEqual(status, 405)
                    self.assertEqual(headers[b"allow"], b"POST")
        self.assert_not_delegated()

    async def test_service_errors_produce_failed_operation_and_searchable_diagnostic_log(self):
        for action in _ROUTES:
            for error, expected in ((ValueError("人物引用已变化，请重新预览"), 400),
                                    (OSError("人物 YAML 无法写入 fixture"), 500),
                                    (RuntimeError("Bangumi 人物读取失败 fixture"), 500)):
                with self.subTest(action=action, error=error):
                    self.handlers[action].side_effect = error
                    operation_id = str(uuid4())
                    status, content, headers = await self.request(action, {"ref": self.ref, "private_body": "not-for-logs"},
                        headers=[(b"x-nimda-operation-id", operation_id.encode())])
                    self.assertEqual(status, expected)
                    result = json.loads(content)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["error"], str(error))
                    self.assertEqual(headers[b"x-nimda-operation-id"].decode(), operation_id)
                    registry = self.app.state.operation_registry
                    operation = registry.snapshot(operation_id)
                    self.assertEqual(operation["status"], "failed")
                    self.assertIn(str(error), json.dumps(operation, ensure_ascii=False))
                    log = registry.log_store.read(operation_id)
                    self.assertTrue(Path(log["path"]).is_file())
                    self.assertIn(str(error), log["content"])
                    self.assertIn(type(error).__name__, log["content"])
                    self.assertIn(_BASE_PATH + action, log["content"])
                    self.assertIn("[ERROR]", log["content"])
                    self.assertNotIn("not-for-logs", log["content"])
        self.assert_no_io()

    async def test_missing_or_invalid_ref_is_rejected_by_people_services_without_io(self):
        for action in _ROUTES:
            self.handlers[action].side_effect = self.original_handlers[action]
        token = people.providers._sign({"kind": "people", "ref": self.ref, "subject_id": 12,
            "snapshot_id": "fixture-snapshot", "scope": people.providers._catalog_scope(self.settings),
            "people_scope": people._root_scope(self.repository), "expires": int(time.time()) + 60})
        for action in _ROUTES:
            for invalid_ref in (None, [], "arbitrary-path", {}, {"yaml_source_rel": "fixture.yaml"}):
                for omit in (False, True) if invalid_ref is None else (False,):
                    with self.subTest(action=action, ref=invalid_ref, omit=omit):
                        body = {} if omit else {"ref": invalid_ref}
                        if action == "apply":
                            body.update(snapshot_id="fixture-snapshot", preview_token=token)
                        status, content, _headers = await self.request(action, body)
                        self.assertEqual(status, 400)
                        self.assertFalse(json.loads(content)["ok"])
        self.assert_no_io()

    async def test_invalid_confirmation_tokens_cannot_read_snapshot_or_write(self):
        self.handlers["apply"].side_effect = self.original_handlers["apply"]
        for token in (None, "", "invalid", "a" * 50 + "." + "b" * 64):
            with self.subTest(token=token):
                status, content, _headers = await self.request("apply", {"ref": self.ref, "preview_token": token})
                self.assertEqual(status, 400)
                self.assertFalse(json.loads(content)["ok"])
                self.assertIn("凭据", json.loads(content)["error"])
        self.repository_factory.assert_not_called()
        self.assert_no_io()


if __name__ == "__main__":
    unittest.main()
