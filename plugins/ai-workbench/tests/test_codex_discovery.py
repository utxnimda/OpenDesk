from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from ai_workbench import codex_discovery as discovery


class CodexDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="opendesk-codex-discovery-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
        path = mock.patch.object(discovery.shutil, "which", return_value=None)
        path.start()
        self.addCleanup(path.stop)

    def binary(self, relative, stamp=1):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"Not an executable; discovery must never launch this file.")
        os.utime(path, (stamp, stamp))
        return path

    @unittest.skipUnless(os.name == "nt", "Official Windows install layout")
    def test_missing_official_version_is_repaired_to_current_official_binary(self):
        current = self.binary("OpenAI/Codex/bin/fedcba9876543210/codex.exe")
        previous = self.root / "OpenAI/Codex/bin/0123456789abcdef/codex.exe"
        result = discovery.inspect_connection(str(previous))
        self.assertTrue(result["available"])
        self.assertTrue(result["repaired"])
        self.assertEqual(result["executable"], str(current.resolve()))
        self.assertEqual(result["source"], "codex-app")
        self.assertFalse(previous.exists())

    @unittest.skipUnless(os.name == "nt", "Official Windows install layout")
    def test_repair_never_switches_to_unrelated_path_candidate(self):
        current = self.binary("OpenAI/Codex/bin/fedcba9876543210/codex.exe")
        unrelated = self.binary("elsewhere/codex.exe", stamp=99)
        with mock.patch.object(discovery.shutil, "which", return_value=str(unrelated)):
            result = discovery.inspect_connection(str(self.root / "OpenAI/Codex/bin/0123456789abcdef/codex.exe"))
        self.assertEqual(result["executable"], str(current.resolve()))

    @unittest.skipUnless(os.name == "nt", "Official Windows install layout")
    def test_arbitrary_or_lookalike_missing_path_is_not_repaired(self):
        self.binary("OpenAI/Codex/bin/fedcba9876543210/codex.exe")
        for relative in ("other/codex.exe", "OpenAI/Codex/bin/custom/codex.exe",
                         "OpenAI/Codex/bin/0123456789abcdef/other.exe",
                         "other-user/OpenAI/Codex/bin/0123456789abcdef/codex.exe",
                         "OpenAI/Codex/bin/0123456789abcdef/../../other/codex.exe"):
            with self.subTest(relative=relative):
                result = discovery.inspect_connection(str(self.root / relative))
                self.assertFalse(result["available"])
                self.assertFalse(result["repaired"])
                self.assertEqual(result["code"], "CODEX_NOT_FOUND")

    def test_valid_explicit_binary_keeps_identity(self):
        path = self.binary("custom/codex.exe")
        result = discovery.inspect_connection(str(path))
        self.assertTrue(result["available"])
        self.assertFalse(result["repaired"])
        self.assertEqual(result["source"], "explicit")
        self.assertEqual(result["executable"], str(path.resolve()))

    def test_invalid_input_does_not_trigger_discovery(self):
        with mock.patch.object(discovery, "_app_candidates") as candidates:
            for value in ("relative", "invalid\0path", {}, 4):
                result = discovery.inspect_connection(value)
                self.assertFalse(result["available"])
                self.assertEqual(result["code"], "INVALID_PARAMS")
            candidates.assert_not_called()

    def test_missing_installation_returns_actionable_state(self):
        result = discovery.inspect_connection()
        self.assertFalse(result["available"])
        self.assertEqual(result["code"], "CODEX_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
