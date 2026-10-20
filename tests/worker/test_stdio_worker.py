"""Contract checks against real worker processes, without extra dependencies."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKER_ROOT = ROOT / "apps" / "framework" / "worker"
MAX_FRAME_BYTES = 65536


def request(request_id: str, method: str = "runtime.ping", **changes: object) -> bytes:
    value = {"protocolVersion": 1, "id": request_id, "method": method, "params": {}}
    value.update(changes)
    return json.dumps(value, ensure_ascii=True).encode("utf-8") + b"\n"


class WorkerProcessTests(unittest.TestCase):
    def run_worker(self, payload: bytes) -> tuple[list[dict], str]:
        completed = subprocess.run(
            [sys.executable, "-m", "nimdadesk_worker"],
            cwd=WORKER_ROOT,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        stderr = completed.stderr.decode("utf-8")
        self.assertEqual(completed.returncode, 0, stderr)
        replies = [json.loads(line) for line in completed.stdout.splitlines()]
        for reply in replies:
            self.assertEqual(reply["protocolVersion"], 1)
            self.assertIsInstance(reply["id"], str)
            self.assertEqual(("result" in reply) + ("error" in reply), 1)
        return replies, stderr

    def test_responses_arrive_while_host_stdin_remains_open(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-m", "nimdadesk_worker"],
            cwd=WORKER_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            worker_pid: int | None = None
            for request_id, method in [
                ("interactive-first", "runtime.ping"),
                ("interactive-second", "runtime.ping"),
                ("stop", "runtime.shutdown"),
            ]:
                received: queue.Queue[bytes] = queue.Queue()
                reader = threading.Thread(target=lambda: received.put(process.stdout.readline()), daemon=True)
                reader.start()
                process.stdin.write(request(request_id, method))
                process.stdin.flush()
                reply = json.loads(received.get(timeout=5))
                reader.join(timeout=1)
                self.assertEqual(reply["protocolVersion"], 1)
                self.assertEqual(reply["id"], request_id)
                if method == "runtime.ping":
                    pid = reply["result"]["pid"]
                    self.assertIs(type(pid), int)
                    self.assertGreater(pid, 0)
                    self.assertEqual(reply["result"]["message"], "pong")
                    # Windows venv executables may be redirector parents. The
                    # protocol identifies the actual Python worker, which must
                    # stay the same across requests on this open connection.
                    if worker_pid is None:
                        worker_pid = pid
                    else:
                        self.assertEqual(pid, worker_pid)
                    self.assertIsNone(process.poll())
                else:
                    self.assertEqual(reply["result"], {"status": "stopping"})
            # shutdown must exit even though the host has not closed its input.
            self.assertEqual(process.wait(timeout=5), 0)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_handshake_multiple_requests_and_shutdown(self) -> None:
        replies, _ = self.run_worker(
            request("hello", "runtime.hello")
            + request("first")
            + request("second")
            + request("stop", "runtime.shutdown")
            + request("must-not-run")
        )
        self.assertEqual([reply["id"] for reply in replies], ["hello", "first", "second", "stop"])
        hello, first, second, stop = [reply["result"] for reply in replies]
        self.assertEqual(hello["protocolVersion"], 1)
        self.assertEqual(hello["maxFrameBytes"], MAX_FRAME_BYTES)
        self.assertEqual(hello["capabilities"], ["runtime.hello", "runtime.ping", "runtime.shutdown"])
        self.assertEqual(first["message"], "pong")
        self.assertEqual(first["pid"], second["pid"])
        self.assertGreater(first["pid"], 0)
        self.assertTrue(first["pythonVersion"])
        self.assertEqual(stop, {"status": "stopping"})

    def test_bad_json_and_utf8_recover_without_leaking_input(self) -> None:
        secret = "TEST_SECRET_MUST_NOT_APPEAR"
        replies, stderr = self.run_worker(
            ('{"token":"' + secret + '" BROKEN}\n').encode()
            + b"\xff\n"
            + b'{"protocolVersion":1,"id":"duplicate","id":"other","method":"runtime.ping","params":{}}\n'
            + request("ok")
        )
        self.assertEqual([reply["error"]["code"] for reply in replies[:-1]], ["INVALID_JSON"] * 3)
        self.assertEqual(replies[-1]["result"]["message"], "pong")
        self.assertNotIn(secret, stderr)
        self.assertNotIn(secret, json.dumps(replies))
        self.assertNotIn("Traceback", stderr)

    def test_invalid_requests_unknown_methods_and_versions_recover(self) -> None:
        invalid = [
            (b"[]\n", "", "INVALID_REQUEST"),
            (request(""), "", "INVALID_REQUEST"),
            (request("x" * 129), "", "INVALID_REQUEST"),
            (request("\ud800"), "", "INVALID_REQUEST"),
            (request("version", protocolVersion=2), "version", "UNSUPPORTED_PROTOCOL"),
            (request("boolean-version", protocolVersion=True), "boolean-version", "UNSUPPORTED_PROTOCOL"),
            (request("array-params", params=[]), "array-params", "INVALID_REQUEST"),
            (request("bad-params", params={"token": "TEST_SECRET"}), "bad-params", "INVALID_PARAMS"),
            (request("unknown", method="shell.exec"), "unknown", "METHOD_NOT_FOUND"),
            (request("extra", unexpected=True), "extra", "INVALID_REQUEST"),
        ]
        replies, stderr = self.run_worker(b"".join(item[0] for item in invalid) + request("recovered"))
        self.assertEqual(len(replies), len(invalid) + 1)
        for reply, (_, expected_id, code) in zip(replies, invalid):
            self.assertEqual(reply["id"], expected_id)
            self.assertEqual(reply["error"]["code"], code)
        self.assertEqual(replies[-1]["result"]["message"], "pong")
        self.assertNotIn("TEST_SECRET", stderr)

    def test_oversize_line_is_drained_and_next_request_survives(self) -> None:
        oversized = b'{"payload":"' + b"x" * (MAX_FRAME_BYTES * 4) + b'"}\n'
        replies, _ = self.run_worker(oversized + request("after-oversize"))
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[0]["error"]["code"], "FRAME_TOO_LARGE")
        self.assertEqual(replies[0]["id"], "")
        self.assertEqual(replies[1]["id"], "after-oversize")
        self.assertEqual(replies[1]["result"]["message"], "pong")

    def test_exact_frame_limit_with_crlf_is_accepted(self) -> None:
        frame = request("boundary").rstrip(b"\n")
        frame += b" " * (MAX_FRAME_BYTES - len(frame))
        replies, _ = self.run_worker(frame + b"\r\n" + request("after-boundary"))
        self.assertEqual([reply["id"] for reply in replies], ["boundary", "after-boundary"])
        self.assertTrue(all("result" in reply for reply in replies))

    def test_eof_exits_and_final_unterminated_frame_is_processed(self) -> None:
        replies, _ = self.run_worker(b"")
        self.assertEqual(replies, [])
        replies, _ = self.run_worker(request("final").rstrip(b"\n"))
        self.assertEqual(replies[0]["id"], "final")
        self.assertEqual(replies[0]["result"]["message"], "pong")
        replies, _ = self.run_worker(b"x" * (MAX_FRAME_BYTES + 1))
        self.assertEqual(replies[0]["error"]["code"], "FRAME_TOO_LARGE")

    def test_nonstandard_numbers_are_not_accepted_as_json(self) -> None:
        frame = b'{"protocolVersion":1,"id":"nan","method":"runtime.ping","params":{"value":NaN}}\n'
        replies, _ = self.run_worker(frame + request("recovered"))
        self.assertEqual(replies[0]["error"]["code"], "INVALID_JSON")
        self.assertEqual(replies[1]["result"]["message"], "pong")


class FrameworkBoundaryTests(unittest.TestCase):
    def test_worker_imports_only_standard_library_or_worker_package(self) -> None:
        for file in (WORKER_ROOT / "nimdadesk_worker").glob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        self.assertEqual(node.level, 1, f"Cross-boundary relative import in {file}")
                        self.assertTrue((file.parent / f"{node.module}.py").exists())
                        continue
                    roots = [(node.module or "").split(".")[0]]
                else:
                    continue
                for root in roots:
                    self.assertTrue(root in sys.stdlib_module_names or root == "nimdadesk_worker", f"Unexpected worker dependency: {root} in {file}")


if __name__ == "__main__":
    unittest.main()
