"""Ordinary source confirmation commits work fields + people in one transaction."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from catalog_library import people, providers, service
from collection_detail.catalog_edit_service import catalog_records
from people_catalog.model import source_identity
from people_catalog.repository import PeopleRepository
from work_catalog_yaml.persistence import PersistenceRollbackError
from work_catalog_yaml.yaml_io import dump_yaml_string
import test_catalog_library_providers as fixtures
from people_import_test_helpers import selections


class CombinedProviderPeopleTest(unittest.TestCase):
    ref = fixtures.ProviderImportTest.ref

    def setUp(self):
        fixtures.ProviderImportTest.setUp(self)
        self.people_preview_patch.stop()
        self.people_root = self.root / "people"
        self.library = self.root / "library"
        self.repo = PeopleRepository(self.people_root)
        self.characters = [{"id": 1, "name": "キャラクター", "relation": "主角", "type": 1,
                            "actors": [{"id": 1, "name": "Actor One", "type": 1}]}]
        self.persons = [{"id": 2, "name": "Director", "type": 1, "relation": "导演", "eps": "1"}]
        self.details = {"character:1": {"id": 1, "name": "キャラクター", "summary": "Character description", "type": 1},
            "person:1": {"id": 1, "name": "Actor One", "summary": "Actor biography", "type": 1, "career": ["seiyu"]},
            "person:2": {"id": 2, "name": "Director", "summary": "Director biography", "type": 1, "career": []}}

    def request(self, path, **kwargs):
        if path == "/v0/subjects/12":
            return deepcopy(self.subject)
        if path == "/v0/episodes":
            return deepcopy(self.page)
        if path == "/v0/subjects/12/characters":
            return deepcopy(self.characters)
        if path == "/v0/subjects/12/persons":
            return deepcopy(self.persons)
        parts = path.split("/")
        return deepcopy(self.details[f"{'character' if parts[2] == 'characters' else 'person'}:{parts[-1]}"])

    def preview(self):
        with patch.object(providers, "_request_json", side_effect=self.request), \
             patch.object(providers.assets, "acquire_cover", side_effect=ValueError("fixture: no cover")):
            return providers.preview_provider_import({"ref": self.ref(), "subject_id": 12}, settings=self.settings,
                source_root=self.sources, people_root=self.people_root)

    def apply(self, preview, fields=None):
        return providers.apply_provider_import({"ref": preview["ref"], "snapshot_id": preview["snapshot_id"],
            "preview_token": preview["preview_token"], "selected_fields": ["metadata.summary"] if fields is None else fields,
            "people_selections": selections(preview, self.repo)},
            settings=self.settings, source_root=self.sources, people_root=self.people_root, data_root=self.library)

    def current(self):
        return catalog_records(self.settings)[0]["record"]

    def test_first_binding_previews_people_without_writing_either_database(self):
        before = self.file.read_bytes()
        preview = self.preview()
        self.assertIsNotNone(preview["people_preview"])
        self.assertEqual(len([row for row in preview["people_preview"]["entries"] if row["entity_type"] == "character"]), 1)
        self.assertEqual(len([row for row in preview["people_preview"]["entries"] if row["entity_type"] == "person"]), 2)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertFalse((self.people_root / "db").exists())

    def test_confirmation_commits_people_and_selected_work_fields_via_one_work_write(self):
        preview = self.preview()
        with patch.object(service, "apply_edits", wraps=service.apply_edits) as writer, \
             patch.object(providers, "_request_json", side_effect=AssertionError("confirm must be offline")), \
             patch.object(people, "apply", side_effect=AssertionError("must not use stale-ref second write")):
            result = self.apply(preview)
        self.assertEqual(writer.call_count, 1)
        self.assertTrue(result["people_updated"])
        record = self.current()
        self.assertEqual(record["metadata"]["summary"], "Source description")
        self.assertEqual(record["attributes"], self.record["attributes"])
        self.assertEqual(record["source_refs"][0]["external_id"], "12")
        self.assertEqual(len(record["people_refs"]), 2)
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 3)

    def test_no_selected_fields_still_confirms_source_and_people(self):
        result = self.apply(self.preview(), [])
        self.assertTrue(result["people_updated"])
        self.assertEqual(self.current()["metadata"]["summary"], "My description")
        self.assertTrue(self.current()["people_refs"])

    def test_repeat_confirmation_is_idempotent(self):
        preview = self.preview()
        self.apply(preview)
        with patch.object(service, "apply_edits", side_effect=AssertionError("no second work write")), \
             patch.object(people, "apply_with_work", side_effect=AssertionError("no second people write")):
            result = self.apply(preview)
        self.assertTrue(result["unchanged"])
        self.assertFalse(result["people_updated"])

    def test_cover_only_refresh_never_requests_or_previews_people(self):
        self.apply(self.preview())
        with patch.object(providers, "_request_json", side_effect=self.request) as request, \
             patch.object(providers.assets, "acquire_cover", side_effect=ValueError("no cover")), \
             patch.object(people, "preview_for_import", side_effect=AssertionError("cover must not fetch people")):
            preview = providers.preview_provider_cover({"ref": self.ref(), "subject_id": 12},
                settings=self.settings, source_root=self.sources)
        self.assertEqual(request.call_count, 1)
        self.assertIsNone(preview["people_preview"])

    def test_invalid_optional_person_field_warns_but_work_and_people_commit(self):
        self.details["person:1"]["birth_mon"] = 99
        preview = self.preview()
        self.assertTrue(preview["warnings"])
        result = self.apply(preview)
        self.assertTrue(result["people_updated"])
        self.assertEqual(self.current()["metadata"]["summary"], "Source description")
        actor = self.repo.read(source_identity("bangumi", "person", 1))
        self.assertNotIn("mon", actor.get("birth_date", {}))

    def test_people_request_failure_does_not_block_work_confirmation(self):
        def request(path, **kwargs):
            if path.endswith("/characters") or path.endswith("/persons"):
                raise ValueError("fixture people network timeout")
            return self.request(path, **kwargs)

        with patch.object(providers, "_request_json", side_effect=request), \
             patch.object(providers.assets, "acquire_cover", side_effect=ValueError("no cover")):
            preview = providers.preview_provider_import({"ref": self.ref(), "subject_id": 12}, settings=self.settings,
                source_root=self.sources, people_root=self.people_root)
        result = self.apply(preview)
        self.assertEqual(self.current()["metadata"]["summary"], "Source description")
        self.assertTrue(result["warnings"])
        self.assertFalse(result["people_updated"])
        self.assertFalse((self.people_root / "db").exists())

    def test_failed_people_refresh_preserves_existing_cast_and_actor_entities(self):
        self.apply(self.preview())
        old_refs = deepcopy(self.current()["people_refs"])
        old_entities = {path.name: path.read_bytes() for path in (self.people_root / "db").glob("*.yaml")}

        def request(path, **kwargs):
            if path.endswith("/characters") or path.endswith("/persons"):
                raise ValueError("fixture people unavailable")
            return self.request(path, **kwargs)

        with patch.object(providers, "_request_json", side_effect=request), \
             patch.object(providers.assets, "acquire_cover", side_effect=ValueError("no cover")):
            preview = providers.preview_provider_import({"ref": self.ref(), "subject_id": 12}, settings=self.settings,
                source_root=self.sources, people_root=self.people_root)
        result = self.apply(preview, ["metadata.episodes"])
        self.assertFalse(result["people_updated"])
        self.assertEqual(self.current()["people_refs"], old_refs)
        self.assertEqual({path.name: path.read_bytes() for path in (self.people_root / "db").glob("*.yaml")}, old_entities)

    def test_work_writer_failure_rolls_back_new_people_and_keeps_original_work(self):
        preview = self.preview()
        before = self.file.read_bytes()
        with patch.object(service, "apply_edits", side_effect=ValueError("work CAS failed")), self.assertRaisesRegex(ValueError, "CAS"):
            self.apply(preview)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(list((self.people_root / "db").glob("*.yaml")), [])

    def test_unknown_work_commit_retains_people_instead_of_leaving_dangling_refs(self):
        preview = self.preview()
        write_work = service.apply_edits

        def commit_then_fail(*args, **kwargs):
            write_work(*args, **kwargs)
            error = PersistenceRollbackError("fixture: work rollback was incomplete")
            error.db_committed = None
            raise error

        with patch.object(service, "apply_edits", side_effect=commit_then_fail), \
             self.assertRaisesRegex(PersistenceRollbackError, "提交状态不确定.*人物数据已保留") as raised:
            self.apply(preview)
        self.assertIsNone(raised.exception.db_committed)
        self.assertEqual(self.current()["metadata"]["summary"], "Source description")
        self.assertTrue(self.current()["people_refs"])
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 3)
        self.assertEqual(people.project(self.current(), people_root=self.people_root)["warnings"], [])

    def test_people_revision_conflict_blocks_all_changes(self):
        self.apply(self.preview())
        preview = self.preview()
        identity = source_identity("bangumi", "person", 1)
        actor = self.repo.read(identity)
        actor["name"] = "Manually edited while preview open"
        self.repo.apply_batch([actor], expected_revision=self.repo.revision([identity]))
        before = self.file.read_bytes()
        with self.assertRaisesRegex(ValueError, "变化|预览"):
            self.apply(preview)
        self.assertEqual(self.file.read_bytes(), before)

    def test_tampered_people_display_is_not_authority_and_root_scope_cannot_change(self):
        preview = self.preview()
        preview["people_preview"]["entries"][0]["candidate"]["name"] = "Tampered client display"
        result = self.apply(preview)
        self.assertEqual(result["people"]["characters"][0]["name"], "キャラクター")
        other = self.preview()
        with self.assertRaisesRegex(ValueError, "不一致|变化|预览"):
            providers.apply_provider_import({"ref": other["ref"], "snapshot_id": other["snapshot_id"],
                "preview_token": other["preview_token"], "selected_fields": []}, settings=self.settings,
                source_root=self.sources, people_root=self.root / "other-people")

    def test_independent_people_endpoint_still_requires_existing_binding(self):
        with patch.object(providers, "_request_json", side_effect=AssertionError("must not fetch")), self.assertRaisesRegex(ValueError, "先关联"):
            people.preview({"ref": self.ref(), "subject_id": 12}, settings=self.settings, people_root=self.people_root)


if __name__ == "__main__":
    unittest.main()
