from contextlib import contextmanager
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

from apps.features.virtual_partner.backend import bilibili


BV = 'BV17x411w7KC'
ROW = {'bvid': BV, 'title': ' 视频标题 ', 'owner': {'name': '作者', 'mid': 123},
       'desc': '简介', 'redirect_url': 'http://127.0.0.1/private', 'stat': {'view': 800}}


def http_reply(value, headers=b'Content-Type: application/json'):
    return b'HTTP/1.1 200 OK\r\n' + headers + b'\r\n\r\n' + json.dumps(value).encode()


@contextmanager
def exchange(reply, *, stall=False):
    """Exercise the actual HTTP request, status handling, headers and bounded body reader."""
    client, peer = socket.socketpair()
    requests = []
    release = threading.Event()

    def serve():
        with peer:
            peer.settimeout(2)
            request = b''
            while b'\r\n\r\n' not in request:
                part = peer.recv(4096)
                if not part:
                    return
                request += part
            requests.append(request)
            try:
                peer.sendall(reply)
            except OSError:
                return  # The byte limit may close the client while the fixture is writing.
            if stall:
                release.wait(2)

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    try:
        with patch.object(bilibili, '_connect', return_value=client) as connect:
            yield requests
            connect.assert_called_once()
    finally:
        release.set()
        client.close()
        server.join(2)


