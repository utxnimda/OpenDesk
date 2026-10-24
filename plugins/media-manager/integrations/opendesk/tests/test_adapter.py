"""Synthetic adapter tests: never open the user's configured catalog or media."""
import asyncio
from contextlib import asynccontextmanager
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nimda_opendesk.controller import CAPABILITIES, Controller, IDENTITY
from nimda_opendesk.controller import normalize_workspace_path, setup_python
from nimda_opendesk.native import NativeWindows
from nimda_opendesk.protocol import ProtocolError, frames, parse
from nimda_opendesk.security import SessionGate
from nimda_opendesk.worker import serve


class FakeNative:
    def __init__(self):
        self.actions = []
    def navigate(self, url): self.actions.append(("navigate", url))
    def present(self, mode): self.actions.append(("present", mode))
    def configure_services(self): self.actions.append(("services", True))
    def cleanup_services(self): self.actions.append(("services", False))
    def destroy(self): self.actions.append(("destroy",))


class FakeController:
    loading = False
    def __init__(self): self.saved = {}
    def status(self, include_url=True): return {"configured": False, "mode": "embedded", "workspace": "", "busy": False}
    def preferences(self): return self.saved
    def save_preference(self, key, value): self.saved[key] = value


async def request(gate, path="/api/write", method="POST", headers=None, body=b"{}", query="", client="127.0.0.1", host="127.0.0.1:45678"):
    messages = []
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "scheme": "http",
             "method": method, "path": path, "raw_path": path.encode(), "query_string": query.encode(),
             "root_path": "", "client": (client, 12345), "server": ("127.0.0.1", 45678),
             "headers": [(b"host", host.encode()), *((k.lower().encode(), v.encode()) for k, v in (headers or {}).items())]}
    async def receive(): return {"type": "http.request", "body": body, "more_body": False}
    async def send(message): messages.append(message)
    await gate(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    content = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), content


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.controller = FakeController()
        self.gate = SessionGate(self.controller, token="synthetic-session-token")
        self.gate.bind("http://127.0.0.1:45678/")
        self.gate.queues = lambda: ()
        self.headers = {"X-Nimda-Session": self.gate.token, "Origin": self.gate.origin}
        async def target(scope, receive, send):
            await JSONResponse({"headers": {k.decode(): v.decode() for k, v in scope["headers"]}, "query": scope["query_string"].decode()})(scope, receive, send)
        self.gate.target = target

    async def test_all_private_requests_require_session(self):
        for path, method in [("/api/read", "GET"), ("/api/write", "POST"), ("/__opendesk/preferences", "GET"), ("/__opendesk/status", "GET")]:
            self.assertEqual((await request(self.gate, path, method))[0], 401)

    async def test_exact_loopback_authority_and_origin_are_enforced(self):
        for host in ["evil.example:45678", "localhost:45678", "127.0.0.1:9999", "127.0.0.1:45678.evil.example"]:
            self.assertEqual((await request(self.gate, headers=self.headers, host=host))[0], 403)
        self.assertEqual((await request(self.gate, headers=self.headers, client="192.168.1.2"))[0], 403)
        for origin in ["null", "https://evil.example", "http://127.0.0.1:45679"]:
            self.assertEqual((await request(self.gate, headers={**self.headers, "Origin": origin}))[0], 401)

    async def test_authenticated_embedded_requests_preserve_original_origin_guard(self):
        code, headers, content = await request(self.gate, headers={**self.headers, "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(code, 200)
        projected = json.loads(content)["headers"]
        self.assertEqual(projected["sec-fetch-site"], "same-origin")
        self.assertEqual(projected["origin"], self.gate.origin)
        self.assertNotIn("x-nimda-session", projected)
        self.assertEqual(headers[b"referrer-policy"], b"no-referrer")

    async def test_resource_token_is_readonly_scoped_and_removed_before_business_logic(self):
        for path in ["/api/catalog-library/assets/abc.webp", "/api/catalog-library/people/assets/abc.jpg", "/api/operations/task-1/log/file"]:
            code, _, content = await request(self.gate, path, "GET", query="download=1&__nimda_session=" + self.gate.token)
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(content)["query"], "download=1")
            self.assertEqual((await request(self.gate, path, "POST", query="__nimda_session=" + self.gate.token))[0], 401)
        self.assertEqual((await request(self.gate, "/api/read", "GET", query="__nimda_session=" + self.gate.token))[0], 401)
        self.assertEqual((await request(self.gate, "/api/catalog-library/assets/a.jpg", "GET", query="__nimda_session=wrong"))[0], 401)
        self.assertEqual((await request(self.gate, "/api/catalog-library/assets/a.jpg", "GET", query="__nimda_session=%E4%B8%AD%E6%96%87"))[0], 401)
        self.assertEqual((await request(self.gate, headers={"X-Nimda-Session": "中文"}))[0], 401)

    async def test_public_bootstrap_has_no_session_secret(self):
        code, _, content = await request(self.gate, "/__opendesk/bootstrap.js", "GET")
        self.assertEqual(code, 200)
        self.assertNotIn(self.gate.token.encode(), content)

    async def test_preference_keys_size_and_methods_are_bounded(self):
        valid = json.dumps({"key": "nimda.catalog-library.view", "value": "cards"}).encode()
        self.assertEqual((await request(self.gate, "/__opendesk/preferences", headers=self.headers, body=valid))[0], 200)
        self.assertEqual(self.controller.saved["nimda.catalog-library.view"], "cards")
        for value in [{"key": "arbitrary.secret", "value": "x"}, {"key": "nimda.a", "value": "x" * 16001}, {"key": "nimda.a", "value": 1}, {"key": "nimda.a", "value": "x", "extra": True}]:
            self.assertEqual((await request(self.gate, "/__opendesk/preferences", headers=self.headers, body=json.dumps(value).encode()))[0], 400)

    async def test_draining_atomically_stops_new_requests_and_waits_started_work(self):
        started, release = asyncio.Event(), asyncio.Event()
        class Queue:
            closing = False
            def begin_shutdown(inner): inner.closing = True
            def snapshot(inner): return {"running": 0, "queued": 0, "closing": inner.closing}
        queue = Queue()
        self.gate.queues = lambda: (queue,)
        async def target(scope, receive, send):
            started.set()
            await release.wait()
            await JSONResponse({"ok": True})(scope, receive, send)
        self.gate.target = target
        task = asyncio.create_task(request(self.gate, headers=self.headers))
        await started.wait()
        self.assertEqual(self.gate.begin_shutdown(), {"ready": False, "active": 1})
        self.assertTrue(queue.closing)
        self.assertEqual((await request(self.gate, headers=self.headers))[0], 503)
        release.set()
        self.assertEqual((await task)[0], 200)
        self.assertEqual(self.gate.begin_shutdown(), {"ready": True, "active": 0})

    async def test_config_page_and_entry_injection_are_module_only(self):
        self.gate.target = None
        code, _, content = await request(self.gate, "/", "GET")
        self.assertEqual(code, 200)
        self.assertIn(b'/__opendesk/bootstrap.js', content)
        self.assertNotIn(self.gate.token.encode(), content)
        async def target(scope, receive, send):
            from starlette.responses import Response
            await Response(b'createApp(App).mount("#app");', media_type="text/javascript")(scope, receive, send)
        self.gate.target = target
        _, _, content = await request(self.gate, "/src/main.js", "GET")
        self.assertTrue(content.startswith(b"await window.NimdaOpenDeskReady;"))


class FakeMutex:
    held = False
    def acquire(self):
        if FakeMutex.held: return False
        FakeMutex.held = True
        return True
    def release(self): FakeMutex.held = False


class FakeServer:
    def __init__(self, gate, preferred_port):
        self.gate, self.port = gate, preferred_port or 45678
        self.loop = asyncio.new_event_loop()
    def start(self):
        self.gate.loop = self.loop
        self.thread = threading.Thread(target=self.loop.run_forever)
        self.thread.start()
        return "http://127.0.0.1:" + str(self.port) + "/"
    def stop(self):
        if self.loop.is_closed():
            return True
        if self.gate._lifespan:
            asyncio.run_coroutine_threadsafe(self.gate._lifespan.__aexit__(None, None, None), self.loop).result(2)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(2)
        self.loop.close()
        return True


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace"
        for directory in ["config", "data"]: (self.workspace / directory).mkdir(parents=True)
        self.native = FakeNative()
        self.started = 0
        @asynccontextmanager
        async def lifespan(app):
            self.started += 1
            yield
            self.started -= 1
        self.controller = Controller(self.base, self.base / "module-data", self.native,
            app_factory=lambda root, workspace: Starlette(lifespan=lifespan), mutex_factory=FakeMutex, server_factory=FakeServer)
        self.controller.gate.queues = lambda: ()
        FakeMutex.held = False

    def tearDown(self):
        if self.controller._loader: self.controller._loader.join(2)
        if not self.controller._shutdown: self.controller.close()
        FakeMutex.held = False
        self.temp.cleanup()

    def test_handshake_identity_and_exact_capabilities_without_workspace(self):
        hello = self.controller.dispatch("runtime.hello", {})
        self.assertEqual(hello["worker"], "opendesk-python")
        self.assertEqual(hello["capabilities"], CAPABILITIES)
        self.assertEqual(len(CAPABILITIES), 9)
        self.assertEqual(self.controller.dispatch("module.identity", {}), IDENTITY)
        self.assertFalse(self.controller.status()["configured"])

    def test_configure_reuses_port_backend_mutex_and_native_popup(self):
        first = self.controller.dispatch("module.webapp", {})["url"]
        loading = self.controller.configure(str(self.workspace))
        self.assertTrue(loading["busy"])
        self.controller._loader.join(2)
        self.assertTrue(self.controller.status()["configured"])
        self.assertEqual(self.controller.status()["url"], first)
        self.assertTrue(FakeMutex.held)
        self.assertEqual(self.started, 1)
        self.assertEqual(self.controller.presentation("popup")["mode"], "popup")
        self.controller.popup_closed()
        self.assertEqual(self.controller.status()["mode"], "embedded")
        self.assertTrue(FakeMutex.held)
        self.controller.presentation("embedded")
        self.assertEqual(self.started, 1)
        self.controller.close()
        self.assertFalse(FakeMutex.held)
        self.assertEqual(self.started, 0)

    def test_start_retries_failed_settings_save_without_restarting_service(self):
        with patch.object(self.controller, "_write_json", side_effect=OSError("synthetic save failure")):
            with self.assertRaises(OSError):
                self.controller.start()
        server = self.controller.server
        self.assertFalse(self.controller._started)
        self.assertEqual(self.native.actions, [])
        first = self.controller.start()
        self.assertIs(self.controller.server, server)
        self.assertTrue(server.thread.is_alive())
        self.assertTrue(self.controller._started)
        self.assertEqual(json.loads((self.base / "module-data" / "settings.json").read_text())["port"], server.port)
        self.assertEqual(self.native.actions, [("navigate", first["url"])])
        self.controller.start()
        self.assertEqual(self.native.actions, [("navigate", first["url"])])

    def test_start_retries_native_navigation_without_restarting_service(self):
        with patch.object(self.native, "navigate", side_effect=RuntimeError("synthetic navigation failure")):
            with self.assertRaises(RuntimeError):
                self.controller.start()
        server = self.controller.server
        self.assertFalse(self.controller._started)
        result = self.controller.start()
        self.assertIs(self.controller.server, server)
        self.assertEqual(self.native.actions, [("navigate", result["url"])])

    def test_existing_original_app_blocks_module_without_writes(self):
        self.controller.start()
        FakeMutex.held = True
        self.controller.configure(str(self.workspace))
        self.controller._loader.join(2)
        status = self.controller.status()
        self.assertFalse(status["configured"])
        self.assertIn("正在运行", status["error"])
        self.assertEqual(self.started, 0)
        self.assertEqual(list((self.workspace / "data").iterdir()), [])

    def test_configure_accepts_explorer_direction_marks_and_quoted_paths(self):
        self.controller.start()
        self.controller.configure("\u202a" + str(self.workspace))
        self.controller._loader.join(2)
        self.assertTrue(self.controller.status()["configured"])
        for value in ['"' + str(self.workspace) + '"', ' \u202a"\u200e' + str(self.workspace) + '\u202c"\u200f ']:
            with self.subTest(value=value):
                status = self.controller.configure(value)
                self.assertTrue(status["configured"])
                self.assertEqual(status["workspace"], str(self.workspace.resolve()))
        self.assertEqual(self.started, 1)

    def test_workspace_normalization_preserves_internal_filename_contents(self):
        actual = str(self.base / "含 空格和\u200e标记" / "workspace")
        self.assertEqual(normalize_workspace_path(' \u202a"' + actual + '"\u202c '), actual)

    def test_workspace_normalization_rejects_controls_empty_and_relative_paths(self):
        for value in [None, "", " \u202a\u202c ", '\u202a""\u202c', str(self.workspace) + "\x00", "\n" + str(self.workspace), str(self.workspace) + "\t", str(self.workspace) + "\x7f"]:
            with self.subTest(value=value), self.assertRaises(ProtocolError) as caught:
                self.controller.configure(value)
            self.assertEqual(caught.exception.code, "INVALID_PARAMS")
        for value in ['\u202a"relative-workspace"', str(self.base / "missing-workspace")]:
            with self.subTest(value=value), self.assertRaises(ProtocolError) as caught:
                self.controller.configure(value)
            self.assertEqual(caught.exception.code, "INVALID_WORKSPACE")
        self.assertFalse(self.controller.loading)
        self.assertEqual(self.started, 0)

    def test_shutdown_requires_drain_and_refuses_new_configuration(self):
        self.controller.loading = True
        self.assertFalse(self.controller.prepare_shutdown()["ready"])
        with self.assertRaises(ProtocolError): self.controller.dispatch("runtime.shutdown", {})
        with self.assertRaises(ProtocolError): self.controller.configure(str(self.workspace))
        self.controller.loading = False
        self.assertEqual(self.controller.dispatch("runtime.shutdown", {}), {"status": "stopping"})

    def test_preferences_persist_under_module_data_only(self):
        self.controller.save_preference("jp-tv-browse-theme", "sakura")
        saved = json.loads((self.base / "module-data" / "preferences.json").read_text())
        self.assertEqual(saved, {"schemaVersion": 1, "jp-tv-browse-theme": "sakura"})
        self.assertEqual(self.controller.preferences(), {"jp-tv-browse-theme": "sakura"})
        self.assertEqual(list((self.workspace / "data").iterdir()), [])

    def test_stdio_validation_errors_keep_channel_usable(self):
        raw = b'not json\n' + json.dumps({"protocolVersion": 1, "id": "hello", "method": "runtime.hello", "params": {}}).encode() + b'\n' + json.dumps({"protocolVersion": 1, "id": "stop", "method": "runtime.shutdown", "params": {}}).encode() + b'\n'
        writer = io.BytesIO()
        serve(self.controller, io.BytesIO(raw), writer)
        rows = [json.loads(line) for line in writer.getvalue().splitlines()]
        self.assertEqual(rows[0]["error"]["code"], "INVALID_JSON")
        self.assertEqual(rows[1]["result"]["capabilities"], CAPABILITIES)
        self.assertEqual(rows[2]["result"]["status"], "stopping")

    def test_failed_shutdown_keeps_control_channel_and_mutex_until_successful_retry(self):
        self.controller.start()
        self.controller.configure(str(self.workspace))
        self.controller._loader.join(2)
        stop = self.controller.server.stop
        attempts = []
        def stop_with_failure():
            attempts.append(True)
            return False if len(attempts) == 1 else stop()
        self.controller.server.stop = stop_with_failure
        frames = [{"protocolVersion": 1, "id": rid, "method": method, "params": {}}
                  for rid, method in [("failed", "runtime.shutdown"), ("ping", "runtime.ping"), ("retry", "runtime.shutdown")]]
        controller, native = self.controller, self.native
        observations = []
        class Writer(io.BytesIO):
            def write(inner, value):
                observations.append((json.loads(value), controller._shutdown, FakeMutex.held, ("destroy",) in native.actions))
                return super().write(value)
        with self.assertLogs(level="ERROR"):
            serve(self.controller, io.BytesIO(b"\n".join(json.dumps(row).encode() for row in frames) + b"\n"), Writer())
        self.assertEqual(observations[0][0]["error"]["code"], "INTERNAL_ERROR")
        self.assertEqual(observations[0][1:], (False, True, False))
        self.assertEqual(observations[1][0]["result"]["message"], "pong")
        self.assertEqual(observations[1][1:], (False, True, False))
        self.assertEqual(observations[2][0]["result"], {"status": "stopping"})
        self.assertEqual(observations[2][1:], (True, False, True))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.native.actions.count(("destroy",)), 1)
        self.assertEqual(self.started, 0)

    def test_native_shutdown_failure_retains_mutex_and_can_be_retried(self):
        self.controller.start()
        self.controller.configure(str(self.workspace))
        self.controller._loader.join(2)
        with patch.object(self.native, "destroy", side_effect=RuntimeError("synthetic destroy failure")):
            with self.assertRaises(RuntimeError):
                self.controller.dispatch("runtime.shutdown", {})
        self.assertFalse(self.controller._shutdown)
        self.assertTrue(FakeMutex.held)
        self.assertEqual(self.controller.dispatch("runtime.shutdown", {}), {"status": "stopping"})
        self.assertTrue(self.controller._shutdown)
        self.assertFalse(FakeMutex.held)


