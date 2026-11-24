"""Local Streamable HTTP MCP bridge exposing OpenDesk host meta-tools and consented
plugin tools (v1 read-only + v2 bounded schema / tiered approval) to DeepSeek Harness.

Security & contract invariants:
- Binds strictly to 127.0.0.1:<os-port>, requires exact Host match and per-session Bearer token.
- Never calls another module directly; every MCP tool call is queued for the wrapper frontend to
  execute through `bridge.host.tools.*` where the Rust host enforces schema validation, generation/digest
  pinning, and per-call native confirmation dialogs for write operations.
- Supports `notifications/tools/list_changed` over GET /mcp SSE so DSH refreshes its tool catalog
  live whenever the user opens a tab or updates tool grants.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import queue
import secrets
import threading
import uuid

MAX_MCP_BODY = 64 * 1024
MAX_ARGUMENTS_BYTES = 16 * 1024
MAX_TOOLS = 16
CALL_TIMEOUT_SECONDS = 120.0
PROTOCOL_VERSION = "2025-03-26"
VALID_ACCESS = ("read", "ui-navigate", "write")

HOST_META_LIST_MODULES = "opendesk_list_modules"
HOST_META_OPEN_MODULE = "opendesk_open_module"

HOST_META_TOOL_DEFS: tuple[dict, ...] = (
    {
        "name": HOST_META_LIST_MODULES,
        "description": (
            "[OpenDesk Host] List installed and enabled OpenDesk tabs/modules with their "
            "open/ready status and whether they export tools. Call this first when the user "
            "asks to use a specific OpenDesk tab."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": HOST_META_OPEN_MODULE,
        "description": (
            "[OpenDesk Host] Request OpenDesk to open (or focus) an installed sibling tab by moduleId. "
            "Requires native user confirmation if the tab is not yet open; waits until the tab is ready "
            "and returns its newly discovered tools."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "moduleId": {
                    "type": "string",
                    "description": "Target OpenDesk module ID from opendesk_list_modules (e.g. tower-assistant, nimda-workspace).",
                },
                "focus": {
                    "type": "boolean",
                    "description": "If true, switch the OpenDesk UI to the target tab; default false keeps DSH focused.",
                },
            },
            "required": ["moduleId"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    },
)


def _validate_input_schema(schema: object) -> dict:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "additionalProperties": False}
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ValueError("INVALID_TOOLS: 工具 inputSchema 必须是 additionalProperties=false 的对象。")
    props = schema.get("properties")
    if not isinstance(props, dict) or len(props) > 12:
        raise ValueError("INVALID_TOOLS: 工具 inputSchema.properties 无效或超过上限。")
    return schema


def validate_tool_catalog(raw_tools: list) -> list[dict]:
    if not isinstance(raw_tools, list) or len(raw_tools) > MAX_TOOLS:
        raise ValueError("INVALID_TOOLS: 工具列表格式无效或超出上限（最多 16 项）。")
    validated: list[dict] = []
    seen_names: set[str] = {HOST_META_LIST_MODULES, HOST_META_OPEN_MODULE}
    for item in raw_tools:
        if not isinstance(item, dict):
            raise ValueError("INVALID_TOOLS: 工具描述符必须是对象。")
        required_str = ("name", "moduleId", "version", "digest", "generation", "title", "description")
        for key in required_str:
            val = item.get(key)
            if not isinstance(val, str) or not val or len(val) > 1000 or any(ord(c) < 32 for c in val):
                raise ValueError(f"INVALID_TOOLS: 字段 {key} 无效。")
        contract_ver = item.get("contractVersion", 1)
        if contract_ver not in (1, 2):
            raise ValueError("INVALID_TOOLS: 仅支持 contractVersion 1 或 2 的插件工具。")
        if contract_ver == 1:
            if item.get("readOnly") is not True:
                raise ValueError("INVALID_TOOLS: contractVersion=1 仅允许只读工具。")
            access = "read"
            input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
        else:
            access = item.get("access", "read")
            if access not in VALID_ACCESS:
                raise ValueError("INVALID_TOOLS: contractVersion=2 工具 access 无效。")
            input_schema = _validate_input_schema(item.get("inputSchema"))
        name = item["name"]
        if len(name) > 64 or name in seen_names:
            raise ValueError("INVALID_TOOLS: 工具名称重复、冲突或过长。")
        seen_names.add(name)
        validated.append({
            "name": name,
            "moduleId": item["moduleId"],
            "version": item["version"],
            "digest": item["digest"],
            "generation": item["generation"],
            "title": item["title"],
            "description": item["description"],
            "readOnly": access == "read",
            "access": access,
            "contractVersion": contract_ver,
            "inputSchema": input_schema,
        })
    return validated


@dataclass
class _PendingCall:
    call_id: str
    kind: str  # "tool-call" | "list-modules" | "open-module"
    tool: dict | None = None
    arguments: dict = field(default_factory=dict)
    module_id: str = ""
    focus: bool = False
    event: threading.Event = field(default_factory=threading.Event)
    dispatched: bool = False
    ok: bool = False
    result: object = None
    error: str = ""


class McpToolBridge:
    """Thread-safe Streamable HTTP MCP server backed by OpenDesk's ModuleToolsClient."""

    def __init__(self, version: str = "0.4.0"):
        self.version = version
        self._lock = threading.RLock()
        self._token = secrets.token_urlsafe(24)
        self._session_id = uuid.uuid4().hex
        self._host_meta_enabled = False
        self._tools: dict[str, dict] = {}
        self._pending: dict[str, _PendingCall] = {}
        self._sse_clients: set[queue.Queue[str | None]] = set()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port = 0
        self._closed = False

    @property
    def port(self) -> int:
        return self._port

    @property
    def token(self) -> str:
        return self._token

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
                    self.send_header("Mcp-Session-Id", bridge._session_id)
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
                    if self.path.split("?", 1)[0] != "/mcp":
                        self._send_json(404, {"error": "Not Found"})
                        return
                    accept = (self.headers.get("Accept") or "").lower()
                    if "text/event-stream" not in accept:
                        self._send_json(405, {"error": "Use POST /mcp for JSON-RPC or GET /mcp with Accept: text/event-stream."})
                        return

                    client_q: queue.Queue[str | None] = queue.Queue(maxsize=32)
                    with bridge._lock:
                        if bridge._closed:
                            self._send_json(503, {"error": "Bridge closing"})
                            return
                        bridge._sse_clients.add(client_q)

                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Mcp-Session-Id", bridge._session_id)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    try:
                        self.wfile.write(b": opendesk-mcp-sse-ready\n\n")
                        self.wfile.flush()
                        while True:
                            try:
                                item = client_q.get(timeout=15.0)
                            except queue.Empty:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                                continue
                            if item is None:
                                break
                            self.wfile.write(f"event: message\ndata: {item}\n\n".encode("utf-8"))
                            self.wfile.flush()
                    except (OSError, BrokenPipeError, ConnectionResetError):
                        pass
                    finally:
                        with bridge._lock:
                            bridge._sse_clients.discard(client_q)

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

    def _broadcast_tools_changed(self) -> None:
        payload = json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        with self._lock:
            clients = list(self._sse_clients)
        for q in clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

    def snapshot(self, *, include_token: bool = False) -> dict:
        with self._lock:
            tools = list(self._tools.values())
            active = (bool(tools) or self._host_meta_enabled) and not self._closed and bool(self._port)
            data = {
                "active": active,
                "listening": not self._closed and bool(self._port),
                "url": self.url if (not self._closed and self._port) else "",
                "hostMetaEnabled": self._host_meta_enabled,
                "toolCount": len(tools),
                "tools": tools,
            }
            if include_token and not self._closed and self._port:
                data["token"] = self._token
            return data

    def sync_tools(self, raw_tools: list, *, host_meta_enabled: bool | None = None) -> dict:
        validated = validate_tool_catalog(raw_tools)
        changed = False
        with self._lock:
            if self._closed:
                raise RuntimeError("SHUTTING_DOWN: MCP 桥接正在关闭。")
            next_map = {item["name"]: item for item in validated}
            next_meta = self._host_meta_enabled if host_meta_enabled is None else bool(host_meta_enabled)
            if next_map != self._tools or next_meta != self._host_meta_enabled:
                changed = True
            self._tools = next_map
            self._host_meta_enabled = next_meta
            if (validated or next_meta) and self._server is None:
                self.start()
            if not validated and not next_meta:
                self._fail_all_pending("MCP 工具桥接授权已撤销。")
            snap = self.snapshot(include_token=True)
        if changed:
            self._broadcast_tools_changed()
        return snap

    def poll_calls(self) -> list[dict]:
        with self._lock:
            out: list[dict] = []
            for call in self._pending.values():
                if not call.dispatched and not call.event.is_set():
                    call.dispatched = True
                    if call.kind == "list-modules":
                        out.append({"callId": call.call_id, "kind": "list-modules"})
                    elif call.kind == "open-module":
                        out.append({
                            "callId": call.call_id,
                            "kind": "open-module",
                            "moduleId": call.module_id,
                            "focus": call.focus,
                        })
                    else:
                        assert call.tool is not None
                        out.append({
                            "callId": call.call_id,
                            "kind": "tool-call",
                            "tool": {
                                "name": call.tool["name"],
                                "moduleId": call.tool["moduleId"],
                                "version": call.tool["version"],
                                "digest": call.tool["digest"],
                                "generation": call.tool["generation"],
                            },
                            "arguments": call.arguments,
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

    def _enqueue_and_wait(self, req_id: object, pending: _PendingCall) -> tuple[int, dict]:
        with self._lock:
            if self._closed:
                return (200, {"jsonrpc": "2.0", "id": req_id, "result": {"isError": True, "content": [{"type": "text", "text": "MCP bridge is shutting down."}]}})
            self._pending[pending.call_id] = pending

        try:
            completed = pending.event.wait(timeout=CALL_TIMEOUT_SECONDS)
        finally:
            with self._lock:
                self._pending.pop(pending.call_id, None)

        if not completed:
            return (200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "OpenDesk host tool call timed out (120s). Ensure the DeepSeek Harness tab is active and check for pending approval dialogs."}],
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
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "opendesk-workbench-bridge", "version": self.version},
                    "instructions": (
                        "OpenDesk local workbench bridge. Use opendesk_list_modules to discover "
                        "installed tabs, opendesk_open_module to wake an unopened tab after user consent, "
                        "and authorized module tools to inspect or perform confirmed actions."
                    ),
                },
            })

        if method == "ping":
            return (200, {"jsonrpc": "2.0", "id": req_id, "result": {}})

        if method == "tools/list":
            with self._lock:
                tools: list[dict] = []
                if self._host_meta_enabled:
                    tools.extend(HOST_META_TOOL_DEFS)
                for t in self._tools.values():
                    access = t.get("access", "read")
                    tag = "read-only" if access == "read" else ("ui-navigate" if access == "ui-navigate" else "write · requires user confirmation")
                    tools.append({
                        "name": t["name"],
                        "description": f"[{t['moduleId']} v{t['version']} · {tag}] {t['title']} — {t['description']}",
                        "inputSchema": t["inputSchema"],
                        "annotations": {
                            "readOnlyHint": access == "read",
                            "destructiveHint": access == "write",
                            "idempotentHint": access == "read",
                        },
                    })
            return (200, {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}})

        if method == "tools/call":
            if not isinstance(params, dict):
                return (200, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Invalid params"}})
            name = params.get("name")
            arguments = params.get("arguments")
            if arguments is None:
                arguments = {}
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return (200, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Invalid tool name or arguments object."}})
            if len(json.dumps(arguments, ensure_ascii=False).encode("utf-8")) > MAX_ARGUMENTS_BYTES:
                return (200, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Tool arguments exceed 16 KiB limit."}})

            with self._lock:
                meta_enabled = self._host_meta_enabled
                tool = self._tools.get(name)

            if name == HOST_META_LIST_MODULES:
                if not meta_enabled:
                    return (200, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "OpenDesk host module discovery is not enabled on this host."}})
                if len(arguments) != 0:
                    return (200, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "opendesk_list_modules takes no arguments."}})
                return self._enqueue_and_wait(req_id, _PendingCall(call_id=uuid.uuid4().hex, kind="list-modules"))

            if name == HOST_META_OPEN_MODULE:
                if not meta_enabled:
                    return (200, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "OpenDesk host module wake is not enabled on this host."}})
                unknown_keys = set(arguments.keys()) - {"moduleId", "focus"}
                module_id = arguments.get("moduleId")
                focus = arguments.get("focus", False)
                if unknown_keys or not isinstance(module_id, str) or not module_id or not isinstance(focus, bool):
                    return (200, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "opendesk_open_module requires moduleId (string) and optional focus (boolean)."}})
                return self._enqueue_and_wait(
                    req_id,
                    _PendingCall(call_id=uuid.uuid4().hex, kind="open-module", module_id=module_id, focus=focus),
                )

            if tool is None:
                return (200, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": f"Tool not authorized: {name}"}})
            if tool.get("contractVersion", 1) == 1 and len(arguments) != 0:
                return (200, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "contractVersion=1 read-only tools accept no arguments."}})
            return self._enqueue_and_wait(
                req_id,
                _PendingCall(call_id=uuid.uuid4().hex, kind="tool-call", tool=tool, arguments=arguments),
            )

        return (200, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._tools.clear()
            self._host_meta_enabled = False
            self._fail_all_pending("OpenDesk 正在关闭。")
            sse_clients = list(self._sse_clients)
            server = self._server
            self._server = None
            thread = self._thread
            self._thread = None
        for q in sse_clients:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass
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
