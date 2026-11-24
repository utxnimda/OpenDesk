"""Tests for strict schema validation and atomic persistence of DeepSeek Harness config."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from deepseek_harness.config import (
    ConfigError,
    ConfigStore,
    DshConfig,
    redact_external_url,
    validate_config_data,
    validate_external_url,
)


class ConfigTests(unittest.TestCase):
    def test_defaults_and_atomic_round_trip(self):
        with tempfile.TemporaryDirectory(prefix="dsh-cfg-") as tmp:
            store = ConfigStore(Path(tmp))
            loaded = store.load()
            self.assertEqual(loaded, DshConfig())

            saved = store.save(DshConfig(profile="custom-web", autoStart=False))
            self.assertEqual(saved.profile, "custom-web")
            self.assertFalse(saved.autoStart)
            self.assertEqual(store.load(), saved)

    def test_rejects_corrupted_or_future_schema_without_overwriting(self):
        with tempfile.TemporaryDirectory(prefix="dsh-cfg-corrupt-") as tmp:
            root = Path(tmp)
            cfg_path = root / "config.json"
            cfg_path.write_text("{not valid json", encoding="utf-8")
            store = ConfigStore(root)
            with self.assertRaises(ConfigError) as ctx:
                store.load()
            self.assertEqual(ctx.exception.code, "CONFIG_CORRUPTED")
            self.assertEqual(cfg_path.read_text(encoding="utf-8"), "{not valid json")

            future = json.dumps({"schemaVersion": 99})
            cfg_path.write_text(future, encoding="utf-8")
            with self.assertRaises(ConfigError) as ctx2:
                store.load()
            self.assertEqual(ctx2.exception.code, "CONFIG_SCHEMA_UNSUPPORTED")
            self.assertEqual(cfg_path.read_text(encoding="utf-8"), future)

    def test_rejects_unknown_fields_desktop_profile_and_relative_paths(self):
        with self.assertRaises(ConfigError):
            validate_config_data({"schemaVersion": 1, "extra": True})
        with self.assertRaises(ConfigError):
            validate_config_data({"schemaVersion": 1, "profile": "desktop"})
        with self.assertRaises(ConfigError):
            validate_config_data({"schemaVersion": 1, "profile": "bad/name"})
        with self.assertRaises(ConfigError):
            validate_config_data({"schemaVersion": 1, "workspaceDir": "relative/path"})
        with self.assertRaises(ConfigError):
            validate_config_data({"schemaVersion": 1, "dshSource": "custom", "customDshPath": ""})
        with self.assertRaises(ConfigError):
            validate_config_data({"schemaVersion": 1, "homeMode": "custom", "customHomeDir": ""})
        with self.assertRaises(ConfigError):
            validate_config_data({"schemaVersion": 1, "sourceLaunchMode": "invalid"})
        cfg = validate_config_data({
            "schemaVersion": 1,
            "dshSource": "custom",
            "customDshPath": str(Path.cwd()),
            "sourceLaunchMode": "dev",
        })
        self.assertEqual(cfg.sourceLaunchMode, "dev")

    def test_external_url_validation_and_redaction(self):
        origin, port, token = validate_external_url("http://127.0.0.1:3080/?token=secret-abc")
        self.assertEqual(origin, "http://127.0.0.1:3080")
        self.assertEqual(port, 3080)
        self.assertEqual(token, "secret-abc")
        self.assertEqual(
            redact_external_url("http://127.0.0.1:3080/?token=secret-abc&foo=1"),
            "http://127.0.0.1:3080/?token=***&foo=1",
        )

        for bad in [
            "",
            "https://127.0.0.1:3080/",
            "http://example.com:3080/",
            "http://user:pass@127.0.0.1:3080/",
            "http://127.0.0.1/",
        ]:
            with self.assertRaises(ConfigError):
                validate_external_url(bad)


if __name__ == "__main__":
    unittest.main()
