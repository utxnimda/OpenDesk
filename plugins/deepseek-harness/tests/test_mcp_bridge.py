"""Tests for the controlled local Streamable HTTP MCP bridge."""
from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from deepseek_harness.mcp_bridge import McpToolBridge


SAMPLE_TOOL = {
    "name": "tower.summary",
    "moduleId": "tower-assistant",
    "version": "0.1.0",
    "digest": "a" * 64,
    "generation": "gen-1",
    "title": "摘要",
    "description": "获取只读摘要",
    "readOnly": True,
    "contractVersion": 1,
}


def _post_mcp(url: str, body: dict, *, token: str = "", host: str | None = None) -> tuple[int, bytes]:
    raw = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if host is not None:
        headers["Host"] = host
    req = urllib.request.Request(url, data=raw, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


class McpBridgeTests(unittest.TestCase):
    def test_auth_host_and_full_tool_call_roundtrip(self):
        bridge = McpToolBridge(version="0.2.0")
        try:
            synced = bridge.sync_tools([SAMPLE_TOOL])
            self.assertTrue(synced["active"])
            url = synced["url"]
            token = synced["token"]
            self.assertTrue(url.startswith("http://127.0.0.1:"))
            self.assertTrue(token)

            # Reject missing or wrong Bearer token
            code, _ = _post_mcp(url, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
            self.assertEqual(code, 401)
            code, _ = _post_mcp(url, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token="wrong")
            self.assertEqual(code, 401)

            # Reject forged Host header (DNS rebinding guard)
            code, _ = _post_mcp(url, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token=token, host="evil.local:80")
            self.assertEqual(code, 403)

            # initialize + tools/list
            code, raw = _post_mcp(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, token=token)
            self.assertEqual(code, 200)
            init_resp = json.loads(raw)
            self.assertEqual(init_resp["result"]["serverInfo"]["name"], "opendesk-readonly-tools")

            code, raw = _post_mcp(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, token=token)
            self.assertEqual(code, 200)
            tools = json.loads(raw)["result"]["tools"]
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0]["name"], "tower.summary")
            self.assertTrue(tools[0]["annotations"]["readOnlyHint"])

            # Reject non-empty arguments or unauthorized tool
            code, raw = _post_mcp(
                url,
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "tower.summary", "arguments": {"x": 1}}},
                token=token,
            )
            self.assertEqual(code, 200)
            self.assertIn("error", json.loads(raw))

            code, raw = _post_mcp(
                url,
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "other.tool", "arguments": {}}},
                token=token,
            )
            self.assertEqual(code, 200)
            self.assertIn("error", json.loads(raw))

            # Full roundtrip: MCP client calls tools/call while wrapper polls & responds
            result_box: dict = {}

            def run_client():
                status, data = _post_mcp(
                    url,
                    {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "tower.summary", "arguments": {}}},
                    token=token,
                )
                result_box["status"] = status
                result_box["body"] = json.loads(data)

            t = threading.Thread(target=run_client)
            t.start()
            deadline = time.monotonic() + 3.0
            calls = []
            while time.monotonic() < deadline and not calls:
                calls = bridge.poll_calls()
                if not calls:
                    time.sleep(0.02)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["tool"]["name"], "tower.summary")
            # Second poll must not duplicate already-dispatched call
            self.assertEqual(bridge.poll_calls(), [])

            ack = bridge.respond_call(calls[0]["callId"], True, result={"count": 7})
            self.assertTrue(ack["accepted"])
            t.join(timeout=3.0)
            self.assertEqual(result_box["status"], 200)
            self.assertFalse(result_box["body"]["result"]["isError"])
            self.assertEqual(json.loads(result_box["body"]["result"]["content"][0]["text"]), {"count": 7})
        finally:
            bridge.close()

    def test_revoke_wakes_pending_call_without_deadlock(self):
        bridge = McpToolBridge(version="0.2.0")
        try:
            synced = bridge.sync_tools([SAMPLE_TOOL])
            url = synced["url"]
            token = synced["token"]
            result_box: dict = {}

            def run_client():
                status, data = _post_mcp(
                    url,
                    {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "tower.summary", "arguments": {}}},
                    token=token,
                )
                result_box["status"] = status
                result_box["body"] = json.loads(data)

            t = threading.Thread(target=run_client)
            t.start()
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not bridge.poll_calls():
                time.sleep(0.02)

            cleared = bridge.sync_tools([])
            self.assertFalse(cleared["active"])
            t.join(timeout=3.0)
            self.assertEqual(result_box["status"], 200)
            self.assertTrue(result_box["body"]["result"]["isError"])
        finally:
            bridge.close()


if __name__ == "__main__":
    unittest.main()
