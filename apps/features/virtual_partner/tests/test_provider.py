import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from apps.features.virtual_partner.backend import provider


def event(content=None, reason=None, **delta):
    if content is not None:
        delta['content'] = content
    choice = {'index': 0, 'delta': delta, 'finish_reason': reason}
    return ('data: ' + json.dumps({'choices': [choice]}) + '\n\n').encode()


class Vault:
    def __init__(self):
        self.secrets = {}

    def put(self, item):
        ref = uuid.uuid4().hex
        self.secrets[ref] = item
        return ref

    def has(self, ref):
        return ref in self.secrets

    def get(self, ref):
        return self.secrets[ref]

    def delete(self, ref):
        self.secrets.pop(ref, None)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.received.append((self.path, self.headers['Authorization'], json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
        self.send_response(self.server.code)
        self.send_header('Content-Type', 'text/event-stream')
        if self.server.code == 307:
            self.send_header('Location', '/leak')
        self.end_headers()
        self.server.started.set()
        self.server.release.wait(4)
        try:
            self.wfile.write(self.server.response)
        except OSError:
            pass  # Cancellation closes the receiver while the fixture is paused.


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = Vault()
        self.connection = provider.Connection(self.root, vault=self.vault)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.received = []
        self.server.started = threading.Event()
        self.server.release = threading.Event()
        self.server.release.set()
        self.server.code = 200
        self.server.response = event('好') + event('啊', 'stop') + b'data: [DONE]\n\n'
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.release.set)
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'
        self.deltas = []

    def reply(self, cancelled=None):
        return self.connection.reply('言简意赅', ['不要重写用户偏好'], [{'role': 'user', 'text': '你好'}], self.deltas.append, cancelled or threading.Event())

    def test_settings_bound_to_endpoint_and_never_reveal_key(self):
        self.assertFalse(self.connection.public()['hasKey'])
        self.assertEqual(self.connection.save(self.url + '/', 'model-a', 'fixture-value'), {'baseUrl': self.url, 'model': 'model-a', 'hasKey': True})
        self.assertNotIn('fixture-value', (self.root / 'connections.json').read_text())
        self.connection.save(self.url, 'model-b')
        for base in (self.url + '/another', 'https://other.invalid/v1'):
            with self.assertRaises(provider.ProviderError):
                self.connection.save(base, 'model-b')
        for base in ('http://provider.invalid/v1', 'https://example.invalid/v1?key=fixture', 'https://user@example.invalid/', 'http://127.0.0.1\\@example.invalid/v1', self.url + '/../new'):
            with self.assertRaises(provider.ProviderError):
                self.connection.save(base, 'model-c', 'other')
        self.assertEqual(len(self.vault.secrets), 1)
        reopened = provider.Connection(self.root, vault=self.vault)
        self.assertEqual(reopened.public(), {'baseUrl': self.url, 'model': 'model-b', 'hasKey': True})
        self.assertFalse(self.server.received)

    def test_draft_never_uses_a_connection_changed_after_its_outing_started(self):
        self.assertIsNone(self.connection.draft_target())
        self.connection.save(self.url, 'model-a', 'fixture-value')
        target = self.connection.draft_target()
        self.connection.save(self.url, 'model-b')
        with self.assertRaisesRegex(provider.ProviderError, '连接已更换'):
            self.connection.reply('本地草稿', [], [{'role': 'user', 'text': '公开简介'}], self.deltas.append,
                                  threading.Event(), expected_target=target)
        self.assertFalse(self.server.received)

    def test_failed_replacement_and_unknown_config_preserve_existing(self):
        self.connection.save(self.url, 'model-a', 'fixture-secret')
        original = (self.root / 'connections.json').read_bytes()
        with patch.object(os, 'replace', side_effect=OSError), self.assertRaises(provider.ProviderError):
            self.connection.save('https://example.invalid', 'model-b', 'other-secret')
        self.assertEqual((self.root / 'connections.json').read_bytes(), original)
        self.assertEqual(list(self.vault.secrets.values()), ['fixture-secret'])
        corruption = b'{"schemaVersion":99}'
        (self.root / 'connections.json').write_bytes(corruption)
        with self.assertRaises(provider.ProviderError):
            self.connection.save(self.url, 'model-c', 'new-secret')
        self.assertEqual((self.root / 'connections.json').read_bytes(), corruption)

    @unittest.skipUnless(os.name == 'nt', 'Uses current Windows user DPAPI')
    def test_partner_vault_roundtrip_keeps_fake_key_out_of_settings_and_disk(self):
        connection = provider.Connection(self.root)
        connection.save(self.url, 'fixture-model', 'synthetic-dpapi-test-key')
        for item in self.root.rglob('*'):
            if item.is_file():
                self.assertNotIn(b'synthetic-dpapi-test-key', item.read_bytes())
        reopened = provider.Connection(self.root)
        answer = reopened.reply('hello', [], [{'role': 'user', 'text': 'Hello'}], lambda _: None, threading.Event())
        self.assertEqual(answer, '好啊')
        self.assertEqual(self.server.received[0][1], 'Bearer synthetic-dpapi-test-key')

    def test_reply_sends_only_explicit_context_as_data(self):
        self.connection.save(self.url, 'fixture-model', 'synthetic-key')
        self.assertEqual(self.reply(), '好啊')
        self.assertEqual(self.deltas, ['好', '啊'])
        self.assertEqual(len(self.server.received), 1)
        path, auth, body = self.server.received[0]
        self.assertEqual((path, auth), ('/v1/chat/completions', 'Bearer synthetic-key'))
        self.assertTrue(body['stream'])
        self.assertEqual(body['model'], 'fixture-model')
        self.assertNotIn('不要重写用户偏好', body['messages'][0]['content'])
        self.assertEqual([m['role'] for m in body['messages']], ['system', 'user', 'user'])
        self.assertIn('不要重写用户偏好', body['messages'][1]['content'])
        self.assertEqual(body['messages'][-1]['content'], '你好')

    def test_credential_not_redirected_and_http_body_never_exposed(self):
        self.connection.save(self.url, 'fixture-model', 'synthetic-key')
        self.server.code = 307
        self.server.response = b'private error details'
        with self.assertRaises(provider.ProviderError) as err:
            self.reply()
        self.assertEqual(err.exception.code, 'API_HTTP')
        self.assertNotIn('private', str(err.exception))
        self.assertEqual([r[0] for r in self.server.received], ['/v1/chat/completions'])

    def test_missing_or_unsupported_terminal_answers_are_not_committed(self):
        self.connection.save(self.url, 'fixture-model', 'synthetic-key')
        for stream in (event('部分'), event('部分') + b'data: [DONE]\n\n', event('部分', 'length'),
                       event(reason='stop') + b'data: [DONE]\n\n', event('x', 'stop') + event('extra'),
                       b'data: {"error":{"message":"sensitive upstream text"}}\n\n', event('x' * (provider.MAX_REPLY + 1))):
            with self.subTest(stream=stream[:20]):
                self.server.response = stream
                with self.assertRaises(provider.ProviderError) as err:
                    self.reply()
                self.assertNotIn('sensitive', str(err.exception))

    def test_cancel_interrupts_blocked_read_without_second_request(self):
        self.connection.save(self.url, 'fixture-model', 'synthetic-key')
        cancelled = threading.Event()
        self.server.release.clear()
        result = []

        def run():
            try:
                self.reply(cancelled)
            except provider.ProviderError as error:
                result.append(error.code)

        worker = threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(self.server.started.wait(2))
            cancelled.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result, ['CANCELLED'])
            self.assertEqual(len(self.server.received), 1)
        finally:
            cancelled.set()
            self.server.release.set()
            worker.join(2)

    def test_deadline_interrupts_stalled_server(self):
        self.connection.save(self.url, 'fixture-model', 'synthetic-key')
        self.server.release.clear()
        with patch.object(provider, 'REPLY_TIMEOUT', .2), self.assertRaises(provider.ProviderError) as error:
            self.reply()
        self.assertEqual(error.exception.code, 'API_TIMEOUT')
        self.assertEqual(len(self.server.received), 1)
