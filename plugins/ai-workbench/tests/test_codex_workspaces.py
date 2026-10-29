from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from ai_workbench import codex_workspaces
from ai_workbench.codex_provider import CodexError


class CodexWorkspacesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="opendesk-workspaces-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = mock.Mock()
        patch = mock.patch.object(codex_workspaces, "_Client", return_value=self.client)
        self.start_client = patch.start()
        self.addCleanup(patch.stop)

    def project(self, name="Example", root=None):
        return {"name": name, "roots": [{"path": str(root or self.root)}],
                "metadata": {"private": "not-for-output"}, "id": "private-project-id"}

    def test_project_list_is_paginated_and_only_workspace_fields_are_returned(self):
        self.client.request.side_effect = [
            {"data": [self.project()], "nextCursor": "next"},
            {"data": [self.project(), self.project("Removed", self.root / "removed")], "nextCursor": None},
        ]
        result = codex_workspaces.list_workspaces("selected-codex.exe", str(self.root))
        self.start_client.assert_called_once_with("selected-codex.exe", str(self.root.resolve()))
        self.client.initialize.assert_called_once_with(experimental=True)
        self.assertEqual(self.client.request.call_args_list, [
            mock.call("project/list", {"limit": 100}),
            mock.call("project/list", {"limit": 100, "cursor": "next"}),
        ])
        self.assertEqual(result["workspaces"], [
            {"path": str(self.root), "label": "Example", "source": "codex-app", "available": True},
            {"path": str(self.root / "removed"), "label": "Removed", "source": "codex-app", "available": False},
        ])
        self.client.close.assert_called_once()

    def test_unknown_protocol_fails_with_update_guidance(self):
        self.client.request.side_effect = CodexError("CODEX_PROTOCOL_UNSUPPORTED", "schema unsupported")
        with self.assertRaises(CodexError) as failure:
            codex_workspaces.list_workspaces()
        self.assertEqual(failure.exception.code, "CODEX_WORKSPACES_UNSUPPORTED")
        self.client.close.assert_called_once()

    def test_auth_errors_are_not_hidden_as_empty_directories(self):
        self.client.request.side_effect = CodexError("CODEX_AUTH_REQUIRED", "Sign in")
        with self.assertRaises(CodexError) as failure:
            codex_workspaces.list_workspaces()
        self.assertEqual(failure.exception.code, "CODEX_AUTH_REQUIRED")
        self.client.close.assert_called_once()

    def test_malformed_entries_do_not_return_partial_success(self):
        for entry in ({"name": "Bad", "roots": "not-a-list"}, self.project(root="relative/path"),
                      {"name": "Bad", "roots": [{"path": "bad\x00path"}]}):
            with self.subTest(entry=entry):
                self.client.request.side_effect = None
                self.client.request.return_value = {"data": [self.project(), entry]}
                with self.assertRaises(CodexError):
                    codex_workspaces.list_workspaces()

    def test_empty_catalog_is_an_explicit_empty_state(self):
        self.client.request.return_value = {"data": []}
        result = codex_workspaces.list_workspaces()
        self.assertEqual(result["workspaces"], [])
        self.assertIn("warning", result)

    def test_pagination_cycles_and_output_limit_are_rejected(self):
        self.client.request.side_effect = [
            {"data": [], "nextCursor": "a"}, {"data": [], "nextCursor": "b"}, {"data": [], "nextCursor": "a"}]
        with self.assertRaises(CodexError) as failure:
            codex_workspaces.list_workspaces()
        self.assertEqual(failure.exception.code, "CODEX_PROTOCOL_ERROR")
        self.client.request.side_effect = None
        self.client.request.return_value = {"data": [self.project(), self.project(root=self.root / "other")]}
        with mock.patch.object(codex_workspaces, "_MAX_WORKSPACES", 1):
            with self.assertRaises(CodexError) as failure:
                codex_workspaces.list_workspaces()
        self.assertEqual(failure.exception.code, "CODEX_OUTPUT_LIMIT")

    @unittest.skipUnless(os.name == "nt", "Windows UNC paths")
    def test_network_roots_are_displayed_without_accessing_share(self):
        self.client.request.return_value = {"data": [self.project(root=r"\\server\share\folder")]}
        with mock.patch.object(Path, "is_dir", side_effect=AssertionError("Network share must not be probed")):
            result = codex_workspaces.list_workspaces()
        self.assertFalse(result["workspaces"][0]["available"])

    def test_other_platform_roots_do_not_hide_local_projects_or_probe_remote_paths(self):
        remote = "/home/example/project" if os.name == "nt" else "C:\\Work\\project"
        self.client.request.return_value = {"data": [self.project(), self.project("Remote project", remote)]}
        original = Path.is_dir
        probed = []

        def probe(path):
            probed.append(str(path))
            return original(path)

        with mock.patch.object(Path, "is_dir", probe):
            result = codex_workspaces.list_workspaces()
        self.assertEqual(probed, [str(self.root)])
        self.assertTrue(result["workspaces"][0]["available"])
        self.assertEqual(result["workspaces"][1], {"path": remote, "label": "Remote project", "source": "codex-app", "available": False})
        self.assertIn("warning", result)


if __name__ == "__main__":
    unittest.main()
