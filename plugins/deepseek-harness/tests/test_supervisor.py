"""Integration tests for DshService / DshSupervisor with a real mock dsh Node process."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
import urllib.request

from deepseek_harness.service import DshService
from deepseek_harness.supervisor import extract_dsh_url_and_token, redact_log_line

MOCK_DSH_JS = r"""
const http = require('node:http');
const args = process.argv.slice(2);
let port = 0;
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--port' && args[i + 1]) port = Number(args[i + 1]);
}
const TOKEN = 'mock-launch-token-999';
const COOKIE = 'dsh-auth-mock=valid-cookie-42';
const server = http.createServer((req, res) => {
  if (req.url === '/?token=' + TOKEN) {
    res.writeHead(303, {
      Location: './',
      'Set-Cookie': COOKIE + '; Path=/; HttpOnly; SameSite=Strict',
    });
    res.end();
    return;
  }
  if ((req.headers.cookie || '').includes(COOKIE)) {
    res.writeHead(200, {
      'Content-Type': 'application/json',
      'X-Frame-Options': 'DENY',
    });
    res.end(JSON.stringify({ ok: true, dshHome: process.env.DSH_HOME || '', cwd: process.cwd() }));
    return;
  }
  res.writeHead(401, { 'Content-Type': 'text/plain' });
  res.end('Unauthorized');
});
server.listen(port, '127.0.0.1', () => {
  const actualPort = server.address().port;
  console.log(`DeepSeek Harness Web UI listening at http://127.0.0.1:${actualPort}/?token=${TOKEN}`);
});
process.on('SIGTERM', () => server.close(() => process.exit(0)));
"""


class SupervisorTests(unittest.TestCase):
    def test_url_extraction_and_log_redaction(self):
        self.assertEqual(
            extract_dsh_url_and_token("Listening on http://127.0.0.1:3080/?token=abc123"),
            (3080, "abc123"),
        )
        self.assertEqual(
            extract_dsh_url_and_token("\x1b[32mdsh web:\x1b[0m \x1b[36mhttp://127.0.0.1:4090/?token=ansiTok\x1b[0m"),
            (4090, "ansiTok"),
        )
        redacted = redact_log_line("\x1b[32mOpen http://127.0.0.1:3080/?token=abc123 and Authorization: Bearer xyz\x1b[0m")
        self.assertNotIn("abc123", redacted)
        self.assertNotIn("xyz", redacted)
        self.assertNotIn("\x1b[", redacted)
        self.assertIn("token=***", redacted)

    def test_managed_lifecycle_proxy_and_safe_drain(self):
        node_bin = shutil.which("node")
        if not node_bin:
            self.skipTest("node is required for supervisor integration test")

        with tempfile.TemporaryDirectory(prefix="dsh-svc-") as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            mock_js = root / "mock-dsh.js"
            mock_js.write_text(MOCK_DSH_JS, encoding="utf-8")

            service = DshService(data_dir, version="0.1.0")
            try:
                proxy_url = service.start_webapp()
                self.assertTrue(proxy_url.startswith("http://127.0.0.1:"))

                service.configure({
                    "launchMode": "managed",
                    "dshSource": "custom",
                    "customDshPath": str(mock_js),
                    "homeMode": "isolated",
                    "autoStart": False,
                })
                service.start_dsh()

                deadline = time.monotonic() + 15.0
                while time.monotonic() < deadline:
                    snap = service.status()["runtime"]
                    if snap["state"] == "running":
                        break
                    self.assertNotEqual(snap["state"], "error", snap.get("error"))
                    time.sleep(0.1)
                self.assertEqual(service.status()["runtime"]["state"], "running")

                # Proxy must forward authenticated request with isolated DSH_HOME and strip X-Frame-Options.
                with urllib.request.urlopen(proxy_url, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertIsNone(resp.headers.get("X-Frame-Options"))
                    payload = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(payload["ok"])
                    self.assertEqual(Path(payload["dshHome"]), (data_dir / "dsh-home").resolve())

                # Logs must never leak the raw launch token.
                log_text = "\n".join(entry["line"] for entry in service.logs()["entries"])
                self.assertNotIn("mock-launch-token-999", log_text)
                self.assertIn("token=***", log_text)

                # Safe drain via prepare_shutdown must terminate the managed child cleanly.
                drain_deadline = time.monotonic() + 10.0
                drain = service.prepare_shutdown()
                while not drain["ready"] and time.monotonic() < drain_deadline:
                    time.sleep(0.1)
                    drain = service.prepare_shutdown()
                self.assertEqual(drain, {"ready": True, "active": 0})
            finally:
                service.close()

    def test_external_mode_connects_and_never_kills_external_process(self):
        from test_proxy import MockDshServer

        external = MockDshServer(expected_token="ext-token-777")
        ext_port = external.start()
        with tempfile.TemporaryDirectory(prefix="dsh-ext-") as tmp:
            service = DshService(Path(tmp) / "data", version="0.1.0")
            try:
                proxy_url = service.start_webapp()
                service.configure({
                    "launchMode": "external",
                    "externalUrl": f"http://127.0.0.1:{ext_port}/?token=ext-token-777",
                    "autoStart": False,
                })
                # Routine status must redact the external token.
                status = service.status()
                self.assertNotIn("ext-token-777", status["config"]["externalUrl"])
                self.assertTrue(status["config"]["hasExternalToken"])

                service.start_dsh()
                self.assertEqual(service.status()["runtime"]["state"], "running")

                with urllib.request.urlopen(f"{proxy_url}api/ping", timeout=5) as resp:
                    self.assertEqual(resp.status, 200)

                # Stopping / preparing shutdown must disconnect immediately without killing external server.
                self.assertEqual(service.prepare_shutdown(), {"ready": True, "active": 0})
                # External server is still alive and reachable directly.
                cookies = service.proxy.exchange_token(ext_port, "ext-token-777")
                self.assertEqual(cookies.get(external.cookie_name), external.cookie_value)
            finally:
                service.close()
                external.stop()


if __name__ == "__main__":
    unittest.main()
