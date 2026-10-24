from __future__ import annotations

from copy import deepcopy
from contextvars import ContextVar
from pathlib import Path
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import patch
from uuid import uuid4

from catalog_library import people, providers, service
from people_catalog.model import source_identity
from people_catalog.repository import PeopleRepository
from people_catalog.source_links import build_source_index
from work_catalog_yaml.jp_tv.browse_settings import JpTvBrowseSettings
from work_catalog_yaml.persistence import PersistenceRollbackError
from work_catalog_yaml.common.operation_logs import OperationLogStore
from work_catalog_yaml.operation_progress import OperationRegistry, execute_operation
from work_catalog_yaml.yaml_io import dump_yaml_string, load_yaml_string
from people_import_test_helpers import selections


class CatalogPeopleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "works"
        self.db.mkdir()
        self.source = self.db / "[JP][TVInfo][2011].yaml"
        self.record = {"attributes": [{"type": "name", "data": "Test Work"}, {"type": "country", "data": "japan"},
            {"type": "date", "data": {"start": "20110101", "end": ""}}, {"type": "collection-type", "data": {
                "domain": "animation", "release_type": "tv", "path": "Z:/Offline/Test", "collectioned": []}}],
            "source_refs": [{"provider": "bangumi", "external_id": "100"}]}
        self.source.write_text(dump_yaml_string([self.record]), encoding="utf-8")
        self.settings = JpTvBrowseSettings(version=1, filesystem_root=self.db, resolved_default_readable=str(self.source),
            resolved_catalog_yaml_paths=(str(self.source),), enum_options={}, enum_labels=MappingProxyType({}),
            enum_section_labels={}, app_features=())
        self.library = self.root / "library"
        self.people_root = self.root / "people"
        self.repo = PeopleRepository(self.people_root)
        self.characters = [{"id": 1, "name": "キャラクター", "relation": "主角", "actors": [
            {"id": 1, "name": "Same Name"}, {"id": 2, "name": "Same Name"}]}]
        self.persons = [{"id": 2, "name": "Same Name", "relation": "导演", "eps": "1-12"}]
        self.details = {"character:1": {"id": 1, "name": "キャラクター", "type": 1, "summary": "Character biography",
            "infobox": [{"key": "中文名", "value": "角色中文"}, {"key": "别名", "value": [{"k": "英文", "v": "Character Alias"}]}]},
            "person:1": {"id": 1, "name": "Same Name", "type": 1, "summary": "Actor One", "career": ["seiyu"]},
            "person:2": {"id": 2, "name": "Same Name", "type": 1, "summary": "Actor Two", "career": ["actor"]}}
        with service._CACHE_LOCK:
            service._CACHE.clear()

    def ref(self):
        return service._records(self.settings)[0]["ref"]

    def current(self):
        return load_yaml_string(self.source.read_text(encoding="utf-8"))[0]

    def request(self, path, **kwargs):
        if path == "/v0/subjects/100/characters":
            return deepcopy(self.characters)
        if path == "/v0/subjects/100/persons":
            return deepcopy(self.persons)
        parts = path.split("/")
        key = f"{'character' if parts[2] == 'characters' else 'person'}:{parts[-1]}"
        return deepcopy(self.details[key])

    def preview(self):
        with patch.object(providers, "_request_json", side_effect=self.request):
            return people.preview({"ref": self.ref(), "subject_id": 100}, settings=self.settings,
                                  data_root=self.library, people_root=self.people_root)

    def apply(self, preview):
        body = {key: preview[key] for key in ("ref", "subject_id", "snapshot_id", "preview_token")}
        body["people_selections"] = selections(preview, self.repo)
        return people.apply(body, settings=self.settings, data_root=self.library, people_root=self.people_root)

    def save_entity(self, entity):
        self.repo.apply_batch([entity], expected_revision=self.repo.revision([entity["id"]]))

    def test_preview_only_writes_snapshot_not_work_or_people(self):
        before = self.source.read_bytes()
        preview = self.preview()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse((self.people_root / "db").exists())
        self.assertEqual(len(list((self.people_root / "sources" / "bangumi").glob("*.json"))), 1)
        self.assertEqual(len(preview["entries"]), 3)
        character = next(row["candidate"] for row in preview["entries"] if row["entity_type"] == "character")
        self.assertEqual(character["name"], "キャラクター")
        self.assertEqual(character["name_cn"], "角色中文")
        self.assertEqual(len([row for row in preview["entries"][0]["relations"] if row["kind"] == "voice_actor"]), 2)
        self.assertTrue(preview["entries"][0]["creation"]["diff"])
        self.assertTrue(all("before" in row and "after" in row for row in preview["entries"][0]["creation"]["diff"]))

    def test_apply_calls_shared_work_writer_and_local_projection_never_networks(self):
        preview = self.preview()
        with patch.object(service, "apply_edits", wraps=service.apply_edits) as writer, \
             patch.object(providers, "_request_json", side_effect=AssertionError("No HTTP during apply/read")):
            result = self.apply(preview)
            projection = people.project(self.current(), people_root=self.people_root)
        self.assertTrue(result["ok"])
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 3)
        self.assertEqual(projection, result["people"])
        refs = self.current()["people_refs"]
        self.assertTrue(all("name" not in row and "summary" not in row for row in refs))
        self.assertNotEqual(projection["characters"][0]["id"], projection["characters"][0]["actors"][0]["id"])
        self.assertNotEqual(projection["characters"][0]["actors"][0]["id"], projection["characters"][0]["actors"][1]["id"])

    def test_refresh_preserves_manual_fields_and_other_work_voice_actor_scope(self):
        self.apply(self.preview())
        identity = source_identity("bangumi", "character", 1)
        character = self.repo.read(identity)
        character["summary"] = "My manually edited biography"
        character["custom_tags"] = ["keep"]
        other = {"person_id": source_identity("bangumi", "person", 2), "scope": {"provider": "bangumi", "subject_id": "200"}, "role": "CV"}
        character["voice_actors"].append(other)
        self.save_entity(character)
        self.characters[0]["actors"] = [{"id": 1}]
        self.details["character:1"]["summary"] = "New remote biography"
        self.details["character:1"]["name"] = "New source title"
        self.apply(self.preview())
        updated = self.repo.read(identity)
        self.assertEqual(updated["summary"], "My manually edited biography")
        self.assertEqual(updated["name"], character["name"])
        self.assertEqual(updated["localized"]["name"]["ja"], character["name"])
        self.assertEqual(updated["localized"]["name"]["und-Latn"], "New source title")
        self.assertEqual(updated["custom_tags"], ["keep"])
        self.assertIn(other, updated["voice_actors"])
        local = people.project(self.current(), people_root=self.people_root)
        self.assertEqual(len(local["characters"][0]["actors"]), 2)  # additive choices never silently remove prior cast

    def test_work_save_failure_leaves_no_people_or_work_refs(self):
        preview = self.preview()
        before = self.source.read_bytes()
        with patch.object(service, "apply_edits", side_effect=ValueError("work commit failed")), self.assertRaisesRegex(ValueError, "work commit"):
            self.apply(preview)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(list((self.people_root / "db").glob("*.yaml")), [])

    def test_uncertain_work_rollback_preserves_all_people_referenced_by_saved_work(self):
        preview = self.preview()
        write_work = service.apply_edits

        def commit_then_fail(*args, **kwargs):
            write_work(*args, **kwargs)
            error = PersistenceRollbackError("fixture: incomplete work rollback")
            error.db_committed = None
            raise error

        with patch.object(service, "apply_edits", side_effect=commit_then_fail), \
             self.assertRaisesRegex(PersistenceRollbackError, "提交状态不确定.*人物数据已保留") as raised:
            self.apply(preview)
        self.assertIsNone(raised.exception.db_committed)
        self.assertTrue(raised.exception.people_records_retained)
        self.assertTrue(self.current()["people_refs"])
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 3)
        projection = people.project(self.current(), people_root=self.people_root)
        self.assertEqual(projection["warnings"], [])

    def test_stale_people_preview_rejected_before_writes(self):
        self.apply(self.preview())
        preview = self.preview()
        character = self.repo.read(source_identity("bangumi", "character", 1))
        character["name"] = "Concurrent local change"
        self.save_entity(character)
        before = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.apply(preview)
        self.assertEqual(self.source.read_bytes(), before)

    def test_stale_work_preview_rejected_before_people_writes(self):
        preview = self.preview()
        self.record["attributes"][0]["data"] = "Changed"
        self.source.write_text(dump_yaml_string([self.record]), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.apply(preview)
        self.assertFalse((self.people_root / "db").exists())

    def test_requires_bound_subject_and_does_not_request_other_ids(self):
        with patch.object(providers, "_request_json", side_effect=AssertionError("No fetch")), self.assertRaisesRegex(ValueError, "先关联"):
            people.preview({"ref": self.ref(), "subject_id": 200}, settings=self.settings, people_root=self.people_root)

    def test_apply_rejects_tampered_token_ref_and_cross_root(self):
        preview = self.preview()
        for key, value in (("preview_token", preview["preview_token"] + "x"), ("subject_id", 200), ("snapshot_id", "a" * 64)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.apply({**preview, key: value})
        body = {key: preview[key] for key in ("ref", "snapshot_id", "preview_token")}
        with self.assertRaisesRegex(ValueError, "不一致"):
            people.apply(body, settings=self.settings, people_root=self.root / "other")

    def test_corrupt_snapshot_rejected(self):
        preview = self.preview()
        path = self.people_root / "sources" / "bangumi" / (preview["snapshot_id"] + ".json")
        path.write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "校验失败"):
            self.apply(preview)

    def test_fetch_failure_produces_confirmable_minimal_profile_without_dangling_refs(self):
        def failure(path, **kwargs):
            if path == "/v0/persons/2":
                raise ValueError("network failed")
            return self.request(path, **kwargs)
        with patch.object(providers, "_request_json", side_effect=failure):
            preview = people.preview({"ref": self.ref()}, settings=self.settings, people_root=self.people_root)
        self.assertFalse((self.people_root / "db").exists())
        self.assertTrue(any("person:2" in warning and "network failed" in warning for warning in preview["warnings"]))
        self.assertEqual(next(row["candidate"] for row in preview["entries"] if row["external_id"] == "person:2")["summary"], "")
        with patch.object(providers, "_request_json", side_effect=AssertionError("No HTTP")):
            result = self.apply(preview)
        self.assertTrue(result["ok"])
        self.assertTrue(result["warnings"])
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 3)
        self.assertFalse(result["people"]["warnings"])

    def test_invalid_optional_fields_leave_empty_and_detailed_warnings(self):
        self.details["person:2"].update(birth_year=1980, birth_mon="unknown", birth_day=8,
            gender=["unknown"], career={"invalid": True}, summary=["invalid"], type=999,
            infobox="invalid")
        preview = self.preview()
        person = next(row["candidate"] for row in preview["entries"] if row["external_id"] == "person:2")
        self.assertEqual(person["birth_date"], {"year": 1980, "day": 8})
        self.assertEqual(person["gender"], "")
        self.assertEqual(person["summary"], "")
        self.assertEqual(person["career"], [])
        self.assertEqual(person["kind"], "")
        self.assertEqual(person["infobox"], [])
        self.assertTrue(any("作品 100 / person:2 / birth_mon" in row for row in preview["warnings"]))
        self.assertTrue(self.apply(preview)["ok"])

    def test_missing_or_invalid_fields_preserve_manual_and_previous_imported_values(self):
        self.details["person:2"].update(birth_mon=3, birth_day=8)
        self.apply(self.preview())
        identity = source_identity("bangumi", "person", 2)
        before = self.repo.read(identity)
        before["summary"] = "manual biography"
        self.save_entity(before)
        self.details["person:2"].update(birth_mon="invalid", career=None, summary={"invalid": True})
        result = self.apply(self.preview())
        after = self.repo.read(identity)
        self.assertEqual(after["birth_date"], before["birth_date"])
        self.assertEqual(after["career"], before["career"])
        self.assertEqual(after["summary"], "manual biography")
        self.assertEqual(after["source_values"]["bangumi"]["birth_date"], {"day": 8})  # raw evidence, independent of explicit preserved override
        self.assertTrue(result["warnings"])

    def test_endpoint_failure_preserves_work_refs_and_cast_and_is_cas_checked(self):
        self.apply(self.preview())
        original = self.current()["people_refs"]

        def failure(path, **kwargs):
            if path.endswith("/characters") or path.endswith("/persons"):
                raise OSError("offline")
            return self.request(path, **kwargs)

        with patch.object(providers, "_request_json", side_effect=failure):
            preview = people.preview({"ref": self.ref()}, settings=self.settings, people_root=self.people_root)
        self.apply(preview)
        self.assertEqual(self.current()["people_refs"], original)
        self.assertEqual(len(people.project(self.current(), people_root=self.people_root)["characters"][0]["actors"]), 2)
        with patch.object(providers, "_request_json", side_effect=failure):
            stale = people.preview({"ref": self.ref()}, settings=self.settings, people_root=self.people_root)
        person = self.repo.read(source_identity("bangumi", "person", 1))
        person["name"] = "concurrent edit"
        self.save_entity(person)
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.apply(stale)

    def test_partial_birth_date_and_missing_relation_fields_keep_previous_values(self):
        self.details["person:2"].update(birth_year=1980, birth_mon=3, birth_day=8)
        self.apply(self.preview())
        identity = source_identity("bangumi", "person", 2)
        self.details["person:2"].update(birth_year=None, birth_mon=4, birth_day="invalid")
        self.persons[0].pop("eps")
        self.persons[0]["relation"] = None
        self.apply(self.preview())
        person = self.repo.read(identity)
        self.assertEqual(person["birth_date"], {"year": 1980, "mon": 4, "day": 8})
        self.assertEqual(person["source_values"]["bangumi"]["birth_date"], {"mon": 4})
        ref = next(row for row in self.current()["people_refs"] if row["id"] == identity)
        self.assertEqual(ref["role"], "导演")
        self.assertEqual(ref["eps"], "1-12")

    def test_missing_actor_array_does_not_clear_existing_cast(self):
        self.apply(self.preview())
        self.characters[0]["actors"] = None
        result = self.apply(self.preview())
        self.assertEqual(len(result["people"]["characters"][0]["actors"]), 2)
        self.assertTrue(any("actors" in warning for warning in result["warnings"]))

    def test_missing_detail_and_name_skips_only_unidentifiable_new_person(self):
        self.persons[0].pop("name")
        self.characters[0]["actors"][1].pop("name")

        def failure(path, **kwargs):
            if path == "/v0/persons/2":
                raise TimeoutError("network timeout")
            return self.request(path, **kwargs)

        with patch.object(providers, "_request_json", side_effect=failure):
            preview = people.preview({"ref": self.ref()}, settings=self.settings, people_root=self.people_root)
        result = self.apply(preview)
        self.assertEqual(result["people"]["persons"], [])
        self.assertEqual(len(result["people"]["characters"][0]["actors"]), 1)
        self.assertFalse(result["people"]["warnings"])

    def test_detail_identity_nsfw_and_request_safety_errors_still_block(self):
        for update in ({"id": 999}, {"id": True}, {"nsfw": True}):
            original = deepcopy(self.details["person:2"])
            self.details["person:2"].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.preview()
            self.details["person:2"] = original
        for message in ("Bangumi API 响应超出安全大小限制", "Bangumi API 返回重定向，已拒绝访问其他地址"):
            with self.subTest(message=message), patch.object(providers, "_request_json", side_effect=ValueError(message)), self.assertRaises(ValueError):
                people.preview({"ref": self.ref()}, settings=self.settings, people_root=self.people_root)
        self.assertFalse((self.people_root / "db").exists())

    def test_optional_field_warnings_are_persisted_in_common_operation_log(self):
        self.details["person:2"]["birth_mon"] = "bad"
        store = OperationLogStore(self.root / "logs")
        registry = OperationRegistry(log_store=store)
        row = registry.register(str(uuid4()), "人物预览")
        execute_operation(registry, row, self.preview)
        result = registry.snapshot(row.id)
        self.assertEqual(result["status"], "warning")
        self.assertTrue(any(detail["level"] == "warning" and "person:2 / birth_mon" in detail["message"]
                            for detail in result["result"]["details"]))
        self.assertIn("birth_mon", store.find(row.id).read_text(encoding="utf-8"))

    def test_combined_import_allows_new_binding_and_commits_final_work_once(self):
        self.record.pop("source_refs")
        self.source.write_text(dump_yaml_string([self.record]), encoding="utf-8")
        item = providers._catalog_item(self.ref(), self.settings)
        with patch.object(providers, "_request_json", side_effect=self.request):
            prepared = people.preview_for_import(item, 100, settings=self.settings, people_root=self.people_root)
        final = deepcopy(item["record"])
        final["source_refs"] = [{"provider": "bangumi", "external_id": "100"}]
        final["attributes"][0]["data"] = "Imported work name"
        prepared["proposal"] = people.with_supplement_selections([prepared["proposal"]], selections(prepared["preview"], self.repo))[0]
        with patch.object(service, "apply_edits", wraps=service.apply_edits) as writer, \
                patch.object(providers, "_request_json", side_effect=AssertionError("No HTTP")):
            result = people.apply_with_work(prepared["proposal"], item, final, settings=self.settings,
                data_root=self.library, people_root=self.people_root)
        self.assertTrue(result["people_updated"])
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(self.current()["attributes"][0]["data"], "Imported work name")
        self.assertEqual(len(self.current()["people_refs"]), 2)

    def test_detail_workers_keep_operation_context(self):
        context = ContextVar("people-test-context", default="missing")
        token = context.set("current-operation")
        self.addCleanup(context.reset, token)

        def request(path, **kwargs):
            self.assertEqual(context.get(), "current-operation")
            return self.request(path, **kwargs)

        with patch.object(providers, "_request_json", side_effect=request):
            people.preview({"ref": self.ref()}, settings=self.settings, people_root=self.people_root)

    def test_aggregate_snapshot_limit_stops_before_snapshot_or_db_write(self):
        with patch.object(providers, "MAX_SNAPSHOT_BYTES", 1200), self.assertRaisesRegex(ValueError, "安全大小"):
            self.preview()
        self.assertFalse(self.people_root.exists())

    def test_refresh_preserves_manually_changed_voice_cast_for_same_subject(self):
        self.apply(self.preview())
        identity = source_identity("bangumi", "character", 1)
        character = self.repo.read(identity)
        character["voice_actors"] = [character["voice_actors"][1]]
        self.save_entity(character)
        self.characters[0]["actors"] = [{"id": 1}]
        preview = self.preview()
        decisions = selections(preview, self.repo)
        for row in decisions[preview["snapshot_id"]]:
            if row["external_id"] == "character:1":
                row["relations"] = []  # user leaves removed voice relationship unchecked
        people.apply({"ref": preview["ref"], "preview_token": preview["preview_token"], "people_selections": decisions},
                     settings=self.settings, data_root=self.library, people_root=self.people_root)
        character = self.repo.read(identity)
        self.assertEqual([row["person_id"] for row in character["voice_actors"]], [source_identity("bangumi", "person", 2)])

    def test_preview_retains_manual_non_source_work_references(self):
        self.apply(self.preview())
        record = self.current()
        ref = {"id": source_identity("bangumi", "person", 1), "type": "person", "role": "My favorite"}
        record["people_refs"].append(ref)
        self.source.write_text(dump_yaml_string([record]), encoding="utf-8")
        self.apply(self.preview())
        self.assertIn(ref, self.current()["people_refs"])

    def test_oversized_and_duplicate_character_lists_rejected(self):
        for rows in ([self.characters[0]] * 201, self.characters * 2):
            self.characters = rows
            with self.assertRaises(ValueError):
                self.preview()
        self.assertFalse((self.people_root / "db").exists())

    def test_empty_upstream_can_be_confirmed_without_deleting_entities(self):
        self.apply(self.preview())
        self.characters = []
        self.persons = []
        result = self.apply(self.preview())
        self.assertEqual(len(self.current()["people_refs"]), 2)
        self.assertEqual(len(result["people"]["characters"]), 1)
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 3)

    def test_missing_local_reference_is_visible_not_a_network_fetch(self):
        record = {"people_refs": [{"id": source_identity("bangumi", "character", 1), "type": "character", "role": "主角"}]}
        projection = people.project(record, people_root=self.people_root)
        self.assertTrue(projection["characters"][0]["missing"])
        self.assertTrue(projection["warnings"])
        self.assertFalse(self.people_root.exists())

    def test_remote_images_never_escape_local_browsing_payload(self):
        self.details["character:1"]["images"] = {"large": "https://example.invalid/image.jpg"}
        preview = self.preview()
        self.assertNotIn("images", preview["entries"][0]["candidate"])
        self.apply(preview)
        self.assertNotIn("images", self.repo.read(source_identity("bangumi", "character", 1)))

    def test_bangumi_refresh_after_explicit_character_merge_keeps_target_and_retired_file(self):
        from catalog_library import character_merge
        self.apply(self.preview())
        donor_id = source_identity("bangumi", "character", 1)
        target_id = source_identity("manual", "character", "saved-character")
        target = {"schema_version": 1, "id": target_id, "type": "character", "kind": "character", "name": "My Hero",
                  "name_cn": "", "summary": "手动保存简介", "avatar": {}}
        self.save_entity(target)
        plan = character_merge.preview({"person_id": donor_id, "target_id": target_id}, settings=self.settings, people_root=self.people_root)
        character_merge.apply({"person_id": donor_id, "target_id": target_id, "preview_token": plan["preview_token"], "selected_fields": []},
                              settings=self.settings, people_root=self.people_root, data_root=self.library)
        retired = self.repo._bytes(donor_id)
        self.details["character:1"].update(name="Changed remote donor", summary="Must not replace saved target")
        preview = self.preview()
        self.assertEqual(preview["entries"][0]["identity_owner_id"], target_id)
        self.apply(preview)
        self.assertEqual(self.repo._bytes(donor_id), retired)
        self.assertEqual(self.repo.read(donor_id)["merged_into"], target_id)
        for field in ("name", "name_cn", "summary", "avatar"):
            self.assertEqual(self.repo.read(target_id)[field], target[field])
        self.assertEqual(build_source_index(self.repo).resolve("bangumi", "character", "1"), target_id)
        self.assertEqual(self.current()["people_refs"][0]["id"], target_id)

    def test_multiple_bangumi_ids_resolving_to_one_character_union_sources_cast_and_changes(self):
        from catalog_library import character_merge
        from people_catalog.localization import set_field
        self.characters[0]["actors"] = [{"id": 1, "name": "Actor 1"}]
        self.characters.append({"id": 2, "name": "Second Character", "relation": "主角", "actors": [{"id": 2, "name": "Actor 2"}]})
        self.details["character:2"] = {"id": 2, "name": "Second Character", "type": 1, "summary": "Original imported target"}
        self.apply(self.preview())
        donor_id, target_id = (source_identity("bangumi", "character", value) for value in (1, 2))
        target = self.repo.read(target_id)
        target.update(name="Manually named hero", name_cn="", summary="手动简介")
        set_field(target, "localized.name.und-Latn", "Manually named hero")
        set_field(target, "localized.name.zh-Hans", "")
        set_field(target, "localized.summary.und-Latn", "手动简介")
        self.save_entity(target)
        plan = character_merge.preview({"person_id": donor_id, "target_id": target_id}, settings=self.settings, people_root=self.people_root)
        character_merge.apply({"person_id": donor_id, "target_id": target_id, "preview_token": plan["preview_token"], "selected_fields": []},
                              settings=self.settings, people_root=self.people_root, data_root=self.library)
        retired = self.repo._bytes(donor_id)
        self.details["character:1"].update(name="Donor remote change", summary="Donor new summary")
        self.details["character:2"].update(name="Canonical remote change", summary="Canonical new summary")
        preview = self.preview()
        self.assertEqual(len([row for row in preview["entries"] if row["identity_owner_id"] == target_id]), 2)
        self.apply(preview)
        updated = self.repo.read(target_id)
        self.assertEqual(self.repo._bytes(donor_id), retired)
        self.assertEqual([source["external_id"] for source in updated["source_refs"] if source["provider"] == "bangumi"], ["1", "2"])
        self.assertEqual({actor["person_id"] for actor in updated["voice_actors"]}, {source_identity("bangumi", "person", 1), source_identity("bangumi", "person", 2)})
        self.assertEqual(len(updated["voice_actors"]), 2)
        for field in ("name", "name_cn", "summary"):
            self.assertEqual(updated[field], target[field])
        self.assertEqual(updated["source_values"]["bangumi"]["localized.name.und-Latn"],
                         target["source_values"]["bangumi"]["localized.name.und-Latn"])

    def test_explicit_same_target_aliases_do_not_reinterpret_manual_fields_as_imported_mid_batch(self):
        target_id = source_identity("manual", "character", "same-target")
        self.save_entity({"schema_version": 1, "id": target_id, "type": "character", "name": "Coincidental first name",
            "summary": "Manual summary", "source_refs": [{"provider": "bangumi", "entity_type": "character", "external_id": str(value)} for value in (1, 2)]})
        self.characters.append({"id": 2, "name": "Second Character", "relation": "配角", "actors": [{"id": 2}]})
        self.details["character:1"]["name"] = "Coincidental first name"
        self.details["character:2"] = {"id": 2, "name": "Do not overwrite manual", "summary": "New summary", "type": 1}
        self.apply(self.preview())
        updated = self.repo.read(target_id)
        self.assertEqual(updated["name"], "Coincidental first name")
        self.assertEqual(updated["summary"], "Manual summary")
        self.assertEqual({row["external_id"] for row in updated["source_refs"]}, {"1", "2"})
        self.assertEqual({row["id"] for row in self.current()["people_refs"] if row["type"] == "character"}, {target_id})

    def test_retired_bangumi_source_without_exact_transferred_owner_is_not_recreated(self):
        self.apply(self.preview())
        donor_id = source_identity("bangumi", "character", 1)
        target_id = source_identity("manual", "character", "unowned")
        self.save_entity({"schema_version": 1, "id": target_id, "type": "character", "name": "Other"})
        donor = self.repo.read(donor_id)
        self.save_entity({**donor, "merged_into": target_id, "source_refs": []})
        before = self.repo._bytes(donor_id)
        with self.assertRaisesRegex(ValueError, "正确目标"):
            self.preview()
        self.assertEqual(self.repo._bytes(donor_id), before)

    def test_late_bangumi_source_owner_rejects_save_and_rolls_back_only_this_import(self):
        preview = self.preview()
        before_work = self.source.read_bytes()
        late_id = source_identity("manual", "character", "late-bangumi-owner")
        late = {"schema_version": 1, "id": late_id, "type": "character", "name": "Concurrent owner",
                "source_refs": [{"provider": "bangumi", "entity_type": "character", "external_id": "1"}]}
        original = PeopleRepository.apply_batch

        def race(repository, records, *, expected_revision, after_write=None, **options):
            def late_owner():
                original(repository, [late], expected_revision=repository.revision([late_id]))
                return after_write()
            return original(repository, records, expected_revision=expected_revision, after_write=late_owner, **options)

        with patch.object(PeopleRepository, "apply_batch", new=race), self.assertRaisesRegex(ValueError, "来源所有者"):
            self.apply(preview)
        self.assertEqual(self.source.read_bytes(), before_work)
        self.assertEqual(self.repo.read(late_id), late)
        self.assertEqual([p.stem for p in (self.people_root / "db").glob("*.yaml")], [late_id])


if __name__ == "__main__":
    unittest.main()
