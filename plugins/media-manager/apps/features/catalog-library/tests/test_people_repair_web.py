from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from catalog_library import people_repair, providers, web
from work_catalog_yaml.jp_tv.browse_app import build_jp_tv_browse_app
from work_catalog_yaml.jp_tv.browse_settings import JpTvBrowseSettings
from work_catalog_yaml.operation_progress import report_progress
from test_playback_web import request


_ROUTES = ("preview", "apply")
_BASE_PATH = "/api/catalog-library/people/birthdays/"


class PeopleBirthdayRepairWebTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.db = self.base / "db"
        self.db.mkdir()
        self.settings = JpTvBrowseSettings(version=1, filesystem_root=self.db,
            resolved_default_readable=str(self.db / "fixture.yaml"), resolved_catalog_yaml_paths=(),
            enum_options={}, enum_labels={}, enum_section_labels={}, app_features=())
        self.settings_lookup = self.start_patch(web, "get_resolved_browse_settings", return_value=(self.settings, None))
        self.handlers = {action: self.start_patch(people_repair, action,
            side_effect=AssertionError("Unexpected birthday repair handler: " + action)) for action in _ROUTES}
        # Exercise the real HTTP and queue adapters only. Every persistence and
        # network boundary is guarded so failures cannot touch the user's data.
        self.guards = [self.start_patch(owner, name,
            side_effect=AssertionError("No DB/network access: " + name)) for owner, name in (
                (people_repair, "PeopleRepository"), (providers, "catalog_records"),
                (providers, "_request_json"), (providers, "_save_snapshot"),
                (providers, "_read_snapshot"), (people_repair.people.service, "apply_edits"))]
        self.app = build_jp_tv_browse_app(operation_log_root=self.base / "logs")
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.network_queue = self.start_patch(self.app.state.api_network_queue, "run",
            side_effect=AssertionError("Birthday repair must use only the local disk queue"))
        self.ref = {"yaml_source_rel": "fixture.yaml", "index_in_file": 0,
                    "source_sha256": "a" * 64, "record_sha256": "b" * 64}
        self.token_payload = {"kind": "people-birthday-repair", "ref": deepcopy(self.ref),
            "scope": "fixture-catalog", "people_scope": "fixture-people", "revision": "c" * 64,
            "proposal_sha256": "d" * 64, "expires": int(time.time()) + 300}
        self.token = providers._sign(self.token_payload)

    def start_patch(self, owner, name, **kwargs):
        replacement = patch.object(owner, name, **kwargs)
        value = replacement.start()
        self.addCleanup(replacement.stop)
        return value

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)
        self.assertEqual(list(self.db.iterdir()), [], "Adapter tests must not create or modify a catalog DB")
        for guard in self.guards:
            guard.assert_not_called()
        self.network_queue.assert_not_called()
        self.temporary.cleanup()

    async def request(self, action, body=None, **kwargs):
        return await request(self.app, action, body, base_path=_BASE_PATH, **kwargs)

    def assert_not_delegated(self):
        self.settings_lookup.assert_not_called()
        for handler in self.handlers.values():
            handler.assert_not_called()

    async def test_preview_and_apply_use_disk_queue_and_preserve_signed_payload(self):
        bodies = {"preview": {"ref": self.ref}, "apply": {"ref": self.ref, "preview_token": self.token}}
        for action, body in bodies.items():
            with self.subTest(action=action):
                received = []
                result = {"ok": True, "repair_count": 18, "warnings": [], "changes": [
                    {"id": "person_fixture", "name": "新井浩一", "diff": [
                        {"field": "birth_date", "before": {"day": 1}, "after": {"year": 1961}}]}]}
                if action == "preview":
                    result["preview_token"] = self.token

                def handler(value, *, settings):
                    received.append((deepcopy(value), settings, threading.current_thread().name))
                    if action == "apply":
                        self.assertEqual(providers._verify_token(value["preview_token"]), self.token_payload)
                    return result

                self.handlers[action].side_effect = handler
                operation_id = str(uuid4())
                status, content, headers = await self.request(action, body, headers=[
                    (b"origin", b"http://127.0.0.1:8765"), (b"x-nimda-operation-id", operation_id.encode())])
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(content), result)
                self.assertEqual(received[0][:2], (body, self.settings))
                self.assertTrue(received[0][2].startswith("nimda-disk"))
                self.assertIn(b"application/json", headers[b"content-type"])
                self.assertIn(b"no-store", headers[b"cache-control"])
                self.assertEqual(headers[b"x-nimda-operation-id"].decode(), operation_id)
                operation = self.app.state.operation_registry.snapshot(operation_id)
                self.assertEqual(operation["status"], "succeeded")
                self.assertEqual(operation["title"], _BASE_PATH + action)
                self.assertTrue(operation["log"]["available"])
                log = self.app.state.operation_registry.log_store.read(operation_id)
                self.assertIn(_BASE_PATH + action, log["content"])
                self.assertNotIn(self.token, log["content"])

    async def test_progress_details_are_searchable_in_operation_log(self):
        for action in _ROUTES:
            with self.subTest(action=action):
                def handler(_value, *, settings):
                    report_progress("生日修复待确认：新井浩一", detail="原始生日 1961年；birth_mon=19；birth_day=1",
                        context={"stage": "生日修复预览", "object": "person_fixture", "action": "仅年份兼容"})
                    return {"ok": True, "repair_count": 1, "warnings": []}

                self.handlers[action].side_effect = handler
                operation_id = str(uuid4())
                status, _content, _headers = await self.request(action, {"ref": self.ref},
                    headers=[(b"x-nimda-operation-id", operation_id.encode())])
                self.assertEqual(status, 200)
                registry = self.app.state.operation_registry
                self.assertEqual(registry.snapshot(operation_id)["status"], "succeeded")
                log = registry.log_store.read(operation_id)["content"]
                self.assertIn("生日修复待确认：新井浩一", log)
                self.assertIn("birth_mon=19", log)
                self.assertIn("birth_day=1", log)
                self.assertIn("[INFO]", log)

    async def test_warning_result_is_http_success_and_logged_as_warning(self):
        warning = "人物 fixture / birth_date：原始生日与结构化年份冲突；保留手工修改"
        for action in _ROUTES:
            with self.subTest(action=action):
                self.handlers[action].side_effect = None
                self.handlers[action].return_value = {"ok": True, "repair_count": 0, "warnings": [warning], "warning_count": 1}
                operation_id = str(uuid4())
                status, content, _headers = await self.request(action, {"ref": self.ref},
                    headers=[(b"x-nimda-operation-id", operation_id.encode())])
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(content)["warnings"], [warning])
                registry = self.app.state.operation_registry
                self.assertEqual(registry.snapshot(operation_id)["status"], "warning")
                log = registry.log_store.read(operation_id)["content"]
                self.assertIn(warning, log)
                self.assertIn("[WARN]", log)

    async def test_invalid_json_and_non_object_bodies_fail_before_handlers(self):
        for action in _ROUTES:
            for raw in (b"{broken", b"[]", b"null", b'"text"', b"true", b"0", b"\xff"):
                with self.subTest(action=action, raw=raw):
                    status, content, headers = await self.request(action, raw=raw)
                    self.assertEqual(status, 400)
                    self.assertFalse(json.loads(content)["ok"])
                    self.assertIn("JSON", json.loads(content)["error"])
                    operation_id = headers[b"x-nimda-operation-id"].decode()
                    self.assertEqual(self.app.state.operation_registry.snapshot(operation_id)["status"], "failed")
        self.assert_not_delegated()

    async def test_wrong_origin_and_cross_site_requests_fail_before_handlers(self):
        for action in _ROUTES:
            for headers in ([(b"origin", b"https://foreign.invalid")],
                            [(b"origin", b"http://127.0.0.1:9999")],
                            [(b"origin", b"http://127.0.0.1:8765"), (b"sec-fetch-site", b"cross-site")]):
                with self.subTest(action=action, headers=headers):
                    status, content, _headers = await self.request(action, {"ref": self.ref}, headers=headers)
                    self.assertEqual(status, 403)
                    self.assertFalse(json.loads(content)["ok"])
        self.assert_not_delegated()

    async def test_get_and_head_are_rejected_before_handlers(self):
        for action in _ROUTES:
            for method in ("GET", "HEAD"):
                with self.subTest(action=action, method=method):
                    status, _content, headers = await self.request(action, {"ref": self.ref}, method=method)
                    self.assertEqual(status, 405)
                    self.assertEqual(headers[b"allow"], b"POST")
        self.assert_not_delegated()

    async def test_errors_fail_operation_with_searchable_diagnostics_without_request_secrets(self):
        for action in _ROUTES:
            for error, expected in ((ValueError("人物生日或来源证据已变化，请重新预览"), 400),
                                    (OSError("人物生日 fixture 无法写入"), 500),
                                    (RuntimeError("生日修复 fixture 校验失败"), 500)):
                with self.subTest(action=action, error=error):
                    self.handlers[action].side_effect = error
                    operation_id = str(uuid4())
                    status, content, headers = await self.request(action,
                        {"ref": self.ref, "preview_token": self.token, "private_body": "not-for-logs"},
                        headers=[(b"x-nimda-operation-id", operation_id.encode())])
                    self.assertEqual(status, expected)
                    self.assertFalse(json.loads(content)["ok"])
                    self.assertEqual(json.loads(content)["error"], str(error))
                    self.assertEqual(headers[b"x-nimda-operation-id"].decode(), operation_id)
                    registry = self.app.state.operation_registry
                    operation = registry.snapshot(operation_id)
                    self.assertEqual(operation["status"], "failed")
                    self.assertIn(str(error), json.dumps(operation, ensure_ascii=False))
                    log = registry.log_store.read(operation_id)
                    self.assertTrue(Path(log["path"]).is_file())
                    for text in (str(error), type(error).__name__, _BASE_PATH + action, "[ERROR]"):
                        self.assertIn(text, log["content"])
                    self.assertNotIn("not-for-logs", log["content"])
                    self.assertNotIn(self.token, log["content"])


if __name__ == "__main__":
    unittest.main()
