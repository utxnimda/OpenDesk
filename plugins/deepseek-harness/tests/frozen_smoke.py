"""Exercise the frozen DeepSeek Harness module-worker.exe over stdio without downloading or running real dsh."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request


def main() -> None:
    exe, version = sys.argv[1:]
    with tempfile.TemporaryDirectory(prefix="opendesk-dsh-frozen-") as directory:
        env = {**os.environ, "OPENDESK_MODULE_DATA_DIR": directory}
        for key in ("PYTHONPATH", "PYTHONHOME"):
            env.pop(key, None)
        child = subprocess.Popen(
            [exe],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        replies: queue.Queue[bytes] = queue.Queue()

        def reader():
            assert child.stdout is not None
            for line in child.stdout:
                replies.put(line)

        threading.Thread(target=reader, daemon=True).start()

        def call(method: str, params: dict | None = None, *, expect_error: bool = False):
            assert child.stdin is not None
            payload = {"protocolVersion": 1, "id": method, "method": method, "params": params or {}}
            child.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
            child.stdin.flush()
            response = json.loads(replies.get(timeout=15))
            if expect_error:
                assert "error" in response, response
                return response["error"]
            assert "error" not in response, response
            return response["result"]

        try:
            capabilities = set(call("runtime.hello")["capabilities"])
            expected = {
                "runtime.hello",
                "runtime.ping",
                "runtime.shutdown",
                "module.identity",
                "module.webapp",
                "module.prepareShutdown",
                "dsh.status",
                "dsh.probe",
                "dsh.configure",
                "dsh.start",
                "dsh.stop",
                "dsh.restart",
                "dsh.install",
                "dsh.install.cancel",
                "dsh.source.install",
                "dsh.source.build",
                "dsh.source.cancel",
                "dsh.logs",
                "dsh.browser.open",
                "dsh.mcp.sync",
                "dsh.mcp.poll",
                "dsh.mcp.respond",
            }
            assert capabilities == expected, (capabilities, expected)
            assert call("module.identity") == {"id": "deepseek-harness", "version": version, "hostApi": 2}

            webapp = call("module.webapp")
            proxy_url = webapp["url"]
            assert proxy_url.startswith("http://127.0.0.1:")

            status = call("dsh.status")
            assert status["version"] == version
            assert status["runtime"]["proxyUrl"] == proxy_url
            assert status["mcp"]["active"] is False

            mcp_cleared = call("dsh.mcp.sync", {"tools": []})
            assert mcp_cleared["active"] is False
            assert call("dsh.mcp.poll") == {"calls": []}

            # Source tasks refuse cleanly when no source repo is configured.
            src_err = call("dsh.source.build", {"restartAfter": False}, expect_error=True)
            assert src_err["code"] == "SOURCE_REPO_UNSET"

            # Before dsh is started, proxy returns 503 status page.
            try:
                urllib.request.urlopen(proxy_url, timeout=5)
            except urllib.error.HTTPError as err:
                assert err.code == 503
            else:
                raise AssertionError("Expected 503 while upstream dsh is idle")

            # Invalid parameters must be rejected without killing the healthy worker.
            err = call("dsh.configure", {"profile": "desktop"}, expect_error=True)
            assert err["code"] == "INVALID_CONFIG"
            assert call("runtime.ping")["message"] == "pong"

            assert call("module.prepareShutdown") == {"ready": True, "active": 0}
            call("runtime.shutdown")
            assert child.wait(timeout=10) == 0
            print("DeepSeek Harness frozen identity, loopback proxy, validation, and drain passed.")
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)


if __name__ == "__main__":
    main()
