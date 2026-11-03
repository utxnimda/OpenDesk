import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from apps.features.virtual_partner.backend import provider
from opendesk_worker.secrets import SecretStoreError, WindowsSecretStore


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        server = self.server
        server.requests.append((self.path, self.headers['Authorization'], self.headers['Accept'],
                                self.headers.get('Content-Length')))
        self.send_response(server.code)
        self.send_header('Content-Type', 'application/json')
        if server.length is not None:
            self.send_header('Content-Length', str(server.length))
        if 300 <= server.code < 400:
            self.send_header('Location', '/redirect-target')
        self.end_headers()
        server.started.set()
        server.release.wait(3)
        try:
            self.wfile.write(server.body)
        except OSError:
            pass  # The client may already have cancelled or rejected an oversized response.


class ModelCatalogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = Path(directory.name) / 'connections.json'
        self.vault = Mock(spec=WindowsSecretStore)
        self.vault.put.return_value = 'a' * 32
        self.vault.has.side_effect = lambda ref: ref == self.vault.put.return_value
        self.vault.get.return_value = 'saved-test-key'
        self.connection = provider.Connection(self.config.parent, vault=self.vault)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), ModelHandler)
        self.server.code = 200
        self.server.body = b'{"object":"list","data":[]}'
        self.server.length = None
        self.server.requests = []
        self.server.started = threading.Event()
        self.server.release = threading.Event()
        self.server.release.set()
        thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.release.set)
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'

    def test_first_lookup_sends_only_draft_key_and_preserves_provider_ids(self):
        ids = ['z-model', 'ft:example:org:custom:123', 'vendor/model:latest', 'z-model']
        self.server.body = json.dumps({'data': [{'id': model, 'owned_by': 'private-fixture'} for model in ids]}).encode()
        result = self.connection.list_models(self.url + '/', 'draft-test-key')
        self.assertEqual(result, ids[:3])
        self.assertEqual(self.server.requests, [('/v1/models', 'Bearer draft-test-key', 'application/json', None)])
        self.assertFalse(self.config.exists())
        self.vault.put.assert_not_called()
        self.vault.get.assert_not_called()

    def test_saved_key_bound_to_entire_canonical_base_and_draft_wins(self):
        self.connection.save(self.url, 'existing', 'saved-test-key')
        self.vault.reset_mock()
        for changed in (self.url + '/other', self.url.replace('/v1', '/V1'),
                        self.url.replace('127.0.0.1', 'localhost'), self.url.replace('http://', 'https://')):
            with self.subTest(changed=changed), self.assertRaises(provider.ProviderError) as err:
                self.connection.list_models(changed)
            self.assertEqual(err.exception.code, 'KEY_REQUIRED')
        self.vault.get.assert_not_called()
        self.assertEqual(self.server.requests, [])
        self.assertEqual(self.connection.list_models(self.url + '/'), [])
        initial = self.config.read_bytes()
        self.assertEqual(self.connection.list_models(self.url, 'replacement-test-key'), [])
        self.assertEqual(self.connection.list_models(self.url + '/other', 'other-test-key'), [])
        self.assertEqual([(path, auth) for path, auth, _, _ in self.server.requests], [
            ('/v1/models', 'Bearer saved-test-key'), ('/v1/models', 'Bearer replacement-test-key'),
            ('/v1/other/models', 'Bearer other-test-key')])
        self.vault.get.assert_called_once_with('a' * 32)
        self.vault.put.assert_not_called()
        self.vault.delete.assert_not_called()
        self.assertEqual(self.config.read_bytes(), initial)

    def test_credentials_and_endpoint_checked_before_request(self):
        for url, key, code in [
            (self.url, None, 'KEY_REQUIRED'), (self.url, 'bad\r\nkey', 'INVALID_KEY'),
            ('http://remote.invalid/v1', 'draft-test-key', 'INVALID_ENDPOINT'),
            (self.url + '?key=private', 'draft-test-key', 'INVALID_ENDPOINT'),
        ]:
            with self.subTest(code=code), self.assertRaises(provider.ProviderError) as err:
                self.connection.list_models(url, key)
            self.assertEqual(err.exception.code, code)
        self.connection.save(self.url, 'existing', 'saved-test-key')
        self.vault.get.side_effect = SecretStoreError('sensitive-vault-error')
        with self.assertRaises(provider.ProviderError) as err:
            self.connection.list_models(self.url)
        self.assertEqual(err.exception.code, 'KEY_UNAVAILABLE')
        self.assertNotIn('sensitive', str(err.exception))
        self.assertEqual(self.server.requests, [])

    def test_redirect_and_http_error_bodies_never_forwarded_or_displayed(self):
        self.connection.save(self.url, 'existing', 'saved-test-key')
        self.server.body = b'private error containing personal data'
        for status in (307, 401, 404, 429):
            self.server.code = status
            with self.subTest(status=status), self.assertRaises(provider.ProviderError) as err:
                self.connection.list_models(self.url)
            self.assertEqual(err.exception.code, 'API_HTTP')
            self.assertIn(str(status), err.exception.message)
            self.assertNotIn('private', err.exception.message)
        self.assertEqual([r[0] for r in self.server.requests], ['/v1/models'] * 4)

    def test_invalid_payload_and_limit_errors_never_look_like_empty_results(self):
        for body in (b'not json', b'[]', b'{"data":null}', b'{"data":[],"error":{"message":"private"}}',
                     b'{"data":[{"id":"ok"},{}]}', b'{"data":[{"id":"\\n"}]}', b'{"data":[null]}',
                     b'{"data":[],"data":[]}', b'{"data":[],"number":NaN}', b'{"data":["\xff"]}'):
            self.server.body = body
            with self.subTest(body=body), self.assertRaises(provider.ProviderError) as err:
                self.connection.list_models(self.url, 'draft-test-key')
            self.assertEqual(err.exception.code, 'API_PROTOCOL')
            self.assertNotIn('private', err.exception.message)
        self.server.body = b'{"data":[{"id":"ok"},{"id":"ok"}]}'
        with patch.object(provider, 'MAX_MODEL_COUNT', 1), self.assertRaises(provider.ProviderError) as err:
            self.connection.list_models(self.url, 'draft-test-key')
        self.assertEqual(err.exception.code, 'API_LIMIT')
        with patch.object(provider, 'MAX_MODEL_LIST_BYTES', 16), self.assertRaises(provider.ProviderError) as err:
            self.connection.list_models(self.url, 'draft-test-key')
        self.assertEqual(err.exception.code, 'API_LIMIT')
        self.server.length = len(self.server.body) + 1
        with self.assertRaises(provider.ProviderError) as err:
            self.connection.list_models(self.url, 'draft-test-key')
        self.assertEqual(err.exception.code, 'API_PROTOCOL')

    def test_held_body_times_out_or_cancels_without_retry(self):
        self.server.release.clear()
        with patch.object(provider, 'MODEL_LIST_TIMEOUT', .15), self.assertRaises(provider.ProviderError) as err:
            self.connection.list_models(self.url, 'draft-test-key')
        self.assertEqual(err.exception.code, 'API_TIMEOUT')
        self.server.started.clear()
        cancelled = threading.Event()
        stopped = []

        def lookup():
            try:
                self.connection.list_models(self.url, 'draft-test-key', cancelled=cancelled)
            except provider.ProviderError as exc:
                stopped.append(exc.code)

        thread = threading.Thread(target=lookup)
        thread.start()
        try:
            self.assertTrue(self.server.started.wait(2))
            cancelled.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(stopped, ['CANCELLED'])
            with self.assertRaises(provider.ProviderError) as err:
                self.connection.list_models(self.url, 'draft-test-key', cancelled=cancelled)
            self.assertEqual(err.exception.code, 'CANCELLED')
            self.assertEqual(len(self.server.requests), 2)
        finally:
            cancelled.set()
            self.server.release.set()
            thread.join(2)
