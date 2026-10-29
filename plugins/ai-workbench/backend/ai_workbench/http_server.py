"""Loopback-only HTTP application; no shell, file browser or credential-read API."""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, unquote, urlsplit

from .errors import ApiError

BODY_LIMIT = 512 * 1024


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON property.")
        result[key] = value
    return result


def _nonfinite(_):
    raise ValueError("Non-finite JSON value.")


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, service):
        self.service = service
        super().__init__(("127.0.0.1", 0), Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenDeskAI"
    sys_version = ""

    def log_message(self, format, *args):
        # HTTP request paths can contain private user content. The task service
        # records only safe operation/error metadata; the HTTP server logs none.
        return

    @property
    def service(self):
        return self.server.service

    def _authorize(self, *, api):
        authority = "127.0.0.1:" + str(self.server.server_port)
        if self.headers.get("Host") != authority:
            raise ApiError("INVALID_HOST", "请求来源无效。", 403)
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + authority:
            raise ApiError("INVALID_ORIGIN", "拒绝来自其他页面的请求。", 403)
        if api:
            expected = "Bearer " + self.service.token
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
                raise ApiError("UNAUTHORIZED", "会话凭证无效，请重新打开 AI 页签。", 401)

    def _json(self, value, status=200):
        content = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(content)

    def _body(self):
        if self.headers.get("Transfer-Encoding"):
            raise ApiError("INVALID_BODY", "不支持分块请求体。", 400)
        if self.headers.get_content_type() != "application/json":
            raise ApiError("CONTENT_TYPE", "请求体必须是 JSON。", 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError("INVALID_BODY", "请求长度无效。") from None
        if length < 0 or length > BODY_LIMIT:
            raise ApiError("BODY_LIMIT", "请求内容过大。", 413)
        try:
            self.connection.settimeout(10)
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError()
            body = json.loads(data.decode("utf-8"), object_pairs_hook=_object, parse_constant=_nonfinite)
            if not isinstance(body, dict):
                raise ValueError()
            return body
        except (ValueError, UnicodeError, TimeoutError):
            raise ApiError("INVALID_JSON", "请求必须是有效的 JSON 对象。") from None

    def _safe_error(self, error):
        if isinstance(error, ApiError):
            self._json(error.payload(), error.status)
        elif hasattr(error, "code") and hasattr(error, "message"):
            self._json({"error": {"code": error.code, "message": error.message}}, 502)
        else:
            self._json({"error": {"code": "INTERNAL_ERROR", "message": "操作失败，请检查连接或稍后重试。"}}, 500)

    def do_OPTIONS(self):
        self._json({"error": {"code": "CORS_DENIED", "message": "不提供跨源 API。"}}, 403)

    def do_GET(self):
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            api = path.startswith("/api/")
            self._authorize(api=api)
            if not api:
                return self._static(path)
            if path == "/api/state":
                return self._json(self.service.state())
            match = re.fullmatch(r"/api/conversations/([a-f0-9]{32})", path)
            if match:
                return self._json(self.service.conversation(match[1]))
            match = re.fullmatch(r"/api/runs/([a-f0-9]{32})(/events)?", path)
            if match:
                try:
                    values = parse_qs(parsed.query, strict_parsing=True)
                except ValueError:
                    raise ApiError("INVALID_CURSOR", "事件查询参数无效。") from None
                if set(values) - {"after"} or any(len(v) != 1 for v in values.values()):
                    raise ApiError("INVALID_CURSOR", "事件查询参数无效。")
                try:
                    after = int(values.get("after", ["0"])[0])
                except ValueError:
                    raise ApiError("INVALID_CURSOR", "事件游标无效。") from None
                if match[2]:
                    return self._stream(match[1], after)
                return self._json(self.service.run(match[1], after))
            raise ApiError("NOT_FOUND", "接口不存在。", 404)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:
            self._safe_error(error)

    def do_POST(self):
        try:
            parsed = urlsplit(self.path)
            self._authorize(api=True)
            if parsed.query or parsed.fragment:
                raise ApiError("INVALID_PATH", "此操作不接受 URL 参数。")
            body = self._body()
            path = parsed.path
            if path == "/api/connections":
                return self._json(self.service.save_connection(body))
            if path == "/api/conversations":
                return self._json(self.service.create_conversation(body), 201)
            if path == "/api/agents/refresh":
                if body:
                    raise ApiError("INVALID_PARAMS", "Agent 发现不接受额外参数。")
                return self._json(self.service.refresh_agents())
            if path == "/api/workspaces":
                return self._json(self.service.save_workspace(body), 201)
            if path == "/api/layout":
                return self._json(self.service.save_layout(body))
            match = re.fullmatch(r"/api/connections/([a-f0-9]{32})/models", path)
            if match:
                if body:
                    raise ApiError("INVALID_PARAMS", "模型发现不接受额外参数。")
                return self._json(self.service.models(match[1]))
            match = re.fullmatch(r"/api/connections/([a-f0-9]{32})/workspaces", path)
            if match:
                if body:
                    raise ApiError("INVALID_PARAMS", "工作区读取不接受额外参数。")
                return self._json(self.service.workspaces(match[1]))
            match = re.fullmatch(r"/api/conversations/([a-f0-9]{32})/model", path)
            if match:
                return self._json(self.service.set_conversation_model(match[1], body))
            match = re.fullmatch(r"/api/conversations/([a-f0-9]{32})/messages", path)
            if match:
                return self._json(self.service.start_run(match[1], body), 202)
            match = re.fullmatch(r"/api/runs/([a-f0-9]{32})/(cancel|approval|tool-result)", path)
            if match:
                if match[2] == "cancel":
                    if body:
                        raise ApiError("INVALID_PARAMS", "取消操作不接受额外参数。")
                    return self._json(self.service.cancel(match[1]))
                return self._json(self.service.reply(match[1], body, "approval" if match[2] == "approval" else "tool"))
            raise ApiError("NOT_FOUND", "接口不存在。", 404)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:
            self._safe_error(error)

    def _static(self, path):
        relative = unquote(path).lstrip("/") or "index.html"
        if "\\" in relative or "\0" in relative:
            raise ApiError("NOT_FOUND", "资源不存在。", 404)
        candidate = (self.service.web_root / relative).resolve()
        if (not candidate.is_relative_to(self.service.web_root) or not candidate.is_file()
                or any(part.startswith(".") for part in Path(relative).parts)
                or candidate.suffix.lower() not in (".html", ".js", ".css", ".svg", ".png", ".ico", ".woff", ".woff2")):
            raise ApiError("NOT_FOUND", "资源不存在。", 404)
        data = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if candidate.suffix == ".js":
            content_type = "text/javascript"
        self.send_response(200)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if candidate.suffix in (".html", ".js", ".css") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, identity, after):
        first = self.service.run(identity, after)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        deadline = time.monotonic() + 25
        cursor = after
        result = first
        while time.monotonic() < deadline:
            for event in result["events"]:
                self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n\n").encode("utf-8"))
                cursor = event["sequence"]
            self.wfile.flush()
            if not result["events"] and result["run"]["status"] not in {"queued", "running", "waiting_tool", "waiting_approval", "cancelling"}:
                return
            control = self.service._runs.get(identity)
            if control:
                with control.changed:
                    control.changed.wait(0.25)
            else:
                return
            result = self.service.run(identity, cursor)
