"""Module-only ASGI boundary; the original local-origin middleware stays intact."""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode

from starlette.responses import HTMLResponse, JSONResponse, Response

RESOURCE = re.compile(r"^/api/(?:catalog-library/(?:people/)?assets/[^/]+|operations/[^/]+/log/file)$")
PREFERENCE = re.compile(r"^(?:nimda\.|jp-tv-browse-)[A-Za-z0-9_.-]{1,120}$")
BOOTSTRAP_PATH = Path(__file__).with_name("bootstrap.js")
CONFIG_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>作品管理器</title></head>
<body style="background:#101827;color:#dce5ef;font:15px system-ui;padding:28px"><h2>作品管理器</h2>
<p id="state">请在 OpenDesk 页签中选择作品管理工作区。</p>
<script>setInterval(async()=>{try {const r=await fetch('/__opendesk/status');const s=await r.json();
if(s.configured) location.reload();else document.getElementById('state').textContent=s.error||(s.busy?'正在加载工作区…':'请在 OpenDesk 页签中选择作品管理工作区。');}catch(_){}},1000);</script></body></html>"""


class SessionGate:
    def __init__(self, controller, token=None):
        self.controller = controller
        self.token = token or secrets.token_urlsafe(32)
        self.origin = ""
        self.authority = ""
        self.target = None
        self.state = SimpleNamespace()
        self.loop = None
        self.lock = threading.RLock()
        self.draining = False
        self.active = 0
        self._lifespan = None

    def bind(self, url):
        self.origin = url.rstrip("/")
        self.authority = self.origin.removeprefix("http://")

    def queues(self):
        if self.target is None:
            return ()
        from work_catalog_yaml.api_runtime import api_work_queues
        return api_work_queues(self.target)

    def begin_shutdown(self):
        with self.lock:
            self.draining = True
            for queue in self.queues():
                queue.begin_shutdown()
            running = sum(queue.snapshot()["running"] for queue in self.queues())
            return {"ready": self.active == 0 and running == 0 and not self.controller.loading,
                    "active": max(self.active, running) + int(self.controller.loading)}

    async def install(self, app):
        with self.lock:
            if self.draining or self.target is not None:
                raise RuntimeError("Module is stopping or already configured")
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        with self.lock:
            stopping = self.draining
            if not stopping:
                self.target, self.state, self._lifespan = app, app.state, lifespan
        if stopping:
            await lifespan.__aexit__(None, None, None)
            raise RuntimeError("Module is stopping")

    async def _lifespan_scope(self, receive, send):
        self.loop = asyncio.get_running_loop()
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                if self._lifespan is not None:
                    await self._lifespan.__aexit__(None, None, None)
                await send({"type": "lifespan.shutdown.complete"})
                return

    @staticmethod
    async def body(receive, maximum=20000):
        parts, length = [], 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                raise ValueError("Request disconnected")
            part = message.get("body", b"")
            length += len(part)
            if length > maximum:
                raise ValueError("Request too large")
            parts.append(part)
            if not message.get("more_body"):
                return b"".join(parts)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self._lifespan_scope(receive, send)
        if scope["type"] != "http":
            return
        path, method = scope["path"], scope["method"]
        headers = scope.get("headers", [])
        host_values = [v.decode("latin1") for k, v in headers if k.lower() == b"host"]
        client = scope.get("client")

        async def safe_send(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [(k, v) for k, v in message.get("headers", []) if k.lower() not in {b"referrer-policy", b"x-content-type-options"}]
                message["headers"] += [(b"referrer-policy", b"no-referrer"), (b"x-content-type-options", b"nosniff")]
            await send(message)

        async def reject(code, message):
            return await JSONResponse({"ok": False, "error": message}, status_code=code)(scope, receive, safe_send)

        if host_values != [self.authority] or (client and client[0] not in {"127.0.0.1", "::1"}):
            return await reject(403, "Invalid local service address")
        query = parse_qsl(scope.get("query_string", b"").decode("utf-8", "replace"), keep_blank_values=True)
        session_headers = [v.decode("latin1") for k, v in headers if k.lower() == b"x-nimda-session"]
        query_tokens = [value for key, value in query if key == "__nimda_session"]
        header_ok = len(session_headers) == 1 and secrets.compare_digest(session_headers[0].encode("utf-8"), self.token.encode("ascii"))
        resource_ok = method in {"GET", "HEAD"} and RESOURCE.fullmatch(path) and len(query_tokens) == 1 and secrets.compare_digest(query_tokens[0].encode("utf-8"), self.token.encode("ascii"))
        protected = path == "/api" or path.startswith("/api/") or path in {"/__opendesk/status", "/__opendesk/preferences"}
        origins = [v.decode("latin1") for k, v in headers if k.lower() == b"origin"]
        if protected and (not (header_ok or resource_ok) or (origins and origins != [self.origin])):
            return await reject(401, "Media Manager session authentication required")
        if path == "/__opendesk/bootstrap.js" and method in {"GET", "HEAD"}:
            return await Response(BOOTSTRAP_PATH.read_bytes(), media_type="text/javascript", headers={"Cache-Control": "no-store"})(scope, receive, safe_send)
        if path == "/__opendesk/status" and method == "GET":
            return await JSONResponse(self.controller.status(include_url=False))(scope, receive, safe_send)
        tracked = protected
        if tracked:
            with self.lock:
                stopping = self.draining
                if not stopping:
                    self.active += 1
            if stopping:
                return await reject(503, "Media Manager is finishing active operations and shutting down")
        try:
            if path == "/__opendesk/preferences":
                try:
                    if method == "GET":
                        result = self.controller.preferences()
                    elif method == "POST":
                        raw = json.loads(await self.body(receive))
                        if not isinstance(raw, dict) or set(raw) != {"key", "value"} or not isinstance(raw["key"], str) or not PREFERENCE.fullmatch(raw["key"]) or not (raw["value"] is None or isinstance(raw["value"], str) and len(raw["value"]) <= 16000):
                            raise ValueError("Invalid preference")
                        self.controller.save_preference(raw["key"], raw["value"])
                        result = {"ok": True}
                    else:
                        return await reject(405, "Method not allowed")
                    return await JSONResponse(result)(scope, receive, safe_send)
                except (ValueError, OSError):
                    return await reject(400, "Could not read or save module preferences")
            if path.startswith("/__opendesk/"):
                return await reject(404, "Not found")
            if self.target is None:
                if path in {"/", "/index.html"} and method in {"GET", "HEAD"}:
                    html = CONFIG_HTML.replace("</head>", '<script src="/__opendesk/bootstrap.js"></script></head>')
                    return await HTMLResponse(html, headers={"Cache-Control": "no-store"})(scope, receive, safe_send)
                return await reject(503, "Choose a Media Manager workspace in OpenDesk first")
            delegated = dict(scope)
            if protected:
                delegated["headers"] = [(k, v) for k, v in headers if k.lower() not in {b"origin", b"sec-fetch-site", b"x-nimda-session"}]
                delegated["headers"] += [(b"origin", self.origin.encode("ascii")), (b"sec-fetch-site", b"same-origin")]
                delegated["query_string"] = urlencode([(k, v) for k, v in query if k != "__nimda_session"]).encode("utf-8")
            if path in {"/", "/index.html", "/src/main.js"} and method == "GET":
                messages = []
                async def capture(message):
                    messages.append(message)
                await self.target(delegated, receive, capture)
                start = next((m for m in messages if m["type"] == "http.response.start"), None)
                if start and start["status"] == 200:
                    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
                    if path == "/src/main.js":
                        body = b"await window.NimdaOpenDeskReady;\n" + body
                    else:
                        body = body.replace(b"</head>", b'<script src="/__opendesk/bootstrap.js"></script></head>', 1)
                    start["headers"] = [(k, v) for k, v in start["headers"] if k.lower() not in {b"content-length", b"etag", b"last-modified"}]
                    start["headers"].append((b"content-length", str(len(body)).encode("ascii")))
                    await safe_send(start)
                    return await safe_send({"type": "http.response.body", "body": body})
                for message in messages:
                    await safe_send(message)
                return
            return await self.target(delegated, receive, safe_send)
        finally:
            if tracked:
                with self.lock:
                    self.active -= 1
