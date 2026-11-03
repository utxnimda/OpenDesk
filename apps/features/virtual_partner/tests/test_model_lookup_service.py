import tempfile
import threading
import unittest
from pathlib import Path

from apps.features.virtual_partner.backend.service import PartnerService
from opendesk_worker.protocol import ProtocolError


class SlowCatalog:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.saved_model = 'original'

    def public(self):
        return {'baseUrl': 'https://example.invalid/v1', 'hasKey': True, 'model': self.saved_model}

    def save(self, base_url, model, api_key=None):
        self.saved_model = model
        return self.public()

    def list_models(self, base_url, api_key, *, cancelled):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(3):
            raise AssertionError('test did not release blocked request')
        return ['first', 'second']


class CatalogJobTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.connection = SlowCatalog()
        self.service = PartnerService(Path(temp.name), self.connection)
        self.addCleanup(self.service.close)
        self.addCleanup(self.connection.release.set)

    def start(self):
        job = self.service.models({'baseUrl': self.connection.public()['baseUrl']})
        self.assertTrue(self.connection.entered.wait(1))
        return job

    def test_slow_catalog_does_not_block_partner_or_change_model(self):
        self.assertEqual(self.service.state({})['connection']['model'], 'original')
        self.assertEqual(self.connection.calls, 0)
        job = self.start()
        self.assertEqual(self.service.models_result(job), {'status': 'loading'})
        self.assertEqual(self.service.state({})['connection']['model'], 'original')
        with self.assertRaises(ProtocolError):
            self.service.models({'baseUrl': 'https://other.invalid/v1'})
        self.connection.release.set()
        self.service.lookup.thread.join(2)
        self.assertEqual(self.service.models_result(job), {'status': 'ready', 'models': ['first', 'second']})
        self.assertEqual(self.connection.calls, 1)
        self.assertEqual(self.connection.saved_model, 'original')

    def test_saving_a_connection_invalidates_even_a_late_catalog_response(self):
        job = self.start()
        self.service.configure_connection({'baseUrl': self.connection.public()['baseUrl'], 'model': 'manual'})
        self.connection.release.set()
        self.service.lookup.thread.join(2)
        with self.assertRaises(ProtocolError) as error:
            self.service.models_result(job)
        self.assertEqual(error.exception.code, 'CATALOG_STALE')
        self.assertEqual(self.connection.saved_model, 'manual')

    def test_switching_between_apis_on_the_same_url_only_delivers_the_latest_catalog(self):
        called = []

        def lookup(url, key, *, cancelled, profile_id):
            called.append(profile_id)
            if len(called) == 1:
                self.connection.entered.set()
                if not self.connection.release.wait(3):
                    raise AssertionError('test did not release first API')
            return [profile_id]

        self.connection.list_models = lookup
        url = self.connection.public()['baseUrl']
        first = self.service.models({'baseUrl': url, 'profileId': 'account-a'})
        self.assertTrue(self.connection.entered.wait(1))
        second = self.service.models({'baseUrl': url, 'profileId': 'account-b'})
        third = self.service.models({'baseUrl': url, 'profileId': 'account-c'})
        self.assertEqual(self.service.models_result(third), {'status': 'loading'})
        self.connection.release.set()
        self.service.lookup.thread.join(2)
        self.assertEqual(called, ['account-a', 'account-c'])
        for superseded in (first, second):
            with self.assertRaises(ProtocolError):
                self.service.models_result(superseded)
        self.assertEqual(self.service.models_result(third), {'status': 'ready', 'models': ['account-c']})
