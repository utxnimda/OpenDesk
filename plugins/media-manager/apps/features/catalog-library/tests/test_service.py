from __future__ import annotations

from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
import os
from unittest.mock import patch

from catalog_library import playback, service
from collection_detail import save
from work_catalog_yaml.jp_tv.browse_settings import JpTvBrowseSettings
from work_catalog_yaml.yaml_io import dump_yaml_string, load_yaml_string


def work(name, start="20111001", path="Z:/Offline/Shared"):
    return {"attributes": [{"type": "name", "data": name}, {"type": "country", "data": "japan"},
            {"type": "date", "data": {"start": start, "end": ""}},
            {"type": "collection-type", "data": {"domain": "animation", "release_type": "tv", "path": path,
             "collectioned": [{"press_format": "BDRip", "press_group": "", "press_path": "Shared_BDRip"}]}}]}


class CatalogLibraryServiceTest(unittest.TestCase):
    def test_browse_press_summary_reuses_authoritative_rows_and_paths_without_disk_probes(self):
        collection = self.raw[0]["attributes"][-1]["data"]
        collection["collectioned"] = [
            {"press_format": "BDRip", "press_group": "VCB", "press_path": "First_BDRip"},
            {"press_format": "BDRip", "press_group": "VCB", "press_path": "Second_BDRip"},
            {"press_format": "R2Jraw", "press_group": "----", "press_path": "Custom_R2Jraw"},
        ]
        collection["continuations"] = [{"title": "Extra", "collectioned": [
            {"press_format": "1080p", "press_group": "", "press_path": "Extra_1080p"}]}]
        self.source.write_text(dump_yaml_string(self.raw), encoding="utf-8")
        before = self.source.read_bytes()
        paths = set(self.root.rglob("*"))
        with patch("collection_detail.catalog_edit_service.resolve_record_directory", side_effect=AssertionError("must remain offline")):
            page = self.call("browse")
        summary = next(row for row in page["items"] if row["name"] == "Fate/Zero I")
        self.assertEqual(summary["press_count"], 4)
        self.assertEqual(summary["press_summary"], [
            {"press_format": "BDRip", "press_group": "VCB", "press_path": "First_BDRip"},
            {"press_format": "BDRip", "press_group": "VCB", "press_path": "Second_BDRip"},
            {"press_format": "R2Jraw", "press_group": "----", "press_path": "Custom_R2Jraw"},
            {"press_format": "1080p", "press_group": "", "press_path": "Extra_1080p"},
        ])
        summary["press_summary"][0]["press_format"] = "Changed"
        summary["press_summary"][0]["press_path"] = "Changed"
        refreshed = next(row for row in self.call("browse")["items"] if row["name"] == "Fate/Zero I")
        self.assertEqual(refreshed["press_summary"][0]["press_format"], "BDRip")
        self.assertEqual(refreshed["press_summary"][0]["press_path"], "First_BDRip")
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(set(self.root.rglob("*")), paths)

    def test_browse_press_summary_leaves_missing_paths_empty(self):
        item = {"record": work("Without a path"), "work_key": "test#0", "press": [
            {"press_format": "BDRip", "press_group": "VCB"},
            {"press_format": "DVDRip", "press_group": "", "press_path": None},
        ]}
        with patch.object(Path, "stat", side_effect=AssertionError("must not probe media")), \
             patch.object(Path, "resolve", side_effect=AssertionError("must not resolve media")):
            summary = service._item(item, {})
        self.assertEqual([row["press_path"] for row in summary["press_summary"]], ["", ""])

    def test_detail_projects_lexical_work_directory_without_probing_media(self):
        ref = service._records(self.settings)[0]["ref"]
        with patch("collection_detail.catalog_edit_service.resolve_record_directory", return_value=Path("Z:/Offline/Shared")) as resolve:
            result = service.detail({"ref": ref}, settings=self.settings, data_root=self.library)
        self.assertEqual(result["work_directory"], str(Path("Z:/Offline/Shared")))
        resolve.assert_called_once()
        with patch("collection_detail.catalog_edit_service.resolve_record_directory", side_effect=ValueError("not configured")):
            result = service.detail({"ref": ref}, settings=self.settings, data_root=self.library)
        self.assertEqual(result["work_directory"], "")

    def test_detail_resolves_independent_local_character_and_cv_without_network(self):
        from catalog_library import providers
        from people_catalog.repository import PeopleRepository
        people_root = self.root / "people"
        repo = PeopleRepository(people_root)
        character_id, person_id = "person_" + "1" * 32, "person_" + "2" * 32
        scope = {"provider": "bangumi", "subject_id": "12"}
        entities = [{"id": person_id, "schema_version": 1, "type": "person", "name": "Local CV"},
                    {"id": character_id, "schema_version": 1, "type": "character", "name": "Local Character",
                     "voice_actors": [{"person_id": person_id, "scope": scope, "role": "CV"}]}]
        repo.apply_batch(entities, expected_revision=repo.revision([character_id, person_id]))
        self.raw[0]["people_refs"] = [{"id": character_id, "type": "character", "role": "主角", "source": scope}]
        self.source.write_text(dump_yaml_string(self.raw), encoding="utf-8")
        before = self.source.read_bytes()
        ref = service._records(self.settings)[0]["ref"]
        with patch.object(providers, "_request_json", side_effect=AssertionError("local view must not call providers")):
            first = service.detail({"ref": ref}, settings=self.settings, data_root=self.library, people_root=people_root)
            entities[0]["name"] = "Updated CV"
            repo.apply_batch([entities[0]], expected_revision=repo.revision([person_id]))
            second = service.detail({"ref": ref}, settings=self.settings, data_root=self.library, people_root=people_root)
        self.assertEqual(first["people"]["characters"][0]["actors"][0]["name"], "Local CV")
        self.assertEqual(second["people"]["characters"][0]["actors"][0]["name"], "Updated CV")
        self.assertEqual(self.source.read_bytes(), before)
        self.assertNotIn("name", second["record"]["people_refs"][0])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "db"
        self.db.mkdir()
        self.source = self.db / "[JP][TVInfo][2011].yaml"
        self.raw = [work("Fate/Zero I"), work("Fate/Zero II", "20120401"), work("Unknown", "")]
        self.source.write_text(dump_yaml_string(self.raw), encoding="utf-8")
        self.library = self.root / "library"
        self.settings = JpTvBrowseSettings(version=1, filesystem_root=self.db, resolved_default_readable=str(self.source),
            resolved_catalog_yaml_paths=(str(self.source),), enum_options={}, enum_labels=MappingProxyType({"domain": MappingProxyType({"animation": "动画"})}),
            enum_section_labels={}, app_features=())
        with service._CACHE_LOCK:
            service._CACHE.clear()

    def call(self, name, body=None):
        return getattr(service, name)(body, settings=self.settings, data_root=self.library)

    def records(self):
        return load_yaml_string(self.source.read_text(encoding="utf-8"))

    def install_playback_cache(self):
        identity = {"press_path": "Shared_BDRip", "press_format": "BDRip", "press_group": "",
                    "segment": "main", "continuation_index": None}
        key = playback._hash(identity)
        root = self.root / "offline-media"
        self.raw[0]["attributes"][-1]["data"]["path"] = str(root)
        self.raw[0]["metadata"] = {"episodes": [{"id": "bangumi:1", "ep": 1, "type": 0, "title": "One"}]}
        self.raw[0]["local_playback"] = {"version": 1, "selected_resource_key": key, "resources": {
            key: {"identity": identity, "assignments": {}, "scan": {
                "root_path": str(root / identity["press_path"]), "root_identity": [1, 2, 3],
                "scanned_at": "2026-10-05T00:00:00+00:00", "files": [
                    {"relative_path": "01.mkv", "episode": "01", "size": 10, "mtime_ns": 20,
                     "signature": [1, 2, 3, 10, 20], "duration_seconds": 60.5}]}}}}
        self.source.write_text(dump_yaml_string(self.raw), encoding="utf-8")
        return key, root

    def first_detail(self):
        return self.call("detail", {"ref": service._records(self.settings)[0]["ref"]})

    def test_detail_reads_selected_playback_cache_without_media_io(self):
        key, media_root = self.install_playback_cache()
        before = self.source.read_bytes()
        with patch.object(playback.media_files, "scan_media_files", side_effect=AssertionError("must not scan")), \
             patch.object(playback.media_files, "probe_media_duration", side_effect=AssertionError("must not probe")), \
             patch.object(playback.media_files, "duration_available", side_effect=AssertionError("must not find tools")), \
             patch.object(playback, "ordinary_directory", side_effect=AssertionError("must not inspect media")):
            detail = self.first_detail()
        self.assertFalse(media_root.exists())
        self.assertEqual(detail["playback"]["resource_key"], key)
        self.assertEqual(detail["playback"]["ref"], detail["ref"])
        self.assertEqual(detail["playback"]["chapters"][0]["duration_seconds"], 60.5)
        self.assertEqual(detail["playback"]["chapters"][0]["status"], "automatic")
        self.assertTrue(detail["resources"][0]["is_playback_resource"])
        self.assertEqual(detail["resources"][0]["numbering_mode"], "local")
        self.assertEqual(self.source.read_bytes(), before)

    def test_detail_projects_both_numberings_without_modifying_source_or_accessing_media(self):
        key, _root = self.install_playback_cache()
        episodes = [{"id": "bangumi:207143", "ep": 1, "sort": 14, "type": 0, "name": "さようなら"},
                    {"id": "bangumi:207144", "ep": 2, "sort": 15, "type": 0, "name": "Chapter 15"}]
        self.raw[0]["metadata"]["episodes"] = episodes
        self.raw[0]["local_playback"]["resources"][key]["numbering_mode"] = "continuous"
        self.source.write_text(dump_yaml_string(self.raw), encoding="utf-8")
        before = self.source.read_bytes()
        with patch.object(playback.media_files, "scan_media_files", side_effect=AssertionError("must not scan")), \
             patch.object(playback.media_files, "probe_media_duration", side_effect=AssertionError("must not probe")), \
             patch.object(playback, "ordinary_directory", side_effect=AssertionError("must not inspect media")):
            detail = self.first_detail()
        self.assertEqual(detail["chapter_numbering"], {"has_continuous": True, "local_start": "1", "continuous_start": "14"})
        self.assertEqual([row["local_number"] for row in detail["chapters"]], ["1", "2"])
        self.assertEqual([row["continuous_number"] for row in detail["chapters"]], ["14", "15"])
        self.assertEqual(detail["resources"][0]["numbering_mode"], "continuous")
        self.assertEqual(detail["record"]["metadata"]["episodes"], episodes)
        self.assertEqual(detail["metadata"]["episodes"], episodes)
        detail["chapters"][0]["name"] = "Edited view"
        self.assertEqual(detail["metadata"]["episodes"], episodes)
        self.assertEqual(self.source.read_bytes(), before)

    def test_detail_playback_selection_follows_resource_identity_after_reorder(self):
        key, _root = self.install_playback_cache()
        before = self.first_detail()["playback"]
        self.raw[0]["attributes"][-1]["data"]["collectioned"].insert(0, {
            "press_path": "Shared_WEBRip", "press_format": "WEBRip", "press_group": "Other"})
        self.source.write_text(dump_yaml_string(self.raw), encoding="utf-8")
        detail = self.first_detail()
        self.assertEqual(detail["playback"]["resource_key"], key)
        self.assertEqual(detail["playback"]["resource_index"], 1)
        self.assertEqual(detail["playback"]["revision"], before["revision"])
        self.assertNotEqual(detail["playback"]["ref"], before["ref"])
        self.assertEqual([row["is_playback_resource"] for row in detail["resources"]], [False, True])

    def test_detail_playback_cache_invalidates_changed_work_path_or_resource_identity(self):
        self.install_playback_cache()
        original = deepcopy(self.raw)
        for field in ("path", "press_path", "press_group", "press_format"):
            with self.subTest(field=field):
                self.raw = deepcopy(original)
                collection = self.raw[0]["attributes"][-1]["data"]
                if field == "path":
                    collection[field] = str(self.root / "another-offline-root")
                else:
                    collection["collectioned"][0][field] = "Changed"
                self.source.write_text(dump_yaml_string(self.raw), encoding="utf-8")
                before = self.source.read_bytes()
                detail = self.first_detail()
                self.assertIsNone(detail["playback"])
                self.assertFalse(any(row["is_playback_resource"] for row in detail["resources"]))
                self.assertTrue(any("播放缓存" in warning for warning in detail["warnings"]))
                self.assertEqual(self.source.read_bytes(), before)

    def test_legacy_assignment_only_cache_remains_unselected_with_lexical_root_projection(self):
        key, _root = self.install_playback_cache()
        state = self.raw[0]["local_playback"]
        del state["selected_resource_key"]
        del state["resources"][key]["scan"]
        state["resources"][key]["assignments"] = {"id:bangumi:1": "01.mkv"}
        self.source.write_text(dump_yaml_string(self.raw), encoding="utf-8")
        with patch("collection_detail.catalog_edit_service.resolve_record_directory", return_value=_root):
            detail = self.first_detail()
        self.assertEqual(detail["work_directory"], str(_root))
        self.assertIsNone(detail["playback"])
        self.assertFalse(detail["resources"][0]["is_playback_resource"])
        self.assertEqual(detail["record"]["local_playback"], state)

    def test_unavailable_playback_binding_does_not_break_work_detail(self):
        self.install_playback_cache()
        with patch("collection_detail.catalog_edit_service.resolve_record_directory", side_effect=ValueError("unavailable configuration")):
            detail = self.first_detail()
        self.assertEqual(detail["item"]["name"], "Fate/Zero I")
        self.assertIsNone(detail["playback"])
        self.assertFalse(detail["resources"][0]["is_playback_resource"])
        self.assertTrue(any("目录绑定不可用" in warning for warning in detail["warnings"]))

    def test_cached_state_is_pure_and_uses_explicit_lexical_work_root(self):
        _key, root = self.install_playback_cache()
        detail = self.first_detail()
        before = deepcopy(detail)
        with patch.object(Path, "stat", side_effect=AssertionError("must not stat")), \
             patch.object(Path, "lstat", side_effect=AssertionError("must not lstat")), \
             patch.object(Path, "open", side_effect=AssertionError("must not open")), \
             patch("collection_detail.catalog_edit_service.resolve_record_directory", side_effect=AssertionError("must not resolve")):
            self.assertEqual(playback.cached_state(detail, work_root=str(root)), detail["playback"])
            self.assertIsNone(playback.cached_state(detail))
            self.assertIsNone(playback.cached_state(detail, work_root=str(root / "different")))
        self.assertEqual(detail, before)

    def test_browse_and_detail_are_read_only_offline_and_keep_same_path_records(self):
        before = self.source.read_bytes()
        paths = set(self.root.rglob("*"))
        page = self.call("browse", {"page_size": 2})
        self.assertEqual(page["total"], 3)
        self.assertEqual(page["legacy_count"], 3)
        self.assertEqual(page["items"][0]["name"], "Fate/Zero II")
        self.assertEqual(page["items"][0]["cover_url"], "")
        self.assertNotIn("record", page["items"][0])
        self.assertNotIn("press", page["items"][0])
        detail = self.call("detail", {"id": page["items"][0]["id"], "ref": page["items"][0]["ref"]})
        self.assertEqual(detail["resources"][0]["press_path"], "Shared_BDRip")
        self.assertEqual(detail["chapters"], [])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(set(self.root.rglob("*")), paths)

    def test_browse_projects_source_separated_ratings_without_network_or_writes(self):
        from catalog_library import providers
        douban = {"score": 8.1, "scale": 10, "votes": 12345,
                  "source_url": "https://movie.douban.com/subject/123/", "external_id": "123",
                  "fetched_at": "2026-10-06T12:00:00+00:00"}
        bangumi = {**douban, "score": 7.8, "source_url": "https://bangumi.tv/subject/456", "external_id": "456"}
        self.raw[0]["metadata"] = {"ratings": {"douban": douban, "bangumi": bangumi}}
        self.source.write_text(dump_yaml_string(self.raw), encoding="utf-8")
        before = self.source.read_bytes()
        with patch.object(providers, "_request_json", side_effect=AssertionError("rating cards are local projections")):
            page = self.call("browse", {"sort": "name"})
        item = next(item for item in page["items"] if item["name"] == "Fate/Zero I")
        self.assertEqual(item["ratings"], {"douban": douban, "bangumi": bangumi})
        self.assertNotIn("metadata", item)
        self.assertNotIn("record", item)
        self.assertEqual(next(item for item in page["items"] if item["name"] == "Unknown")["ratings"], {})
        item["ratings"]["douban"]["score"] = 1
        second = self.call("browse", {"search": "Fate/Zero I"})
        self.assertEqual(next(item for item in second["items"] if item["name"] == "Fate/Zero I")["ratings"]["douban"], douban)
        self.assertEqual(self.source.read_bytes(), before)

    def test_item_drops_invalid_legacy_ratings_without_losing_valid_sources(self):
        valid = {"score": 8.1, "scale": 10, "votes": 12345,
                 "source_url": "https://movie.douban.com/subject/123/", "external_id": "123",
                 "fetched_at": "2026-10-06T12:00:00+00:00"}
        for malformed in (None, [], "unknown", {"douban": {**valid, "score": "unknown"}},
                          {"douban": {**valid, "source_url": "https://external.test/123"}}):
            with self.subTest(malformed=malformed):
                record = {"metadata": {"summary": "仍可查看", "ratings": malformed}}
                before = deepcopy(record)
                result = service._item({"record": record, "work_key": "legacy-rating"}, {})
                self.assertEqual(result["ratings"], {})
                self.assertEqual(result["summary"], "仍可查看")
                self.assertTrue(any("评分" in warning for warning in result["warnings"]))
                self.assertEqual(record, before)
        record = {"metadata": {"ratings": {"douban": valid, "broken": {"score": 9}}}}
        before = deepcopy(record)
        result = service._item({"record": record, "work_key": "legacy-rating"}, {})
        self.assertEqual(result["ratings"], {"douban": valid})
        self.assertTrue(any("broken" in warning for warning in result["warnings"]))
        result["ratings"]["douban"]["score"] = 2
        self.assertEqual(record, before)

    def test_browse_caches_parse_but_invalidates_when_source_changes(self):
        with patch.object(service, "catalog_records", wraps=service.catalog_records) as reader:
            self.call("browse")
            self.call("browse", {"page": 2})
            self.assertEqual(reader.call_count, 1)
            self.raw.append(work("New"))
            self.source.write_text(dump_yaml_string(self.raw), encoding="utf-8")
            self.assertEqual(self.call("browse")["total"], 4)
            self.assertEqual(reader.call_count, 2)

    def test_query_and_pagination(self):
        self.assertEqual(self.call("browse", {"search": "fate/zero", "year": "2012"})["total"], 1)
        page = self.call("browse", {"page_size": 1, "page": 100, "sort": "name"})
        self.assertEqual(page["page"], 3)
        self.assertEqual(page["items"][0]["name"], "Unknown")
        for body in ({"page": True}, {"page_size": 0}, {"page_size": 101}, {"sort": "disk"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.call("browse", body)

    def test_equal_length_same_mtime_external_change_invalidates_cache(self):
        self.call("browse")
        timestamp = self.source.stat()
        self.source.write_bytes(self.source.read_bytes().replace(b"Unknown", b"Changed"))
        os.utime(self.source, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
        self.assertEqual(self.call("browse", {"search": "Changed"})["total"], 1)

    def test_catalog_membership_change_during_read_is_not_cached_under_old_paths(self):
        extra = self.db / "[JP][TVInfo][2012].yaml"
        original_reader = service.catalog_records

        def add_year_during_read(settings):
            extra.write_text(dump_yaml_string([work("Concurrent new file", "20120101")]), encoding="utf-8")
            return original_reader(settings)

        with patch.object(service, "catalog_records", side_effect=add_year_during_read):
            self.assertEqual(self.call("browse")["total"], 4)
        # Reverting the additional file must never retrieve its rows from a
        # cache entry fingerprinted before that file existed.
        extra.unlink()
        self.assertEqual(self.call("browse")["total"], 3)
        self.assertEqual(self.call("browse", {"search": "Concurrent new file"})["total"], 0)

    def test_legacy_identity_needs_current_version_and_rejects_boolean_index(self):
        item = self.call("browse")["items"][0]
        with self.assertRaises(ValueError):
            self.call("detail", {"id": item["id"]})
        with self.assertRaises(ValueError):
            self.call("detail", {"ref": {**item["ref"], "index_in_file": True}})
        self.source.write_text(dump_yaml_string(list(reversed(self.raw))), encoding="utf-8")
        first = self.call("browse")["items"][0]
        # Pick a moved record, not the middle record which happens to stay put.
        with self.assertRaises(ValueError):
            self.call("detail", {"ref": {**item["ref"], "index_in_file": 0}})

    def test_empty_identity_selection_does_not_hide_remaining_legacy_records(self):
        self.assertEqual(self.call("preview_identity_initialization", {"refs": []})["remaining"], 3)

    def test_nonstandard_legacy_extensions_have_safe_views_and_unchanged_raw_records(self):
        self.raw[0].update({"metadata": {"aliases": [1, "Alias"], "summary": {"custom": "old"},
                                       "episodes": [None, {"title": "Valid chapter"}]},
                            "source_refs": None,
                            "classifications": [{"type": "series", "value_id": "classification_" + "b" * 32, "order": "first"}]})
        self.raw[1].update({"metadata": None, "source_refs": [1, {"provider": "custom", "external_id": "1"}]})
        self.raw[2]["classifications"] = None
        self.source.write_text(dump_yaml_string(self.raw), encoding="utf-8")
        before = self.source.read_bytes()
        page = self.call("browse", {"search": "Alias", "sort": "series_order", "classification": "classification_" + "b" * 32})
        self.assertEqual(page["total"], 1)
        item = page["items"][0]
        self.assertEqual(item["aliases"], ["Alias"])
        self.assertEqual(item["summary"], "")
        self.assertEqual(item["source_count"], 0)
        self.assertNotIn("order", item["classifications"][0])
        self.assertTrue(item["warnings"])
        detail = self.call("detail", {"ref": item["ref"]})
        self.assertEqual(detail["record"], self.raw[0])
        self.assertEqual(detail["chapters"], [{"title": "Valid chapter", "local_number": "1",
                                             "continuous_number": "", "has_continuous_number": False}])
        self.assertEqual(detail["sources"], [])
        self.assertTrue(detail["warnings"])
        self.assertEqual(self.call("browse")["total"], 3)
        self.assertEqual(self.source.read_bytes(), before)

    def test_classification_directory_junctions_are_rejected_before_read_or_write(self):
        # Mock reparse detection so this safety test also runs on hosts without
        # permission to create Windows symlinks/junctions.
        with patch.object(Path, "is_junction", lambda path: path == self.library / "db", create=True):
            with self.assertRaisesRegex(ValueError, "符号链接或联接"):
                self.call("classifications_payload")
            with self.assertRaisesRegex(ValueError, "符号链接或联接"):
                self.call("save_classification", {"revision": "", "name": "Unsafe"})
        revision = self.call("classifications_payload")["revision"]
        with patch.object(Path, "is_junction", lambda path: path == self.library / "history", create=True):
            with self.assertRaisesRegex(ValueError, "符号链接或联接"):
                self.call("save_classification", {"revision": revision, "name": "Unsafe"})
        self.assertFalse(self.library.exists())

    def test_classification_parent_regular_file_is_rejected(self):
        self.library.mkdir()
        (self.library / "db").write_text("not a directory", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不是普通目录"):
            self.call("classifications_payload")

    def test_unknown_dates_do_not_use_storage_year_as_actual_date(self):
        page = self.call("browse", {"sort": "date_asc"})
        unknown = page["items"][-1]
        self.assertEqual(unknown["name"], "Unknown")
        self.assertEqual(unknown["begin_date"], "")
        self.assertEqual(unknown["year"], "2011")

    def test_explicit_identity_preview_then_shared_apply_with_history(self):
        before = self.source.read_bytes()
        paths = set(self.root.rglob("*"))
        preview = self.call("preview_identity_initialization")
        self.assertEqual(len(preview["edits"]), 3)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(set(self.root.rglob("*")), paths)
        with patch.object(service, "apply_catalog_edits", wraps=service.apply_catalog_edits) as writer:
            result = self.call("apply_edits", {"edits": preview["edits"]})
            writer.assert_called_once()
        self.assertTrue(result["db_committed"])
        self.assertEqual([item["id"] for item in self.records()], [item["record"]["id"] for item in preview["edits"]])
        self.assertEqual(self.call("browse")["legacy_count"], 0)
        self.assertEqual(self.call("preview_identity_initialization")["edits"], [])
        self.assertTrue(list((self.root / "History").glob("*.yaml")))

    def test_stable_id_survives_reorder_and_raw_legacy_editor_preserves_extensions(self):
        preview = self.call("preview_identity_initialization")
        self.call("apply_edits", {"edits": preview["edits"]})
        current = self.records()
        identity = current[0]["id"]
        current.reverse()
        self.source.write_text(dump_yaml_string(current), encoding="utf-8")
        detail = self.call("detail", {"id": identity})
        self.assertEqual(detail["item"]["name"], "Fate/Zero I")
        raw = deepcopy(detail["record"])
        raw.pop("id")
        raw.pop("schema_version")
        normalized = save.normalize_raw_record(raw, previous=detail["record"])
        self.assertEqual(normalized["id"], identity)
        with self.assertRaisesRegex(ValueError, "不允许更改"):
            save.normalize_raw_record({**raw, "id": "work_" + "f" * 32}, previous=detail["record"])

    def test_old_table_and_raw_saves_preserve_new_metadata_fields(self):
        extended = deepcopy(self.raw[0])
        extended.update({"id": "work_" + "a" * 32, "schema_version": 1,
                         "metadata": {"summary": "User-written", "aliases": ["Zero"]},
                         "source_refs": [{"provider": "bangumi", "external_id": "100"}],
                         "classifications": [{"type": "series", "value_id": "classification_" + "b" * 32}]})
        self.source.write_text(dump_yaml_string([extended, *self.raw[1:]]), encoding="utf-8")
        detail = self.call("detail", {"id": extended["id"]})
        save.browse_save_yaml_from_ui_body({"rows": [{**detail["ref"], "name": "Table edit"}]}, settings=self.settings)
        after = self.records()[0]
        for key in ("id", "schema_version", "metadata", "source_refs", "classifications"):
            self.assertEqual(after[key], extended[key])
        current = self.call("detail", {"id": extended["id"]})
        raw = deepcopy(current["record"])
        for key in ("id", "schema_version", "metadata", "source_refs", "classifications"):
            raw.pop(key)
        raw["attributes"][0]["data"] = "Raw edit"
        save.browse_save_yaml_from_ui_body({"rows": [{**current["ref"], "raw_record": raw}]}, settings=self.settings)
        after = self.records()[0]
        for key in ("id", "schema_version", "metadata", "source_refs", "classifications"):
            self.assertEqual(after[key], extended[key])

    def test_classification_lifecycle_and_membership_are_separate(self):
        original = self.call("classifications_payload")
        created = self.call("save_classification", {"revision": original["revision"], "name": "Fate/Zero"})
        classification = created["item"]
        refs = [item["ref"] for item in self.call("browse", {"search": "Fate/Zero"})["items"]]
        preview = self.call("preview_classification_edits", {"refs": refs, "value_id": classification["id"], "action": "assign", "order": 1})
        self.call("apply_edits", {"edits": preview["edits"]})
        grouped = self.call("browse", {"classification": classification["id"], "sort": "series_order"})
        self.assertEqual(grouped["total"], 2)
        before = self.source.read_bytes()
        renamed = self.call("save_classification", {"revision": created["revision"], "id": classification["id"], "name": "Fate Zero"})
        self.assertEqual(renamed["item"]["id"], classification["id"])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertTrue(list((self.library / "history").glob("*.json")))
        preview = self.call("preview_classification_edits", {"refs": [grouped["items"][0]["ref"]], "value_id": classification["id"], "action": "remove"})
        self.call("apply_edits", {"edits": preview["edits"]})
        self.assertEqual(self.call("browse", {"classification": classification["id"]})["total"], 1)
        self.assertEqual(self.call("browse")["total"], 3)

    def test_classification_revision_and_duplicate_name_are_checked(self):
        revision = self.call("classifications_payload")["revision"]
        created = self.call("save_classification", {"revision": revision, "name": "Series"})
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.call("save_classification", {"revision": revision, "name": "Another"})
        with self.assertRaisesRegex(ValueError, "同名"):
            self.call("save_classification", {"revision": created["revision"], "name": " SERIES "})

    def test_stale_preview_and_duplicate_identity_are_rejected(self):
        preview = self.call("preview_identity_initialization")
        self.raw[0]["attributes"][0]["data"] = "Changed"
        self.source.write_text(dump_yaml_string(self.raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "变化"):
            self.call("apply_edits", {"edits": preview["edits"]})
        preview = self.call("preview_identity_initialization")
        preview["edits"][1]["record"]["id"] = preview["edits"][0]["record"]["id"]
        with self.assertRaisesRegex(ValueError, "已被其他"):
            self.call("apply_edits", {"edits": preview["edits"]})

    def test_legacy_new_row_gets_id_and_cannot_clone_an_existing_id(self):
        save.browse_save_yaml_from_ui_body({"new_rows": [{"raw_record": work("New")} ]}, settings=self.settings)
        new = self.records()[-1]
        self.assertRegex(new["id"], r"^work_[0-9a-f]{32}$")
        self.assertEqual(new["schema_version"], 1)
        with self.assertRaisesRegex(ValueError, "重复"):
            save.browse_save_yaml_from_ui_body({"new_rows": [{"raw_record": new}]}, settings=self.settings)


if __name__ == "__main__":
    unittest.main()
