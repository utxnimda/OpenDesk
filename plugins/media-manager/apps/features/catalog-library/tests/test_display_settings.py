from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from catalog_library import display_settings


class DisplaySettingsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "config" / "display.yaml"
        self.history = self.root / "history"

    def read(self):
        return display_settings.payload(config_path=self.path)

    def save(self, payload):
        return display_settings.save(payload, config_path=self.path, history_root=self.history)

    def work(self, domain, country):
        return {"attributes": [{"type": "collection-type", "data": {"domain": domain}}, {"type": "country", "data": country}]}

    def test_default_preferences_are_read_only_and_type_specific(self):
        self.assertEqual(display_settings.priority(self.work("animation", "japan"), config_path=self.path), "original")
        self.assertEqual(display_settings.priority(self.work("television", "korea"), config_path=self.path), "chinese")
        self.assertEqual(display_settings.priority(self.work("television", "china"), config_path=self.path), "original")
        self.assertFalse(self.path.parent.exists())

    def test_persist_reload_version_conflict_and_history(self):
        before = self.read()
        changed = deepcopy(before["config"])
        changed["chapter_titles"][1]["priority"] = "original"
        receipt = self.save({"revision": before["revision"], "config": changed})
        self.assertNotEqual(receipt["revision"], before["revision"])
        self.assertEqual(display_settings.priority(self.work("television", "korea"), config_path=self.path), "original")
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.save({"revision": before["revision"], "config": before["config"]})
        previous = self.path.read_bytes()
        self.save({"revision": receipt["revision"], "config": before["config"]})
        self.assertEqual(next(self.history.iterdir()).read_bytes(), previous)

    def test_invalid_values_and_duplicate_types_do_not_write(self):
        original = self.read()
        for edit in ({"default_priority": []}, {"schema_version": True}, {"chapter_titles": [original["config"]["chapter_titles"][0]] * 2}, {"chapter_titles": "bad"}):
            payload = deepcopy(original["config"]); payload.update(edit)
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.save({"revision": original["revision"], "config": payload})
        self.assertFalse(self.path.exists())

    def test_observed_collection_types_get_enumerated_without_db_write(self):
        result = display_settings.payload(config_path=self.path, records=[{"domain": "music", "country": "china"}, {"domain": "../../escape", "country": "china"}])
        self.assertTrue(any(row["label"] == "音乐 · 中国" for row in result["collection_types"]))
        self.assertEqual(len(result["collection_types"]), 3)
        self.assertFalse(self.path.exists())

    def test_atomic_failure_preserves_current_configuration(self):
        before = self.read(); receipt = self.save({"revision": before["revision"], "config": before["config"]})
        old = self.path.read_bytes(); changed = deepcopy(before["config"]); changed["default_priority"] = "chinese"
        with patch.object(display_settings, "commit_file_writes", side_effect=OSError("test write failure")), self.assertRaises(OSError):
            self.save({"revision": receipt["revision"], "config": changed})
        self.assertEqual(self.path.read_bytes(), old)

    def test_invalid_yaml_is_not_silently_reset(self):
        self.path.parent.mkdir(); self.path.write_text("chapter_titles: [", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "无法解析"):
            self.read()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "chapter_titles: [")


if __name__ == "__main__":
    unittest.main()
