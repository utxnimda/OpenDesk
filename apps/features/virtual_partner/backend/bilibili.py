"""Anonymous, read-only Bilibili video metadata for a partner outing."""
from __future__ import annotations

import http.client
import ipaddress
import json
from queue import Empty, Queue
import re
import socket
import ssl
import threading
import time

from .provider import _ResponseInput


_HOST = 'api.bilibili.com'
_BV = re.compile(r'BV[1-9A-HJ-NP-Za-km-z]{10}')
_COUNT = 5
_MAX_BYTES = 256 * 1024
_TIMEOUT = 5
_DNS_SLOTS = threading.BoundedSemaphore(2)


class BilibiliError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _invalid_metadata():
    return BilibiliError('BILIBILI_FORMAT', 'B 站返回的视频资料格式异常，请稍后重试。')


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


def _public_addresses(deadline):
    # System DNS has no per-query timeout. Bound both the wait and outstanding lookups.
    if not _DNS_SLOTS.acquire(blocking=False):
        raise BilibiliError('BILIBILI_DNS', 'B 站域名解析仍在等待，请稍后重试。')
    result = Queue(maxsize=1)

    def resolve():
        try:
            result.put(socket.getaddrinfo(_HOST, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP))
        except OSError as error:
            result.put(error)
        finally:
            _DNS_SLOTS.release()

    threading.Thread(target=resolve, name='bilibili-dns', daemon=True).start()
    try:
        addresses = result.get(timeout=_remaining(deadline))
    except Empty:
        raise TimeoutError from None
    if isinstance(addresses, OSError):
        raise addresses
    if not addresses:
        raise OSError('DNS returned no addresses')
    for _, _, _, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise BilibiliError('BILIBILI_ADDRESS', 'B 站域名解析到了非公网地址，本次读取已停止。')
    return addresses


def _connect(deadline):
    # Connect to the verified address directly; still verify TLS against Bilibili's hostname.
    for family, socktype, protocol, _, address in _public_addresses(deadline):
        with socket.socket(family, socktype, protocol) as raw:
            raw.settimeout(_remaining(deadline))
            try:
                raw.connect(address)
            except OSError as error:
                last_error = error
                continue
            raw.settimeout(_remaining(deadline))
            return ssl.create_default_context().wrap_socket(raw, server_hostname=_HOST)
    raise last_error


def _read_body(response):
    if response.status != 200:
        raise BilibiliError('BILIBILI_HTTP', f'B 站返回 HTTP {response.status}，暂时无法匿名读取。')
    if (response.headers.get_content_type() != 'application/json'
            or response.getheader('Content-Encoding', 'identity').lower() != 'identity'):
        raise _invalid_metadata()
    if response.length is not None and response.length > _MAX_BYTES:
        raise BilibiliError('BILIBILI_LIMIT', 'B 站返回的资料超过读取上限。')
    raw = response.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise BilibiliError('BILIBILI_LIMIT', 'B 站返回的资料超过读取上限。')
    if response.length not in (None, 0):
        raise _invalid_metadata()
    return raw


def _request(path):
    deadline = time.monotonic() + _TIMEOUT
    try:
        with _connect(deadline) as sock:
            connection = http.client.HTTPSConnection(_HOST)
            connection.sock = sock
            try:
                sock.settimeout(_remaining(deadline))
                connection.request('GET', path, headers={
                    'Accept': 'application/json', 'User-Agent': 'OpenDesk-VirtualPartner',
                    'Accept-Encoding': 'identity',
                })
                # Check the absolute deadline throughout headers, chunks and payload reads.
                sock.setblocking(False)
                with http.client.HTTPResponse(_ResponseInput(sock, lambda: _remaining(deadline)), method='GET') as response:
                    response.begin()
                    raw = _read_body(response)
                _remaining(deadline)
            finally:
                connection.close()
    except TimeoutError:
        raise BilibiliError('BILIBILI_TIMEOUT', '读取 B 站资料超时，请稍后重试。') from None
    except (OSError, http.client.HTTPException):
        raise BilibiliError('BILIBILI_NETWORK', '未收到 B 站的完整响应，请检查网络后重试。') from None
    def unique_fields(pairs):
        obj = dict(pairs)
        if len(obj) != len(pairs):
            raise ValueError
        return obj

    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_fields)
    except (ValueError, RecursionError):
        raise _invalid_metadata() from None
    if not isinstance(result, dict) or type(result.get('code')) is not int:
        raise _invalid_metadata()
    if result['code'] != 0:
        raise BilibiliError('BILIBILI_UNAVAILABLE', f'B 站暂不提供这份公开资料（代码 {result["code"]}）。')
    if not isinstance(result.get('data'), dict):
        raise _invalid_metadata()
    return result['data']


def _text(value, limit, *, required=True):
    if not isinstance(value, str) or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]', value):
        raise _invalid_metadata()
    value = value.strip()
    if required and not value:
        raise _invalid_metadata()
    return value[:limit]


def _video(row):
    if not isinstance(row, dict) or not isinstance(row.get('owner'), dict):
        raise _invalid_metadata()
    bvid = row.get('bvid')
    if not isinstance(bvid, str) or not _BV.fullmatch(bvid):
        raise _invalid_metadata()
    return {'bvid': bvid, 'title': _text(row.get('title'), 160),
            'author': _text(row['owner'].get('name'), 100),
            'description': _text(row.get('desc'), 1200, required=False)}


def discover_videos() -> list[dict[str, str]]:
    rows = _request(f'/x/web-interface/popular?ps={_COUNT}&pn=1').get('list')
    if not isinstance(rows, list) or len(rows) > _COUNT:
        raise _invalid_metadata()
    videos = [_video(row) for row in rows]
    if len({video['bvid'] for video in videos}) != len(videos):
        raise _invalid_metadata()
    return videos


def read_video(bvid: str) -> dict[str, str]:
    if not isinstance(bvid, str) or not _BV.fullmatch(bvid):
        raise BilibiliError('BILIBILI_ID', '需要有效的 B 站视频 BV 号。')
    video = _video(_request('/x/web-interface/view?bvid=' + bvid))
    if video['bvid'] != bvid:
        raise _invalid_metadata()
    return video
