"""Pure-stdlib asyncio HTTP/1.1 + WebSocket streaming reverse proxy for DeepSeek Harness.

Responsibilities:
- Listens strictly on 127.0.0.1:<os-port> with Host header verification.
- Exchanges dsh's one-time ?token= launch URL server-side for its signed session
  cookie so the launch token never enters the browser URL and iframe SameSite=Strict
  restrictions in WebView2 cannot break authentication.
- Rewrites Host, Origin, and Referer to 127.0.0.1:<upstream_port> to satisfy dsh's
  browser-trust fence.
- Strips X-Frame-Options / CSP / COOP / COEP headers so OpenDesk's Web App iframe can
  embed the full dsh web UI and any dynamic Cordis UI plugins.
- Streams HTTP request/response bodies (including SSE, chunked transfers, uploads,
  downloads) without buffering and splices WebSocket connections (/api/remote.mux
  and plugin WS endpoints) full-duplex.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from http.cookies import SimpleCookie
import html
import threading
from typing import Callable

MAX_HEADER_BYTES = 64 * 1024
STREAM_CHUNK = 16 * 1024
STRIP_RESPONSE_HEADERS = frozenset({
    "x-frame-options",
    "content-security-policy",
    "content-security-policy-report-only",
    "cross-origin-opener-policy",
    "cross-origin-embedder-policy",
    "cross-origin-resource-policy",
})


@dataclass
class UpstreamTarget:
    port: int
    launch_token: str = ""
    cookies: dict[str, str] | None = None


def _parse_headers(raw_block: bytes) -> tuple[str, list[tuple[str, str]]]:
    text = raw_block.decode("iso-8859-1")
    lines = text.split("\r\n")
    first_line = lines[0]
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            raise ValueError("Malformed HTTP header")
        name, value = line.split(":", 1)
        headers.append((name.strip(), value.strip()))
    return first_line, headers


def _extract_set_cookie_pairs(value: str) -> dict[str, str]:
    jar = SimpleCookie()
    try:
        jar.load(value)
    except Exception:
        # Fallback simple split on first ';'
        first = value.split(";", 1)[0]
        if "=" in first:
            k, v = first.split("=", 1)
            k = k.strip()
            if k:
                return {k: v.strip()}
        return {}
    return {k: morsel.value for k, morsel in jar.items()}


def _relax_set_cookie_for_loopback(value: str) -> str:
    parts = [part.strip() for part in value.split(";")]
    rewritten: list[str] = []
    for idx, part in enumerate(parts):
        if idx == 0:
            rewritten.append(part)
            continue
        lower = part.lower()
        if lower == "secure":
            continue
        if lower.startswith("samesite="):
            rewritten.append("SameSite=Lax")
            continue
        rewritten.append(part)
    return "; ".join(rewritten)


def _merge_cookie_header(client_cookie: str, managed_cookies: dict[str, str]) -> str:
    merged: dict[str, str] = {}
    if client_cookie:
        for item in client_cookie.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                k = k.strip()
                if k:
                    merged[k] = v.strip()
    # Managed upstream auth cookies take authority over browser-supplied values.
    for k, v in managed_cookies.items():
        merged[k] = v
    return "; ".join(f"{k}={v}" for k, v in merged.items())


def _is_websocket_upgrade(headers: list[tuple[str, str]]) -> bool:
    has_upgrade_conn = False
    has_ws = False
    for name, value in headers:
        lower = name.lower()
        if lower == "connection" and "upgrade" in value.lower():
            has_upgrade_conn = True
        elif lower == "upgrade" and value.lower() == "websocket":
            has_ws = True
    return has_upgrade_conn and has_ws


async def _read_http_head(reader: asyncio.StreamReader) -> bytes:
    buf = bytearray()
    while True:
        chunk = await reader.readuntil(b"\r\n\r\n")
        buf.extend(chunk)
        if len(buf) > MAX_HEADER_BYTES:
            raise ValueError("HTTP header block too large")
        return bytes(buf)


async def _forward_chunked_body(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while True:
        size_line = await reader.readline()
        if not size_line:
            return
        writer.write(size_line)
        size_token = size_line.split(b";", 1)[0].strip()
        size = int(size_token, 16)
        if size == 0:
            # Forward optional trailers up to the terminating blank line.
            while True:
                trailer = await reader.readline()
                writer.write(trailer)
                if trailer in (b"\r\n", b"\n", b""):
                    break
            await writer.drain()
            return
        remaining = size + 2  # payload + trailing CRLF
        while remaining > 0:
            data = await reader.read(min(STREAM_CHUNK, remaining))
            if not data:
                await writer.drain()
                return
            writer.write(data)
            remaining -= len(data)
        await writer.drain()


async def _forward_exact_body(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, length: int) -> None:
    remaining = length
    while remaining > 0:
        data = await reader.read(min(STREAM_CHUNK, remaining))
        if not data:
            break
        writer.write(data)
        await writer.drain()
        remaining -= len(data)


async def _pipe_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(STREAM_CHUNK)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError, asyncio.CancelledError):
        pass
    finally:
        try:
            if writer.can_write_eof():
                writer.write_eof()
        except (OSError, RuntimeError):
            pass


class ReverseProxyServer:
    """Thread-hosted asyncio reverse proxy bound to 127.0.0.1:<random-port>."""

    def __init__(self, log_callback: Callable[[str], None] | None = None):
        self._log = log_callback or (lambda _msg: None)
        self._lock = threading.RLock()
        self._upstream: UpstreamTarget | None = None
        self._status_message = "DeepSeek Harness 尚未启动。"
        self._port = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.AbstractServer | None = None
        self._thread: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._start_error: BaseException | None = None
        self._tasks: set[asyncio.Task] = set()
        self._closing = False

    @property
    def port(self) -> int:
        return self._port

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self._port}" if self._port else ""

    def start(self) -> str:
        with self._lock:
            if self._thread and self._thread.is_alive() and self._port:
                return f"{self.origin}/"
            self._closing = False
            self._start_error = None
            self._ready_event.clear()
            self._thread = threading.Thread(target=self._run_loop, name="dsh-reverse-proxy", daemon=True)
            self._thread.start()
        if not self._ready_event.wait(timeout=10.0):
            raise RuntimeError("PROXY_START_TIMEOUT: local reverse proxy did not bind in time.")
        if self._start_error is not None:
            raise RuntimeError(f"PROXY_START_FAILED: {self._start_error}") from self._start_error
        return f"{self.origin}/"

    def set_status_message(self, message: str) -> None:
        with self._lock:
            self._status_message = message

    def clear_upstream(self, message: str = "DeepSeek Harness 已停止。") -> None:
        with self._lock:
            self._upstream = None
            self._status_message = message

    def set_upstream(self, port: int, launch_token: str = "", cookies: dict[str, str] | None = None) -> None:
        with self._lock:
            self._upstream = UpstreamTarget(
                port=port,
                launch_token=launch_token,
                cookies=dict(cookies or {}),
            )
            self._status_message = "DeepSeek Harness 运行中。"

    def current_cookies(self) -> dict[str, str]:
        with self._lock:
            if not self._upstream or not self._upstream.cookies:
                return {}
            return dict(self._upstream.cookies)

    def exchange_token(self, port: int, token: str, timeout: float = 8.0) -> dict[str, str]:
        """Exchange a dsh launch token server-side for its signed session cookie."""
        if not token:
            return {}
        loop = self._loop
        if loop is None or loop.is_closed():
            return asyncio.run(self._exchange_token_async(port, token, timeout))
        future = asyncio.run_coroutine_threadsafe(self._exchange_token_async(port, token, timeout), loop)
        return future.result(timeout=timeout + 2.0)

    async def _exchange_token_async(self, port: int, token: str, timeout: float = 8.0) -> dict[str, str]:
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=timeout)
        try:
            req = (
                f"GET /?token={token} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("iso-8859-1")
            writer.write(req)
            await writer.drain()
            raw_head = await asyncio.wait_for(_read_http_head(reader), timeout=timeout)
            _status_line, resp_headers = _parse_headers(raw_head[:-4])
            cookies: dict[str, str] = {}
            for name, value in resp_headers:
                if name.lower() == "set-cookie":
                    cookies.update(_extract_set_cookie_pairs(value))
            return cookies
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    def stop(self) -> None:
        with self._lock:
            self._closing = True
            self._upstream = None
            loop = self._loop
            thread = self._thread
        if loop and not loop.is_closed():
            fut = asyncio.run_coroutine_threadsafe(self._shutdown_async(), loop)
            try:
                fut.result(timeout=5.0)
            except Exception:
                pass
        if thread and thread.is_alive():
            thread.join(timeout=5.0)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._start_server())
            self._ready_event.set()
            loop.run_forever()
        except BaseException as error:
            self._start_error = error
            self._ready_event.set()
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            finally:
                loop.close()

    async def _start_server(self) -> None:
        server = await asyncio.start_server(self._handle_connection, host="127.0.0.1", port=0)
        sock = server.sockets[0]
        self._port = sock.getsockname()[1]
        self._server = server

    async def _shutdown_async(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except OSError:
                pass
            self._server = None
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        await asyncio.sleep(0)
        if self._loop and self._loop.is_running():
            self._loop.stop()

    def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(self._serve_client(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send_simple(
        self,
        writer: asyncio.StreamWriter,
        status: str,
        content_type: str,
        body: bytes,
    ) -> None:
        head = (
            f"HTTP/1.1 {status}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Cache-Control: no-store\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("iso-8859-1")
        writer.write(head + body)
        await writer.drain()

    async def _serve_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            try:
                raw_head = await asyncio.wait_for(_read_http_head(reader), timeout=15.0)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ValueError, OSError):
                return

            try:
                request_line, req_headers = _parse_headers(raw_head[:-4])
            except ValueError:
                await self._send_simple(writer, "400 Bad Request", "text/plain; charset=utf-8", b"Bad Request")
                return

            parts = request_line.split(" ")
            if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
                await self._send_simple(writer, "400 Bad Request", "text/plain; charset=utf-8", b"Bad Request")
                return
            method, target, version = parts

            # Enforce strict loopback Host header matching this proxy listener.
            expected_host = f"127.0.0.1:{self._port}"
            host_values = [v for k, v in req_headers if k.lower() == "host"]
            if len(host_values) != 1 or host_values[0].strip() != expected_host:
                await self._send_simple(writer, "403 Forbidden", "text/plain; charset=utf-8", b"Invalid Host")
                return

            with self._lock:
                upstream = self._upstream
                status_message = self._status_message
                managed_cookies = dict(upstream.cookies or {}) if upstream else {}

            if upstream is None:
                safe_msg = html.escape(status_message)
                page = (
                    "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
                    "<title>DeepSeek Harness</title>"
                    "<style>body{margin:0;padding:32px;font:14px/1.6 system-ui,sans-serif;"
                    "background:#0f172a;color:#e2e8f0}main{max-width:520px;margin:10vh auto;"
                    "padding:24px;border-radius:12px;background:#1e293b;border:1px solid #334155}"
                    "h1{margin:0 0 8px;font-size:18px}p{margin:0;color:#cbd5e1}</style>"
                    "</head><body><main><h1>DeepSeek Harness 代理已就绪</h1>"
                    f"<p>{safe_msg}</p></main></body></html>"
                ).encode("utf-8")
                await self._send_simple(writer, "503 Service Unavailable", "text/html; charset=utf-8", page)
                return

            upstream_port = upstream.port
            upstream_host = f"127.0.0.1:{upstream_port}"
            upstream_origin = f"http://{upstream_host}"
            proxy_origin = f"http://{expected_host}"
            is_ws = _is_websocket_upgrade(req_headers)

            try:
                up_reader, up_writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", upstream_port),
                    timeout=10.0,
                )
                upstream_writer = up_writer
            except (OSError, asyncio.TimeoutError) as error:
                self._log(f"proxy upstream connect failed (port={upstream_port}): {error}")
                await self._send_simple(
                    writer,
                    "502 Bad Gateway",
                    "text/plain; charset=utf-8",
                    b"DeepSeek Harness upstream is unavailable.",
                )
                return

            # Rewrite request headers for dsh Host/Origin fence and session cookie authority.
            out_headers: list[tuple[str, str]] = []
            client_cookie = ""
            content_length = 0
            is_chunked_req = False

            for name, value in req_headers:
                lower = name.lower()
                if lower == "host":
                    out_headers.append(("Host", upstream_host))
                elif lower == "origin":
                    out_headers.append(("Origin", upstream_origin))
                elif lower == "referer":
                    if value.startswith(proxy_origin):
                        out_headers.append(("Referer", upstream_origin + value[len(proxy_origin):]))
                    else:
                        out_headers.append(("Referer", upstream_origin + "/"))
                elif lower == "cookie":
                    client_cookie = f"{client_cookie}; {value}" if client_cookie else value
                elif lower == "connection":
                    if is_ws:
                        out_headers.append((name, value))
                elif lower == "keep-alive":
                    continue
                elif lower == "content-length":
                    try:
                        content_length = max(0, int(value.strip()))
                    except ValueError:
                        content_length = 0
                    out_headers.append((name, value))
                elif lower == "transfer-encoding":
                    if "chunked" in value.lower():
                        is_chunked_req = True
                    out_headers.append((name, value))
                else:
                    out_headers.append((name, value))

            if not is_ws:
                out_headers.append(("Connection", "close"))

            merged_cookie = _merge_cookie_header(client_cookie, managed_cookies)
            if merged_cookie:
                out_headers.append(("Cookie", merged_cookie))

            req_head_bytes = (
                f"{method} {target} {version}\r\n"
                + "".join(f"{k}: {v}\r\n" for k, v in out_headers)
                + "\r\n"
            ).encode("iso-8859-1")
            up_writer.write(req_head_bytes)
            await up_writer.drain()

            if is_ws:
                # Read upstream HTTP 101 (or error) response head before splicing.
                up_raw_head = await asyncio.wait_for(_read_http_head(up_reader), timeout=15.0)
                status_line, resp_headers = _parse_headers(up_raw_head[:-4])
                self._capture_upstream_cookies(upstream, resp_headers)
                client_resp_headers = self._rewrite_response_headers(
                    resp_headers, upstream_port, is_ws=True
                )
                resp_bytes = (
                    f"{status_line}\r\n"
                    + "".join(f"{k}: {v}\r\n" for k, v in client_resp_headers)
                    + "\r\n"
                ).encode("iso-8859-1")
                writer.write(resp_bytes)
                await writer.drain()

                status_parts = status_line.split(" ", 2)
                if len(status_parts) >= 2 and status_parts[1] == "101":
                    t1 = asyncio.create_task(_pipe_stream(reader, up_writer))
                    t2 = asyncio.create_task(_pipe_stream(up_reader, writer))
                    done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*done, *pending, return_exceptions=True)
                else:
                    await _pipe_stream(up_reader, writer)
                return

            # Standard HTTP request body forwarding (streamed)
            if is_chunked_req:
                await _forward_chunked_body(reader, up_writer)
            elif content_length > 0:
                await _forward_exact_body(reader, up_writer, content_length)

            # Read upstream response head (skip interim 1xx headers except 101)
            while True:
                up_raw_head = await asyncio.wait_for(_read_http_head(up_reader), timeout=120.0)
                status_line, resp_headers = _parse_headers(up_raw_head[:-4])
                status_parts = status_line.split(" ", 2)
                code = int(status_parts[1]) if len(status_parts) >= 2 and status_parts[1].isdigit() else 200
                if 100 <= code < 200 and code != 101:
                    continue
                break

            self._capture_upstream_cookies(upstream, resp_headers)
            client_resp_headers = self._rewrite_response_headers(
                resp_headers, upstream_port, is_ws=False
            )
            resp_bytes = (
                f"{status_line}\r\n"
                + "".join(f"{k}: {v}\r\n" for k, v in client_resp_headers)
                + "\r\n"
            ).encode("iso-8859-1")
            writer.write(resp_bytes)
            await writer.drain()

            if method.upper() != "HEAD" and code not in (204, 304):
                await _pipe_stream(up_reader, writer)

        except (ConnectionError, OSError, asyncio.CancelledError, asyncio.TimeoutError):
            pass
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
                try:
                    await upstream_writer.wait_closed()
                except OSError:
                    pass
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    def _capture_upstream_cookies(self, upstream: UpstreamTarget, resp_headers: list[tuple[str, str]]) -> None:
        updates: dict[str, str] = {}
        for name, value in resp_headers:
            if name.lower() == "set-cookie":
                updates.update(_extract_set_cookie_pairs(value))
        if updates:
            with self._lock:
                if self._upstream is upstream:
                    if self._upstream.cookies is None:
                        self._upstream.cookies = {}
                    self._upstream.cookies.update(updates)

    def _rewrite_response_headers(
        self,
        resp_headers: list[tuple[str, str]],
        upstream_port: int,
        *,
        is_ws: bool,
    ) -> list[tuple[str, str]]:
        proxy_origin = f"http://127.0.0.1:{self._port}"
        upstream_prefixes = (
            f"http://127.0.0.1:{upstream_port}",
            f"http://localhost:{upstream_port}",
        )
        rewritten: list[tuple[str, str]] = []
        for name, value in resp_headers:
            lower = name.lower()
            if lower in STRIP_RESPONSE_HEADERS:
                continue
            if lower == "connection":
                if is_ws:
                    rewritten.append((name, value))
                continue
            if lower == "keep-alive":
                continue
            if lower == "set-cookie":
                rewritten.append((name, _relax_set_cookie_for_loopback(value)))
                continue
            if lower == "location":
                replaced = value
                for prefix in upstream_prefixes:
                    if replaced.startswith(prefix):
                        replaced = proxy_origin + replaced[len(prefix):]
                        break
                rewritten.append((name, replaced))
                continue
            rewritten.append((name, value))
        if not is_ws:
            rewritten.append(("Connection", "close"))
        return rewritten