class ProtocolTests(unittest.TestCase):
    def test_oversized_frame_drained_before_next_request(self):
        values = list(frames(io.BytesIO(b"x" * 70000 + b"\n{}\n")))
        self.assertIsInstance(values[0], ProtocolError)
        self.assertEqual(values[1], b"{}")
    def test_duplicate_keys_and_unknown_envelope_fields_rejected(self):
        for raw in [b'{"id":"a","id":"b"}', b'{"protocolVersion":1,"id":"a","method":"x","params":{},"extra":0}', b'{"protocolVersion":true,"id":"a","method":"x","params":{}}']:
            with self.assertRaises(ProtocolError): parse(raw)


class ConfigurationTests(unittest.TestCase):
    def test_legacy_documents_are_read_without_writing_and_upgraded_on_explicit_save(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            originals = {
                "settings.json": b'{ "port": 45678, "workspace": "C:/synthetic-workspace" }\n',
                "preferences.json": b'{ "jp-tv-browse-theme": "sakura", "nimda.removed": null }\n',
            }
            for name, raw in originals.items():
                (root / name).write_bytes(raw)
            controller = Controller(root, root, FakeNative())
            for name, raw in originals.items():
                self.assertEqual((root / name).read_bytes(), raw)
            self.assertEqual(controller._settings, {"port": 45678, "workspace": "C:/synthetic-workspace"})
            self.assertEqual(controller.preferences(), {"jp-tv-browse-theme": "sakura", "nimda.removed": None})
            controller.save_preference("nimda.layout", "cards")
            self.assertEqual((root / "settings.json").read_bytes(), originals["settings.json"])
            controller._write_json("settings.json", controller._settings)
            for name in originals:
                self.assertEqual(json.loads((root / name).read_text())["schemaVersion"], 1)
            restored = Controller(root, root, FakeNative())
            self.assertEqual(restored._settings, controller._settings)
            self.assertEqual(restored.preferences(), controller.preferences())
            self.assertNotIn("schemaVersion", restored.preferences())

    def test_unknown_schema_and_invalid_structures_preserve_all_original_bytes(self):
        cases = [
            ("settings.json", {"schemaVersion": 2, "port": 45678}),
            ("preferences.json", {"schemaVersion": 2, "jp-tv-browse-theme": "sakura"}),
            ("settings.json", {"schemaVersion": True}),
            ("settings.json", {"port": "45678"}),
            ("settings.json", {"workspace": []}),
            ("settings.json", {"unknown": "setting"}),
            ("preferences.json", {"schemaVersion": 1, "nimda.view": {"unexpected": "object"}}),
            ("preferences.json", {"unrelated": "value"}),
            ("preferences.json", []),
        ]
        for name, value in cases:
            with self.subTest(name=name, value=value), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                originals = {"settings.json": b'{ "port": 45678 }\n', "preferences.json": b'{}\n'}
                originals[name] = (json.dumps(value) + "\n").encode()
                for filename, raw in originals.items():
                    (root / filename).write_bytes(raw)
                with self.assertRaises((RuntimeError, ValueError)):
                    Controller(root, root, FakeNative())
                for filename, raw in originals.items():
                    self.assertEqual((root / filename).read_bytes(), raw)
                self.assertEqual(sorted(path.name for path in root.iterdir()), ["preferences.json", "settings.json"])

    def test_schema_metadata_cannot_be_written_as_a_business_preference(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            controller = Controller(root, root, FakeNative())
            with self.assertRaises(ValueError):
                controller.save_preference("schemaVersion", "999")
            self.assertEqual(controller.preferences(), {})
            self.assertFalse((root / "preferences.json").exists())


class NativeTests(unittest.TestCase):
    def test_popup_close_hides_without_destroying_backend_or_document(self):
        class Event:
            def __iadd__(self, value): self.handler = value; return self
        actions = []
        window = SimpleNamespace(events=SimpleNamespace(closing=Event()),
            hide=lambda: actions.append("hide"), show=lambda: actions.append("show"),
            restore=lambda: actions.append("restore"), destroy=lambda: actions.append("destroy"),
            load_url=lambda url: actions.append(("load", url)))
        def create(*args, **kwargs):
            self.assertTrue(kwargs["hidden"])
            return window
        webview = SimpleNamespace(create_window=create, start=lambda started, **kwargs: started())
        controller = SimpleNamespace(popup_closed=lambda: actions.append("embedded"))
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {"webview": webview}):
            native = NativeWindows(folder)
            native.navigate("http://127.0.0.1:45678/#synthetic")
            native.run(controller)
            native.present("popup")
            self.assertFalse(window.events.closing.handler())
            self.assertEqual(actions[-2:], ["hide", "embedded"])
            self.assertNotIn("destroy", actions)
            self.assertEqual(sum(isinstance(row, tuple) and row[0] == "load" for row in actions), 1)
            native.destroy()
            self.assertTrue(window.events.closing.handler())
            self.assertEqual(actions[-1], "destroy")


@unittest.skipUnless(os.environ.get("MEDIA_MANAGER_TEST_APPLICATION_ROOT"), "Set MEDIA_MANAGER_TEST_APPLICATION_ROOT for the original queue integration test")
class OriginalQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_queue_cancels_pending_and_finishes_started_work(self):
        setup_python(Path(os.environ["MEDIA_MANAGER_TEST_APPLICATION_ROOT"]))
        from work_catalog_yaml.api_runtime import ApiWorkQueue, WorkQueueClosed
        work = ApiWorkQueue()
        started, release = threading.Event(), threading.Event()
        results = []
        def first():
            started.set()
            release.wait(5)
            results.append("finished")
        running = asyncio.create_task(work.run(first))
        await asyncio.to_thread(started.wait, 2)
        queued = asyncio.create_task(work.run(lambda: results.append("must-not-run")))
        await asyncio.sleep(0)
        self.assertEqual(work.snapshot()["queued"], 1)
        gate = SessionGate(FakeController())
        gate.queues = lambda: (work,)
        self.assertEqual(gate.begin_shutdown(), {"ready": False, "active": 1})
        self.assertEqual(work.snapshot()["queued"], 0)
        with self.assertRaises(WorkQueueClosed): await work.run(lambda: None)
        release.set()
        await running
        with self.assertRaises(WorkQueueClosed): await queued
        self.assertEqual(results, ["finished"])
        self.assertTrue(gate.begin_shutdown()["ready"])
        await work.close()


if __name__ == "__main__": unittest.main()
