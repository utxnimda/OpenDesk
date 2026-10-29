import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "plugins/ai-workbench/backend"), str(ROOT / "apps/framework/worker")]
from ai_workbench.api_provider import ApiProvider, validate_endpoint
from ai_workbench.errors import ApiError


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FixtureHandler)
        self.requests = []
        self.responses = []
        self.status = 200
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        data = json.dumps({"data": [{"id": "model-a"}, {"id": "model-a"}, {"id": "model-b"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        if self.server.status != 200:
            self.send_response(self.server.status)
            if self.server.status == 302:
                self.send_header("Location", "http://127.0.0.1:1/leaked")
            self.end_headers()
            self.wfile.write(b"synthetic-credential reflected upstream error")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for item in self.server.responses.pop(0):
            data = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            self.wfile.write(("data: " + data + "\n\n").encode("utf-8"))


class ApiProviderTests(unittest.TestCase):
    def setUp(self):
        self.server = FixtureServer()
        self.events = []

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def provider(self, kind="openai-responses"):
        return ApiProvider({"kind": kind, "baseUrl": f"http://127.0.0.1:{self.server.server_port}/v1"}, "synthetic-credential", timeout=2)

    def run_provider(self, kind="openai-responses", tools=None, request_tool=None):
        return self.provider(kind).run("model-a", [{"role": "user", "text": "hello"}], tools or [],
                                       emit=self.events.append, cancelled=threading.Event(),
                                       request_tool=request_tool or (lambda *_: self.fail("Unexpected tool")))

    def test_models_read_actual_catalog_and_deduplicate(self):
        self.assertEqual([v["id"] for v in self.provider().models()], ["model-a", "model-b"])

    def test_responses_stream_unicode_and_terminal_event(self):
        self.server.responses = [[{"type": "response.output_text.delta", "delta": "你好"},
                                  {"type": "response.output_text.delta", "delta": " world"},
                                  {"type": "response.completed", "response": {"output": [], "usage": {"input_tokens": 3}}}]]
        result = self.run_provider()
        self.assertEqual(result["text"], "你好 world")
        self.assertFalse(self.server.requests[0]["store"])
        self.assertEqual(self.server.requests[0]["input"][0], {"role": "user", "content": "hello"})

    def test_chat_stream_and_usage(self):
        self.server.responses = [[{"choices": [{"delta": {"content": "A"}, "finish_reason": None}]},
                                  {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"total_tokens": 4}}, "[DONE]"]]
        result = self.run_provider("openai-chat")
        self.assertEqual(result, {"text": "A", "usage": {"total_tokens": 4}})

    def test_missing_terminal_is_not_success(self):
        self.server.responses = [[{"type": "response.output_text.delta", "delta": "partial"}]]
        with self.assertRaises(ApiError) as error:
            self.run_provider()
        self.assertEqual(error.exception.code, "API_STREAM_INTERRUPTED")
        self.assertEqual(self.events[0]["text"], "partial")

    def test_authoritative_final_text_without_deltas_is_supported(self):
        self.server.responses = [[{"type": "response.completed", "response": {"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "Final answer"}]}]}}]]
        self.assertEqual(self.run_provider()["text"], "Final answer")

    def test_empty_completed_response_is_not_success(self):
        self.server.responses = [[{"type": "response.completed", "response": {"output": []}}]]
        with self.assertRaises(ApiError) as error:
            self.run_provider()
        self.assertEqual(error.exception.code, "API_EMPTY_RESULT")

    def test_responses_real_function_roundtrip(self):
        tool = {"name": "tower_assistant__runtime_summary", "description": "Read summary",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}
        self.server.responses = [[{"type": "response.completed", "response": {"output": [
            {"type": "function_call", "id": "item1", "call_id": "call1", "name": "od_tool_0", "arguments": "{}"}]}}],
                                 [{"type": "response.output_text.delta", "delta": "Seven"},
                                  {"type": "response.completed", "response": {"output": []}}]]
        calls = []
        def invoke(name, arguments):
            calls.append((name, arguments))
            return {"source": "test", "data": {"count": 7}}
        result = self.run_provider(tools=[tool], request_tool=invoke)
        self.assertEqual(result["text"], "Seven")
        self.assertEqual(calls, [(tool["name"], {})])
        returned = self.server.requests[1]["input"][-1]
        self.assertEqual(returned["type"], "function_call_output")
        self.assertEqual(json.loads(returned["output"])["data"]["count"], 7)

    def test_chat_function_arguments_are_assembled_then_checked(self):
        tool = {"name": "read", "description": "Read", "inputSchema": {"type": "object", "additionalProperties": False}}
        self.server.responses = [[{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call1", "function": {"name": "od_tool_0", "arguments": "{"}}]}, "finish_reason": None}]},
                                  {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]}, "finish_reason": "tool_calls"}]}, "[DONE]"],
                                 [{"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}, "[DONE]"]]
        self.assertEqual(self.run_provider("openai-chat", [tool], lambda *_: {"ok": True})["text"], "done")
        self.assertEqual(self.server.requests[1]["messages"][-1]["role"], "tool")

    def test_unknown_tool_and_nonempty_arguments_never_execute(self):
        tool = {"name": "read", "description": "Read", "inputSchema": {"type": "object", "additionalProperties": False}}
        for name, arguments in (("other", "{}"), ("od_tool_0", '{"path":"private"}')):
            self.server.responses = [[{"type": "response.completed", "response": {"output": [
                {"type": "function_call", "call_id": "c", "name": name, "arguments": arguments}]}}]]
            with self.assertRaises(ApiError):
                self.run_provider(tools=[tool])

    def test_http_error_does_not_return_reflected_body_or_secret(self):
        for status in (401, 403, 404, 429, 500):
            self.server.status = status
            with self.assertRaises(ApiError) as error:
                self.run_provider()
            self.assertEqual(error.exception.code, "API_HTTP_" + str(status))
            self.assertNotIn("synthetic-credential", error.exception.message)

    def test_redirect_is_never_followed_with_credentials(self):
        self.server.status = 302
        with self.assertRaises(ApiError) as error:
            self.run_provider()
        self.assertEqual(error.exception.code, "UPSTREAM_REDIRECT")

    def test_endpoint_rejects_remote_cleartext_and_embedded_credentials(self):
        for value in ("http://example.com/v1", "https://name:password@example.com/v1", "https://example.com/v1?key=test", "https://example.com/#fragment", "file:///tmp/model"):
            with self.assertRaises(ApiError):
                validate_endpoint(value)
        self.assertEqual(validate_endpoint("https://example.com/v1/"), "https://example.com/v1")


if __name__ == "__main__":
    unittest.main()
