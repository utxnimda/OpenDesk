"""Independent people library edits/clear/refresh only synthetic temp fixtures."""
from copy import deepcopy
import os
import unittest
from unittest.mock import patch

from catalog_library import people_library as library, providers, service, tmdb_source, wikipedia_people, people_infobox
from people_catalog.model import source_identity
from people_catalog.repository import PeopleRepository
from people_catalog.source_links import build_source_index
from work_catalog_yaml.yaml_io import dump_yaml_string
import test_people as fixtures


class PeopleLibraryTest(unittest.TestCase):
    save_entity = fixtures.CatalogPeopleTest.save_entity

    def setUp(self):
        fixtures.CatalogPeopleTest.setUp(self)
        self.identity = source_identity("bangumi", "person", "11")
        self.person = {"schema_version": 1, "id": self.identity, "type": "person", "kind": "individual",
            "name": "Original Name", "name_cn": "中文名", "aliases": ["Existing alias"], "summary": "Local summary",
            "career": ["actor"], "gender": "男性", "birth_date": {"year": 1980, "month": 1, "day": 2},
            "infobox": [{"key": "出生地", "value": "Local"}], "avatar": {},
            "source_refs": [{"provider": "bangumi", "entity_type": "person", "external_id": "11"}],
            "source_values": {"bangumi": {"summary": "Imported summary", "name_cn": "Imported Chinese"}}}
        self.save_entity(self.person)
        self.record["people_refs"] = [{"id": self.identity, "type": "person", "role": "导演"}]
        self.source.write_text(dump_yaml_string([self.record]), encoding="utf-8")
        self.kw = {"settings": self.settings, "people_root": self.people_root, "data_root": self.library}
        with library._ENTITY_CACHE_LOCK:
            library._ENTITY_CACHE.clear()
            library._ENTITY_CACHE_BYTES = 0

    def detail(self, identity=None):
        with patch("socket.socket.connect", side_effect=AssertionError("detail is offline")):
            return library.detail({"person_id": identity or self.identity}, **self.kw)

    def preview(self, mode="edit", **body):
        detail = self.detail()
        return getattr(library, mode + "_preview")({"person_id": self.identity, "version": detail["version"], **body}, **self.kw)

    def apply(self, preview, fields=None, **body):
        mode = preview["mode"].removeprefix("people-library-")
        with patch("socket.socket.connect", side_effect=AssertionError("apply must be offline")), \
             patch.object(service, "apply_edits", side_effect=AssertionError("must not touch work records")):
            return getattr(library, mode + "_apply")({"person_id": self.identity, "preview_token": preview["preview_token"],
                "selected_fields": preview["selected_fields"] if fields is None else fields, **body}, **self.kw)

    def bytes(self):
        return self.source.read_bytes(), {path.name: path.read_bytes() for path in (self.people_root / "db").glob("*.yaml")}

    def test_browse_search_filter_and_pagination_skip_retired(self):
        char_id = source_identity("bangumi", "character", "12")
        self.save_entity({"schema_version": 1, "id": char_id, "type": "character", "name": "Fictional", "kind": "character"})
        retired = {**self.person, "id": source_identity("douban", "person", "personage:7"), "source_refs": [], "merged_into": self.identity}
        self.save_entity(retired)
        result = library.browse({"page_size": 1}, **self.kw)
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(library.browse({"query": "EXISTING ALIAS", "type": "person", "provider": "bangumi"}, **self.kw)["items"][0]["id"], self.identity)
        self.assertEqual(library.browse({"type": "character"}, **self.kw)["items"][0]["id"], char_id)
        with self.assertRaises(ValueError):
            self.detail(retired["id"])

    def test_browse_full_ui_payload_all_empty_filters_is_valid(self):
        result = library.browse({"query": "", "type": "", "provider": "", "page": 1, "page_size": 36}, **self.kw)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["id"], self.identity)
        self.assertEqual((result["page"], result["page_size"]), (1, 36))
        self.assertEqual(library.browse({"query": "", "type": "", "kind": "", "provider": "", "page": 1, "page_size": 36}, **self.kw)["total"], 1)
        self.assertEqual(library.browse({"provider": "custom-local-source"}, **self.kw)["total"], 0)

    def test_taxonomy_filters_counts_and_labels_do_not_mutate_legacy_records(self):
        for index, (entity_type, kind) in enumerate((("person", "corporation"), ("person", "association"),
                                                 ("character", "organization"), ("character", "mechanic"), ("person", "")), 100):
            self.save_entity({"schema_version": 1, "id": source_identity("bangumi", entity_type, str(index)),
                              "type": entity_type, "kind": kind, "name": kind or "Unknown"})
        before = self.bytes()
        result = library.browse({"category": "organization"}, **self.kw)
        self.assertEqual(result["total"], 3)
        self.assertTrue(all("组织" in row["taxonomy_label"] for row in result["items"]))
        self.assertEqual({row["value"]: row["count"] for row in result["facets"]["categories"]},
                         {"person": 1, "organization": 3, "object": 1, "unknown": 1})
        self.assertEqual(library.browse({"category": "organization", "reality": "real", "subtype": "corporation"}, **self.kw)["total"], 1)
        self.assertEqual(library.browse({"category": "unknown"}, **self.kw)["items"][0]["taxonomy_label"], "待分类")
        self.assertEqual(self.bytes(), before)

    def test_organization_edit_hides_person_fields_and_preserves_existing_values(self):
        self.person["kind"] = "corporation"
        self.save_entity(self.person)
        detail = self.detail()
        fields = {row["field"]: row for row in detail["edit_fields"]}
        self.assertNotIn("gender", fields)
        self.assertNotIn("birth_date", fields)
        self.assertEqual(detail["person"]["gender"], self.person["gender"])
        value = {"category": "organization", "reality": "real", "subtype": "studio"}
        preview = self.preview(changes={"taxonomy": value})
        before = self.bytes()
        self.assertEqual(preview["selected_fields"], ["taxonomy"])
        self.assertNotIn("taxonomy", self.repo.read(self.identity))
        self.apply(preview)
        saved = self.repo.read(self.identity)
        for key in ("id", "type", "kind", "source_refs", "gender", "birth_date"):
            self.assertEqual(saved[key], self.person[key])
        self.assertEqual(self.source.read_bytes(), before[0])
        self.assertEqual(saved["taxonomy"], value)
        self.assertEqual(saved["field_sources"]["taxonomy"]["provider"], "manual")
        with self.assertRaises(ValueError):
            self.preview(changes={"gender": "女性"})

    def test_source_refresh_preserves_manual_taxonomy_despite_changed_legacy_kind(self):
        self.person["taxonomy"] = {"category": "organization", "reality": "real", "subtype": "studio"}
        self.save_entity(self.person)
        with patch.object(providers, "_request_json", return_value={"id": 11, "name": "Studio", "type": 1, "summary": "Source summary"}):
            preview = self.preview("source", provider="bangumi")
        self.apply(preview)
        self.assertEqual(self.repo.read(self.identity)["taxonomy"], self.person["taxonomy"])
        self.assertIn("工作室", self.detail()["person"]["taxonomy_label"])
        with patch.object(tmdb_source, "_request_json", side_effect=AssertionError("not a person endpoint")), self.assertRaises(ValueError):
            self.preview("source", provider="tmdb", external_id=99)

    def test_invalid_classification_filters_and_edits_rejected(self):
        for body in ({"category": []}, {"reality": "maybe"}, {"subtype": "anything"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                library.browse(body, **self.kw)
        with self.assertRaises(ValueError):
            self.preview(changes={"taxonomy": {"category": "organization", "reality": "real", "subtype": "individual"}})

    def test_browse_and_detail_report_read_only_scan_progress(self):
        with patch.object(library, "report_progress") as report:
            library.browse({}, **self.kw)
            self.detail()
        messages = "\n".join(str(call) for call in report.call_args_list)
        for word in ("正在校验人员库", "人员库扫描完成", "正在检查此人物", "人物关联扫描完成"):
            self.assertIn(word, messages)

    def test_browse_rejects_nonobjects_and_unknown_filters_without_provider_mutation_validation(self):
        for body in ([], "", False, {"unexpected": True}, {"provider": []}, {"type": []}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                library.browse(body, **self.kw)

    def test_detail_signed_version_relation_sources_and_enum_schema(self):
        before = self.bytes()
        result = self.detail()
        self.assertEqual(result["person"]["id"], self.identity)
        self.assertEqual(result["relations"][0]["role"], "导演")
        self.assertEqual(result["sources"], self.person["source_refs"])
        self.assertFalse(result["type_editable"])
        fields = {row["field"]: row for row in result["edit_fields"]}
        self.assertEqual(fields["taxonomy"]["type"], "taxonomy")
        self.assertNotIn("kind", fields)
        self.assertTrue(fields["name"]["required"])
        self.assertEqual(providers._verify_token(result["version"])["person_id"], self.identity)
        self.assertEqual(self.bytes(), before)

    def test_independent_cast_character_links_back_to_work_and_actor(self):
        role_id = source_identity("tmdb", "character", "role:local")
        self.save_entity({"schema_version": 1, "id": role_id, "type": "character", "name": "Role", "kind": "character"})
        self.record["people_refs"] = [{"id": self.identity, "type": "person", "role": "演员", "character_name": "Role", "character_id": role_id}]
        self.source.write_text(dump_yaml_string([self.record]), encoding="utf-8")
        result = self.detail(role_id)
        relation = result["relations"][0]
        self.assertEqual(relation["kind"], "portrayed_character")
        self.assertEqual(relation["role"], "饰演角色")
        self.assertEqual(relation["actor"]["id"], self.identity)
        self.assertEqual(relation["character_name"], "Role")
        self.assertIn("ref", relation)

    def test_detail_reads_only_work_referenced_characters_not_all_other_people(self):
        related_id = source_identity("bangumi", "character", "700")
        unrelated_character_id = source_identity("bangumi", "character", "701")
        unrelated_person_id = source_identity("tmdb", "person", "702")
        scope = {"provider": "bangumi", "subject_id": "100"}
        character = {"schema_version": 1, "id": related_id, "type": "character", "name": "Character",
            "voice_actors": [{"person_id": self.identity, "scope": scope, "role": "CV"}]}
        self.save_entity(character)
        self.save_entity({**character, "id": unrelated_character_id})
        self.save_entity({**self.person, "id": unrelated_person_id, "source_refs": []})
        self.record["people_refs"].append({"id": related_id, "type": "character", "role": "主角", "source": scope})
        self.source.write_text(dump_yaml_string([self.record]), encoding="utf-8")
        with patch.object(library, "_cached_entity", wraps=library._cached_entity) as cached, \
             patch.object(library, "_inventory", side_effect=AssertionError("detail must not scan every people profile")):
            result = self.detail()
        identities = {call.args[1] for call in cached.call_args_list}
        self.assertIn(related_id, identities)
        self.assertNotIn(unrelated_character_id, identities)
        self.assertNotIn(unrelated_person_id, identities)
        self.assertEqual([row["kind"] for row in result["relations"]], ["direct", "voice_actor"])

    def test_inventory_cache_reuses_parses_but_detects_same_size_same_mtime_edit(self):
        original_loader = library.load_yaml_string
        with patch.object(library, "load_yaml_string", wraps=original_loader) as parser:
            library.browse({}, **self.kw)
            first = parser.call_count
            library.browse({"page_size": 1}, **self.kw)
            self.assertEqual(parser.call_count, first)
            path = self.repo.path(self.identity)
            before_stat = path.stat()
            raw = path.read_bytes()
            self.assertIn(b"Original Name", raw)
            path.write_bytes(raw.replace(b"Original Name", b"Modified Name"))
            os.utime(path, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
            result = library.browse({}, **self.kw)
            self.assertEqual(parser.call_count, first + 1)
        self.assertEqual(result["items"][0]["name"], "Modified Name")

    def test_cached_rows_are_copy_isolated_and_memory_bound(self):
        result = library.browse({}, **self.kw)
        result["items"][0]["aliases"].append("Should not mutate cache")
        self.assertNotIn("Should not mutate cache", library.browse({}, **self.kw)["items"][0]["aliases"])
        with patch.object(library, "_MAX_CACHE_BYTES", 1):
            path = self.repo.path(self.identity)
            path.write_bytes(path.read_bytes() + b"\n# new cache key\n")
            library.browse({}, **self.kw)
        self.assertEqual(library._ENTITY_CACHE_BYTES, 0)
        self.assertEqual(len(library._ENTITY_CACHE), 0)

    def test_edit_preview_is_read_only_then_selected_fields_save_with_history(self):
        before = self.bytes()
        preview = self.preview(changes={"name_cn": "手工中文", "summary": "手工简介"})
        self.assertEqual(self.bytes(), before)
        result = self.apply(preview, ["name_cn"])
        saved = self.repo.read(self.identity)
        self.assertEqual(saved["name_cn"], "手工中文")
        self.assertEqual(saved["summary"], self.person["summary"])
        self.assertEqual(saved["source_refs"], self.person["source_refs"])
        self.assertEqual(saved["id"], self.identity)
        self.assertEqual(self.source.read_bytes(), before[0])
        self.assertTrue(list((self.people_root / "history").glob("*.yaml")))
        self.assertEqual(result["changed_fields"], ["name_cn"])

    def test_empty_selection_does_not_rewrite_comment_formatted_yaml(self):
        path = self.repo.path(self.identity)
        path.write_bytes(b"# preserve this comment\n" + path.read_bytes())
        before = self.bytes()
        self.apply(self.preview(changes={"name_cn": "Unused"}), [])
        self.assertEqual(self.bytes(), before)
        self.assertFalse((self.people_root / "history").exists())

    def test_manual_id_type_source_and_invalid_enum_date_edits_rejected(self):
        for changes in ({"id": source_identity("manual", "person", "x")}, {"type": "character"}, {"source_refs": []},
                        {"name": ""}, {"kind": "character"}, {"gender": "unvalidated"}, {"birth_date": {"year": 2023, "month": 2, "day": 30}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.preview(changes=changes)

    def test_character_editor_uses_independent_taxonomy_without_rewriting_source_kind(self):
        identity = source_identity("bangumi", "character", "88")
        self.save_entity({"schema_version": 1, "id": identity, "type": "character", "name": "Character", "kind": "character"})
        detail = self.detail(identity)
        fields = {row["field"]: row for row in detail["edit_fields"]}
        self.assertIn({"value": "mechanic", "label": "机体"}, fields["taxonomy"]["options"]["subtypes"]["object"])
        value = {"category": "object", "reality": "fictional", "subtype": "mechanic"}
        preview = library.edit_preview({"person_id": identity, "version": detail["version"], "changes": {"taxonomy": value}}, **self.kw)
        library.edit_apply({"person_id": identity, "preview_token": preview["preview_token"], "selected_fields": ["taxonomy"]}, **self.kw)
        self.assertEqual(self.repo.read(identity)["kind"], "character")
        self.assertEqual(self.repo.read(identity)["taxonomy"], value)

    def test_clear_preserves_shared_id_name_type_sources_relations_and_imported_values(self):
        before = self.bytes()
        preview = self.preview("clear")
        self.assertEqual(preview["selected_fields"], [])
        self.assertEqual(self.bytes(), before)
        self.apply(preview, ["summary", "aliases", "birth_date"])
        saved = self.repo.read(self.identity)
        self.assertEqual((saved["summary"], saved["aliases"], saved["birth_date"]), ("", [], {}))
        for field in ("id", "name", "type", "source_refs", "source_values"):
            self.assertEqual(saved[field], self.person[field])
        self.assertEqual(self.source.read_bytes(), before[0])

    def test_stale_detail_and_preview_versions_cannot_overwrite_concurrent_edits(self):
        detail = self.detail()
        preview = self.preview(changes={"name_cn": "Stale"})
        self.save_entity({**self.person, "name_cn": "Concurrent"})
        before = self.bytes()
        with self.assertRaises(ValueError):
            library.edit_preview({"person_id": self.identity, "version": detail["version"], "changes": {"name_cn": "Stale"}}, **self.kw)
        with self.assertRaises(ValueError):
            self.apply(preview)
        self.assertEqual(self.bytes(), before)

    def test_wrong_operation_database_identity_and_snapshot_tamper_rejected(self):
        preview = self.preview(changes={"name_cn": "Choice"})
        before = self.bytes()
        with self.assertRaises(ValueError):
            library.clear_apply({"person_id": self.identity, "preview_token": preview["preview_token"], "selected_fields": ["name_cn"]}, **self.kw)
        payload = providers._verify_token(preview["preview_token"])
        path = self.people_root / "sources" / "people-library" / (payload["snapshot_id"] + ".json")
        path.write_bytes(b"{}")
        with self.assertRaises(ValueError):
            self.apply(preview)
        self.assertEqual(self.bytes(), before)

    def test_bangumi_person_refresh_preserves_manual_defaults_and_offline_apply(self):
        raw = {"id": 11, "type": 1, "name": "Upstream Name", "name_cn": "来源中文", "summary": "来源简介",
               "career": ["actor"], "gender": "男", "infobox": [], "birth_year": 1980}
        before = self.bytes()
        with patch.object(providers, "_request_json", return_value=raw) as request:
            preview = self.preview("source", provider="bangumi")
        self.assertEqual(request.call_args.args[0], "/v0/persons/11")
        self.assertIn("localized.summary.und", preview["selected_fields"])
        self.assertEqual(self.bytes(), before)
        self.apply(preview, ["localized.summary.und", "localized.name.zh-Hans"])
        saved = self.repo.read(self.identity)
        self.assertEqual(saved["localized"]["summary"]["und"], "来源简介")
        self.assertEqual(saved["summary"], self.person["summary"])
        self.assertEqual(saved["name"], self.person["name"])
        self.assertEqual(saved["source_values"]["bangumi"]["localized.summary.und"], "来源简介")
        self.assertEqual(self.source.read_bytes(), before[0])

    def test_source_defaults_fill_empty_values_but_never_replace_populated_imports(self):
        self.person.update(name_cn="", summary="Imported summary")
        self.save_entity(self.person)
        raw = {"id": 11, "type": 1, "name": "Original Name", "name_cn": "补齐中文名",
               "summary": "New imported summary", "birth_year": 1990,
               "infobox": [{"key": "别名", "value": "New source alias"}]}
        before = self.bytes()
        with patch.object(providers, "_request_json", return_value=raw):
            preview = self.preview("source", provider="bangumi")
        rows = {row["field"]: row for row in preview["diff"]}
        self.assertEqual(set(preview["selected_fields"]), {"localized.name.zh-Hans", "localized.aliases.und-Latn", people_infobox.field_path("别名")})
        self.assertEqual(rows["localized.name.zh-Hans"]["merge_action"], "fill")
        self.assertFalse(rows["localized.name.zh-Hans"]["requires_confirmation"])
        self.assertEqual(rows["localized.summary.und-Latn"]["merge_action"], "same")
        self.assertFalse(rows["localized.summary.und-Latn"]["requires_confirmation"])
        self.assertEqual(rows["localized.name.und-Latn"]["merge_action"], "same")
        self.assertFalse(rows["localized.name.und-Latn"]["selectable"])
        self.assertEqual(rows["localized.aliases.und-Latn"]["after"], ["New source alias"])
        self.assertEqual(rows["birth_date"]["after"], {"year": 1990})
        self.assertEqual(rows["birth_date"]["merge_action"], "conflict")
        self.assertEqual(self.bytes(), before)
        self.apply(preview)
        saved = self.repo.read(self.identity)
        self.assertEqual(saved["name_cn"], "补齐中文名")
        self.assertEqual(saved["aliases"], ["Existing alias", "New source alias"])
        for field in ("summary", "birth_date"):
            self.assertEqual(saved[field], self.person[field])
        self.assertEqual(saved["infobox"], self.person["infobox"] + [{"key": "别名", "value": ["New source alias"]}])

    def test_source_diff_keeps_one_sided_local_values_and_autofills_missing_avatar(self):
        avatar = {"path": "source-avatar.png"}
        rows = {row["field"]: row for row in library._diff(self.person,
            {"summary": "", "aliases": [], "birth_date": {}, "avatar": avatar, "gender": "unknown"}, supplement=True)}
        for field in ("summary", "aliases", "birth_date", "gender"):
            self.assertEqual(rows[field]["merge_action"], "keep")
            self.assertFalse(rows[field]["selected"])
            self.assertFalse(rows[field]["selectable"])
            self.assertEqual(rows[field]["merged_value"], self.person[field])
        self.assertEqual(rows["avatar"]["merge_action"], "fill")
        self.assertTrue(rows["avatar"]["selected"])

    def test_source_autofill_can_be_deselected_but_confirmed_source_version_is_retained(self):
        self.person["summary"] = ""
        self.save_entity(self.person)
        before = self.bytes()
        history_before = {path.name: path.read_bytes() for path in (self.people_root / "history").glob("*.yaml")}
        with patch.object(providers, "_request_json", return_value={"id": 11, "type": 1,
                "name": "Original Name", "summary": "Source summary"}):
            preview = self.preview("source", provider="bangumi")
        self.assertEqual(preview["selected_fields"], ["localized.summary.und-Latn"])
        result = self.apply(preview, [])
        self.assertTrue(result["people_updated"])
        self.assertEqual(self.source.read_bytes(), before[0])
        saved = self.repo.read(self.identity)
        self.assertEqual(saved["summary"], "")
        self.assertEqual(saved["profile_variants"]["summary"]["und-Latn"]["bangumi"]["value"], "Source summary")
        self.assertNotIn("localized.summary.und-Latn", saved.get("field_sources", {}))

    def test_source_apply_cannot_select_empty_replacements_or_same_values(self):
        person, revision = library._read_state(self.repo, self.identity)
        preview = library._preview(person, revision, "source",
            {"summary": "", "aliases": [], "name": person["name"]}, self.repo)
        before = self.bytes()
        self.assertEqual(preview["selected_fields"], [])
        for field in ("summary", "aliases", "name"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.apply(preview, [field])
        self.assertEqual(self.bytes(), before)

    def test_bangumi_character_refresh_never_changes_voice_actor_links(self):
        identity = source_identity("bangumi", "character", "12")
        character = {"schema_version": 1, "id": identity, "type": "character", "kind": "character", "name": "Character",
            "source_refs": [{"provider": "bangumi", "entity_type": "character", "external_id": "12"}],
            "voice_actors": [{"person_id": self.identity, "scope": {"provider": "bangumi", "subject_id": "100"}, "role": "CV"}]}
        self.save_entity(character)
        detail = self.detail(identity)
        with patch.object(providers, "_request_json", return_value={"id": 12, "type": 1, "name": "Character", "summary": "Refresh"}) as request:
            preview = library.source_preview({"person_id": identity, "version": detail["version"], "provider": "bangumi"}, **self.kw)
        self.assertEqual(request.call_args.args[0], "/v0/characters/12")
        library.source_apply({"person_id": identity, "preview_token": preview["preview_token"], "selected_fields": ["localized.summary.und-Latn"]}, **self.kw)
        self.assertEqual(self.repo.read(identity)["voice_actors"], character["voice_actors"])

    def test_tmdb_person_refresh_targets_existing_uuid_without_work(self):
        raw = {"id": 99, "name": "Actor", "also_known_as": ["来源中文"], "biography": "TMDB biography", "gender": 2, "birthday": "1980-01-02"}
        with patch.object(tmdb_source, "_request_json", return_value=raw) as request:
            preview = self.preview("source", provider="tmdb", external_id="99")
        self.assertEqual(request.call_args.args[0], "/person/99")
        self.apply(preview)
        self.apply(self.preview(changes={"profile_preferences": {"summary": {"und-Latn": {"source": "tmdb"}}}}))
        self.assertEqual(self.repo.read(self.identity)["localized"]["summary"]["und-Latn"], "TMDB biography")
        self.assertEqual(build_source_index(self.repo).find("tmdb", "person", "99"), self.identity)
        self.assertFalse(self.repo.path(source_identity("tmdb", "person", "99")).exists())

    def test_source_identity_collision_and_wrong_api_identity_reject_without_write(self):
        before = self.bytes()
        with patch.object(providers, "_request_json", return_value={"id": 999, "type": 1}), self.assertRaises(ValueError):
            self.preview("source", provider="bangumi")
        self.assertEqual(self.bytes(), before)
        self.save_entity({**self.person, "id": source_identity("tmdb", "person", "99"), "source_refs": []})
        with patch.object(tmdb_source, "_request_json") as request, self.assertRaises(ValueError):
            self.preview("source", provider="tmdb", external_id=99)
        request.assert_not_called()

    def test_source_identity_appearing_after_preview_blocks_apply(self):
        with patch.object(tmdb_source, "_request_json", return_value={"id": 99, "name": "Actor", "biography": "New"}):
            preview = self.preview("source", provider="tmdb", external_id=99)
        self.save_entity({**self.person, "id": source_identity("tmdb", "person", "99"), "source_refs": []})
        before = self.bytes()
        with self.assertRaises(ValueError):
            self.apply(preview, ["summary"])
        self.assertEqual(self.bytes(), before)

    def test_wikipedia_explicit_page_stores_raw_and_simplified_evidence_applies_offline(self):
        profile = {"provider": "wikipedia", "external_id": "77", "fetched_at": "2026-10-07T00:00:00Z",
            "name": "한국 배우", "name_cn": "简体姓名", "aliases": ["原始繁體姓名"], "summary": "简体简介", "infobox": [],
            "source_url": "https://zh.wikipedia.org/wiki/Canonical_title", "source_metadata": {
                "page_title": "Canonical_title", "revision_id": 99, "display_variant": "zh-hans", "license": "CC BY-SA 4.0"},
            "raw": {"query": {"pages": [{"title": "原始標題", "revisions": [{"revid": 99}]}]}},
            "display_conversion": {"pairs": [["簡體", "简体"]]}, "warnings": []}
        with patch.object(wikipedia_people, "fetch_person_profile", return_value=profile) as request:
            preview = self.preview("source", provider="wikipedia", external_id="Explicit person title")
        self.assertEqual(request.call_args.args[0], "Explicit person title")
        payload = providers._verify_token(preview["preview_token"])
        plan = library._read_plan(payload["snapshot_id"], self.repo)
        self.assertEqual(plan["raw"]["raw"], profile["raw"])
        self.assertEqual(plan["raw"]["display_conversion"], profile["display_conversion"])
        with patch.object(wikipedia_people, "fetch_person_profile", side_effect=AssertionError("offline apply")):
            self.apply(preview, ["name_cn", "summary"])
        saved = self.repo.read(self.identity)
        self.assertEqual(saved["name_cn"], "简体姓名")
        self.assertEqual(saved["name"], self.person["name"])
        source = next(row for row in saved["source_refs"] if row["provider"] == "wikipedia")
        self.assertEqual(source["page_title"], "Canonical_title")
        self.assertEqual(source["display_variant"], "zh-hans")
        self.assertEqual(build_source_index(self.repo).find("wikipedia", "person", "77"), self.identity)

    def test_wikipedia_autofills_empty_fields_and_merges_aliases_without_confirmation(self):
        self.person.update(name_cn="", summary="")
        self.save_entity(self.person)
        profile = {"provider": "wikipedia", "external_id": "77", "fetched_at": "2026-10-07T00:00:00Z",
            "name": "Original Name", "name_cn": "维基中文", "aliases": ["维基别名"], "summary": "维基简介",
            "infobox": [], "source_url": "https://zh.wikipedia.org/wiki/Actor", "warnings": []}
        with patch.object(wikipedia_people, "fetch_person_profile", return_value=profile):
            preview = self.preview("source", provider="wikipedia", external_id="77")
        self.assertEqual(preview["selected_fields"], ["name_cn", "summary", "aliases"])
        aliases = next(row for row in preview["diff"] if row["field"] == "aliases")
        self.assertEqual(aliases["merge_action"], "merge")
        self.assertEqual(aliases["after"], ["维基别名"])
        self.assertEqual(aliases["merged_value"], ["Existing alias", "维基别名"])
        self.assertTrue(aliases["selected"])
        self.apply(preview)
        saved = self.repo.read(self.identity)
        self.assertEqual(saved["name_cn"], "维基中文")
        self.assertEqual(saved["summary"], "维基简介")
        self.assertEqual(saved["aliases"], ["Existing alias", "维基别名"])
        with patch.object(wikipedia_people, "fetch_person_profile", return_value=profile):
            preview = self.preview("source", provider="wikipedia")
        self.assertEqual(preview["selected_fields"], [])
        self.apply(preview)
        self.assertEqual(self.repo.read(self.identity)["aliases"], ["Existing alias", "维基别名"])


if __name__ == "__main__":
    unittest.main()
