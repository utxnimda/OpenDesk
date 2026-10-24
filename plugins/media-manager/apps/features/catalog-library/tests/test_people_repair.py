from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import patch

from catalog_library import people, people_repair, providers
from people_catalog.model import source_identity
from work_catalog_yaml.yaml_io import dump_yaml_string
import test_people as fixtures


class PeopleBirthdayRepairTest(unittest.TestCase):
    setUp = fixtures.CatalogPeopleTest.setUp
    ref = fixtures.CatalogPeopleTest.ref
    current = fixtures.CatalogPeopleTest.current
    request = fixtures.CatalogPeopleTest.request
    preview = fixtures.CatalogPeopleTest.preview
    apply = fixtures.CatalogPeopleTest.apply
    save_entity = fixtures.CatalogPeopleTest.save_entity

    def legacy(self):
        self.details["person:2"].update(birth_mon=19, birth_day=1,
            infobox=[{"key": "生日", "value": "1961年"}])
        self.apply(self.preview())
        self.identity = source_identity("bangumi", "person", 2)
        entity = self.repo.read(self.identity)
        entity["birth_date"] = {"day": 1}
        entity["source_values"]["bangumi"]["birth_date"] = {"day": 1}
        self.save_entity(entity)

    def repair_preview(self):
        return people_repair.preview({"ref": self.ref()}, settings=self.settings, people_root=self.people_root)

    def repair_apply(self, preview):
        return people_repair.apply({"ref": preview["ref"], "preview_token": preview["preview_token"]},
            settings=self.settings, people_root=self.people_root)

    def test_repair_is_local_preview_first_and_changes_only_proven_source_birthday(self):
        self.legacy()
        before_work = self.source.read_bytes()
        before = self.repo.read(self.identity)
        with patch.object(providers, "_request_json", side_effect=AssertionError("offline")), \
             patch.object(providers, "_save_snapshot", side_effect=AssertionError("immutable source")):
            preview = self.repair_preview()
            self.assertEqual(preview["repair_count"], 1)
            self.assertEqual(preview["changes"][0]["diff"], [{"field": "birth_date", "before": {"day": 1}, "after": {"year": 1961}}])
            self.assertEqual(self.repo.read(self.identity), before)
            result = self.repair_apply(preview)
        self.assertTrue(result["people_updated"])
        expected = deepcopy(before)
        expected["birth_date"] = expected["source_values"]["bangumi"]["birth_date"] = {"year": 1961}
        self.assertEqual(self.repo.read(self.identity), expected)
        self.assertEqual(self.source.read_bytes(), before_work)
        self.assertEqual(self.repair_preview()["repair_count"], 0)

    def test_manual_birthday_and_source_provenance_are_preserved(self):
        self.legacy()
        entity = self.repo.read(self.identity)
        entity["birth_date"] = {"year": 1970, "mon": 2, "day": 3}
        self.save_entity(entity)
        preview = self.repair_preview()
        self.assertEqual(preview["repair_count"], 0)
        self.assertEqual(preview["changes"][0]["preserved_fields"], ["birth_date"])
        self.repair_apply(preview)
        self.assertEqual(self.repo.read(self.identity), entity)

    def test_explicitly_cleared_manual_birthday_is_preserved_by_repair_and_refresh(self):
        self.legacy()
        entity = self.repo.read(self.identity)
        entity["birth_date"] = {}
        self.save_entity(entity)
        self.assertEqual(self.repair_preview()["repair_count"], 0)
        self.apply(self.preview())
        self.assertEqual(self.repo.read(self.identity)["birth_date"], {})

    def test_malformed_source_reference_or_source_values_is_a_warning_not_a_crash(self):
        self.legacy()
        for field, value in (("snapshot_id", []), ("snapshot_id", {}), ("source_values", [])):
            entity = self.repo.read(self.identity)
            if field == "source_values":
                entity["source_refs"][0]["snapshot_id"] = next((self.people_root / "sources" / "bangumi").glob("*.json")).stem
                entity["source_values"]["bangumi"] = value
            else:
                entity["source_refs"][0][field] = value
            self.save_entity(entity)
            result = self.repair_preview()
            self.assertEqual(result["repair_count"], 0)
            self.assertTrue(result["warnings"])

    def test_changed_people_and_work_after_preview_are_rejected(self):
        self.legacy()
        preview = self.repair_preview()
        entity = self.repo.read(self.identity)
        entity["summary"] = "new manual edit"
        self.save_entity(entity)
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.repair_apply(preview)
        self.assertEqual(self.repo.read(self.identity)["birth_date"], {"day": 1})
        preview = self.repair_preview()
        record = self.current()
        record["attributes"][0]["data"] = "new name"
        self.source.write_text(dump_yaml_string([record]), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.repair_apply(preview)

    def test_tampered_source_is_reported_and_never_used_for_repair(self):
        self.legacy()
        source = self.repo.read(self.identity)["source_refs"][0]["snapshot_id"]
        path = self.people_root / "sources" / "bangumi" / (source + ".json")
        path.write_bytes(path.read_bytes() + b" ")
        preview = self.repair_preview()
        self.assertEqual(preview["repair_count"], 0)
        self.assertTrue(any("校验失败" in warning for warning in preview["warnings"]))
        self.assertEqual(self.repo.read(self.identity)["birth_date"], {"day": 1})

    def test_missing_raw_snapshot_is_reported_without_using_local_infobox(self):
        self.legacy()
        entity = self.repo.read(self.identity)
        entity["source_refs"][0]["snapshot_id"] = "0" * 64
        entity["infobox"] = [{"key": "生日", "value": "1999年"}]
        self.save_entity(entity)
        preview = self.repair_preview()
        self.assertEqual(preview["repair_count"], 0)
        self.assertTrue(preview["warnings"])

    def test_invalid_or_different_scope_tokens_never_write(self):
        self.legacy()
        preview = self.repair_preview()
        with self.assertRaises(ValueError):
            people_repair.apply({"ref": preview["ref"], "preview_token": "invalid"},
                settings=self.settings, people_root=self.people_root)
        with self.assertRaisesRegex(ValueError, "不一致"):
            people_repair.apply({"ref": preview["ref"], "preview_token": preview["preview_token"]},
                settings=self.settings, people_root=self.root / "other-people")

    def test_other_work_voice_actors_are_outside_current_repair_scope(self):
        self.legacy()
        character_id = source_identity("bangumi", "character", 1)
        character = self.repo.read(character_id)
        other = deepcopy(self.repo.read(self.identity))
        other["id"] = source_identity("bangumi", "person", 999)
        other["source_refs"] = []
        self.save_entity(other)
        character["voice_actors"].append({"person_id": other["id"], "scope": {"provider": "bangumi", "subject_id": "999"}, "role": "CV"})
        self.save_entity(character)
        plan = people_repair._build(self.current(), self.repo, report=False)
        self.assertNotIn(other["id"], [row["id"] for row in plan["entities"]])


if __name__ == "__main__":
    unittest.main()
