from pathlib import Path
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from ai_workbench import cursor_provider as provider


class CursorCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="opendesk-cli-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.report = self.root / "report.jsonl"
        self.events, self.children = [], []
        self.cancelled = threading.Event()
        self.scenario = "normal"
        fixture = Path(__file__).parent / "fixtures/cursor_cli.py"
        def command(_):
            return [sys.executable, "-u", str(fixture), "--scenario", self.scenario, "--report", str(self.report)]
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(provider, "_command", side_effect=command).start()
        original = subprocess.Popen
        def start(*args, **kwargs):
            child = original(*args, **kwargs)
            self.children.append(child)
            return child
        mock.patch.object(provider.subprocess, "Popen", side_effect=start).start()
        for name in ("RPC_TIMEOUT", "TURN_TIMEOUT"):
            mock.patch.object(provider, name, 10).start()

    def run_turn(self, **overrides):
        values = dict(executable=None, workspace=str(self.root), model="claude-fixture-high",
                      prompt="synthetic prompt", thread_id=None, emit=self.events.append,
                      cancelled=self.cancelled, request_approval=lambda request: "decline")
        values.update(overrides)
        try:
            return provider.run_cursor(**values)
        finally:
            for child in self.children:
                self.assertIsNotNone(child.poll(), "All owned processes must be reaped")

    def records(self):
        return [json.loads(line) for line in self.report.read_text(encoding="utf-8").splitlines()]

    def test_models_command_is_authoritative_and_never_starts_acp(self):
        catalog = provider.list_models(workspace=str(self.root))
        self.assertEqual([item["id"] for item in catalog], ["claude-fixture-high", "claude-fixture-low"])
        self.assertEqual(catalog[0]["label"], "Claude Fixture High")
        self.assertEqual(catalog[0]["parameters"], [])
        self.assertEqual(self.records(), [{"command": ["--disable-auto-update", "models"]}])
        self.assertIsNotNone(self.children[0].poll())

    def test_catalog_errors_and_timeouts_are_explicit_and_processes_are_reaped(self):
        for scenario, expected in (("catalog_failure", "CURSOR_AUTH_REQUIRED"),
                                   ("catalog_empty", "CURSOR_PROTOCOL_ERROR"),
                                   ("catalog_timeout", "CURSOR_TIMEOUT")):
            with self.subTest(scenario=scenario):
                self.scenario = scenario
                timeout = 2 if scenario == "catalog_timeout" else 10
                with mock.patch.object(provider, "RPC_TIMEOUT", timeout), self.assertRaises(provider.CursorError) as error:
                    provider.list_models()
                self.assertEqual(error.exception.code, expected)
                self.assertNotIn("sk-secret", str(error.exception))
                self.assertIsNotNone(self.children[-1].poll())

    def test_stream_deduplicates_flushes_hides_reasoning_and_confirms_terminal_exit(self):
        result = self.run_turn()
        self.assertEqual(result, {"text": "你好，完成。", "threadId": "cli-fixture-session"})
        self.assertEqual([event["text"] for event in self.events if event["type"] == "delta"], ["你好", "，完成。"])
        args = self.records()[1]["command"]
        self.assertEqual(args[args.index("--model") + 1], "claude-fixture-high")
        self.assertEqual(args[args.index("--mode") + 1], "ask")
        self.assertIn("--stream-partial-output", args)
        self.assertNotIn("acp", args)
        self.assertNotIn("--force", args)
        self.assertNotIn("--trust", args)
        self.assertNotIn("--approve-mcps", args)
        self.assertNotIn("synthetic prompt", args)

    def test_large_unicode_prompt_is_sent_by_stdin_and_explicit_session_is_resumed(self):
        prompt = "中文测试" * 20000
        result = self.run_turn(prompt=prompt, thread_id="saved-session")
        self.assertEqual(result["threadId"], "saved-session")
        args = self.records()[1]["command"]
        self.assertEqual(args[args.index("--resume") + 1], "saved-session")
        self.assertEqual(self.records()[2]["prompt"], prompt)
        self.assertLess(len(" ".join(args)), 1000)

    def test_final_only_output_is_delivered_once(self):
        self.scenario = "final_only"
        self.run_turn()
        self.assertEqual([event["text"] for event in self.events if event["type"] == "delta"], ["你好，完成。"])

    def test_failure_paths_never_return_success_or_raw_stderr(self):
        cases = {"quota":"CURSOR_RATE_LIMIT", "untrusted":"CURSOR_WORKSPACE_UNTRUSTED",
                 "wrong_model":"CURSOR_MODEL_UNAVAILABLE", "wrong_session":"CURSOR_PROTOCOL_ERROR",
                 "invalid_json":"CURSOR_PROTOCOL_ERROR", "truncated":"CURSOR_INCOMPLETE",
                 "nonzero_after_result":"CURSOR_PROVIDER_ERROR", "empty":"CURSOR_EMPTY_RESULT",
                 "inconsistent":"CURSOR_PROTOCOL_ERROR", "timeout":"CURSOR_TIMEOUT"}
        for scenario, expected in cases.items():
            with self.subTest(scenario=scenario):
                self.scenario = scenario
                timeout = 2 if scenario == "timeout" else 10
                with mock.patch.object(provider, "TURN_TIMEOUT", timeout), self.assertRaises(provider.CursorError) as error:
                    self.run_turn()
                self.assertEqual(error.exception.code, expected)
                self.assertNotIn("sk-secret", str(error.exception))

    def test_cancellation_reaps_live_process_and_does_not_report_success(self):
        self.scenario = "cancel"
        def emit(event):
            self.events.append(event)
            if event["type"] == "delta":
                self.cancelled.set()
        with self.assertRaises(provider.CursorError) as error:
            self.run_turn(emit=emit)
        self.assertEqual(error.exception.code, "CANCELLED")
        self.assertIn({"type":"status", "status":"cancelling"}, self.events)

    def test_blocked_stdin_writer_is_released_on_timeout(self):
        self.scenario = "blocked_input"
        with mock.patch.object(provider, "TURN_TIMEOUT", 2), self.assertRaises(provider.CursorError) as error:
            self.run_turn(prompt="x" * 100000)
        self.assertEqual(error.exception.code, "CURSOR_TIMEOUT")
        self.assertTrue(self.children[-1].stdin.closed)

    def test_success_event_does_not_hide_a_live_process_or_late_cancellation(self):
        self.scenario = "cancel_after_result"
        timer = threading.Timer(.8, self.cancelled.set)
        timer.start()
        try:
            with self.assertRaises(provider.CursorError) as error:
                self.run_turn()
            self.assertEqual(error.exception.code, "CANCELLED")
        finally:
            timer.cancel()

    def test_unknown_models_and_extra_options_cannot_reach_inference(self):
        with self.assertRaises(provider.CursorError) as error:
            self.run_turn(model="unlisted")
        self.assertEqual(error.exception.code, "CURSOR_MODEL_UNAVAILABLE")
        self.assertEqual(len(self.children), 1)
        with self.assertRaises(provider.CursorError) as error:
            self.run_turn(model_options={"effort":"high"})
        self.assertEqual(error.exception.code, "MODEL_OPTION_UNSUPPORTED")
        self.assertEqual(len(self.children), 1)


if __name__ == "__main__":
    unittest.main()
