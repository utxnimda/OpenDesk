from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from ai_workbench import cursor_workspaces as workspaces
from ai_workbench.cursor_provider import CursorError


class CursorWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix="opendesk-cursor-workspaces-")
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.storage=self.root/"storage"
        self.storage.mkdir()
        self.project=self.root/"project"
        self.project.mkdir()

    def write_storage(self,data):
        (self.storage/"storage.json").write_text(json.dumps(data),encoding="utf-8")

    def write_database(self,value):
        database=sqlite3.connect(self.storage/"state.vscdb")
        try:
            with database:
                database.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY,value TEXT)")
                database.execute("INSERT INTO ItemTable VALUES (?,?)",("history.recentlyOpenedPathsList",value))
                database.execute("INSERT INTO ItemTable VALUES (?,?)",("private_auth_record","secret-value-never-read"))
                database.execute("INSERT INTO ItemTable VALUES (?,?)",("private_chat_record","private prompt never read"))
        finally:
            database.close()

    def client(self,pages,capabilities=None):
        client=mock.MagicMock()
        client.request.side_effect=[{"protocolVersion":1,"agentCapabilities":capabilities if capabilities is not None else {"sessionCapabilities":{"list":{}}}},*pages]
        return client

    def test_app_metadata_reads_only_registered_folder_fields_and_fixed_database_key(self):
        self.write_storage({"backupWorkspaces":{"folders":[{"folderUri":self.project.as_uri()}]},
            "windowsState":{"openedWindows":[],"lastActiveWindow":{"folder":self.project.as_uri()}},
            "ignoredAuth":"secret-value-never-read"})
        self.write_database(json.dumps({"entries":[{"folderUri":self.project.as_uri()},
            {"fileUri":(self.root/"private.txt").as_uri(),"label":"Private file"}]}))
        before=(self.storage/"state.vscdb").read_bytes()
        result,warnings=workspaces._app_directories(self.storage)
        self.assertFalse(warnings)
        self.assertEqual(len(result),3)
        self.assertTrue(all(item["source"]=="cursor-app" for item in result))
        self.assertNotIn("private",json.dumps(result))
        self.assertEqual(before,(self.storage/"state.vscdb").read_bytes())

    def test_agent_catalog_paginates_without_loading_or_returning_chat_content(self):
        client=self.client([{"sessions":[{"cwd":str(self.project),"title":"private title","sessionId":"private-id","messages":["private prompt"]}],"nextCursor":"next"},
                            {"sessions":[{"cwd":str(self.project)}]}])
        with mock.patch.object(workspaces,"_Client",return_value=client):
            result=workspaces._agent_directories(None,None)
        methods=[call.args[0] for call in client.request.call_args_list]
        self.assertEqual(methods,["initialize","session/list","session/list"])
        self.assertEqual(client.request.call_args_list[2].args[1],{"cursor":"next"})
        self.assertNotIn("private",json.dumps(result))
        client.process.close.assert_called_once()

    def test_app_wins_duplicate_path_over_agent(self):
        app=workspaces._workspace_option(str(self.project),"cursor-app")
        agent=workspaces._workspace_option(str(self.project),"cursor-agent")
        with mock.patch.object(workspaces,"_app_directories",return_value=([app],[])), mock.patch.object(workspaces,"_agent_directories",return_value=[agent]):
            result=workspaces.list_workspaces()
        self.assertEqual(result["workspaces"],[app])

    def test_agent_without_capability_is_not_queried(self):
        client=self.client([],capabilities={})
        with mock.patch.object(workspaces,"_Client",return_value=client):
            with self.assertRaises(CursorError) as failure:
                workspaces._agent_directories(None,None)
        self.assertEqual(failure.exception.code,"CURSOR_WORKSPACES_UNSUPPORTED")
        self.assertEqual(client.request.call_count,1)
        client.process.close.assert_called_once()

    def test_repeated_pagination_cursor_is_an_error(self):
        client=self.client([{"sessions":[],"nextCursor":"repeat"},{"sessions":[],"nextCursor":"repeat"}])
        with mock.patch.object(workspaces,"_Client",return_value=client):
            with self.assertRaises(CursorError) as failure:
                workspaces._agent_directories(None,None)
        self.assertEqual(failure.exception.code,"CURSOR_PROTOCOL_ERROR")
        client.process.close.assert_called_once()

    def test_metadata_failure_is_visible_without_discarding_available_agent_directory(self):
        self.write_storage({"backupWorkspaces":{"folders":"broken"}})
        app,warnings=workspaces._app_directories(self.storage)
        self.assertTrue(warnings)
        with mock.patch.object(workspaces,"_app_directories",return_value=(app,warnings)), mock.patch.object(workspaces,"_agent_directories",return_value=[workspaces._workspace_option(str(self.project),"cursor-agent")]):
            result=workspaces.list_workspaces()
        self.assertEqual(len(result["workspaces"]),1)
        self.assertIn("warning",result)

    def test_database_oversize_is_explicit(self):
        self.write_database("x"*(workspaces._MAX_METADATA_BYTES+1))
        result,warnings=workspaces._app_directories(self.storage)
        self.assertEqual(result,[])
        self.assertIn("超过",warnings[0])

    def test_json_depth_and_entry_counts_are_bounded(self):
        nested={}
        for _ in range(18):
            nested={"next":nested}
        with self.assertRaises(CursorError):
            workspaces._parse_json(json.dumps(nested))
        self.write_storage({"backupWorkspaces":{"folders":[{}]*(workspaces._MAX_ENTRIES+1)}})
        _,warnings=workspaces._app_directories(self.storage)
        self.assertTrue(warnings)

    def test_remote_workspaces_are_not_probed_and_url_credentials_are_removed(self):
        with mock.patch.object(workspaces.Path,"is_dir",side_effect=AssertionError("remote path must not be probed")):
            remote=workspaces._workspace_option("ssh://user:password@host/repo?token=secret#fragment","cursor-app",uri=True)
            share=workspaces._workspace_option("file://server/share/repo","cursor-app",uri=True)
        self.assertFalse(remote["available"])
        self.assertFalse(share["available"])
        self.assertEqual(remote["path"],"ssh://host/repo")

    def test_cleanup_failure_cannot_be_silenced_by_metadata_fallback(self):
        error=CursorError("CURSOR_STOP_FAILED","still running")
        error.cleanupPending=True
        with mock.patch.object(workspaces,"_app_directories",return_value=([],[])),mock.patch.object(workspaces,"_agent_directories",side_effect=error):
            with self.assertRaises(CursorError):
                workspaces.list_workspaces()


if __name__=="__main__":
    unittest.main()
