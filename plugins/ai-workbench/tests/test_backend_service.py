"""Real lifecycle tests with deterministic providers and isolated temporary data."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "plugins/ai-workbench/backend"), str(ROOT / "apps/framework/worker")]
from ai_workbench.errors import ApiError, Cancelled
from ai_workbench.service import AiService


class MemoryVault:
    def __init__(self):
        self.values = {}

    def put(self, value):
        key = uuid.uuid4().hex
        self.values[key] = value
        return key

    def get(self, key):
        return self.values[key]

    def has(self, key):
        return key in self.values

    def delete(self, key):
        self.values.pop(key, None)


class Provider:
    action = "reply"
    entered = threading.Event()

    def __init__(self, connection, key):
        if key != "synthetic-credential":
            raise AssertionError("Unexpected credential")

    def models(self):
        return [{"id": "test-model", "label": "Test model"}]

    def run(self, model, messages, tools, *, emit, cancelled, request_tool):
        type(self).entered.set()
        if self.action == "wait":
            cancelled.wait(3)
            raise Cancelled()
        if self.action == "tool":
            result = request_tool(tools[0]["name"], {})
            emit({"type": "delta", "text": "count=" + str(result["data"]["count"])})
            return {"text": "count=" + str(result["data"]["count"])}
        if self.action == "approval":
            raise RuntimeError("unexpected synthetic provider exception")
        emit({"type": "delta", "text": "你好 "})
        emit({"type": "delta", "text": model})
        return {"text": "你好 " + model, "usage": {"input_tokens": 2, "output_tokens": 3}}


def inspect_fixture_connection(connection, has_key=False):
    if connection["kind"] in {"codex", "cursor"}:
        return {"status": "ready", "message": "Fixture executable", "resolvedExecutable": connection.get("executable") or sys.executable, "autoRepaired": False}
    return {"status": "ready" if has_key else "unconfigured", "message": "Fixture API"}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        inspection = patch("ai_workbench.service.inspect_connection", side_effect=inspect_fixture_connection)
        inspection.start()
        self.addCleanup(inspection.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.web = self.base / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<!doctype html><title>AI</title>", encoding="utf-8")
        self.vault = MemoryVault()
        Provider.action = "reply"
        Provider.entered.clear()
        self.service = AiService(self.base / "data", self.web, vault=self.vault, provider_factory=Provider)
        self.connection = self.service.save_connection({"name": "Synthetic", "kind": "openai-responses",
                                                        "baseUrl": "https://example.invalid/v1", "apiKey": "synthetic-credential"})

    def tearDown(self):
        if not self.service._closed:
            self.service.prepare_shutdown()
            for control in self.service._runs.values():
                if control.thread:
                    control.thread.join(4)
            self.service.close()
        self.tmp.cleanup()

    def conversation(self):
        return self.service.create_conversation({"connectionId": self.connection["id"], "model": "test-model"})

    def test_existing_conversation_cannot_be_redirected_to_a_different_provider(self):
        conversation = self.conversation()
        with self.assertRaises(ApiError) as error:
            self.service.save_connection({"id": self.connection["id"], "name": "Different provider", "kind": "openai-responses",
                                          "baseUrl": "https://another.invalid/v1", "apiKey": "replacement-credential"})
        self.assertEqual(error.exception.code, "CONNECTION_IN_USE")
        self.assertEqual(self.service.state()["connections"][0]["baseUrl"], "https://example.invalid/v1")
        self.assertEqual(self.service.conversation(conversation["id"])["messages"], [])
        # Renaming and credential rotation at the original endpoint remain possible.
        saved = self.service.save_connection({"id": self.connection["id"], "name": "Renamed", "kind": "openai-responses",
                                             "baseUrl": "https://example.invalid/v1", "apiKey": "replacement-credential"})
        self.assertEqual(saved["name"], "Renamed")

    def test_layout_preference_is_bounded_and_survives_reopening(self):
        self.assertEqual(self.service.state()["layout"], {"sidebarWidth": 238})
        self.assertEqual(self.service.save_layout({"sidebarWidth": 340}), {"sidebarWidth": 340})
        for invalid in (True, 179, 481, 300.5, "300"):
            with self.assertRaises(ApiError):
                self.service.save_layout({"sidebarWidth": invalid})
        with self.assertRaises(ApiError):
            self.service.save_layout({"sidebarWidth": 300, "path": "unrelated"})
        self.assertEqual(self.service.layout()["sidebarWidth"], 340)
        self.service.close()
        self.service = AiService(self.base / "data", self.web, vault=self.vault, provider_factory=Provider)
        self.assertEqual(self.service.state()["layout"]["sidebarWidth"], 340)

    def test_invalid_saved_layout_is_preserved_and_reported(self):
        invalid = {"id": "layout", "schemaVersion": 99, "sidebarWidth": 900}
        self.service.store.put("preferences", invalid)
        with self.assertRaises(ApiError) as error:
            self.service.layout()
        self.assertEqual(error.exception.code, "LAYOUT_DATA_INVALID")
        with self.assertRaises(ApiError):
            self.service.save_layout({"sidebarWidth": 300})
        self.assertEqual(self.service.store.get("preferences", "layout"), invalid)

    def complete(self, rid):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = self.service.run(rid)
            if result["run"]["status"] not in ("queued", "running", "waiting_approval", "waiting_tool", "cancelling"):
                return result
            time.sleep(.01)
        self.fail("Run did not reach terminal state")

    def test_stream_persistence_and_exact_event_cursor(self):
        conversation = self.conversation()
        rid = self.service.start_run(conversation["id"], {"text": "Hello"})["runId"]
        result = self.complete(rid)
        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual("".join(e["text"] for e in result["events"] if e["type"] == "delta"), "你好 test-model")
        cursor = result["nextSequence"]
        self.assertEqual(self.service.run(rid, cursor)["events"], [])
        self.assertEqual(self.service.run(rid, cursor)["nextSequence"], cursor)
        self.assertEqual(self.service.conversation(conversation["id"])["messages"][-1]["text"], "你好 test-model")
        self.service._runs[rid].thread.join()
        self.service.close()
        self.service = AiService(self.base / "data", self.web, vault=self.vault, provider_factory=Provider)
        self.assertEqual(self.service.conversation(conversation["id"])["messages"][-1]["text"], "你好 test-model")

    def test_terminal_poll_waits_for_final_events_before_returning(self):
        final_event_pending = threading.Event()
        release_final_event = threading.Event()
        poll_started = threading.Event()
        poll_finished = threading.Event()
        observed = {}
        original_emit = self.service._emit

        def pause_final_event(rid, event):
            if event.get("type") == "status" and event.get("status") == "completed":
                final_event_pending.set()
                if not release_final_event.wait(5):
                    raise AssertionError("Test did not release final event publication")
            return original_emit(rid, event)

        def poll(rid):
            poll_started.set()
            try:
                observed["result"] = self.service.run(rid)
            except Exception as error:
                observed["error"] = error
            finally:
                poll_finished.set()

        with patch.object(self.service, "_emit", side_effect=pause_final_event):
            rid = self.service.start_run(self.conversation()["id"], {"text": "Hello"})["runId"]
            reader = threading.Thread(target=poll, args=(rid,))
            try:
                self.assertTrue(final_event_pending.wait(2))
                reader.start()
                self.assertTrue(poll_started.wait(2))
                self.assertFalse(poll_finished.wait(.1), "Terminal polling exposed an incomplete event stream")
            finally:
                release_final_event.set()
                if reader.ident is not None:
                    reader.join(3)
                self.service._runs[rid].thread.join(3)
        self.assertTrue(poll_finished.is_set())
        self.assertNotIn("error", observed)
        result = observed["result"]
        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual(result["events"][-1]["type"], "completed")
        self.assertEqual(result["events"][-1]["text"], "你好 test-model")
        self.assertEqual(self.service.run(rid, result["nextSequence"])["events"], [])

    def test_cancel_is_request_then_terminal_and_shutdown_rejects_work(self):
        Provider.action = "wait"
        conversation = self.conversation()
        rid = self.service.start_run(conversation["id"], {"text": "Wait"})["runId"]
        self.assertTrue(Provider.entered.wait(1))
        response = self.service.cancel(rid)
        self.assertEqual(response["run"]["status"], "cancelling")
        self.assertEqual(self.complete(rid)["run"]["status"], "cancelled")
        self.service.prepare_shutdown()
        with self.assertRaises(ApiError) as error:
            self.service.start_run(conversation["id"], {"text": "Next"})
        self.assertEqual(error.exception.code, "SHUTTING_DOWN")

    def test_only_one_run_per_conversation(self):
        Provider.action = "wait"
        conversation = self.conversation()
        self.service.start_run(conversation["id"], {"text": "Wait"})
        self.assertTrue(Provider.entered.wait(1))
        with self.assertRaises(ApiError) as error:
            self.service.start_run(conversation["id"], {"text": "Duplicate"})
        self.assertEqual(error.exception.code, "CONVERSATION_BUSY")

    def test_model_change_keeps_history_and_uses_new_model_next_turn(self):
        conversation = self.conversation()
        first = self.service.start_run(conversation["id"], {"text": "First"})["runId"]
        self.complete(first)
        before = self.service.conversation(conversation["id"])
        changed = self.service.set_conversation_model(conversation["id"], {"model": "another-model"})
        self.assertEqual(changed["messages"], before["messages"])
        self.assertEqual(changed["connectionId"], before["connectionId"])
        second = self.service.start_run(conversation["id"], {"text": "Next"})["runId"]
        self.complete(second)
        self.assertEqual(self.service.conversation(conversation["id"])["messages"][-1]["text"], "你好 another-model")

    def test_model_change_refuses_active_run_and_invalid_payload(self):
        Provider.action = "wait"
        conversation = self.conversation()
        self.service.start_run(conversation["id"], {"text": "Wait"})
        self.assertTrue(Provider.entered.wait(1))
        with self.assertRaises(ApiError) as error:
            self.service.set_conversation_model(conversation["id"], {"model": "other"})
        self.assertEqual(error.exception.code, "CONVERSATION_BUSY")
        self.assertEqual(self.service.conversation(conversation["id"])["model"], "test-model")
        with self.assertRaises(ApiError):
            self.service.set_conversation_model(conversation["id"], {"model": "other", "connectionId": "different"})

    def test_workspaces_are_explicit_codex_metadata_and_cannot_read_arbitrary_paths(self):
        with self.assertRaises(ApiError) as error:
            self.service.workspaces(self.connection["id"])
        self.assertEqual(error.exception.code, "WORKSPACES_UNAVAILABLE")
        connection = self.service.save_connection({"name": "Codex", "kind": "codex", "workspace": str(self.base)})
        expected = {"workspaces": [{"path": str(self.base), "label": "Fixture", "source": "codex-app", "available": True}]}
        with patch("ai_workbench.codex_workspaces.list_workspaces", return_value=expected) as reader:
            self.assertEqual(self.service.workspaces(connection["id"]), expected)
            reader.assert_called_once_with(sys.executable, str(self.base.resolve()))

    def test_cursor_dispatch_uses_agent_identity_without_api_key(self):
        connection = self.service.save_connection({"name": "Cursor", "kind": "cursor", "workspace": str(self.base)})
        self.assertFalse(connection["hasKey"])
        with patch("ai_workbench.cursor_provider.list_models", return_value=[{"id": "cursor-fixture", "label": "Fixture"}]):
            self.assertEqual(self.service.models(connection["id"])["models"][0]["id"], "cursor-fixture")
        conversation = self.service.create_conversation({"connectionId": connection["id"], "model": "cursor-fixture"})
        with patch("ai_workbench.cursor_provider.run_cursor", return_value={"text": "Local agent reply", "threadId": "cursor-thread"}) as adapter:
            result = self.complete(self.service.start_run(conversation["id"], {"text": "Hello"})["runId"])
            self.assertEqual(result["run"]["status"], "completed")
            self.assertEqual(adapter.call_args.kwargs["model"], "cursor-fixture")
            self.assertEqual(adapter.call_args.kwargs["workspace"], str(self.base.resolve()))
        with patch("ai_workbench.cursor_provider.run_cursor", return_value={"text": "Continued", "threadId": "cursor-thread"}) as adapter:
            self.complete(self.service.start_run(conversation["id"], {"text": "Next"})["runId"])
            self.assertEqual(adapter.call_args.kwargs["thread_id"], "cursor-thread")
        with self.assertRaises(ApiError) as error:
            self.service.save_connection({"name": "Cursor", "kind": "cursor", "apiKey": "not-accepted"})
        self.assertEqual(error.exception.code, "INVALID_KEY")

    def test_metadata_query_is_counted_during_shutdown(self):
        entered, released = threading.Event(), threading.Event()
        def query():
            entered.set()
            released.wait(3)
            return []
        with patch.object(Provider, "models", side_effect=query):
            task = threading.Thread(target=lambda: self.service.models(self.connection["id"]))
            task.start()
            self.assertTrue(entered.wait(1))
            try:
                self.assertFalse(self.service.prepare_shutdown()["ready"])
            finally:
                released.set()
                task.join(3)
            self.assertTrue(self.service.prepare_shutdown()["ready"])

    def test_metadata_cleanup_failure_blocks_until_retry_confirms_exit(self):
        from ai_workbench.codex_provider import CodexError
        connection = self.service.save_connection({"name": "Codex", "kind": "codex"})
        failure = CodexError("CODEX_STOP_FAILED", "pending")
        failure.cleanupPending = True
        attempts = []
        def retry():
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("still alive")
        failure.retry_cleanup = retry
        with patch("ai_workbench.codex_provider.list_models", side_effect=failure):
            with self.assertRaises(CodexError):
                self.service.models(connection["id"])
        self.assertFalse(self.service.prepare_shutdown()["ready"])
        self.assertTrue(self.service.prepare_shutdown()["ready"])

    def test_real_tool_round_trip_and_stale_replies_rejected(self):
        Provider.action = "tool"
        conversation = self.conversation()
        descriptor = {"name": "tower_assistant__runtime_summary", "description": "Read count", "readOnly": True,
                      "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}
        rid = self.service.start_run(conversation["id"], {"text": "Read count", "tools": [descriptor]})["runId"]
        deadline = time.monotonic() + 2
        request = None
        while time.monotonic() < deadline:
            request = next((e for e in self.service.run(rid)["events"] if e["type"] == "tool_request"), None)
            if request:
                break
            time.sleep(.01)
        self.assertIsNotNone(request)
        self.assertEqual(request["arguments"], {})
        with self.assertRaises(ApiError):
            self.service.reply(rid, {"requestId": "stale", "result": {}}, "tool")
        result = {"source": {"moduleId": "tower-assistant", "version": "test"}, "data": {"count": 7}}
        self.service.reply(rid, {"requestId": request["requestId"], "result": result}, "tool")
        completed = self.complete(rid)
        self.assertEqual(completed["run"]["status"], "completed")
        self.assertEqual(self.service.conversation(conversation["id"])["messages"][-1]["text"], "count=7")
        with self.assertRaises(ApiError):
            self.service.reply(rid, {"requestId": request["requestId"], "result": result}, "tool")

    def test_private_key_not_returned_or_stored_in_database(self):
        self.assertTrue(self.connection["hasKey"])
        self.assertNotIn("credentialRef", self.connection)
        self.assertNotIn("synthetic-credential", json.dumps(self.service.state()))
        database = self.service.store._database
        self.assertNotIn("synthetic-credential", "\n".join(database.iterdump()))
        changed = self.service.save_connection({"id": self.connection["id"], "name": "Renamed", "kind": "openai-responses",
                                               "baseUrl": "https://example.invalid/v1", "apiKey": ""})
        self.assertTrue(changed["hasKey"])
        with self.assertRaises(ApiError) as error:
            self.service.save_connection({"id": self.connection["id"], "name": "Moved", "kind": "openai-responses",
                                          "baseUrl": "https://another.invalid/v1"})
        self.assertEqual(error.exception.code, "ENDPOINT_CHANGED")

    def test_data_root_has_single_authoritative_instance(self):
        with self.assertRaises(ApiError) as error:
            AiService(self.base / "data", self.web, vault=self.vault)
        self.assertEqual(error.exception.code, "DATA_BUSY")

    def test_incompatible_data_is_not_replaced(self):
        data = self.base / "future"
        data.mkdir()
        database = sqlite3.connect(data / "workbench.sqlite3")
        database.execute("PRAGMA user_version=99")
        database.close()
        with self.assertRaises(ApiError) as error:
            AiService(data, self.web, vault=self.vault)
        self.assertEqual(error.exception.code, "DATA_VERSION")
        with closing(sqlite3.connect(data / "workbench.sqlite3")) as database:
            self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], 99)

    def test_restart_marks_unfinished_work_interrupted(self):
        conversation = self.conversation()
        rid = uuid.uuid4().hex
        conversation["activeRun"] = rid
        self.service.store.put("conversations", conversation)
        self.service.store.put("runs", {"id": rid, "conversationId": conversation["id"], "status": "running"})
        self.service.close()
        self.service = AiService(self.base / "data", self.web, vault=self.vault)
        self.assertEqual(self.service.run(rid)["run"]["status"], "interrupted")
        self.assertIsNone(self.service.conversation(conversation["id"])["activeRun"])

    def test_provider_failure_is_not_success_and_does_not_expose_exception(self):
        Provider.action = "approval"
        conversation = self.conversation()
        rid = self.service.start_run(conversation["id"], {"text": "Fail"})["runId"]
        result = self.complete(rid)
        self.assertEqual(result["run"]["status"], "failed")
        self.assertNotIn("unexpected synthetic", result["run"]["error"])
        self.assertFalse(any(e["type"] == "completed" for e in result["events"]))

    def test_codex_approval_and_resume_are_real_callback_roundtrips(self):
        connection = self.service.save_connection({"name": "Codex fixture", "kind": "codex", "workspace": str(self.base)})
        conversation = self.service.create_conversation({"connectionId": connection["id"], "model": "fixture-model"})
        calls = []
        def adapter(**kwargs):
            calls.append(kwargs)
            choice = kwargs["request_approval"]({"title": "Read fixture", "detail": "Synthetic operation"})
            self.assertEqual(choice, "decline")
            kwargs["emit"]({"type": "delta", "text": "Declined"})
            return {"text": "Declined", "threadId": "native-fixture-thread"}
        with patch("ai_workbench.codex_provider.run_codex", side_effect=adapter):
            rid = self.service.start_run(conversation["id"], {"text": "First"})["runId"]
            deadline = time.monotonic() + 2
            approval = None
            while time.monotonic() < deadline:
                approval = self.service.run(rid)["run"].get("approval")
                if approval:
                    break
                time.sleep(.01)
            self.assertIsNotNone(approval)
            self.service.reply(rid, {"requestId": approval["requestId"], "decision": "decline"}, "approval")
            self.assertEqual(self.complete(rid)["run"]["status"], "completed")
        self.assertIsNone(calls[0]["thread_id"])
        self.assertEqual(self.service.conversation(conversation["id"])["nativeThreadId"], "native-fixture-thread")

    def test_codex_failed_cleanup_is_unknown_and_blocks_safe_shutdown(self):
        from ai_workbench.codex_provider import CodexError
        connection = self.service.save_connection({"name": "Codex fixture", "kind": "codex", "workspace": str(self.base)})
        conversation = self.service.create_conversation({"connectionId": connection["id"], "model": "fixture-model"})
        permit_cleanup = threading.Event()
        def cleanup():
            if not permit_cleanup.is_set():
                raise RuntimeError("still alive")
        def adapter(**kwargs):
            kwargs["cancelled"].set()
            error = CodexError("CODEX_STOP_FAILED", "Synthetic process has not stopped.")
            error.cleanupPending = True
            error.retry_cleanup = cleanup
            raise error
        with patch("ai_workbench.codex_provider.run_codex", side_effect=adapter):
            rid = self.service.start_run(conversation["id"], {"text": "Start"})["runId"]
            self.assertEqual(self.complete(rid)["run"]["status"], "unknown")
        self.service._runs[rid].thread.join()
        self.assertEqual(self.service.prepare_shutdown(), {"ready": False, "active": 1})
        permit_cleanup.set()
        self.assertEqual(self.service.prepare_shutdown(), {"ready": True, "active": 0})
        self.assertEqual(self.service.run(rid)["run"]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
