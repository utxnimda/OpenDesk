"""Local Streamable HTTP MCP bridge exposing OpenDesk-consented read-only plugin tools to dsh.

Security & contract invariants:
- Binds strictly to 127.0.0.1:<os-port>, requires exact Host match and per-session Bearer token.
- Only exposes zero-argument read-only tools previously authorized through OpenDesk's native
  host consent dialog (`bridge.host.tools.grant`).
- Never calls another module directly; every `tools/call` is queued for the wrapper frontend to
  execute through `bridge.host.tools.call(tool, {})` where the Rust host re-verifies version,
  digest, generation, and active grant snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
import uuid

MAX_MCP_BODY = 64 * 1024
MAX_TOOLS = 16
CALL_TIMEOUT_SECONDS = 15.0
PROTOCOL_VERSION = "2025-03-26"


def validate_tool_catalog(raw_tools: list) -> list[dict]:
    if not isinstance(raw_tools, list) or len(raw_tools) > MAX_TOOLS:
        raise ValueError("INVALID_TOOLS: 工具列表格式无效或超出上限（最多 16 项）。")
    validated: list[dict] = []
    seen_names: set[str] = set()
    for item in raw_tools:
        if not isinstance(item, dict):
            raise ValueError("INVALID_TOOLS: 工具描述符必须是对象。")
        required_str = ("name", "moduleId", "version", "digest", "generation", "title", "description")
        for key in required_str:
            val = item.get(key)
            if not isinstance(val, str) or not val or len(val) > 1000 or any(ord(c) < 32 for c in val):
                raise ValueError(f"INVALID_TOOLS: 字段 {key} 无效。")
        if item.get("readOnly") is not True or item.get("contractVersion") != 1:
            raise ValueError("INVALID_TOOLS: 仅允许 contractVersion=1 的只读工具。")
        name = item["name"]
        if len(name) > 64 or name in seen_names:
            raise ValueError("INVALID_TOOLS: 工具名称重复或过长。")
        seen_names.add(name)
        validated.append({
            "name": name,
            "moduleId": item["moduleId"],
            "version": item["version"],
            "digest": item["digest"],
            "generation": item["generation"],
            "title": item["title"],
            "description": item["description"],
            "readOnly": True,
            "contractVersion": 1,
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        })
    return validated


@dataclass
class _PendingCall:
    call_id: str
    tool: dict
    event: threading.Event = field(default_factory=threading.Event)
    dispatched: bool = False
    ok: bool = False
    result: object = None
    error: str = ""


class McpToolBridge:
    """Thread-safe Streamable HTTP MCP server backed by OpenDesk's ModuleToolsClient."""

    def __init__(self, version: str = "0.2.0"):
        self.version = version
        self._lock = threading.RLock()
        self._token = secrets.token_urlsafe(24)
        self._tools: dict[str, dict] = {}
        self._pending: dict[str, _PendingCall] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port = 0
        self._closed = False

    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/mcp" if self._port else ""

    def start(self) -> str:
        with self._lock:
            if self._server is not None and self._port:
                return self.url
            self._closed = False
            bridge = self

            class Handler(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def log_message(self, format: str, *args) -> None:
                    # Never emit bearer tokens or request details to stderr.
                    return

                def _send_json(self, code: int, payload: object | None) -> None:
                    body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    self.send_response(code)
                    if payload is not None:
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    if body:
                        self.wfile.write(body)

                def _authorized(self) -> bool:
                    expected_host = f"127.0.0.1:{bridge._port}"
                    if (self.headers.get("Host") or "").strip() != expected_host:
                        self._send_json(403, {"error": "Invalid Host"})
                        return False
                    auth = (self.headers.get("Authorization") or "").strip()
                    if not secrets.compare_digest(auth, f"Bearer {bridge._token}"):
                        self._send_json(401, {"error": "Unauthorized"})
                        return False
                    return True

                def do_GET(self) -> None:
                    if not self._authorized():
                        return
                    self._send_json(405, {"error": "Use POST /mcp for JSON-RPC requests."})

                def do_DELETE(self) -> None:
                    if not self._authorized():
                        return
                    self._send_json(204, None)

                def do_POST(self) -> None:
                    if not self._authorized():
                        return
                    if self.path.split("?", 1)[0] != "/mcp":
                        self._send_json(404, {"error": "Not Found"})
                        return
                    try:
                        length = int(self.headers.get("Content-Length") or "0")
                    except ValueError:
                        self._send_json(400, {"error": "Invalid Content-Length"})
                        return
                    if length <= 0 or length > MAX_MCP_BODY:
                        self._send_json(413 if length > MAX_MCP_BODY else 400, {"error": "Invalid body size"})
                        return
                    raw = self.rfile.read(length)
                    try:
                        req = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        self._send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                        return
                    if not isinstance(req, dict):
                        self._send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}})
                        return

                    status_code, response = bridge._handle_rpc(req)
                    self._send_json(status_code, response)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.daemon_threads = True
            self._port = server.server_address[1]
            self._server = server
            self._thread = threading.Thread(target=server.serve_forever, name="dsh-mcp-bridge", daemon=True)
            self._thread.start()
            return self.url

    def snapshot(self, *, include_token: bool = False) -> dict:
        with self._lock:
            tools = list(self._tools.values())
            data = {
                "active": bool(tools) and not self._closed and bool(self._port),
                "url": self.url if tools else "",
                "toolCount": len(tools),
                "tools": tools,
            }
            if include_token and tools:
                data["token"] = self._token
            return data

    def sync_tools(self, raw_tools: list) -> dict:
        validated = validate_tool_catalog(raw_tools)
        with self._lock:
            if self._closed:
                raise RuntimeError("SHUTTING_DOWN: MCP 桥接正在关闭。")
            self._tools = {item["name"]: item for item in validated}
            if validated and self._server is None:
                self.start()
            if not validated:
                self._fail_all_pending("MCP 只读工具授权已撤销。")
            return self.snapshot(include_token=True)

    def poll_calls(self) -> list[dict]:
        with self._lock:
            out: list[dict] = []
            for call in self._pending.values():
                if not call.dispatched and not call.event.is_set():
                    call.dispatched = True
                    out.append({
                        "callId": call.call_id,
                        "tool": {
                            "name": call.tool["name"],
                            "moduleId": call.tool["moduleId"],
                            "version": call.tool["version"],
                            "digest": call.tool["digest"],
                            "generation": call.tool["generation"],
                        },
                    })
            return out

    def respond_call(self, call_id: str, ok: bool, result: object = None, error: str = "") -> dict:
        if not isinstance(call_id, str) or not call_id or len(call_id) > 100:
            raise ValueError("INVALID_PARAMS: callId 无效。")
        with self._lock:
            pending = self._pending.get(call_id)
            if pending is None:
                return {"accepted": False}
            pending.ok = bool(ok)
            pending.result = result
            pending.error = str(error or "")[:1000]
            pending.event.set()
            return {"accepted": True}

    def _fail_all_pending(self, reason: str) -> None:
        with self._lock:
            for call in self._pending.values():
                if not call.event.is_set():
                    call.ok = False
                    call.error = reason
                    call.event.set()

    def _handle_rpc(self, req: dict) -> tuple[int, dict | None]:
        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params")

        if method == "notifications/initialized":
            return (202, None)

        if not isinstance(method, str):
            return (200, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32600, "message": "Invalid Request"}})

        if method == "initialize":
            return (200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "opendesk-readonly-tools", "version": self.version},
                },
            })

        if method == "ping":
            return (200, {"jsonrpc": "2.0", "id": req_id, "result": {}})

        if method == "tools/list":
            with self._lock:
                tools = [
                    {
                        "name": t["name"],
                        "description": f"[{t['moduleId']} v{t['version']}] {t['title']} — {t['description']}",
                        "inputSchema": t["inputSchema"],
                        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
                    }
                    for t in self._tools.values()
                ]
            return (200, {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}})

        if method == "tools/call":
            if not isinstance(params, dict):
                return (200, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Invalid params"}})
            name = params.get("name")
            arguments = params.get("arguments")
            if arguments is None:
                arguments = {}
            if not isinstance(name, str) or not isinstance(arguments, dict) or len(arguments) != 0:
                return (200, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": "Only zero-argument read-only OpenDesk tools are supported."},
                })

            with self._lock:
                if self._closed:
                    return (200, {"jsonrpc": "2.0", "id": req_id, "result": {"isError": True, "content": [{"type": "text", "text": "MCP bridge is shutting down."}]}})
                tool = self._tools.get(name)
                if tool is None:
                    return (200, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": f"Tool not authorized: {name}"}})
                call_id = uuid.uuid4().hex
                pending = _PendingCall(call_id=call_id, tool=tool)
                self._pending[call_id] = pending

            try:
                completed = pending.event.wait(timeout=CALL_TIMEOUT_SECONDS)
            finally:
                with self._lock:
                    self._pending.pop(call_id, None)

            if not completed:
                return (200, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": "OpenDesk host tool call timed out (15s). Ensure the DeepSeek Harness tab is active."}],
                    },
                })
            if not pending.ok:
                return (200, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": pending.error or "OpenDesk host tool call failed."}],
                    },
                })
            text = json.dumps(pending.result, ensure_ascii=False)
            return (200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "isError": False,
                    "content": [{"type": "text", "text": text}],
                },
            })

        return (200, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._tools.clear()
            self._fail_all_pending("OpenDesk 正在关闭。")
            server = self._server
            self._server = None
            thread = self._thread
            self._thread = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
