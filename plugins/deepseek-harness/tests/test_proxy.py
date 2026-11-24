"""End-to-end loopback tests for HTTP streaming, token cookie exchange, header stripping, and WebSocket splice."""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import unittest
import urllib.error
import urllib.request

from deepseek_harness.proxy import ReverseProxyServer


class MockDshServer:
    """Minimal asyncio server mimicking dsh web: launch-token cookie auth, fence checks, SSE, and WebSocket."""

    def __init__(self, expected_token: str = "launch-secret-123"):
        self.expected_token = expected_token
        self.cookie_name = "dsh-auth-test"
        self.cookie_value = "signed-session-xyz"
        self.port = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.AbstractServer | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> int:
        self._thread.start()
        self._ready.wait(timeout=5.0)
        return self.port

    def stop(self) -> None:
        if self._loop and not self._loop.is_closed():
            fut = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
            try:
                fut.result(timeout=3.0)
            except Exception:
                pass
        self._thread.join(timeout=3.0)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._bind())
            self._ready.set()
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def _bind(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def _shutdown(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        await asyncio.sleep(0)
        if self._loop:
            self._loop.stop()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                raw = await reader.readuntil(b"\r\n\r\n")
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                return
            lines = raw[:-4].decode("iso-8859-1").split("\r\n")
            method, target, _ver = lines[0].split(" ")
            headers: dict[str, str] = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            expected_host = f"127.0.0.1:{self.port}"
            if headers.get("host") != expected_host:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 9\r\nConnection: close\r\n\r\nBad Fence")
                await writer.drain()
                return

            if target == f"/?token={self.expected_token}":
                resp = (
                    "HTTP/1.1 303 See Other\r\n"
                    "Location: ./\r\n"
                    f"Set-Cookie: {self.cookie_name}={self.cookie_value}; Path=/; HttpOnly; SameSite=Strict\r\n"
                    "Content-Length: 0\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("iso-8859-1")
                writer.write(resp)
                await writer.drain()
                return

            authed = f"{self.cookie_name}={self.cookie_value}" in headers.get("cookie", "")
            if not authed:
                writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 12\r\nConnection: close\r\n\r\nUnauthorized")
                await writer.drain()
                return

            if target == "/api/remote.mux" and headers.get("upgrade", "").lower() == "websocket":
                resp = (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Accept: mock-accept\r\n\r\n"
                ).encode("iso-8859-1")
                writer.write(resp)
                await writer.drain()
                # Echo binary frames back until closed.
                while True:
                    chunk = await reader.read(1024)
                    if not chunk:
                        break
                    writer.write(b"ECHO:" + chunk)
                    await writer.drain()
                return

            if target == "/api/events":
                writer.write(
                    (
                        "HTTP/1.1 200 OK\r\n"
                        "Content-Type: text/event-stream\r\n"
                        "X-Frame-Options: DENY\r\n"
                        "Content-Security-Policy: frame-ancestors 'none'\r\n"
                        "Connection: close\r\n\r\n"
                    ).encode("iso-8859-1")
                )
                await writer.drain()
                for idx in range(3):
                    writer.write(f"data: chunk-{idx}\n\n".encode("utf-8"))
                    await writer.drain()
                    await asyncio.sleep(0.02)
                return

            body_bytes = b""
            cl = int(headers.get("content-length", "0") or "0")
            if cl > 0:
                body_bytes = await reader.readexactly(cl)

            payload = json.dumps({
                "method": method,
                "target": target,
                "origin": headers.get("origin", ""),
                "body": body_bytes.decode("utf-8", errors="replace"),
            }).encode("utf-8")
            resp = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                "X-Frame-Options: DENY\r\n"
                "Content-Security-Policy: frame-ancestors 'none'\r\n"
                "Cross-Origin-Opener-Policy: same-origin\r\n"
                f"Content-Length: {len(payload)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("iso-8859-1") + payload
            writer.write(resp)
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


class ProxyTests(unittest.TestCase):
    def test_token_exchange_headers_streaming_websocket_and_host_fence(self):
        upstream = MockDshServer()
        up_port = upstream.start()
        proxy = ReverseProxyServer()
        proxy_url = proxy.start()
        try:
            # Before upstream is attached, proxy responds with 503.
            with self.assertRaises(urllib.error.HTTPError) as ctx503:
                urllib.request.urlopen(proxy_url, timeout=5)
            self.assertEqual(ctx503.exception.code, 503)

            # Exchange launch token server-side and bind upstream.
            cookies = proxy.exchange_token(up_port, upstream.expected_token)
            self.assertEqual(cookies.get(upstream.cookie_name), upstream.cookie_value)
            proxy.set_upstream(up_port, launch_token=upstream.expected_token, cookies=cookies)

            # Authenticated POST with Origin rewrite and iframe-blocking headers stripped.
            req = urllib.request.Request(
                f"{proxy.origin}/api/echo",
                data=b'{"hello":"dsh"}',
                headers={
                    "Content-Type": "application/json",
                    "Origin": proxy.origin,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIsNone(resp.headers.get("X-Frame-Options"))
                self.assertIsNone(resp.headers.get("Content-Security-Policy"))
                self.assertIsNone(resp.headers.get("Cross-Origin-Opener-Policy"))
                body = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(body["origin"], f"http://127.0.0.1:{up_port}")
                self.assertEqual(body["body"], '{"hello":"dsh"}')

            # SSE streaming pass-through.
            with urllib.request.urlopen(f"{proxy.origin}/api/events", timeout=5) as sse:
                data = sse.read().decode("utf-8")
                self.assertIn("data: chunk-0", data)
                self.assertIn("data: chunk-2", data)

            # Full-duplex WebSocket upgrade (/api/remote.mux) with server-injected auth cookie.
            with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as sock:
                handshake = (
                    "GET /api/remote.mux HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{proxy.port}\r\n"
                    f"Origin: {proxy.origin}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    "Sec-WebSocket-Version: 13\r\n\r\n"
                ).encode("iso-8859-1")
                sock.sendall(handshake)
                reply = sock.recv(4096)
                self.assertIn(b"101 Switching Protocols", reply)
                sock.sendall(b"ping-dsh-mux")
                echoed = sock.recv(4096)
                self.assertEqual(echoed, b"ECHO:ping-dsh-mux")

            # Host header spoofing must be rejected with 403.
            with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as sock:
                sock.sendall(b"GET / HTTP/1.1\r\nHost: attacker.example\r\nConnection: close\r\n\r\n")
                denied = sock.recv(1024)
                self.assertTrue(denied.startswith(b"HTTP/1.1 403"))
        finally:
            proxy.stop()
            upstream.stop()


if __name__ == "__main__":
    unittest.main()
