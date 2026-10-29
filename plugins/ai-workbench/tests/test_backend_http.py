import http.client
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_backend_service import AiService, MemoryVault, Provider, inspect_fixture_connection


class HttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        inspection = patch("ai_workbench.service.inspect_connection", side_effect=inspect_fixture_connection)
        inspection.start()
        self.addCleanup(inspection.stop)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.web = root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<!doctype html><title>AI</title>")
        (root / "private.json").write_text('{"private":true}')
        self.service = AiService(root / "data", self.web, vault=MemoryVault(), provider_factory=Provider)
        self.url = urlsplit(self.service.start())

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def request(self, path="/api/state", method="GET", body=None, headers=None, authenticated=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.url.port, timeout=3)
        values = {"Host": self.url.netloc}
        if authenticated:
            values["Authorization"] = "Bearer " + self.service.token
        if body is not None:
            values["Content-Type"] = "application/json"
        values.update(headers or {})
        connection.request(method, path, body, values)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_bearer_and_origin_and_host_are_independently_enforced(self):
        self.assertEqual(self.request()[0], 200)
        self.assertEqual(self.request(authenticated=False)[0], 401)
        self.assertEqual(self.request(headers={"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.request(headers={"Origin": "https://example.invalid"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": "null"})[0], 403)
        self.assertEqual(self.request(headers={"Host": "evil.invalid"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": self.service.origin})[0], 200)

    def test_no_cross_origin_preflight_and_no_key_read_endpoint(self):
        status, headers, _ = self.request(method="OPTIONS", authenticated=False)
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.request("/api/credentials")[0], 404)

    def test_json_validation_and_oversize_fail_before_mutation(self):
        status, _, _ = self.request("/api/connections", "POST", '{"name":"a","name":"b"}')
        self.assertEqual(status, 400)
        self.assertEqual(self.request("/api/connections", "POST", '{"x":NaN}')[0], 400)
        self.assertEqual(self.request("/api/connections", "POST", '{}', {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("/api/connections", "POST", '{}', {"Content-Length": "600000"})[0], 413)
        self.assertEqual(self.service.state()["connections"], [])

    def test_static_only_serves_web_root_and_never_injects_token(self):
        status, headers, data = self.request("/", authenticated=False)
        self.assertEqual(status, 200)
        self.assertNotIn(self.service.token.encode(), data)
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("/%2e%2e/private.json", authenticated=False)[0], 404)
        self.assertEqual(self.request("/../private.json", authenticated=False)[0], 404)
        self.assertEqual(self.request("/.instance.lock", authenticated=False)[0], 404)

    def test_real_connection_create_and_discovery_http_contract(self):
        body = json.dumps({"name": "Test", "kind": "openai-chat", "baseUrl": "https://example.invalid/v1", "apiKey": "synthetic-credential"})
        status, _, raw = self.request("/api/connections", "POST", body)
        self.assertEqual(status, 200)
        created = json.loads(raw)
        self.assertTrue(created["hasKey"])
        status, _, raw = self.request("/api/connections/" + created["id"] + "/models", "POST", "{}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["models"][0]["id"], "test-model")

    def test_workspace_and_model_endpoints_enforce_auth_and_bounded_payloads(self):
        connection = self.service.save_connection({"name": "Codex fixture", "kind": "codex", "workspace": str(self.web)})
        path = "/api/connections/" + connection["id"] + "/workspaces"
        with patch("ai_workbench.codex_workspaces.list_workspaces", return_value={"workspaces": []}) as reader:
            self.assertEqual(self.request(path, "POST", "{}", authenticated=False)[0], 401)
            self.assertEqual(self.request(path, "POST", '{"path":"/arbitrary"}')[0], 400)
            reader.assert_not_called()
            self.assertEqual(self.request(path, "POST", "{}")[0], 200)
            reader.assert_called_once_with(sys.executable, str(self.web.resolve()))
        conversation = self.service.create_conversation({"connectionId": connection["id"], "model": "before"})
        path = "/api/conversations/" + conversation["id"] + "/model"
        self.assertEqual(self.request(path, "POST", '{"model":"after"}', authenticated=False)[0], 401)
        status, _, raw = self.request(path, "POST", '{"model":"after"}')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["model"], "after")
        self.assertEqual(self.request(path, "POST", '{"model":""}')[0], 400)

    def test_agent_refresh_and_manual_workspace_are_named_authorized_operations(self):
        with patch.object(self.service, "refresh_agents", return_value={"connections": [], "workspaces": [], "warnings": []}) as refresh:
            self.assertEqual(self.request("/api/agents/refresh", "POST", "{}", authenticated=False)[0], 401)
            self.assertEqual(self.request("/api/agents/refresh", "POST", '{"path":"/arbitrary"}')[0], 400)
            refresh.assert_not_called()
            self.assertEqual(self.request("/api/agents/refresh", "POST", "{}")[0], 200)
            refresh.assert_called_once_with()
        connection = self.service.save_connection({"name": "Workspace fixture", "kind": "codex", "workspace": str(self.web)})
        body = json.dumps({"connectionId": connection["id"], "path": str(self.web), "label": "Manual"})
        self.assertEqual(self.request("/api/workspaces", "POST", body, authenticated=False)[0], 401)
        self.assertEqual(self.service.store.list("workspaces"), [])
        status, _, raw = self.request("/api/workspaces", "POST", body)
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(raw)["source"], "manual")

    def test_layout_endpoint_requires_auth_and_rejects_out_of_range_values(self):
        self.assertEqual(self.request("/api/layout", "POST", '{"sidebarWidth":320}', authenticated=False)[0], 401)
        self.assertEqual(self.service.layout()["sidebarWidth"], 238)
        self.assertEqual(self.request("/api/layout", "POST", '{"sidebarWidth":900}')[0], 400)
        status, _, raw = self.request("/api/layout", "POST", '{"sidebarWidth":320}')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {"sidebarWidth": 320})


if __name__ == "__main__":
    unittest.main()
