"""Tests for the controlled local Streamable HTTP MCP bridge (v1 + v2 + meta-tools + SSE)."""
from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from deepseek_harness.mcp_bridge import (
    HOST_META_LIST_MODULES,
    HOST_META_OPEN_MODULE,
    McpToolBridge,
)


SAMPLE_V1_TOOL = {
    "name": "tower__summary",
    "moduleId": "tower-assistant",
    "version": "0.10.1",
    "digest": "a" * 64,
    "generation": "gen-1",
    "title": "摘要",
    "description": "获取只读摘要",
    "readOnly": True,
    "contractVersion": 1,
}

SAMPLE_V2_TOOL = {
    "name": "tower__summary_filtered",
    "moduleId": "tower-assistant",
    "version": "0.10.2",
    "digest": "b" * 64,
    "generation": "gen-2",
    "title": "筛选统计摘要",
    "description": "按分类筛选只读统计",
    "readOnly": True,
    "access": "read",
    "contractVersion": 2,
    "inputSchema": {
        "type": "object",
        "properties": {
            "section": {"type": "string", "enum": ["all", "combat", "work"]},
        },
        "additionalProperties": False,
    },
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
    def test_auth_host_v1_v2_and_meta_tool_roundtrips(self):
        bridge = McpToolBridge(version="0.4.0")
        try:
            synced = bridge.sync_tools([SAMPLE_V1_TOOL, SAMPLE_V2_TOOL], host_meta_enabled=True)
            self.assertTrue(synced["active"])
            self.assertTrue(synced["hostMetaEnabled"])
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

            # initialize declares listChanged: true
            code, raw = _post_mcp(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, token=token)
            self.assertEqual(code, 200)
            init_resp = json.loads(raw)
            self.assertEqual(init_resp["result"]["serverInfo"]["name"], "opendesk-workbench-bridge")
            self.assertTrue(init_resp["result"]["capabilities"]["tools"]["listChanged"])

            # tools/list exposes both host meta-tools and v1/v2 module tools
            code, raw = _post_mcp(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, token=token)
            self.assertEqual(code, 200)
            tools = json.loads(raw)["result"]["tools"]
            names = [t["name"] for t in tools]
            self.assertEqual(names, [HOST_META_LIST_MODULES, HOST_META_OPEN_MODULE, "tower__summary", "tower__summary_filtered"])

            # v1 tool rejects non-empty arguments; unauthorized tool is rejected
            code, raw = _post_mcp(
                url,
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "tower__summary", "arguments": {"x": 1}}},
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

            # Roundtrip 1: v2 parameterized tool call passes arguments verbatim
            result_box: dict = {}

            def run_v2():
                status, data = _post_mcp(
                    url,
                    {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "tower__summary_filtered", "arguments": {"section": "combat"}}},
                    token=token,
                )
                result_box["status"] = status
                result_box["body"] = json.loads(data)

            t = threading.Thread(target=run_v2)
            t.start()
            deadline = time.monotonic() + 3.0
            calls = []
            while time.monotonic() < deadline and not calls:
                calls = bridge.poll_calls()
                if not calls:
                    time.sleep(0.02)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["kind"], "tool-call")
            self.assertEqual(calls[0]["tool"]["name"], "tower__summary_filtered")
            self.assertEqual(calls[0]["arguments"], {"section": "combat"})
            self.assertEqual(bridge.poll_calls(), [])

            ack = bridge.respond_call(calls[0]["callId"], True, result={"section": "combat", "count": 7})
            self.assertTrue(ack["accepted"])
            t.join(timeout=3.0)
            self.assertEqual(result_box["status"], 200)
            self.assertFalse(result_box["body"]["result"]["isError"])
            self.assertEqual(json.loads(result_box["body"]["result"]["content"][0]["text"]), {"section": "combat", "count": 7})

            # Roundtrip 2: opendesk_open_module meta-tool
            meta_box: dict = {}

            def run_open():
                status, data = _post_mcp(
                    url,
                    {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": HOST_META_OPEN_MODULE, "arguments": {"moduleId": "tower-assistant", "focus": False}}},
                    token=token,
                )
                meta_box["status"] = status
                meta_box["body"] = json.loads(data)

            t2 = threading.Thread(target=run_open)
            t2.start()
            deadline = time.monotonic() + 3.0
            open_calls = []
            while time.monotonic() < deadline and not open_calls:
                open_calls = bridge.poll_calls()
                if not open_calls:
                    time.sleep(0.02)
            self.assertEqual(open_calls[0]["kind"], "open-module")
            self.assertEqual(open_calls[0]["moduleId"], "tower-assistant")
            self.assertFalse(open_calls[0]["focus"])
            bridge.respond_call(open_calls[0]["callId"], True, result={"moduleId": "tower-assistant", "opened": True})
            t2.join(timeout=3.0)
            self.assertEqual(json.loads(meta_box["body"]["result"]["content"][0]["text"])["opened"], True)
        finally:
            bridge.close()

    def test_sse_notifies_tools_list_changed_and_revoke_wakes_pending(self):
        bridge = McpToolBridge(version="0.4.0")
        try:
            url = bridge.start()
            token = bridge.token
            received_lines: list[str] = []

            def read_sse():
                req = urllib.request.Request(
                    url,
                    headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    for raw_line in resp:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if line:
                            received_lines.append(line)
                            if "notifications/tools/list_changed" in line:
                                break

            sse_thread = threading.Thread(target=read_sse, daemon=True)
            sse_thread.start()
            time.sleep(0.15)
            bridge.sync_tools([SAMPLE_V1_TOOL], host_meta_enabled=False)
            sse_thread.join(timeout=3.0)
            self.assertTrue(any("notifications/tools/list_changed" in line for line in received_lines), received_lines)

            # Revoke wakes pending tool call without deadlock
            result_box: dict = {}

            def run_client():
                status, data = _post_mcp(
                    url,
                    {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "tower__summary", "arguments": {}}},
                    token=token,
                )
                result_box["status"] = status
                result_box["body"] = json.loads(data)

            t = threading.Thread(target=run_client)
            t.start()
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not bridge.poll_calls():
                time.sleep(0.02)

            cleared = bridge.sync_tools([], host_meta_enabled=False)
            self.assertFalse(cleared["active"])
            t.join(timeout=3.0)
            self.assertEqual(result_box["status"], 200)
            self.assertTrue(result_box["body"]["result"]["isError"])
        finally:
            bridge.close()


if __name__ == "__main__":
    unittest.main()
