from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from ai_workbench import cursor_provider as provider


class CursorProviderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="opendesk-cursor-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.report = self.root / "report.jsonl"
        self.events, self.processes = [], []
        self.cancelled = threading.Event()
        self.fixture = Path(__file__).parent / "fixtures" / "cursor_acp.py"
        self.original_popen = subprocess.Popen

    def patch_client(self, scenario="normal"):
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(provider, "_command", return_value=[sys.executable, "-u", str(self.fixture),
                          "--scenario", scenario, "--report", str(self.report)]).start()
        for name, value in (("RPC_TIMEOUT", 1.5), ("TURN_TIMEOUT", 1.5), ("INTERRUPT_TIMEOUT", .25), ("CLOSE_TIMEOUT", .25)):
            mock.patch.object(provider, name, value).start()
        def start(*args, **kwargs):
            process = self.original_popen(*args, **kwargs)
            self.processes.append(process)
            return process
        mock.patch.object(provider.subprocess, "Popen", side_effect=start).start()

    def run_turn(self, **overrides):
        values = dict(executable=None, workspace=str(self.root), model="fixture-a[]", prompt="你好",
                      thread_id=None, emit=self.events.append, cancelled=self.cancelled,
                      request_approval=lambda payload: "decline")
        values.update(overrides)
        try:
            return provider.run_cursor(**values)
        finally:
            for process in self.processes:
                self.assertIsNotNone(process.poll(), "Only a reaped owned process may finish a run.")

    def messages(self):
        return [json.loads(line) for line in self.report.read_text(encoding="utf-8").splitlines()]

    def test_dynamic_model_catalog_has_no_session_or_inference(self):
        self.patch_client()
        result = provider.list_models(workspace=str(self.root))
        self.assertEqual([model["id"] for model in result], ["fixture-a[]", "fixture-b[effort=high]"])
        self.assertEqual(result[1]["parameters"], [{"id":"effort","label":"Effort","defaultValue":"high", "options":[{"value":"low","label":"Low"},{"value":"high","label":"High"}]}])
        methods = [item.get("method") for item in self.messages() if "method" in item]
        self.assertEqual(methods, ["initialize", "cursor/list_available_models"])
        self.assertIsNotNone(self.processes[0].poll())

    def test_model_failure_does_not_expose_stderr_or_invent_catalog(self):
        self.patch_client("models_failure")
        with self.assertRaises(provider.CursorError) as failure:
            provider.list_models(workspace=str(self.root))
        self.assertEqual(failure.exception.code, "CURSOR_AUTH_REQUIRED")
        self.assertNotIn("sk-secret", str(failure.exception))

    def test_streams_only_answer_and_enforces_ask_and_selected_model(self):
        self.patch_client()
        result = self.run_turn()
        self.assertEqual(result, {"text": "你好，已完成。", "threadId": "cursor-fixture"})
        self.assertEqual("".join(item["text"] for item in self.events if item["type"] == "delta"), result["text"])
        messages = self.messages()
        self.assertNotIn("--force", messages[0]["command"])
        self.assertIn("--disable-auto-update", messages[0]["command"])
        initialized = next(item for item in messages if item.get("method") == "initialize")
        self.assertEqual(initialized["params"]["clientCapabilities"], {})
        model = next(item for item in messages if item.get("method") == "session/set_model")
        self.assertEqual(model["params"]["modelId"], "fixture-a[]")
        mode = next(item for item in messages if item.get("method") == "session/set_mode")
        self.assertEqual(mode["params"]["modeId"], "ask")
        self.assertNotIn("你好", json.dumps(messages[0]))

    def test_resume_only_explicit_session_does_not_duplicate_history(self):
        self.patch_client()
        result = self.run_turn(thread_id="our-session")
        self.assertEqual(result["threadId"], "our-session")
        self.assertEqual(result["text"], "你好，已完成。")
        methods = [item.get("method") for item in self.messages()]
        self.assertIn("session/load", methods)
        self.assertNotIn("session/list", methods)
        self.assertNotIn("session/new", methods)

    def test_permission_roundtrip_only_selects_once_and_redacts_detail(self):
        self.patch_client("approval")
        approvals = []
        def approve(payload):
            approvals.append(payload)
            return "accept"
        self.run_turn(request_approval=approve)
        self.assertNotIn("private-token", json.dumps(approvals))
        response = next(item for item in self.messages() if item.get("id") == "permission-fixture")
        self.assertEqual(response["result"]["outcome"], {"outcome": "selected", "optionId": "once"})

    def test_invalid_approval_is_rejected(self):
        self.patch_client("approval")
        self.run_turn(request_approval=lambda payload: "allow_always")
        response = next(item for item in self.messages() if item.get("id") == "permission-fixture")
        self.assertEqual(response["result"]["outcome"]["optionId"], "deny")

    def test_always_only_permission_never_grants(self):
        self.patch_client("always_only")
        self.run_turn(request_approval=lambda payload: "accept")
        response = next(item for item in self.messages() if item.get("id") == "permission-fixture")
        self.assertEqual(response["result"]["outcome"], {"outcome": "cancelled"})

    def test_wrong_session_and_unknown_client_requests_are_rejected(self):
        for scenario in ("wrong_approval", "unknown_request"):
            with self.subTest(scenario=scenario):
                self.patch_client(scenario)
                with self.assertRaises(provider.CursorError) as failure:
                    self.run_turn()
                self.assertEqual(failure.exception.code, "CURSOR_UNSUPPORTED_REQUEST")
                mock.patch.stopall()

    def test_cancel_waits_for_terminal_and_process_exit(self):
        self.patch_client("cancel")
        def emit(event):
            if event["type"] == "delta":
                self.cancelled.set()
        with self.assertRaises(provider.CursorError) as failure:
            self.run_turn(emit=emit)
        self.assertEqual(failure.exception.code, "CANCELLED")
        self.assertIn("session/cancel", [item.get("method") for item in self.messages()])

    def test_unresponsive_cancel_reaps_owned_child(self):
        self.patch_client("ignore_cancel")
        def emit(event):
            if event["type"] == "delta":
                self.cancelled.set()
        with self.assertRaises(provider.CursorError) as failure:
            self.run_turn(emit=emit)
        self.assertEqual(failure.exception.code, "CANCELLED")

    def test_pre_cancel_does_not_create_process(self):
        self.patch_client()
        self.cancelled.set()
        with self.assertRaises(provider.CursorError):
            self.run_turn()
        self.assertEqual(self.processes, [])

    def test_protocol_and_provider_failures_are_not_success(self):
        cases = {"auth": "CURSOR_AUTH_REQUIRED", "wrong_version": "CURSOR_PROTOCOL_UNSUPPORTED",
                 "invalid_json": "CURSOR_PROTOCOL_ERROR", "unsupported_model": "CURSOR_PROTOCOL_UNSUPPORTED",
                 "incomplete": "CURSOR_INCOMPLETE", "empty": "CURSOR_EMPTY_RESULT",
                 "mode_change": "CURSOR_MODE_CHANGED", "exit": "CURSOR_EXITED", "timeout": "CURSOR_TIMEOUT",
                 "turn_timeout": "CURSOR_TIMEOUT"}
        for scenario, code in cases.items():
            with self.subTest(scenario=scenario):
                self.patch_client(scenario)
                with self.assertRaises(provider.CursorError) as failure:
                    self.run_turn()
                self.assertEqual(failure.exception.code, code)
                self.assertNotIn("sk-secret", str(failure.exception))
                mock.patch.stopall()

    def test_resume_requires_peer_capability(self):
        self.patch_client("no_resume")
        with self.assertRaises(provider.CursorError) as failure:
            self.run_turn(thread_id="saved")
        self.assertEqual(failure.exception.code, "CURSOR_RESUME_UNSUPPORTED")

    def test_cleanup_failure_retains_retry_callback(self):
        process = provider._Process.__new__(provider._Process)
        process.closed = False
        with mock.patch.object(process, "_close", side_effect=[OSError("locked"), None]):
            with self.assertRaises(provider.CursorError) as failure:
                process.close()
            self.assertTrue(failure.exception.cleanupPending)
            self.assertEqual(failure.exception.code, "CURSOR_STOP_FAILED")
            failure.exception.retry_cleanup()
        self.assertTrue(process.closed)

    def test_missing_agent_and_editor_path_are_clear_errors(self):
        with mock.patch.object(provider.shutil, "which", return_value=None), mock.patch.object(provider.Path, "is_file", return_value=False):
            with self.assertRaises(provider.CursorError) as failure:
                provider._command(None)
        self.assertEqual(failure.exception.code, "CURSOR_NOT_FOUND")
        editor = self.root / "Cursor.exe"
        editor.touch()
        with self.assertRaises(provider.CursorError) as failure:
            provider._command(str(editor))
        self.assertEqual(failure.exception.code, "CURSOR_WRONG_EXECUTABLE")

    @unittest.skipUnless(os.name == "nt", "Official Windows runtime layout")
    def test_official_windows_runtime_is_started_without_evaluating_script(self):
        launcher = self.root / "cursor-agent.cmd"
        launcher.write_text("this script must never be evaluated", encoding="utf-8")
        runtime = self.root / "node.exe"
        runtime.touch()
        entry = self.root / "index.js"
        entry.touch()
        self.assertEqual(provider._command(str(launcher)), [str(runtime.resolve()), str(entry.resolve())])

    def test_provider_environment_does_not_inherit_other_connection_secrets(self):
        self.patch_client()
        with mock.patch.dict(os.environ, {"CURSOR_API_KEY": "private", "CURSOR_AUTH_TOKEN": "private", "OPENAI_API_KEY": "private"}):
            self.run_turn()
        kwargs = provider.subprocess.Popen.call_args.kwargs
        self.assertFalse(kwargs["shell"])
        self.assertNotIn("CURSOR_API_KEY", kwargs["env"])
        self.assertNotIn("CURSOR_AUTH_TOKEN", kwargs["env"])
        self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
        self.assertEqual(kwargs["env"]["CURSOR_AGENT_DISABLE_DEBUG_LOG"], "1")

    def test_cursor_acp_catalog_preserves_parameter_order_and_rejects_invalid_values(self):
        options = provider._model_options([{"value": "auto-smart", "name": "Auto", "configOptions": [
            {"id": "optimize_for", "name":"Optimize For", "currentValue": "balanced", "options":[{"value":"balanced","name":"Balance"}]},
            {"id": "fast", "name":"Fast", "currentValue": "true", "options":[{"value":"true","name":"On"},{"value":"false","name":"Off"}]}]}])
        self.assertEqual(options[0]["id"], "auto-smart[optimize_for=balanced,fast=true]")
        self.assertEqual(provider._model_options([{"value":"plain-model", "name":"Plain", "configOptions":[]}])[0]["id"], "plain-model[]")
        with self.assertRaises(provider.CursorError):
            provider._model_options([{"value": "auto-smart", "name": "Auto", "configOptions": [{"id": "effort"}]}])
        with self.assertRaises(provider.CursorError):
            provider._model_options([{"value": "auto-smart", "name": "Auto", "configOptions": [{"id": "effort", "currentValue": "high,fast=true"}]}])
        error = provider._provider_error({"code": -32602, "message": "Invalid params", "data": {"message": "Invalid model value: auto"}})
        self.assertEqual(error.code, "CURSOR_MODEL_UNAVAILABLE")

    def test_run_validates_parameters_before_prompt_and_selects_actual_variant(self):
        self.patch_client()
        self.run_turn(model="fixture-b[effort=high]", model_options={"effort":"low"})
        messages=self.messages()
        selected=[item["params"] for item in messages if item.get("method")=="session/set_config_option"]
        self.assertEqual([(item["configId"],item["value"]) for item in selected],[("model","fixture-b"),("effort","low")])
        initialized=next(item for item in messages if item.get("method")=="initialize")
        self.assertTrue(initialized["params"]["clientCapabilities"]["_meta"]["parameterizedModelPicker"])
        self.assertIn("cursor/list_available_models",[item.get("method") for item in messages])

    def test_unknown_parameter_or_value_cannot_reach_inference(self):
        for options in ({"effort":"ultra"},{"unknown":"high"}):
            with self.subTest(options=options):
                self.patch_client()
                if self.report.exists():
                    self.report.unlink()
                with self.assertRaises(provider.CursorError) as failure:
                    self.run_turn(model="fixture-b[effort=high]",model_options=options)
                self.assertEqual(failure.exception.code,"MODEL_OPTION_UNSUPPORTED")
                self.assertNotIn("session/prompt",[item.get("method") for item in self.messages()])
                mock.patch.stopall()

    def test_parameter_confirmation_mismatch_stops_before_inference(self):
        self.patch_client("options_mismatch")
        with self.assertRaises(provider.CursorError) as failure:
            self.run_turn(model="fixture-b[effort=high]",model_options={"effort":"low"})
        self.assertEqual(failure.exception.code,"CURSOR_MODEL_OPTIONS_MISMATCH")
        self.assertNotIn("session/prompt",[item.get("method") for item in self.messages()])

    def test_changed_catalog_default_does_not_break_saved_variant_options(self):
        catalog=[{"id":"model[effort=low,fast=true]","parameters":[
            {"id":"effort","defaultValue":"low","options":[{"value":"low"},{"value":"high"}]},
            {"id":"fast","defaultValue":"true","options":[{"value":"true"},{"value":"false"}]}]}]
        self.assertEqual(provider.validate_model_options("model[effort=high,fast=false]",catalog,{"effort":"low"}),{"effort":"low"})
        self.assertEqual(provider._model_with_options("model[effort=high,fast=false]",catalog,{"effort":"low"}),"model[effort=low,fast=false]")

    def test_inspection_only_checks_files_without_starting_process(self):
        launcher=self.root/"cursor-agent.cmd"
        launcher.touch()
        (self.root/"node.exe").touch()
        (self.root/"index.js").touch()
        with mock.patch.object(provider.subprocess,"Popen",side_effect=AssertionError("must not start")):
            info=provider.inspect_connection(str(launcher))
            missing=provider.inspect_connection(str(self.root/"absent.cmd"))
        self.assertTrue(info["available"])
        self.assertEqual(info["executable"],str(launcher.resolve()))
        self.assertFalse(missing["available"])


if __name__ == "__main__":
    unittest.main()