class BilibiliTests(unittest.TestCase):
    def test_named_reads_send_only_fixed_anonymous_gets_and_project_public_fields(self):
        expected = {'bvid': BV, 'title': '视频标题', 'author': '作者', 'description': '简介'}
        for call, payload, path, result in [
            (bilibili.discover_videos, {'list': [ROW]}, '/x/web-interface/popular?ps=5&pn=1', [expected]),
            (lambda: bilibili.read_video(BV), ROW, '/x/web-interface/view?bvid=' + BV, expected),
        ]:
            with self.subTest(path=path), exchange(http_reply({'code': 0, 'data': payload})) as requests:
                self.assertEqual(call(), result)
                self.assertEqual(len(requests), 1)
                headers = requests[0].decode().split('\r\n')
                self.assertEqual(headers[0], f'GET {path} HTTP/1.1')
                self.assertIn('Host: api.bilibili.com', headers)
                self.assertFalse(any(h.startswith(('Cookie:', 'Authorization:', 'Referer:')) for h in headers))

    def test_id_input_rejected_before_opening_connection(self):
        with patch.object(bilibili, '_connect') as connect:
            for bad in (None, 421, ['BV'], BV + '\n', BV + '&mid=1', 'http://localhost/', BV.lower(), 'BV１２３４５６７８９０'):
                with self.subTest(bad=bad), self.assertRaises(bilibili.BilibiliError) as caught:
                    bilibili.read_video(bad)
                self.assertEqual(caught.exception.code, 'BILIBILI_ID')
            connect.assert_not_called()

    def test_errors_redirects_and_upstream_messages_do_not_escape_the_adapter(self):
        for reply, code in [
            (b'HTTP/1.1 302 Moved\r\nLocation: http://127.0.0.1/private\r\n\r\n', 'BILIBILI_HTTP'),
            (b'HTTP/1.1 412 Denied\r\n\r\nprivate body', 'BILIBILI_HTTP'),
            (http_reply({'code': -352, 'message': 'private details'}), 'BILIBILI_UNAVAILABLE'),
            (http_reply({'code': False, 'data': ROW}), 'BILIBILI_FORMAT'),
            (http_reply([], b'Content-Type: text/html'), 'BILIBILI_FORMAT'),
            (http_reply([], b'Content-Type: application/json\r\nContent-Encoding: gzip'), 'BILIBILI_FORMAT'),
            (http_reply([], b'Content-Type: application/json\r\nContent-Length: 180'), 'BILIBILI_FORMAT'),
            (b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\nnot JSON', 'BILIBILI_FORMAT'),
            (http_reply({'code': 0, 'data': ROW}).replace(b'"code": 0', b'"code": -101, "code": 0'), 'BILIBILI_FORMAT'),
        ]:
            with self.subTest(reply=reply[:100]), exchange(reply), self.assertRaises(bilibili.BilibiliError) as caught:
                bilibili.read_video(BV)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn('private', str(caught.exception))

    def test_announced_and_unannounced_oversized_payloads_are_rejected(self):
        header = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
        for reply in (header + b'Content-Length: 999\r\n\r\n', header + b'\r\n' + b'X' * 33):
            with patch.object(bilibili, '_MAX_BYTES', 32), exchange(reply), self.assertRaises(bilibili.BilibiliError) as caught:
                bilibili.read_video(BV)
            self.assertEqual(caught.exception.code, 'BILIBILI_LIMIT')

    def test_list_shape_and_identity_failures_are_not_returned_as_empty_success(self):
        for rows in (None, [ROW, ROW], [ROW] * 6, [{'bvid': BV}], [dict(ROW, title=' ')]):
            with self.subTest(rows=rows), patch.object(bilibili, '_request', return_value={'list': rows}):
                with self.assertRaises(bilibili.BilibiliError):
                    bilibili.discover_videos()
        with patch.object(bilibili, '_request', return_value={'list': []}):
            self.assertEqual(bilibili.discover_videos(), [])

    def test_detail_enforces_identity_character_and_length_limits(self):
        for row in (dict(ROW, bvid='BV1xx411c7mD'), dict(ROW, owner={'name': None}), dict(ROW, desc='bad\x01'),
                    dict(ROW, desc=None), dict(ROW, title='bad\ud800')):
            with self.subTest(row=row), patch.object(bilibili, '_request', return_value=row):
                with self.assertRaises(bilibili.BilibiliError):
                    bilibili.read_video(BV)
        with patch.object(bilibili, '_request', return_value=dict(ROW, desc='', title='字' * 200)):
            video = bilibili.read_video(BV)
            self.assertEqual((video['title'], video['description']), ('字' * 160, ''))
        with patch.object(bilibili, '_request', return_value=dict(ROW, desc='字' * 1400, owner={'name': '人' * 150})):
            video = bilibili.read_video(BV)
            self.assertEqual((len(video['author']), len(video['description'])), (100, 1200))

    def test_mixed_public_and_internal_dns_answers_never_connect(self):
        public = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('8.8.8.8', 443))
        for address in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '224.0.0.1', '::1'):
            local = (socket.AF_INET6 if ':' in address else socket.AF_INET, *public[1:4], (address, 443))
            with self.subTest(address=address), patch.object(socket, 'getaddrinfo', return_value=[public, local]), \
                    patch.object(socket, 'socket') as open_socket, self.assertRaises(bilibili.BilibiliError) as caught:
                bilibili.discover_videos()
            self.assertEqual(caught.exception.code, 'BILIBILI_ADDRESS')
            open_socket.assert_not_called()

    def test_dns_and_partial_header_stalls_respect_read_deadline(self):
        release = threading.Event()

        def hung_lookup(*args, **kwargs):
            release.wait(2)
            return []

        try:
            with patch.object(bilibili, '_TIMEOUT', .05), patch.object(socket, 'getaddrinfo', side_effect=hung_lookup):
                with self.assertRaises(bilibili.BilibiliError) as caught:
                    bilibili.discover_videos()
            self.assertEqual(caught.exception.code, 'BILIBILI_TIMEOUT')
        finally:
            release.set()
        start = time.monotonic()
        with patch.object(bilibili, '_TIMEOUT', .1), exchange(b'HTTP/1.1 200 OK\r\nCont', stall=True):
            with self.assertRaises(bilibili.BilibiliError) as caught:
                bilibili.discover_videos()
        self.assertEqual(caught.exception.code, 'BILIBILI_TIMEOUT')
        self.assertLess(time.monotonic() - start, 1.5)
