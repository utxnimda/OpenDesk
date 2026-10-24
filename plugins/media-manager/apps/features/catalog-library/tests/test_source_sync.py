"""Multi-source refresh fixtures never contact providers or a real workspace."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from catalog_library import providers, service, source_sync, source_bindings, tmdb_source, wikipedia_source
from collection_detail.catalog_edit_service import catalog_records
from work_catalog_yaml.yaml_io import dump_yaml_string
import test_catalog_library_external_providers as fixtures


class SourceSyncTest(unittest.TestCase):
    ref = fixtures.ExternalProviderPipelineTests.ref

    def setUp(self):
        fixtures.ExternalProviderPipelineTests.setUp(self)
        self.bindings = [
            {"provider": "tmdb", "external_id": "50", "season_number": 2, "scope": "season:2"},
            {"provider": "wikipedia", "external_id": "82", "scope": ""},
        ]
        self.save_sources(self.bindings)

    def current(self):
        return catalog_records(self.settings)[0]["record"]

    def save_sources(self, refs):
        record = self.current()
        record["source_refs"] = deepcopy(refs)
        self.file.write_text(dump_yaml_string([record]), encoding="utf-8")

    def preview(self, **body):
        with patch.object(tmdb_source, "fetch_snapshot", return_value=deepcopy(self.tmdb)), \
             patch.object(wikipedia_source, "fetch_snapshot", return_value=deepcopy(self.wiki)), \
             patch.object(tmdb_source, "acquire_cover", side_effect=ValueError("fixture no cover")):
            return source_sync.preview({"ref": self.ref(), **body}, settings=self.settings, source_root=self.sources)

    def apply(self, preview, fields=None, **body):
        return source_sync.apply({"ref": preview["ref"], "preview_token": preview["preview_token"],
            "selected_fields": preview["selected_fields"] if fields is None else fields, **body},
            settings=self.settings, source_root=self.sources, data_root=self.root / "library")

    def test_preview_is_read_only_short_signed_manifest_and_manual_values_unselected(self):
        before = self.file.read_bytes()
        preview = self.preview()
        self.assertEqual(self.file.read_bytes(), before)
        self.assertLess(len(preview["preview_token"]), 8192)
        self.assertEqual([row["status"] for row in preview["sources"]], ["ok", "ok"])
        rows = {row["field"]: row for row in preview["diff"]}
        for field in ("name", "metadata.summary", "metadata.episodes", "air_date_start"):
            self.assertFalse(rows[field]["default_selected"])
        self.assertEqual(rows["name"]["provider"], "tmdb")
        self.assertEqual(rows["metadata.aliases"]["binding_id"], "all")

    def test_all_fields_commit_once_offline_and_keep_both_bindings(self):
        preview = self.preview()
        fields = ["metadata.summary", "metadata.aliases", "metadata.episodes", "air_date_end"]
        wiki = next(row for row in preview["sources"] if row["provider"] == "wikipedia")
        with patch.object(service, "apply_edits", wraps=service.apply_edits) as writer, \
             patch.object(tmdb_source, "fetch_snapshot", side_effect=AssertionError("offline confirm")), \
             patch.object(wikipedia_source, "fetch_snapshot", side_effect=AssertionError("offline confirm")):
            result = self.apply(preview, fields, field_choices={"metadata.summary": wiki["binding_id"]})
        self.assertTrue(result["db_committed"])
        self.assertEqual(writer.call_count, 1)
        record = self.current()
        self.assertEqual(record["metadata"]["summary"], "中文简介")
        self.assertEqual(record["metadata"]["episodes"][0]["id"], "tmdb:511")
        self.assertTrue({"手写别名", "中文", "维基别名", "中文条目", "원작"}.issubset(record["metadata"]["aliases"]))
        self.assertEqual(providers._field(record, "name"), "Local title")
        self.assertEqual({row["provider"] for row in record["source_refs"]}, {"tmdb", "wikipedia"})

    def test_one_source_failure_preserves_it_and_does_not_block_other(self):
        before = deepcopy(self.current()["source_refs"][0])
        with patch.object(tmdb_source, "fetch_snapshot", side_effect=ValueError("TMDB unavailable")), \
             patch.object(wikipedia_source, "fetch_snapshot", return_value=deepcopy(self.wiki)):
            preview = source_sync.preview({"ref": self.ref()}, settings=self.settings, source_root=self.sources)
        self.assertEqual([row["status"] for row in preview["sources"]], ["error", "ok"])
        name = next(row for row in preview["diff"] if row["field"] == "name")
        self.assertFalse(name["default_selected"])
        self.apply(preview, ["metadata.summary"])
        current = next(row for row in self.current()["source_refs"] if row["provider"] == "tmdb")
        self.assertTrue(all(current[key] == value for key, value in before.items()))
        self.assertNotIn("snapshot_id", current)

    def test_all_failures_return_unapplicable_status_without_database_change(self):
        before = self.file.read_bytes()
        with patch.object(tmdb_source, "fetch_snapshot", side_effect=ValueError("unavailable")), \
             patch.object(wikipedia_source, "fetch_snapshot", side_effect=ValueError("unavailable")):
            preview = source_sync.preview({"ref": self.ref()}, settings=self.settings, source_root=self.sources)
        self.assertFalse(preview["can_apply"])
        self.assertIsNone(preview["preview_token"])
        self.assertEqual(preview["diff"], [])
        self.assertEqual(self.file.read_bytes(), before)

    def test_disabled_source_not_fetched_and_cannot_be_explicitly_selected(self):
        self.bindings[0]["enabled"] = False
        self.save_sources(self.bindings)
        with patch.object(tmdb_source, "fetch_snapshot", side_effect=AssertionError("disabled")):
            with patch.object(wikipedia_source, "fetch_snapshot", return_value=deepcopy(self.wiki)):
                result = source_sync.preview({"ref": self.ref()}, settings=self.settings, source_root=self.sources)
            self.assertEqual(len(result["sources"]), 1)
            with self.assertRaisesRegex(ValueError, "停用"):
                source_sync.preview({"ref": self.ref(), "binding_ids": [source_bindings.binding_id(self.bindings[0])]}, settings=self.settings)

    def test_nonempty_imported_field_difference_requires_confirmation(self):
        self.apply(self.preview(), ["metadata.summary"])
        self.tmdb["candidates"]["metadata.summary"] = "Updated provider text"
        preview = self.preview()
        self.assertNotIn("metadata.summary", preview["selected_fields"])
        record = self.current()
        record["metadata"]["summary"] = "Manual edit"
        self.file.write_text(dump_yaml_string([record]), encoding="utf-8")
        self.assertNotIn("metadata.summary", self.preview()["selected_fields"])
        with self.assertRaisesRegex(ValueError, "变化"):
            self.apply(preview, ["metadata.summary"])

    def test_cleared_imported_field_is_preselected_and_can_be_unchecked(self):
        self.apply(self.preview(), ["metadata.summary"])
        record = self.current()
        record["metadata"]["summary"] = ""
        self.file.write_text(dump_yaml_string([record]), encoding="utf-8")
        preview = self.preview()
        row = next(row for row in preview["diff"] if row["field"] == "metadata.summary")
        self.assertFalse(row["locally_modified"])
        self.assertTrue(row["default_selected"])
        self.apply(preview, [])
        self.assertEqual(self.current()["metadata"]["summary"], "")
        self.apply(self.preview())
        self.assertEqual(self.current()["metadata"]["summary"], "TMDB 简介")

    def test_tampered_manifest_and_field_choice_rejected_without_write(self):
        preview = self.preview()
        before = self.file.read_bytes()
        with self.assertRaisesRegex(ValueError, "来源"):
            self.apply(preview, ["name"], field_choices={"name": "not-in-preview"})
        payload = providers._verify_token(preview["preview_token"])
        path = self.sources / "_sync" / (payload["snapshot_id"] + ".json")
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "校验"):
            self.apply(preview, [])
        self.assertEqual(self.file.read_bytes(), before)

    def test_child_snapshot_tampering_is_atomic(self):
        preview = self.preview()
        payload = providers._verify_token(preview["preview_token"])
        manifest = providers._read_snapshot(payload["snapshot_id"], payload["snapshot_sha256"], self.sources / "_sync")
        child = manifest["entries"][-1]["preview"]["snapshot_id"]
        (self.sources / (child + ".json")).write_bytes(b"changed")
        before = self.file.read_bytes()
        with patch.object(service, "apply_edits", side_effect=AssertionError("no partial write")), self.assertRaisesRegex(ValueError, "校验"):
            self.apply(preview, ["metadata.summary"])
        self.assertEqual(self.file.read_bytes(), before)

    def test_multiple_primary_chapter_conflicts_require_choice_not_last_wins(self):
        self.bindings.append({"provider": "tmdb", "external_id": "51", "season_number": 2, "scope": "season:2"})
        self.save_sources(self.bindings)
        other = deepcopy(self.tmdb)
        other["subject_id"] = 51
        other["candidates"]["metadata.episodes"][0]["id"] = "tmdb:other"
        with patch.object(tmdb_source, "fetch_snapshot", side_effect=lambda identity, **_kw: deepcopy(self.tmdb if identity == 50 else other)), \
             patch.object(wikipedia_source, "fetch_snapshot", return_value=deepcopy(self.wiki)), \
             patch.object(tmdb_source, "acquire_cover", side_effect=ValueError("none")):
            preview = source_sync.preview({"ref": self.ref()}, settings=self.settings, source_root=self.sources)
        chapters = next(row for row in preview["diff"] if row["field"] == "metadata.episodes")
        self.assertEqual(len(chapters["choices"]), 2)
        self.assertFalse(chapters["default_selected"])
        chosen = next(row for row in preview["sources"] if row["external_id"] == "51")
        self.apply(preview, ["metadata.episodes"], field_choices={"metadata.episodes": chosen["binding_id"]})
        self.assertEqual(self.current()["metadata"]["episodes"][0]["id"], "tmdb:other")
        self.assertEqual(len(self.current()["source_refs"]), 3)

    def test_budget_exhaustion_does_not_start_more_network_work(self):
        with patch.object(source_sync, "TOTAL_NETWORK_BUDGET", -1), \
             patch.object(providers, "preview_provider_import", side_effect=AssertionError("no network")):
            result = source_sync.preview({"ref": self.ref()}, settings=self.settings, source_root=self.sources)
        self.assertFalse(result["can_apply"])
        self.assertEqual(len(result["sources"]), 2)

    def test_replacement_keeps_slot_drops_old_target_and_preserves_other_sources(self):
        identity = source_bindings.binding_id(self.bindings[0])
        other = deepcopy(self.tmdb)
        other["subject_id"] = 99
        with patch.object(tmdb_source, "fetch_snapshot", return_value=other), \
             patch.object(tmdb_source, "acquire_cover", side_effect=ValueError("none")):
            preview = providers.preview_provider_import({"ref": self.ref(), "provider": "tmdb", "subject_id": 99,
                "season_number": 2, "replace_binding_id": identity}, settings=self.settings, source_root=self.sources)
        result = providers.apply_provider_import({"ref": preview["ref"], "provider": "tmdb", "snapshot_id": preview["snapshot_id"],
            "preview_token": preview["preview_token"], "selected_fields": []}, settings=self.settings, source_root=self.sources)
        self.assertTrue(result["db_committed"])
        refs = self.current()["source_refs"]
        self.assertEqual(len(refs), 2)
        replacement = next(row for row in refs if row["binding_id"] == identity)
        self.assertEqual(replacement["external_id"], "99")
        self.assertEqual(replacement["title"], "원작")
        self.assertFalse(any(row["external_id"] == "50" for row in refs))
        self.assertEqual(self.current()["metadata"]["summary"], "My description")

    def test_missing_replacement_or_bad_role_rejected_before_network(self):
        for extra in ({"replace_binding_id": "missing"}, {"binding_role": []}):
            with patch.object(tmdb_source, "fetch_snapshot", side_effect=AssertionError("no network")), self.assertRaises(ValueError):
                providers.preview_provider_import({"ref": self.ref(), "provider": "tmdb", "subject_id": 50, "season_number": 2, **extra}, settings=self.settings)

    def test_first_korean_primary_binding_retains_pending_wikipedia_default(self):
        record = self.current()
        record.pop("source_refs", None)
        for attr in record["attributes"]:
            if attr["type"] == "country":
                attr["data"] = "korea"
            if attr["type"] == "collection-type":
                attr["data"]["domain"] = "television"
        self.file.write_text(dump_yaml_string([record]), encoding="utf-8")
        with patch.object(tmdb_source, "fetch_snapshot", return_value=deepcopy(self.tmdb)), \
             patch.object(tmdb_source, "acquire_cover", side_effect=ValueError("none")):
            preview = providers.preview_provider_import({"ref": self.ref(), "provider": "tmdb", "subject_id": 50,
                "season_number": 2, "binding_role": "primary"}, settings=self.settings, source_root=self.sources)
        providers.apply_provider_import({"ref": preview["ref"], "provider": "tmdb", "snapshot_id": preview["snapshot_id"],
            "preview_token": preview["preview_token"], "selected_fields": []}, settings=self.settings, source_root=self.sources)
        policy = source_bindings.source_policy(self.current())
        self.assertFalse(policy["custom"])
        self.assertEqual(policy["missing_providers"], ["wikipedia"])

    def test_alias_preview_merges_every_source_for_each_name_candidate(self):
        original = self.file.read_bytes()
        for name_source in ("tmdb", "wikipedia"):
            with self.subTest(name=name_source):
                self.file.write_bytes(original)
                preview = self.preview()
                by_provider = {row["provider"]: row["binding_id"] for row in preview["sources"]}
                name_id = by_provider[name_source]
                row = next(row for row in preview["diff"] if row["field"] == "metadata.aliases")
                self.assertTrue(row["default_selected"])
                self.assertEqual(len(row["choices"]), 1)
                candidate = row["choices"][0]
                self.assertEqual(candidate["binding_id"], "all")
                expected = candidate["after_by_name_binding"][name_id]
                result = self.apply(preview, ["name", "metadata.aliases"], field_choices={"name": name_id})
                self.assertEqual(self.current()["metadata"]["aliases"], expected)
                self.assertTrue({"手写别名", "中文", "维基别名"}.issubset(expected))
                self.assertEqual(result["automatic_aliases_added"], preview["automatic_aliases_by_name_binding"][name_id])

    def test_same_source_titles_do_not_leave_temporary_alias_when_later_source_renames(self):
        self.tmdb["candidates"]["name"] = "Same"
        self.wiki["candidates"]["name"] = "Same"
        for existing_provenance in (None, {"provider": "manual", "imported_value_sha256": "a" * 64}):
            with self.subTest(existing_provenance=existing_provenance):
                record = self.current()
                providers._set_field(record, "name", "Local title")
                record["metadata"]["aliases"] = ["手写别名"]
                if existing_provenance is None:
                    record["metadata"].setdefault("field_sources", {}).pop("metadata.aliases", None)
                else:
                    record["metadata"].setdefault("field_sources", {})["metadata.aliases"] = deepcopy(existing_provenance)
                self.file.write_text(dump_yaml_string([record]), encoding="utf-8")
                preview = self.preview()
                wiki_id = next(row["binding_id"] for row in preview["sources"] if row["provider"] == "wikipedia")
                self.assertEqual(preview["automatic_aliases_by_name_binding"][wiki_id], [])
                result = self.apply(preview, ["name"], field_choices={"name": wiki_id})
                current = self.current()
                self.assertEqual(providers._field(current, "name"), "Same")
                self.assertEqual(current["metadata"]["aliases"], ["手写别名"])
                self.assertEqual(result["automatic_aliases_added"], [])
                self.assertEqual(current["metadata"].get("field_sources", {}).get("metadata.aliases"), existing_provenance)

    def test_replacement_collision_is_not_silent_duplicate_or_partial_write(self):
        self.bindings.append({"provider": "tmdb", "external_id": "99", "season_number": 2, "scope": "season:2"})
        self.save_sources(self.bindings)
        identity = source_bindings.binding_id(self.bindings[0])
        other = deepcopy(self.tmdb)
        other["subject_id"] = 99
        with patch.object(tmdb_source, "fetch_snapshot", return_value=other), \
             patch.object(tmdb_source, "acquire_cover", side_effect=ValueError("none")):
            preview = providers.preview_provider_import({"ref": self.ref(), "provider": "tmdb", "subject_id": 99,
                "season_number": 2, "replace_binding_id": identity}, settings=self.settings, source_root=self.sources)
        before = self.file.read_bytes()
        with self.assertRaisesRegex(ValueError, "另一来源关联"):
            providers.apply_provider_import({"ref": preview["ref"], "provider": "tmdb", "snapshot_id": preview["snapshot_id"],
                "preview_token": preview["preview_token"], "selected_fields": []}, settings=self.settings, source_root=self.sources)
        self.assertEqual(self.file.read_bytes(), before)

    def test_refreshed_titles_are_persisted_and_empty_replacement_does_not_keep_old_title(self):
        self.apply(self.preview(), [])
        refs = self.current()["source_refs"]
        self.assertEqual({row["provider"]: row["title"] for row in refs}, {"tmdb": "원작", "wikipedia": "中文条目"})
        identity = next(row["binding_id"] for row in refs if row["provider"] == "tmdb")
        self.tmdb["candidates"]["name"] = ""
        self.apply(self.preview(), [])
        self.assertEqual(next(row["title"] for row in self.current()["source_refs"] if row["provider"] == "tmdb"), "원작")
        self.tmdb["subject_id"] = 99
        with patch.object(tmdb_source, "fetch_snapshot", return_value=deepcopy(self.tmdb)), \
             patch.object(tmdb_source, "acquire_cover", side_effect=ValueError("none")):
            preview = providers.preview_provider_import({"ref": self.ref(), "provider": "tmdb", "subject_id": 99,
                "season_number": 2, "replace_binding_id": identity}, settings=self.settings, source_root=self.sources)
        providers.apply_provider_import({"ref": preview["ref"], "provider": "tmdb", "snapshot_id": preview["snapshot_id"],
            "preview_token": preview["preview_token"], "selected_fields": []}, settings=self.settings, source_root=self.sources)
        self.assertEqual(next(row["title"] for row in self.current()["source_refs"] if row["provider"] == "tmdb"), "")


if __name__ == "__main__":
    unittest.main()
