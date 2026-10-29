import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps/framework/worker"))
from opendesk_worker.secrets import SecretStoreError, WindowsSecretStore


class SecretStoreTests(unittest.TestCase):
    def test_reference_cannot_escape_vault(self):
        with tempfile.TemporaryDirectory() as root:
            vault = WindowsSecretStore(Path(root), "test-only")
            with self.assertRaises(SecretStoreError):
                vault.get("../private")

    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
    def test_dpapi_roundtrip_encrypted_at_rest_and_bound_to_reference(self):
        with tempfile.TemporaryDirectory() as root:
            vault = WindowsSecretStore(Path(root), "test-only")
            reference = vault.put("synthetic-credential-测试")
            self.assertTrue(vault.has(reference))
            path = Path(root) / (reference + ".dpapi")
            self.assertNotIn(b"synthetic-credential", path.read_bytes())
            self.assertEqual(vault.get(reference), "synthetic-credential-测试")
            other = "a" * 32
            (Path(root) / (other + ".dpapi")).write_bytes(path.read_bytes())
            with self.assertRaises(SecretStoreError):
                vault.get(other)
            vault.delete(reference)
            self.assertFalse(vault.has(reference))


if __name__ == "__main__":
    unittest.main()
