"""The isolated tower module entry exposes only named read-only game operations."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from nimdadesk_worker.protocol import MAX_RESPONSE_BYTES, ProtocolError, write_response

ROOT = Path(__file__).resolve().parents[2]


class CompositionTests(unittest.TestCase):
    def test_public_capabilities_and_invalid_request_recovery(self):
        messages = [
            {"protocolVersion": 1, "id": "hello", "method": "runtime.hello", "params": {}},
            {"protocolVersion": 1, "id": "invalid", "method": "tower.inspect", "params": {"gameDir": "relative"}},
            {"protocolVersion": 1, "id": "not-a-tool", "method": "tower.execute", "params": {}},
            {"protocolVersion": 1, "id": "ping", "method": "runtime.ping", "params": {}},
            {"protocolVersion": 1, "id": "stop", "method": "runtime.shutdown", "params": {}},
        ]
        process = subprocess.run(
            [sys.executable, str(ROOT / "apps/features/tower_assistant/backend/freeze_entry.py")],
            input="".join(json.dumps(row) + "\n" for row in messages).encode(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
            cwd=tempfile.gettempdir(), check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([row["id"] for row in replies], [row["id"] for row in messages])
        public = json.loads((ROOT / "apps/features/tower_assistant/manifest.json").read_text(encoding="utf-8"))["methods"]
        self.assertEqual(set(replies[0]["result"]["capabilities"]), set(public) | {"runtime.hello", "runtime.ping", "runtime.shutdown"})
        self.assertEqual(replies[1]["error"]["code"], "INVALID_PARAMS")
        self.assertEqual(replies[2]["error"]["code"], "METHOD_NOT_FOUND")
        self.assertEqual(replies[3]["result"]["message"], "pong")

    def test_request_validation_never_passes_unknown_inputs_to_reader(self):
        from apps.features.tower_assistant.backend import service
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "package.nw").write_bytes(b"test placeholder")
            invalid = [
                {"gameDir": str(root), "execute": "arbitrary"},
                {"gameDir": str(root), "saveId": 7},
                {"gameDir": str(root), "platformId": "\n"},
                {"gameDir": "relative"},
                {"gameDir": "\\\\server\\share"},
            ]
            with patch.object(service.reader, "load_latest") as load:
                for params in invalid:
                    with self.subTest(params=params), self.assertRaises(ProtocolError):
                        service.analyze(params)
                load.assert_not_called()

    def test_inspection_returns_reader_snapshot_without_game_mutation(self):
        from apps.features.tower_assistant.backend import service
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root / "package.nw"
            package.write_bytes(b"read-only fixture")
            snapshot = {"schemaVersion": 1, "entities": [], "items": [], "source": {"name": "fixture"}}
            with patch.object(service.reader, "load_latest", return_value=snapshot) as load, \
                 patch.object(service.reader, "discover_saves", return_value=[{"id": "slot1"}]), \
                 patch.object(service.planner, "analyze") as analyze:
                result = service.inspect({"gameDir": str(root)})
                self.assertEqual(result["snapshot"], snapshot)
                load.assert_called_once_with(root.resolve(), None)
                analyze.assert_not_called()
            self.assertEqual(package.read_bytes(), b"read-only fixture")
            self.assertEqual(list(root.iterdir()), [package])

    def test_visuals_accepts_only_bounded_icon_keys_and_local_game_root(self):
        from apps.features.tower_assistant.backend import service
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "package.nw").write_bytes(b"fixture")
            invalid = [
                {"gameDir": str(root), "iconKeys": ["person:B6"], "path": "extra"},
                {"gameDir": str(root), "iconKeys": ["person:B6"] * 33},
                {"gameDir": str(root), "iconKeys": [None]},
                {"gameDir": str(root), "iconKeys": "person:B6"},
            ]
            with patch.object(service.visuals, "load", return_value={"schemaVersion": 1}) as load:
                for params in invalid:
                    with self.subTest(params=params), self.assertRaises(ProtocolError):
                        service.read_visuals(params)
                load.assert_not_called()
                self.assertEqual(service.read_visuals({"gameDir": str(root), "iconKeys": ["person:B6"]}), {"schemaVersion": 1})
                load.assert_called_once_with(root.resolve(), ["person:B6"])

    def test_response_can_exceed_request_limit_but_is_still_bounded(self):
        output = io.BytesIO()
        write_response(output, {"result": "x" * 70000})
        self.assertEqual(len(json.loads(output.getvalue())["result"]), 70000)
        rejected = io.BytesIO()
        with self.assertRaises(ValueError):
            write_response(rejected, {"result": "x" * MAX_RESPONSE_BYTES})
        self.assertEqual(rejected.getvalue(), b"")


if __name__ == "__main__":
    unittest.main()
