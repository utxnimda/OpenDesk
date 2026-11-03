"""Versioned local API profiles and their opaque credential references."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from opendesk_worker.secrets import SecretStoreError, WindowsSecretStore


DEFAULT_BASE = 'https://api.openai.com/v1'
MAX_PROFILES = 16
MAX_MODEL_COUNT = 2048
MAX_CONFIG_BYTES = 18 * 1024 * 1024
ACTIVE_PROFILE = object()
_MISSING = object()

class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _base_url(value: str) -> str:
    invalid = ProviderError('INVALID_ENDPOINT', '请填写 API 基础地址，例如 https://api.openai.com/v1。')
    if not isinstance(value, str) or len(value) > 2048 or not re.fullmatch(r'[!-~]+', value) or any(c in value for c in '\\?#'):
        raise invalid
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise invalid from None
    if not host or parsed.username is not None or parsed.scheme not in ('http', 'https') or port == 0:
        raise invalid
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None and not re.fullmatch(r'[a-zA-Z0-9.-]+', host):
        raise invalid
    if parsed.scheme == 'http' and not (host == 'localhost' or (address and address.is_loopback)):
        raise ProviderError('INVALID_ENDPOINT', '远程模型地址需要 HTTPS，本机回环地址可以使用 HTTP。')
    if '%' in host or not re.fullmatch(r'[/a-zA-Z0-9._~-]*', parsed.path) or any(p in ('.', '..') for p in parsed.path.split('/')):
        raise invalid
    authority = f'[{host}]' if ':' in host else host
    if port is not None and port != (443 if parsed.scheme == 'https' else 80):
        authority += f':{port}'
    return f'{parsed.scheme}://{authority}{parsed.path.rstrip("/")}'


def _model(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[!-~]{1,256}', value):
        raise ProviderError('INVALID_MODEL', '请填写服务商提供的模型 ID。')
    return value


def _check_key(value):
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[!-~]{0,8192}', value)):
        raise ProviderError('INVALID_KEY', 'Key 格式无效，请重新输入。')


def _profile_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', value):
        raise ProviderError('INVALID_PROFILE', 'API 配置编号无效。')
    return value


def _profile_name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80 or not value.strip().isprintable():
        raise ProviderError('INVALID_PROFILE', '请填写 1 至 80 个可显示字符作为 API 名称。')
    return value.strip()


def _config_error():
    return ProviderError('CONFIG_FORMAT', '伙伴连接文件格式无效或版本不兼容，请检查原文件。')


def _read(path, limit):
    try:
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        return _MISSING
    except OSError:
        raise ProviderError('CONFIG_READ', '读取伙伴连接设置失败。') from None

    def unique_fields(pairs):
        obj = dict(pairs)
        if len(obj) != len(pairs):
            raise ValueError
        return obj

    try:
        if len(raw) > limit:
            raise ValueError
        return json.loads(raw.decode('utf-8'), object_pairs_hook=unique_fields)
    except (ValueError, RecursionError):
        raise _config_error() from None


def _validate_connection(row):
    if row['baseUrl'] != _base_url(row['baseUrl']):
        raise ValueError
    _model(row['model'])
    ref = row['credentialRef']
    if ref is not None and (not isinstance(ref, str) or not re.fullmatch(r'[a-f0-9]{32}', ref)):
        raise ValueError


def _validate_profiles(state):
    try:
        if (not isinstance(state, dict) or set(state) != {'schemaVersion', 'activeId', 'profiles'}
                or type(state['schemaVersion']) is not int or state['schemaVersion'] != 2
                or not isinstance(state['profiles'], list) or len(state['profiles']) > MAX_PROFILES):
            raise ValueError
        ids, refs = set(), set()
        for row in state['profiles']:
            if not isinstance(row, dict) or set(row) != {'id', 'name', 'baseUrl', 'model', 'credentialRef', 'models'}:
                raise ValueError
            _validate_connection(row)
            if _profile_id(row['id']) in ids or _profile_name(row['name']) != row['name']:
                raise ValueError
            ids.add(row['id'])
            ref = row['credentialRef']
            if ref is not None:
                if ref in refs:
                    raise ValueError
                refs.add(ref)
            models = row['models']
            if models is not None and (not ref or not isinstance(models, list) or len(models) > MAX_MODEL_COUNT
                                       or len({_model(model) for model in models}) != len(models)):
                raise ValueError
        active = state['activeId']
        if (ids and _profile_id(active) not in ids) or (not ids and active is not None):
            raise ValueError
    except (ValueError, ProviderError):
        raise _config_error() from None


def _find(state, profile_id):
    if profile_id is ACTIVE_PROFILE:
        profile_id = state['activeId']
    if profile_id is None:
        return None
    _profile_id(profile_id)
    for row in state['profiles']:
        if row['id'] == profile_id:
            return row
    raise ProviderError('PROFILE_NOT_FOUND', '找不到此 API 配置，请重新打开连接设置。')


class ApiProfiles:
    def __init__(self, directory: Path, *, vault=None):
        directory = Path(directory)
        if not directory.is_absolute():
            raise ValueError('An absolute module data directory is required.')
        directory.mkdir(parents=True, exist_ok=True)
        self._file = directory / 'connections.json'
        self._vault = vault if vault is not None else WindowsSecretStore(directory / 'credentials', 'opendesk.virtual-partner')
        self._lock = threading.Lock()
        self._draft_salt = os.urandom(32)
        self._draft_catalog = None  # One successful draft lookup, bound to its credentials for the next save.

    @contextmanager
    def _transaction(self):
        # The local configuration helper and the worker may run in different processes.
        with self._lock, self._file.with_suffix('.lock').open('a+b') as guard:
            deadline = time.monotonic() + 2
            while True:
                try:
                    if os.name == 'nt':
                        import msvcrt
                        guard.seek(0)
                        msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise ProviderError('CONFIG_READ', '无法锁定伙伴连接设置，请检查数据目录。') from None
                    if time.monotonic() >= deadline:
                        raise ProviderError('CONFIG_BUSY', 'API 配置正在被其他操作使用，请重试。') from None
                    time.sleep(.02)
            yield self._load()  # Closing the file releases the OS lock, including on worker exit.

    def _load(self):
        state = _read(self._file, MAX_CONFIG_BYTES)
        if state is not _MISSING:
            _validate_profiles(state)
            return state
        state = {'schemaVersion': 2, 'activeId': None, 'profiles': []}
        legacy = _read(self._file.with_name('connection.json'), 16384)
        if legacy is _MISSING:
            return state
        try:
            if (not isinstance(legacy, dict) or set(legacy) != {'schemaVersion', 'baseUrl', 'model', 'credentialRef'}
                    or type(legacy['schemaVersion']) is not int or legacy['schemaVersion'] != 1 or not legacy['credentialRef']):
                raise ValueError
            _validate_connection(legacy)
        except (ProviderError, ValueError):
            raise _config_error() from None
        # A downgraded writer can rotate and delete its own ref. Give v2 a separate copy.
        reference = self._put(self._saved_key(legacy)) if self._vault.has(legacy['credentialRef']) else None
        row = {'id': 'legacy', 'name': '默认 API', 'baseUrl': legacy['baseUrl'], 'model': legacy['model'],
               'credentialRef': reference, 'models': None}
        state.update(activeId=row['id'], profiles=[row])
        self._write(state, new_ref=reference)
        return state

    def _write(self, state, new_ref=None):
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self._file.parent, delete=False) as stream:
                temp_path = Path(stream.name)
                json.dump(state, stream, ensure_ascii=False, separators=(',', ':'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self._file)
        except OSError:
            if new_ref:
                self._vault.delete(new_ref)
            raise ProviderError('CONFIG_SAVE', '保存连接设置失败，请检查数据目录。') from None
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def _put(self, key):
        try:
            return self._vault.put(key)
        except (SecretStoreError, OSError):
            raise ProviderError('KEY_UNAVAILABLE', 'Windows 无法保护此 Key，请检查系统凭据环境后重试。') from None

    def _saved_key(self, row):
        if not row or not self._vault.has(row['credentialRef']):
            raise ProviderError('KEY_REQUIRED', '请为此 API 配置输入 Key。')
        try:
            key = self._vault.get(row['credentialRef'])
            if not key:
                raise ValueError
            _check_key(key)
            return key
        except (SecretStoreError, OSError, ValueError, ProviderError):
            raise ProviderError('KEY_UNAVAILABLE', '无法读取此连接的 Key，请重新保存。') from None

    def _public(self, row):
        if row is None:
            return {'baseUrl': DEFAULT_BASE, 'model': '', 'hasKey': False}
        return {'baseUrl': row['baseUrl'], 'model': row['model'], 'hasKey': self._vault.has(row['credentialRef'])}

    def _profiles(self, state):
        return {'activeId': state['activeId'], 'profiles': [
            {'id': row['id'], 'name': row['name'], **self._public(row)} for row in state['profiles']
        ]}

    def public(self):
        with self._transaction() as state:
            return self._public(_find(state, ACTIVE_PROFILE))

    def profiles(self):
        with self._transaction() as state:
            return self._profiles(state)

    def select_profile(self, profile_id):
        with self._transaction() as state:
            row = _find(state, _profile_id(profile_id))
            if row['id'] != state['activeId']:
                state['activeId'] = row['id']
                self._write(state)
            return self._profiles(state)

    def cache_for(self, profile_id):
        with self._transaction() as state:
            return _find(state, _profile_id(profile_id))['models']

    def save(self, base_url, model, api_key=None):
        return self._save(ACTIVE_PROFILE, None, base_url, model, api_key, create=False)

    def save_profile(self, profile_id, name, base_url, model, api_key=None, *, create=False):
        if type(create) is not bool:
            raise ProviderError('INVALID_PROFILE', 'API 配置的新增标志无效。')
        return self._save(_profile_id(profile_id), _profile_name(name), base_url, model, api_key, create)

    def _save(self, profile_id, name, base_url, model, api_key, create):
        base_url, model = _base_url(base_url), _model(model)
        _check_key(api_key)
        with self._transaction() as state:
            legacy_call = profile_id is ACTIVE_PROFILE
            if legacy_call:
                profile_id = state['activeId'] or uuid4().hex
                create = state['activeId'] is None
            row = next((item for item in state['profiles'] if item['id'] == profile_id), None)
            if create and row is not None:
                if ((name, base_url, model) == (row['name'], row['baseUrl'], row['model']) and api_key
                        and hmac.compare_digest(api_key, self._saved_key(row))):
                    return self._profiles(state)
                raise ProviderError('PROFILE_CONFLICT', '该编号已用于另一份 API 配置，请重新新建。')
            if row is None and not create:
                raise ProviderError('PROFILE_NOT_FOUND', '找不到此 API 配置，请重新打开连接设置。')
            if create and len(state['profiles']) >= MAX_PROFILES:
                raise ProviderError('PROFILE_LIMIT', f'最多可保存 {MAX_PROFILES} 个 API 配置。')
            if not api_key and (not row or base_url != row['baseUrl'] or not self._vault.has(row['credentialRef'])):
                raise ProviderError('KEY_REQUIRED', '请为此 API 配置输入 Key。')
            if row is None:
                row = {'id': profile_id, 'name': '默认 API', 'credentialRef': None, 'models': None}
                state['profiles'].append(row)
            prior = row['credentialRef']
            if api_key:
                row['credentialRef'] = self._put(api_key)
                draft_id = None if create else profile_id
                stamp = self._draft_stamp(draft_id, base_url, api_key)
                row['models'] = self._draft_catalog[1] if self._draft_catalog and stamp == self._draft_catalog[0] else None
            row.update(name=name or row['name'], baseUrl=base_url, model=model)
            state['activeId'] = profile_id
            self._write(state, new_ref=row['credentialRef'] if api_key else None)
            self._draft_catalog = None
            if prior and prior != row['credentialRef']:
                try:
                    self._vault.delete(prior)
                except OSError:
                    raise ProviderError('KEY_CLEANUP', '配置已保存，但清理旧凭据失败，请检查凭据目录。') from None
            return self._public(row) if legacy_call else self._profiles(state)

    def _draft_stamp(self, profile_id, base_url, api_key):
        return profile_id, base_url, hmac.digest(self._draft_salt, api_key.encode('ascii'), 'sha256')
