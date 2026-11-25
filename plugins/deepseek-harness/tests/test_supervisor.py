"""Integration tests for DshService / DshSupervisor with a real mock dsh Node process."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest import mock
import urllib.request

from deepseek_harness.probe import PnpmInfo, probe_environment
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

                # Stage 1 & 4: isolated mode stages zero-config MCP patch (no raw token on disk) & router SKILL.md.
                patch_file = data_dir / "opendesk-bridge.patch.yml"
                self.assertTrue(patch_file.is_file())
                patch_text = patch_file.read_text(encoding="utf-8")
                self.assertIn("@deepseek-ai/dsh-mcp-client", patch_text)
                self.assertIn("OPENDESK_MCP_TOKEN", patch_text)
                self.assertNotIn(service.mcp.token, patch_text)
                skill_file = data_dir / "dsh-home" / "skills" / "opendesk-workbench" / "SKILL.md"
                self.assertTrue(skill_file.is_file())
                self.assertIn("opendesk_list_modules", skill_file.read_text(encoding="utf-8"))

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

    def test_managed_child_appends_pnpm_fallback_shim_to_path(self):
        node_bin = shutil.which("node")
        if not node_bin:
            self.skipTest("node is required for supervisor integration test")

        with tempfile.TemporaryDirectory(prefix="dsh-shim-svc-") as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            fake_pnpm_js = root / "fake-pnpm.mjs"
            fake_pnpm_js.write_text(
                "console.log(JSON.stringify({ ok: true, argv: process.argv.slice(2), prompt: process.env.COREPACK_ENABLE_DOWNLOAD_PROMPT || '' }));\n",
                encoding="utf-8",
            )
            mock_dsh_js = root / "mock-dsh-pnpm.js"
            mock_dsh_js.write_text(
                r"""
const http = require('node:http');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const args = process.argv.slice(2);
let port = 0;
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--port' && args[i + 1]) port = Number(args[i + 1]);
}
const server = http.createServer((req, res) => {
  const parts = (process.env.PATH || '').split(path.delimiter).filter(Boolean);
  const shimDir = parts[parts.length - 1] || '';
  const shimCmd = process.platform === 'win32' ? path.join(shimDir, 'pnpm.cmd') : path.join(shimDir, 'pnpm');
  const run = process.platform === 'win32'
    ? spawnSync('cmd.exe', ['/d', '/s', '/c', `""${shimCmd}" -v"`], { encoding: 'utf8', windowsVerbatimArguments: true })
    : spawnSync(shimCmd, ['-v'], { encoding: 'utf8' });
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({
    pathLast: shimDir,
    rc: run.status,
    stdout: (run.stdout || '').trim(),
    stderr: (run.stderr || '').trim(),
  }));
});
server.listen(port, '127.0.0.1', () => {
  console.log(`DeepSeek Harness Web UI listening at http://127.0.0.1:${server.address().port}/`);
});
process.on('SIGTERM', () => server.close(() => process.exit(0)));
""",
                encoding="utf-8",
            )

            service = DshService(data_dir, version="0.1.0")
            try:
                proxy_url = service.start_webapp()
                service.configure({
                    "launchMode": "managed",
                    "dshSource": "custom",
                    "customDshPath": str(mock_dsh_js),
                    "homeMode": "isolated",
                    "autoStart": False,
                })
                real_probe = probe_environment(
                    data_dir,
                    dsh_source="custom",
                    custom_dsh_path=str(mock_dsh_js),
                )
                fallback_probe = real_probe.__class__(
                    node=real_probe.node,
                    npmPath=real_probe.npmPath,
                    pnpm=PnpmInfo(
                        True,
                        "corepack",
                        str(Path(node_bin).resolve()),
                        str(fake_pnpm_js.resolve()),
                        "corepack pnpm",
                    ),
                    dsh=real_probe.dsh,
                    sourceRepo=real_probe.sourceRepo,
                    managedCliDir=real_probe.managedCliDir,
                    managedCliInstalled=real_probe.managedCliInstalled,
                    systemDshHome=real_probe.systemDshHome,
                )

                with mock.patch("deepseek_harness.service.probe_environment", return_value=fallback_probe):
                    service.start_dsh()

                deadline = time.monotonic() + 15.0
                while time.monotonic() < deadline:
                    snap = service.status()["runtime"]
                    if snap["state"] == "running":
                        break
                    self.assertNotEqual(snap["state"], "error", snap.get("error"))
                    time.sleep(0.1)
                self.assertEqual(service.status()["runtime"]["state"], "running")

                with urllib.request.urlopen(proxy_url, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    data = json.loads(resp.read().decode("utf-8"))
                expected_shim_dir = str((data_dir / "runtime-shims").resolve())
                self.assertEqual(os.path.normcase(data["pathLast"]), os.path.normcase(expected_shim_dir))
                self.assertEqual(data["rc"], 0, data["stderr"])
                parsed = json.loads(data["stdout"])
                self.assertTrue(parsed["ok"])
                self.assertEqual(parsed["argv"], ["pnpm", "-v"])
                self.assertEqual(parsed["prompt"], "0")

                log_text = "\n".join(entry["line"] for entry in service.logs()["entries"])
                self.assertIn("未检测到全局 pnpm，已自动注入隔离 pnpm 垫片", log_text)
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
