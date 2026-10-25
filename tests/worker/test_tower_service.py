"""The isolated tower module entry exposes only named read-only game operations."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from opendesk_worker.protocol import MAX_RESPONSE_BYTES, ProtocolError, write_response

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

    def test_capture_rpc_rejects_unknown_fields_and_explain_requires_safe_integer(self):
        from apps.features.tower_assistant.backend import service
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "package.nw").write_bytes(b"fixture")
            with patch.object(service.capture, "manager") as manager:
                with self.assertRaises(ProtocolError):
                    service.capture_operation({"gameDir": str(root), "path": "arbitrary"}, "install")
                for sequence in (True, -1, 2**53, "1", 1.5):
                    with self.subTest(sequence=sequence), self.assertRaises(ProtocolError):
                        service.statistics_explain({"gameDir": str(root), "sequence": sequence})
                manager.install.assert_not_called()
                manager.explain.assert_not_called()
                service.capture_operation({"gameDir": str(root)}, "status")
                manager.status.assert_called_once_with(str(root))
                service.statistics_explain({"gameDir": str(root), "sequence": 1})
                manager.explain.assert_called_once_with(str(root), 1)
                manager.explain.reset_mock()
                for session in (None, True, "", " ", "x" * 129, "line\nbreak"):
                    with self.subTest(session=session), self.assertRaises(ProtocolError):
                        service.statistics_explain({"gameDir": str(root), "sequence": 1, "sessionId": session})
                manager.explain.assert_not_called()
                service.statistics_explain({"gameDir": str(root), "sequence": 1, "sessionId": "session-one"})
                manager.explain.assert_called_once_with(str(root), 1, "session-one")

    def test_latest_explanation_rpc_requires_session_and_one_exact_group_selector(self):
        from apps.features.tower_assistant.backend import service
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "package.nw").write_bytes(b"fixture")
            work = {"kind": "work", "personId": "person:1", "category": "锻造"}
            damage = {"kind": "damage", "personId": "person:1", "attackKind": "skill", "skillId": "one"}
            base = {"gameDir": str(root), "sessionId": "session-one", "latest": work}
            invalid = [
                {"gameDir": str(root), "latest": work}, {**base, "sequence": 1}, {**base, "extra": True},
                *({**base, "sessionId": session} for session in (None, True, "", " ", "x" * 129, "line\nbreak")),
                *({**base, "latest": selector} for selector in (
                    None, [], {}, {**work, "category": ""}, {**work, "personId": "x" * 101},
                    {**work, "personId": "line\nbreak"}, {**work, "kind": "unknown"}, {**work, "skillId": "one"},
                    {**damage, "category": "同名技能"}, {"kind": "damage", "personId": "person:1", "attackKind": "skill"},
                    {**damage, "attackKind": "magic"}, {**damage, "skillId": True}, {**damage, "skillId": ""})),
            ]
            with patch.object(service.capture, "manager") as manager:
                for params in invalid:
                    with self.subTest(params=params), self.assertRaises(ProtocolError) as rejected:
                        service.statistics_explain(params)
                    self.assertEqual(rejected.exception.code, "INVALID_PARAMS")
                manager.explain.assert_not_called()
                manager.explain_latest.assert_not_called()
                for selector in (work, damage, {**damage, "attackKind": "unknown", "skillId": None}):
                    service.statistics_explain({**base, "latest": selector})
                    manager.explain_latest.assert_called_with(str(root), selector, "session-one")
                manager.explain.assert_not_called()

    def test_snapshot_explanation_requires_exact_sequence_session_and_bounded_opaque_capsule(self):
        from apps.features.tower_assistant.backend import service
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "package.nw").write_bytes(b"fixture")
            snapshot = {"schemaVersion": 1, "payload": "opaque signed payload", "signature": "0" * 64}
            base = {"gameDir": str(root), "sequence": 1, "sessionId": "original-session", "snapshot": snapshot}
            invalid = [{key: value for key, value in base.items() if key != "sessionId"},
                       {**base, "latest": {"kind": "work", "personId": "one", "category": "work"}},
                       {**base, "extra": True}, {**base, "sequence": True},
                       *({**base, "snapshot": value} for value in (None, [], {}, {**snapshot, "extra": True},
                         {**snapshot, "schemaVersion": True}, {**snapshot, "schemaVersion": 2}, {**snapshot, "payload": {}},
                         {**snapshot, "payload": "界" * 4097}, {**snapshot, "payload": "\ud800"},
                         {**snapshot, "signature": "x" * 64}, {**snapshot, "signature": "0" * 63}))]
            with patch.object(service.capture, "manager") as manager:
                for params in invalid:
                    with self.subTest(params=params), self.assertRaises(ProtocolError) as rejected:
                        service.statistics_explain(params)
                    self.assertEqual(rejected.exception.code, "INVALID_PARAMS")
                manager.explain_snapshot.assert_not_called()
                service.statistics_explain(base)
                manager.explain_snapshot.assert_called_once_with(str(root), 1, "original-session", snapshot)
                manager.explain.assert_not_called()
                manager.explain_latest.assert_not_called()

    def test_snapshot_signature_failure_remains_a_correlated_analysis_error(self):
        from apps.features.tower_assistant.backend import service
        from opendesk_worker.protocol import Request
        from opendesk_worker.registry import MethodRegistry
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "package.nw").write_bytes(b"fixture")
            params = {"gameDir": str(root), "sequence": 1, "sessionId": "original-session", "snapshot": {
                "schemaVersion": 1, "payload": "opaque", "signature": "0" * 64}}
            registry = MethodRegistry()
            service.register(registry)
            with patch.object(service.capture, "manager") as manager:
                manager.explain_snapshot.side_effect = service.StatisticsError("事件快照签名无效")
                with self.assertRaises(ProtocolError) as rejected:
                    registry.dispatch(Request("receipt-request", "tower.statistics.explain", params))
                self.assertEqual(rejected.exception.code, "TOWER_ANALYSIS_REJECTED")
                self.assertEqual(rejected.exception.request_id, "receipt-request")
                manager.explain_latest.assert_not_called()
                manager.explain.assert_not_called()


if __name__ == "__main__":
    unittest.main()
