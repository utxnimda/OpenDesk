"""Registry behavior across upgrades, duplicate identities and model settings."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_backend_service as fixtures
from ai_workbench.errors import ApiError
from ai_workbench.codex_provider import CodexError
from ai_workbench.agent_registry import workspace_entry


class RegistryTests(unittest.TestCase):
    setUp = fixtures.ServiceTests.setUp
    tearDown = fixtures.ServiceTests.tearDown
    complete = fixtures.ServiceTests.complete
    def test_duplicate_upgraded_connection_keeps_history_identity(self):
        first = self.service.save_connection({"kind": "codex", "name": "Pinned", "executable": sys.executable, "workspace": str(self.base)})
        second = self.service.save_connection({"kind": "codex", "name": "Automatic", "workspace": str(self.base)})
        conversation = self.service.create_conversation({"connectionId": first["id"], "model": "fixture"})
        state = self.service.state()
        pinned = next(c for c in state["connections"] if c["id"] == first["id"])
        automatic = next(c for c in state["connections"] if c["id"] == second["id"])
        self.assertEqual(pinned["duplicateOf"], automatic["id"])
        self.assertNotIn("duplicateOf", automatic)
        self.assertEqual(self.service.conversation(conversation["id"])["connectionId"], first["id"])

    def test_missing_connection_does_not_become_another_executable(self):
        connection = self.service.save_connection({"kind": "codex", "name": "Missing", "workspace": str(self.base)})
        with patch("ai_workbench.service.inspect_connection", return_value={"status": "missing", "message": "Missing custom path"}):
            state = self.service.state()
            self.assertEqual(next(c for c in state["connections"] if c["id"] == connection["id"])["health"]["status"], "missing")
            with self.assertRaises(ApiError) as error:
                self.service.models(connection["id"])
            self.assertEqual(error.exception.code, "AGENT_UNAVAILABLE")

    def test_discovery_is_idempotent_and_combines_registered_workspaces(self):
        with patch("ai_workbench.codex_workspaces.list_workspaces", return_value={"workspaces": [workspace_entry("unused", str(self.base), source="codex-app")]}), patch("ai_workbench.cursor_workspaces.list_workspaces", return_value={"workspaces": []}):
            first = self.service.refresh_agents()
            second = self.service.refresh_agents()
        self.assertEqual([c["id"] for c in first["connections"]], [c["id"] for c in second["connections"]])
        detected = [c for c in first["connections"] if c["origin"] == "detected"]
        self.assertEqual({c["kind"] for c in detected}, {"codex", "cursor"})
        self.assertEqual(first["workspaces"][0]["connectionId"], next(c["id"] for c in detected if c["kind"] == "codex"))

    def test_manual_workspace_persists_without_rewriting_native_project(self):
        connection = self.service.save_connection({"kind": "codex", "name": "Local", "workspace": str(self.base)})
        first = self.service.save_workspace({"connectionId": connection["id"], "path": str(self.base), "label": "Project"})
        second = self.service.save_workspace({"connectionId": connection["id"], "path": str(self.base), "label": "Renamed"})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.service.store.list("workspaces")), 1)
        with patch("ai_workbench.codex_workspaces.list_workspaces", return_value={"workspaces": [], "warning": "Offline"}), patch("ai_workbench.cursor_workspaces.list_workspaces", return_value={"workspaces": []}):
            catalog = self.service.refresh_agents()
        own = [w for w in catalog["workspaces"] if w["connectionId"] == connection["id"]]
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["label"], "Renamed")
        self.assertTrue(catalog["warnings"])

    def test_model_parameters_are_validated_persisted_and_sent_to_adapter(self):
        connection = self.service.save_connection({"kind": "codex", "name": "Local", "workspace": str(self.base)})
        catalog = [{"id": "fixture", "parameters": [{"id": "reasoningEffort", "options": [{"value": "max", "label": "Max"}]}, {"id": "serviceTier", "options": [{"value": "default", "label": "Standard"}]}]}]
        options = {"reasoningEffort": "max", "serviceTier": "default"}
        with self.assertRaises(ApiError) as error:
            self.service.create_conversation({"connectionId": connection["id"], "model": "fixture", "modelOptions": options})
        self.assertEqual(error.exception.code, "MODEL_CATALOG_REQUIRED")
        with patch("ai_workbench.codex_provider.list_models", return_value=catalog):
            self.service.models(connection["id"])
        conversation = self.service.create_conversation({"connectionId": connection["id"], "model": "fixture", "modelOptions": options})
        self.assertEqual(conversation["modelOptions"], options)
        with patch("ai_workbench.codex_provider.run_codex", return_value={"text": "Verified"}) as run:
            result = self.complete(self.service.start_run(conversation["id"], {"text": "Fixture"})["runId"])
        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual(run.call_args.kwargs["model_options"], options)
        with self.assertRaises(CodexError):
            self.service.set_conversation_model(conversation["id"], {"model": "fixture", "modelOptions": {"serviceTier": "invented"}})
        self.assertEqual(self.service.conversation(conversation["id"])["modelOptions"], options)

    def test_registry_operation_blocks_shutdown_between_metadata_calls(self):
        self.service._agent_refresh_lock.acquire()
        try:
            self.assertFalse(self.service.prepare_shutdown()["ready"])
        finally:
            self.service._agent_refresh_lock.release()
        self.assertTrue(self.service.prepare_shutdown()["ready"])

    def test_metadata_remains_active_through_cache_validation(self):
        original = self.service._get
        reads = 0
        def get(table, identity):
            nonlocal reads
            if table == "connections":
                reads += 1
                if reads == 2:
                    self.assertFalse(self.service.prepare_shutdown()["ready"])
            return original(table, identity)
        with patch.object(self.service, "_get", side_effect=get):
            self.assertTrue(self.service.models(self.connection["id"])["models"])
        self.assertTrue(self.service.prepare_shutdown()["ready"])

    def test_repairable_pinned_path_can_be_renamed_without_identity_change(self):
        connection = self.service.save_connection({"kind": "codex", "name": "Old name", "workspace": str(self.base)})
        original = self.service.store.get("connections", connection["id"])
        old_path = str(self.base / "removed-vendor-version" / "codex.exe")
        original["executable"] = old_path
        self.service.store.put("connections", original)
        self.service.create_conversation({"connectionId": connection["id"], "model": "fixture"})
        with patch("ai_workbench.service.inspect_connection", return_value={"status": "ready", "resolvedExecutable": sys.executable, "message": "Vendor update", "autoRepaired": True}):
            saved = self.service.save_connection({"id": connection["id"], "name": "Renamed", "kind": "codex", "workspace": str(self.base), "executable": old_path})
        self.assertEqual(saved["executable"], old_path)
        self.assertEqual(saved["name"], "Renamed")

    def test_inflight_catalog_cannot_repopulate_after_workspace_change(self):
        connection = self.service.save_connection({"kind": "codex", "name": "Local", "workspace": str(self.base)})
        entered, released = threading.Event(), threading.Event()
        failures = []
        def query(*_):
            entered.set()
            released.wait(3)
            return [{"id": "old-directory-model"}]
        def read():
            try:
                self.service.models(connection["id"])
            except ApiError as error:
                failures.append(error.code)
        with patch("ai_workbench.codex_provider.list_models", side_effect=query):
            task = threading.Thread(target=read)
            task.start()
            self.assertTrue(entered.wait(1))
            try:
                self.service.save_connection({"id": connection["id"], "kind": "codex", "name": "Local", "workspace": str(self.web)})
            finally:
                released.set()
                task.join(3)
        self.assertEqual(failures, ["CONNECTION_CHANGED"])
        self.assertNotIn(connection["id"], self.service._catalogs)
