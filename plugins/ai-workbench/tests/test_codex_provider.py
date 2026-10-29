from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from ai_workbench import codex_provider as provider
from ai_workbench import codex_discovery as discovery


class CodexProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="opendesk-codex-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = self.root / "report.jsonl"
        self.events = []
        self.processes = []
        self.cancelled = threading.Event()
        self.fixture = Path(__file__).parent / "fixtures" / "codex_app_server.py"
        self.original_popen = subprocess.Popen

    def client_patch(self, scenario="normal"):
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(provider, "_command", return_value=[sys.executable, "-u", str(self.fixture),
                                                              "--scenario", scenario, "--report", str(self.report)]).start()
        mock.patch.object(provider, "RPC_TIMEOUT", 1.5).start()
        mock.patch.object(provider, "TURN_TIMEOUT", 1.5).start()
        mock.patch.object(provider, "INTERRUPT_TIMEOUT", 0.25).start()
        mock.patch.object(provider, "CLOSE_TIMEOUT", 0.25).start()

        def start(*args, **kwargs):
            process = self.original_popen(*args, **kwargs)
            self.processes.append(process)
            return process
        mock.patch.object(provider.subprocess, "Popen", side_effect=start).start()

    def run_turn(self, **overrides):
        args = dict(executable=None, workspace=str(self.root), model="fixture-model", prompt="你好",
                    thread_id=None, emit=self.events.append, cancelled=self.cancelled,
                    request_approval=lambda payload: "decline")
        args.update(overrides)
        try:
            return provider.run_codex(**args)
        finally:
            for process in self.processes:
                self.assertIsNotNone(process.poll(), "Owned app-server must be reaped before returning.")

    def messages(self):
        return [json.loads(line) for line in self.report.read_text(encoding="utf-8").splitlines()]

    def test_discovers_account_model_catalog_without_starting_thread(self):
        self.client_patch()
        models = provider.list_models(workspace=str(self.root))
        self.assertEqual([entry["id"] for entry in models], ["fixture-model", "fixture-model-2"])
        methods = [entry.get("method") for entry in self.messages()]
        self.assertEqual(methods, ["initialize", "initialized", "model/list", "model/list"])
        self.assertIsNotNone(self.processes[0].poll())

    def test_model_parameters_come_from_discovery_and_keep_wire_tier(self):
        self.client_patch()
        models = provider.list_models(workspace=str(self.root))
        parameters = {item["id"]: item for item in models[0]["parameters"]}
        self.assertEqual(parameters["reasoningEffort"]["defaultValue"], "low")
        self.assertEqual([item["value"] for item in parameters["reasoningEffort"]["options"]], ["low", "max", "ultra"])
        self.assertEqual([item["value"] for item in parameters["serviceTier"]["options"]], ["default", "priority"])
        self.assertEqual(parameters["serviceTier"]["options"][1]["label"], "Fast")
        self.assertEqual(models[0]["defaultReasoningEffort"], "low")
        self.assertEqual(models[1]["parameters"], [], "Older catalogs must not acquire invented parameter support")

    def test_run_revalidates_parameters_before_inference_and_sends_exact_fields(self):
        self.client_patch()
        self.run_turn(model_options={"reasoningEffort": "max", "serviceTier": "priority"})
        messages = self.messages()
        methods = [item.get("method") for item in messages]
        self.assertLess(methods.index("model/list"), methods.index("thread/start"))
        params = next(item["params"] for item in messages if item.get("method") == "turn/start")
        self.assertEqual(params["effort"], "max")
        self.assertEqual(params["serviceTierForTurn"], "priority")

    def test_standard_speed_is_explicit_and_does_not_inherit_fast(self):
        self.client_patch()
        self.run_turn(model_options={"serviceTier": "default"})
        params = next(item["params"] for item in self.messages() if item.get("method") == "turn/start")
        self.assertEqual(params["serviceTierForTurn"], "default")
        self.assertNotIn("effort", params)

    def test_unsupported_parameter_is_rejected_before_thread_or_inference(self):
        self.client_patch()
        with self.assertRaises(provider.CodexError) as failure:
            self.run_turn(model_options={"reasoningEffort": "invented-extra-max"})
        self.assertEqual(failure.exception.code, "MODEL_OPTION_UNSUPPORTED")
        methods = [item.get("method") for item in self.messages()]
        self.assertNotIn("thread/start", methods)
        self.assertNotIn("turn/start", methods)

    def test_parameter_shape_rejects_arbitrary_configuration(self):
        self.client_patch()
        for options in ({"sandbox": "danger-full-access"}, {"reasoningEffort": True}, {"serviceTier": ""}, []):
            with self.subTest(options=options):
                with self.assertRaises(provider.CodexError) as failure:
                    self.run_turn(model_options=options)
                self.assertEqual(failure.exception.code, "INVALID_MODEL_OPTIONS")
        self.assertEqual(self.processes, [])

    def test_catalog_validator_uses_only_current_model_options(self):
        catalog = [{"id": "first", "parameters": [{"id": "reasoningEffort", "options": [{"value": "low"}]}]},
                   {"id": "second", "parameters": [{"id": "reasoningEffort", "options": [{"value": "ultra"}]}]}]
        self.assertEqual(provider.validate_model_options("second", catalog, {"reasoningEffort": "ultra"}), {"reasoningEffort": "ultra"})
        with self.assertRaises(provider.CodexError):
            provider.validate_model_options("first", catalog, {"reasoningEffort": "ultra"})
        with self.assertRaises(provider.CodexError):
            provider.validate_model_options("manual-model", catalog, {"reasoningEffort": "low"})
        self.assertEqual(provider.validate_model_options("manual-model", catalog, {}), {})

    def test_workspace_catalog_uses_read_rpc_and_reaps_owned_process(self):
        from ai_workbench.codex_workspaces import list_workspaces
        self.client_patch()
        result = list_workspaces(workspace=str(self.root))
        self.assertEqual(result["workspaces"][0]["path"], str(self.root))
        messages = self.messages()
        self.assertTrue(messages[0]["params"]["capabilities"]["experimentalApi"])
        self.assertEqual([entry.get("method") for entry in messages], ["initialize", "initialized", "project/list"])
        self.assertIsNotNone(self.processes[0].poll())

    def test_model_catalog_cycles_fail_instead_of_repeating_pages(self):
        client = mock.Mock()
        client.request.side_effect = [
            {"data": [{"model": "first"}], "nextCursor": "a"},
            {"data": [{"model": "second"}], "nextCursor": "b"},
            {"data": [{"model": "third"}], "nextCursor": "a"},
        ]
        with mock.patch.object(provider, "_Client", return_value=client):
            with self.assertRaises(provider.CodexError) as failure:
                provider.list_models()
        self.assertEqual(failure.exception.code, "CODEX_PROTOCOL_ERROR")
        client.close.assert_called_once()

    def test_empty_model_catalog_is_actionable_failure(self):
        client = mock.Mock()
        client.request.return_value = {"data": [], "nextCursor": None}
        with mock.patch.object(provider, "_Client", return_value=client):
            with self.assertRaises(provider.CodexError) as failure:
                provider.list_models()
        self.assertEqual(failure.exception.code, "CODEX_NO_MODELS")
        client.close.assert_called_once()

    def test_explicit_missing_executable_does_not_silently_switch_provider(self):
        with mock.patch.object(discovery, "_app_candidates") as discover:
            with self.assertRaises(provider.CodexError) as failure:
                provider._command(str(self.root / "retired" / "codex.exe"))
        discover.assert_not_called()
        self.assertEqual(failure.exception.code, "CODEX_NOT_FOUND")
        self.assertIn("自动发现", failure.exception.message)

    @unittest.skipUnless(os.name == "nt", "Windows App installation discovery")
    def test_auto_discovery_uses_newest_app_binary_when_path_is_missing(self):
        installation = self.root / "OpenAI" / "Codex" / "bin"
        old = installation / "0123456789abcdef" / "codex.exe"
        current = installation / "fedcba9876543210" / "codex.exe"
        for stamp, binary in enumerate((old, current), 1):
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"test-only")
            os.utime(binary, (stamp, stamp))
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}), mock.patch.object(discovery.shutil, "which", return_value=None):
            self.assertEqual(provider._command(None)[0], str(current.resolve()))
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}), mock.patch.object(discovery.shutil, "which", return_value=str(old)):
            self.assertEqual(provider._command(None)[0], str(old.resolve()), "Explicit PATH precedence must remain stable")

    def test_streamed_utf8_text_and_authoritative_final_reply(self):
        self.client_patch()
        result = self.run_turn()
        self.assertEqual(result["text"], "你好，已完成。")
        self.assertEqual(result["threadId"], "thread-fixture")
        self.assertEqual(result["usage"], {"last": {"inputTokens": 5, "outputTokens": 3}})
        self.assertEqual("".join(event["text"] for event in self.events if event["type"] == "delta"), "你好，已完成。")
        messages = self.messages()
        self.assertFalse(messages[0]["params"]["capabilities"]["experimentalApi"])
        thread = next(message for message in messages if message.get("method") == "thread/start")
        self.assertEqual(thread["params"]["sandbox"], "read-only")
        self.assertEqual(thread["params"]["approvalPolicy"], "on-request")
        turn = next(message for message in messages if message.get("method") == "turn/start")
        self.assertEqual(turn["params"]["sandboxPolicy"], {"type": "readOnly"})

    def test_resume_uses_only_explicit_stored_thread(self):
        self.client_patch()
        result = self.run_turn(thread_id="our-saved-thread")
        self.assertEqual(result["threadId"], "our-saved-thread")
        methods = [message.get("method") for message in self.messages()]
        self.assertNotIn("thread/list", methods)
        self.assertNotIn("thread/read", methods)
        resumed = next(message for message in self.messages() if message.get("method") == "thread/resume")
        self.assertTrue(resumed["params"]["excludeTurns"])

    def test_approval_roundtrip_is_explicit_and_redacted(self):
        self.client_patch("approval")
        approvals = []
        def accept(payload):
            approvals.append(payload)
            return "accept"
        result = self.run_turn(request_approval=accept)
        self.assertEqual(result["text"], "已批准操作。")
        self.assertEqual(len(approvals), 1)
        self.assertNotIn("private-key", approvals[0]["detail"])
        self.assertNotIn("sk-secret-value", approvals[0]["detail"])
        self.assertNotIn("private-token", approvals[0]["detail"])
        response = next(message for message in self.messages() if message.get("id") == "approval-fixture")
        self.assertEqual(response["result"], {"decision": "accept"})

    def test_invalid_approval_callback_cannot_grant_execution(self):
        self.client_patch("decline")
        result = self.run_turn(request_approval=lambda payload: "acceptForSession")
        self.assertEqual(result["text"], "已拒绝操作。")

    def test_cancel_requests_interrupt_then_waits_for_terminal(self):
        self.client_patch("cancel")
        def emit(event):
            self.events.append(event)
            if event["type"] == "delta":
                self.cancelled.set()
        with self.assertRaises(provider.CodexError) as failure:
            self.run_turn(emit=emit)
        self.assertEqual(failure.exception.code, "CANCELLED")
        self.assertIn("turn/interrupt", [message.get("method") for message in self.messages()])

    def test_unresponsive_cancel_still_reaps_owned_process(self):
        self.client_patch("ignore_cancel")
        def emit(event):
            if event["type"] == "delta":
                self.cancelled.set()
        with self.assertRaises(provider.CodexError) as failure:
            self.run_turn(emit=emit)
        self.assertEqual(failure.exception.code, "CANCELLED")
        self.assertIsNotNone(self.processes[0].poll())

    def test_pre_cancel_does_not_start_process(self):
        self.client_patch()
        self.cancelled.set()
        with self.assertRaises(provider.CodexError) as failure:
            self.run_turn()
        self.assertEqual(failure.exception.code, "CANCELLED")
        self.assertEqual(self.processes, [])

    def test_rpc_timeout_is_reported_and_process_reaped(self):
        self.client_patch("timeout")
        with self.assertRaises(provider.CodexError) as failure:
            self.run_turn()
        self.assertEqual(failure.exception.code, "CODEX_TIMEOUT")

    def test_turn_timeout_is_not_success(self):
        self.client_patch("turn_timeout")
        with self.assertRaises(provider.CodexError) as failure:
            self.run_turn()
        self.assertEqual(failure.exception.code, "CODEX_TIMEOUT")

    def test_exit_without_turn_completed_never_returns_partial_success(self):
        self.client_patch("turn_exit")
        with self.assertRaises(provider.CodexError) as failure:
            self.run_turn()
        self.assertEqual(failure.exception.code, "CODEX_EXITED")
        self.assertNotIn("never-show", failure.exception.message)

    def test_auth_error_is_safe_and_actionable(self):
        self.client_patch("auth")
        with self.assertRaises(provider.CodexError) as failure:
            self.run_turn()
        self.assertEqual(failure.exception.code, "CODEX_AUTH_REQUIRED")
        self.assertNotIn("sk-secret", failure.exception.message)

    def test_unknown_server_request_is_explicitly_rejected(self):
        self.client_patch("unknown_request")
        with self.assertRaises(provider.CodexError) as failure:
            self.run_turn()
        self.assertEqual(failure.exception.code, "CODEX_UNSUPPORTED_REQUEST")
        rejected = next(message for message in self.messages() if message.get("id") == "approval-fixture")
        self.assertEqual(rejected["error"]["code"], -32601)

    def test_other_thread_approval_is_not_shown_or_accepted(self):
        self.client_patch("wrong_approval")
        callback = mock.Mock(return_value="accept")
        with self.assertRaises(provider.CodexError) as failure:
            self.run_turn(request_approval=callback)
        self.assertEqual(failure.exception.code, "CODEX_PROTOCOL_ERROR")
        callback.assert_not_called()

    def test_missing_approval_identity_cannot_grant_execution(self):
        self.client_patch("missing_approval_identity")
        callback = mock.Mock(return_value="accept")
        with self.assertRaises(provider.CodexError) as failure:
            self.run_turn(request_approval=callback)
        self.assertEqual(failure.exception.code, "CODEX_PROTOCOL_ERROR")
        callback.assert_not_called()

    def test_oversized_approval_is_declined_not_silently_truncated(self):
        self.client_patch("large_approval")
        callback = mock.Mock(return_value="accept")
        with self.assertRaises(provider.CodexError) as failure:
            self.run_turn(request_approval=callback)
        self.assertEqual(failure.exception.code, "CODEX_APPROVAL_LIMIT")
        callback.assert_not_called()

    def test_cleanup_failure_retains_retry_instead_of_claiming_stopped(self):
        self.client_patch()
        client = provider._Client(None, str(self.root))
        client.initialize()
        with mock.patch.object(client, "_finish_close", side_effect=OSError("temporary cleanup failure")):
            with self.assertRaises(provider.CodexError) as failure:
                client.close()
        self.assertEqual(failure.exception.code, "CODEX_STOP_FAILED")
        self.assertTrue(failure.exception.cleanupPending)
        self.assertFalse(client._closed)
        self.assertTrue(callable(failure.exception.retry_cleanup))
        failure.exception.retry_cleanup()
        self.assertTrue(client._closed)
        self.assertIsNotNone(client.process.poll())

    def test_resume_identity_mismatch_fails_before_inference(self):
        self.client_patch("wrong_thread")
        with self.assertRaises(provider.CodexError):
            self.run_turn(thread_id="our-thread")
        self.assertNotIn("turn/start", [message.get("method") for message in self.messages()])

    def test_bad_protocol_frames_are_rejected(self):
        for scenario in ("invalid_json", "large_frame", "boolean_id"):
            with self.subTest(scenario=scenario):
                self.client_patch(scenario)
                with self.assertRaises(provider.CodexError) as failure:
                    self.run_turn()
                self.assertEqual(failure.exception.code, "CODEX_PROTOCOL_ERROR")
                mock.patch.stopall()

    def test_invalid_cli_schema_has_explicit_compatibility_error(self):
        self.client_patch("schema_error")
        with self.assertRaises(provider.CodexError) as failure:
            self.run_turn()
        self.assertEqual(failure.exception.code, "CODEX_PROTOCOL_UNSUPPORTED")

    def test_empty_final_reply_is_not_reported_success(self):
        self.client_patch("empty")
        with self.assertRaises(provider.CodexError) as failure:
            self.run_turn()
        self.assertEqual(failure.exception.code, "CODEX_EMPTY_RESULT")

    def test_validation_never_invokes_a_shell_or_falls_back_to_another_agent(self):
        with self.assertRaises(provider.CodexError):
            provider._command("codex --unsafe")
        with mock.patch.object(discovery.shutil, "which", return_value=None), mock.patch.object(discovery, "_app_candidates", return_value=[]):
            with self.assertRaises(provider.CodexError) as failure:
                provider._command(None)
        self.assertEqual(failure.exception.code, "CODEX_NOT_FOUND")
        with self.assertRaises(provider.CodexError):
            provider._workspace("relative/path", True)

    @unittest.skipUnless(os.name == "nt", "Windows Job lifecycle check")
    def test_owned_job_reaps_descendant_even_after_root_exits(self):
        self.client_patch("child")
        self.run_turn()
        child_pid = next(entry["ownedChildPid"] for entry in self.messages() if "ownedChildPid" in entry)
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        api.OpenProcess.restype = ctypes.c_void_p
        api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = api.OpenProcess(0x100000, False, child_pid)
        if handle:
            try:
                self.assertEqual(api.WaitForSingleObject(handle, 3000), 0, "Owned child still running")
            finally:
                api.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()
