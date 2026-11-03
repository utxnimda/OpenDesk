from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from apps.features.virtual_partner.backend import api_profiles, provider
from apps.features.virtual_partner.tests.test_provider import Vault


class Body(io.BytesIO):
    @property
    def length(self):
        return len(self.getvalue()) - self.tell()


class ProfileTests(unittest.TestCase):
    url = 'https://example.invalid/v1'

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.vault = Vault()
        self.conn = self.reopen()
        self.current = self.root / 'connections.json'
        self.old = self.root / 'connection.json'
        self.requests = []
        self.replies = {'first-key': ['first-model'], 'second-key': [], 'draft-key': ['draft-model']}
        self.release = threading.Event()
        self.release.set()
        self.started = threading.Event()

        @contextmanager
        def request(base_url, method, path, headers, check_active):
            key = headers['Authorization'].removeprefix('Bearer ')
            self.requests.append((base_url, method, path, key))
            self.started.set()
            if not self.release.wait(3):
                raise AssertionError('fixture request was not released')
            models = self.replies[key]
            yield Body(json.dumps({'data': [{'id': item} for item in models]}).encode())

        self.enterContext(patch.object(provider, '_api_response', request))

    def reopen(self):
        return provider.Connection(self.root, vault=self.vault)

    def add(self, ident, key, url=None):
        return self.conn.save_profile(ident, ident.upper(), url or self.url, ident + '-model', key, create=True)

    def error(self, code, action, *args, **kwargs):
        with self.assertRaises(provider.ProviderError) as failure:
            action(*args, **kwargs)
        self.assertEqual(failure.exception.code, code)

    def legacy(self, key):
        ref = self.vault.put(key)
        self.old.write_text(json.dumps({'schemaVersion': 1, 'baseUrl': self.url, 'model': 'previous',
                                        'credentialRef': ref}), encoding='utf-8')
        return ref

    def test_selection_legacy_projection_and_retries_survive_restart(self):
        self.assertEqual(self.conn.profiles(), {'activeId': None, 'profiles': []})
        first = self.add('one', 'first-key')
        self.assertEqual(first['profiles'], [{'id': 'one', 'name': 'ONE', 'baseUrl': self.url,
                                               'model': 'one-model', 'hasKey': True}])
        self.assertEqual(first, self.add('one', 'first-key'))
        self.error('PROFILE_CONFLICT', self.add, 'one', 'second-key')
        self.add('two', 'second-key')
        self.assertEqual([r['id'] for r in self.conn.profiles()['profiles']], ['one', 'two'])
        self.assertEqual(self.reopen().public()['model'], 'two-model')
        self.assertEqual(self.conn.select_profile('one')['activeId'], 'one')
        self.assertEqual(self.reopen().save(self.url, 'new-model')['model'], 'new-model')
        self.assertEqual([r['model'] for r in self.conn.profiles()['profiles']], ['new-model', 'two-model'])
        self.assertEqual(len(self.vault.secrets), 2)
        self.error('PROFILE_NOT_FOUND', self.conn.save_profile, 'missing', 'NEW', self.url, 'm', 'draft-key')
        self.error('PROFILE_NOT_FOUND', self.conn.select_profile, 'missing')
        self.error('INVALID_PROFILE', self.conn.select_profile, None)

    def test_single_profile_import_clones_credential_and_preserves_legacy_writer(self):
        original_ref = self.legacy('first-key')
        original_settings = self.old.read_bytes()
        view = self.conn.profiles()
        imported = view['activeId']
        stored = json.loads(self.current.read_text('utf-8'))['profiles'][0]
        self.assertEqual(stored['id'], imported)
        self.assertNotEqual(stored['credentialRef'], original_ref)
        self.assertEqual(self.old.read_bytes(), original_settings)
        self.assertEqual(self.vault.get(original_ref), 'first-key')
        self.assertEqual(set(view['profiles'][0]), {'id', 'name', 'baseUrl', 'model', 'hasKey'})
        self.assertNotIn('first-key', self.current.read_text('utf-8'))
        # Downgraded writer rotates its own credential, then the newer writer rotates its copy.
        new_legacy_ref = self.legacy('second-key')
        self.vault.delete(original_ref)
        self.assertEqual(self.reopen().list_models(self.url, profile_id=imported), ['first-model'])
        self.conn.save_profile(imported, '迁入的 API', self.url, 'replacement', 'draft-key')
        self.assertEqual(self.vault.get(new_legacy_ref), 'second-key')
        self.assertFalse(self.vault.has(stored['credentialRef']))
        self.assertEqual(self.reopen().list_models(self.url), ['draft-model'])

    def test_failed_migration_unrecognized_formats_and_failed_rotation_preserve_data(self):
        reference = self.legacy('first-key')
        with patch.object(api_profiles.os, 'replace', side_effect=OSError):
            self.error('CONFIG_SAVE', self.conn.profiles)
        self.assertEqual(list(self.vault.secrets), [reference])
        self.assertFalse(self.current.exists())
        legacy = self.old.read_bytes()
        for invalid in (b'{', b'null', b'{"schemaVersion":99}', b'{"schemaVersion":1,"schemaVersion":1}'):
            self.old.write_bytes(invalid)
            self.error('CONFIG_FORMAT', self.add, 'two', 'second-key')
            self.assertEqual(self.old.read_bytes(), invalid)
            self.assertFalse(self.current.exists())
        self.old.write_bytes(legacy)
        self.conn.profiles()
        metadata = self.current.read_bytes()
        refs = dict(self.vault.secrets)
        with patch.object(api_profiles.os, 'replace', side_effect=OSError):
            self.error('CONFIG_SAVE', self.conn.save, self.url, 'changed', 'draft-key')
        self.assertEqual(self.current.read_bytes(), metadata)
        self.assertEqual(self.vault.secrets, refs)
        damaged = json.loads(metadata)
        damaged['profiles'].append(damaged['profiles'][0])
        for raw in (json.dumps(damaged).encode(), b'{"schemaVersion":9}', b'null', b'{'):
            self.current.write_bytes(raw)
            self.error('CONFIG_FORMAT', self.conn.save, self.url, 'changed', 'draft-key')
            self.assertEqual(self.current.read_bytes(), raw)
        self.assertEqual(self.old.read_bytes(), legacy)
        self.assertEqual(self.vault.secrets, refs)

    def test_profile_catalog_isolated_by_id_endpoint_and_key_and_persisted_by_profile(self):
        self.add('one', 'first-key')
        self.add('two', 'second-key')
        self.error('KEY_REQUIRED', self.conn.list_models, self.url, profile_id=None)
        self.error('KEY_REQUIRED', self.conn.list_models, self.url + '/different', profile_id='one')
        self.error('PROFILE_NOT_FOUND', self.conn.list_models, self.url, 'draft-key', profile_id='missing')
        self.assertEqual(self.requests, [])
        self.assertIsNone(self.conn.cache_for('one'))
        self.assertEqual(self.conn.list_models(self.url, profile_id='one'), ['first-model'])
        self.assertEqual(self.conn.list_models(self.url), [])
        self.assertEqual(self.conn.list_models(self.url, 'draft-key', profile_id='two'), ['draft-model'])
        self.assertEqual(self.reopen().cache_for('one'), ['first-model'])
        self.assertEqual(self.reopen().cache_for('two'), [])
        self.assertEqual([r[-1] for r in self.requests], ['first-key', 'second-key', 'draft-key'])
        self.conn.save_profile('one', '换 Key', self.url, 'changed-model', 'draft-key')
        self.assertIsNone(self.conn.cache_for('one'))  # Lookup for two cannot populate one.
        self.assertEqual(self.conn.cache_for('two'), [])
        self.error('KEY_REQUIRED', self.conn.save_profile, 'two', '换地址', self.url + '/different', 'model')

    def test_only_matching_draft_catalog_can_be_adopted_on_save(self):
        self.conn.list_models(self.url, 'draft-key', profile_id=None)
        self.add('a', 'first-key')
        self.assertIsNone(self.conn.cache_for('a'))
        self.conn.list_models(self.url, 'draft-key', profile_id=None)
        self.add('b', 'draft-key', self.url + '/changed')
        self.assertIsNone(self.conn.cache_for('b'))
        self.conn.list_models(self.url, 'second-key', profile_id=None)
        self.add('c', 'second-key')
        self.assertEqual(self.reopen().cache_for('c'), [])
        self.conn.list_models(self.url, 'draft-key', profile_id='a')
        self.conn.save_profile('a', 'A', self.url, 'draft-model', 'draft-key')
        self.assertEqual(self.reopen().cache_for('a'), ['draft-model'])
        self.assertEqual(len(self.requests), 4)

    def test_late_response_from_another_worker_cannot_restore_rotated_key_catalog(self):
        self.add('one', 'first-key')
        self.release.clear()
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(self.conn.list_models, self.url, profile_id='one')
            try:
                self.assertTrue(self.started.wait(1))
                other = self.reopen()
                other.save_profile('one', 'changed', self.url, 'changed', 'second-key')
            finally:
                self.release.set()
            self.error('CATALOG_STALE', result.result, 2)
        self.assertIsNone(self.conn.cache_for('one'))
        self.assertEqual(self.conn.list_models(self.url), [])

    def test_overlapping_writers_keep_both_rows_and_limits_are_enforced(self):
        barrier = threading.Barrier(2)

        def insert(ident):
            connection = self.reopen()
            barrier.wait(2)
            connection.save_profile(ident, ident, self.url, ident, 'first-key', create=True)

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(insert, ('one', 'two')))
        self.assertEqual({row['id'] for row in self.reopen().profiles()['profiles']}, {'one', 'two'})
        with patch.object(api_profiles, 'MAX_PROFILES', 2):
            self.error('PROFILE_LIMIT', self.add, 'three', 'third-key')
        for ident in ('', '../invalid', 'x' * 65):
            self.error('INVALID_PROFILE', self.add, ident, 'third-key')
        self.error('INVALID_PROFILE', self.conn.save_profile, 'one', 'x' * 81, self.url, 'model')
