from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collection_detail.catalog_edit_service import catalog_records
from directory_organizer.service import OrganizerService
from directory_organizer.execution import MediaExecution
from directory_organizer.snapshots import relative_path, snapshot
from work_catalog_yaml.common.record_merge import merge_confirmed_changes
from work_catalog_yaml.jp_tv.browse_settings import JpTvBrowseSettings
from work_catalog_yaml.yaml_io import dump_yaml_string


def record(name, root, releases):
    return {"custom_top": {"keep": True}, "attributes": [
        {"type": "name", "data": name}, {"type": "country", "data": "japan"},
        {"type": "date", "data": {"start": "20050101", "end": "20050331", "note": "keep"}},
        {"type": "collection-type", "data": {"domain": "animation", "release_type": "tv", "path": root.as_posix(),
            "markers": [], "custom": {"keep": 1}, "collectioned": [
                {"press_format": "DVDRip" if "DVDRip" in release else "BDRip", "press_group": "Jsum", "press_path": release, "custom_press": index}
                for index, release in enumerate(releases)]}},
        {"type": "extra-attribute", "data": {"list": ["keep"]}},
    ]}


def collection(value):
    return next(item["data"] for item in value["attributes"] if item["type"] == "collection-type")


class OrganizerServiceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.db = self.base / "db"
        self.db.mkdir()
        self.root = self.base / "media"
        self.root.mkdir()
        self.child = self.root / "Demo_BDRip(Jsum)"
        self.child.mkdir()
        (self.child / "Demo 01.mkv").write_bytes(b"episode-one")
        (self.child / "Demo 02.mkv").write_bytes(b"episode-two")
        self.settings = JpTvBrowseSettings(version=1, filesystem_root=self.db, resolved_default_readable=None,
            resolved_catalog_yaml_paths=(), enum_options={}, enum_labels={}, enum_section_labels={}, app_features=())
        self.service = OrganizerService(journal_root=self.base / "receipts")

    def seed(self, records=None):
        self.source = self.db / "catalog.yaml"
        self.source.write_text(dump_yaml_string(records or [record("Demo", self.root, [self.child.name])]), encoding="utf-8")

    def preview(self, child=None, **body):
        return self.service.preview({"root": str(self.root), "child": (child or self.child).name, **body}, settings=self.settings)

    def execute(self, *plans, **body):
        return self.service.execute({"plan_ids": [plan["id"] for plan in plans], "confirm": True, **body}, settings=self.settings)

    def test_scan_disk_first_ignores_db_only_records_and_lists_root_files(self):
        self.seed([record("DB only", self.root, ["missing_BDRip"])])
        (self.root / "loose.txt").write_text("keep", encoding="utf-8")
        result = self.service.scan({"root": str(self.root)}, settings=self.settings)
        self.assertEqual([row["name"] for row in result["children"]], [self.child.name])
        self.assertEqual(result["root_files"][0]["name"], "loose.txt")
        self.assertFalse((self.base / "receipts").exists())

    def test_preview_is_read_only_and_new_db_requires_explicit_information_confirmation(self):
        before = snapshot(self.child)
        plan = self.preview()
        self.assertFalse(plan["can_execute"])
        self.assertTrue(any(issue["code"] == "new-record-confirmation" for issue in plan["issues"]))
        self.assertEqual(snapshot(self.child), before)
        self.assertEqual(list(self.db.iterdir()), [])
        self.assertFalse((self.base / "receipts").exists())
        with self.assertRaises(ValueError):
            self.service.execute({"plan_ids": [plan["id"]]}, settings=self.settings)

    def test_confirmed_new_work_moves_files_saves_db_and_preserves_extensions(self):
        first = self.preview()
        draft = deepcopy(first["draft"])
        draft["records"][0]["confirmed"] = True
        draft["records"][0]["record"]["user_extra"] = {"keep": "value"}
        plan = self.preview(draft=draft)
        self.assertTrue(plan["can_execute"], plan["issues"])
        result = self.execute(plan)
        self.assertEqual(result["results"][0]["status"], "succeeded", result)
        self.assertEqual(len(catalog_records(self.settings)), 1)
        self.assertEqual(catalog_records(self.settings)[0]["record"]["user_extra"], {"keep": "value"})
        for row in plan["files"]:
            self.assertTrue(Path(row["target_path"]).is_file())
        replay = self.execute(plan)
        self.assertTrue(replay["results"][0]["replayed"])
        self.assertEqual(len(catalog_records(self.settings)), 1)

    def test_existing_work_and_unknown_metadata_are_preserved(self):
        self.seed()
        plan = self.preview()
        self.assertTrue(plan["can_execute"], plan["issues"])
        original = catalog_records(self.settings)[0]["record"]
        result = self.execute(plan)
        self.assertEqual(result["results"][0]["status"], "succeeded", result)
        self.assertEqual(catalog_records(self.settings)[0]["record"], original)
        next_plan = self.preview()
        self.assertEqual(next_plan["status"], "complete", next_plan)
        self.assertFalse(any(row["changed"] for row in next_plan["files"]))

    def test_new_category_name_follows_release_directory_not_translated_database_name(self):
        self.seed([record("数据库译名", self.root, [self.child.name])])
        before_db = self.source.read_bytes()
        before_media = snapshot(self.child)
        plan = self.preview()
        self.assertTrue(plan["can_execute"], plan["issues"])
        self.assertTrue(all(row["target_rel"].startswith("Demo_BDRip_Disc/") for row in plan["files"]))
        self.assertEqual(next(item["data"] for item in plan["draft"]["records"][0]["record"]["attributes"] if item["type"] == "name"), "数据库译名")
        self.assertEqual(self.source.read_bytes(), before_db)
        self.assertEqual(snapshot(self.child), before_media)
        self.assertFalse((self.base / "receipts").exists())

    def test_repreview_category_uses_edited_final_release_name_and_format(self):
        self.seed([record("数据库译名", self.root, [self.child.name])])
        first = self.preview()
        draft = deepcopy(first["draft"])
        draft["release_name"] = "魔法少女リリカルなのは_DVDRip(VCBA)"
        press = collection(draft["records"][0]["record"])["collectioned"][0]
        press.update(press_path=draft["release_name"], press_format="DVDRip", press_group="VCBA")
        draft["records"][0]["press_keys"] = ["0:manual"]
        revised = self.preview(draft=draft)
        self.assertTrue(revised["can_execute"], revised["issues"])
        self.assertTrue(all(row["target_rel"].startswith("魔法少女リリカルなのは_DVDRip_Disc/") for row in revised["files"]))
        self.assertFalse((self.root / draft["release_name"]).exists())
        self.assertTrue((self.child / "Demo 01.mkv").is_file())
        self.assertEqual(catalog_records(self.settings)[0]["press"][0]["press_path"], self.child.name)

    def test_nonstandard_target_name_falls_back_to_selected_work_metadata(self):
        self.seed([record("数据库作品名", self.root, [self.child.name])])
        first = self.preview()
        draft = deepcopy(first["draft"])
        draft["release_name"] = "手动目录"
        collection(draft["records"][0]["record"])["collectioned"][0]["press_path"] = draft["release_name"]
        draft["records"][0]["press_keys"] = ["0:manual"]
        revised = self.preview(draft=draft)
        self.assertTrue(revised["can_execute"], revised["issues"])
        self.assertTrue(all(row["target_rel"].startswith("数据库作品名_BDRip_Disc/") for row in revised["files"]))
        self.assertFalse((self.root / draft["release_name"]).exists())

    def test_edited_release_name_keeps_existing_categories_and_manual_file_targets(self):
        preserved = self.child / "旧名_BDRip_Music" / "album.flac"
        preserved.parent.mkdir()
        preserved.write_bytes(b"keep-manual-music")
        self.seed()
        first = self.preview()
        draft = deepcopy(first["draft"])
        draft["release_name"] = "新目录名_BDRip(Jsum)"
        collection(draft["records"][0]["record"])["collectioned"][0]["press_path"] = draft["release_name"]
        draft["records"][0]["press_keys"] = ["0:manual"]
        draft["file_targets"] = {"Demo 01.mkv": "手动路径/custom.mkv"}
        revised = self.preview(draft=draft)
        self.assertTrue(revised["can_execute"], revised["issues"])
        targets = {row["source_rel"]: row["target_rel"] for row in revised["files"]}
        self.assertEqual(targets["旧名_BDRip_Music/album.flac"], "新目录名_BDRip_Music/album.flac")
        self.assertEqual(targets["Demo 01.mkv"], "手动路径/custom.mkv")
        self.assertEqual(targets["Demo 02.mkv"], "新目录名_BDRip_Disc/Demo 02.mkv")
        self.assertEqual(preserved.read_bytes(), b"keep-manual-music")

    def test_source_changes_make_confirmed_plan_non_executable(self):
        self.seed()
        plan = self.preview()
        (self.child / "new.txt").write_text("new", encoding="utf-8")
        result = self.execute(plan)
        self.assertEqual(result["results"][0]["status"], "failed")
        self.assertTrue((self.child / "Demo 01.mkv").is_file())
        self.assertIn("来源目录已变化", result["results"][0]["message"])

    def test_collision_and_traversal_are_blocked(self):
        self.seed()
        plan = self.preview()
        draft = deepcopy(plan["draft"])
        draft["file_targets"] = {row["source_rel"]: "same.mkv" for row in plan["files"]}
        collision = self.preview(draft=draft)
        self.assertFalse(collision["can_execute"])
        self.assertTrue(any(row["code"] == "target-collision" for row in collision["issues"]))
        for path in ("../escape.mkv", "C:/escape.mkv", "file:stream", "CON", "a/../b", "trailing.", "a//b"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                relative_path(path)

    def test_repreview_only_invalidates_same_child(self):
        other = self.root / "Other_BDRip"
        other.mkdir()
        (other / "01.mkv").write_bytes(b"other")
        self.seed([record("Demo", self.root, [self.child.name]), record("Other", self.root, [other.name])])
        first = self.preview()
        second = self.preview(other)
        replacement = self.preview()
        with self.assertRaises(ValueError):
            self.service.plans.get(first["id"])
        self.assertEqual(self.service.plans.get(second["id"])["id"], second["id"])
        self.assertTrue(replacement["can_execute"])

    def test_second_move_failure_rolls_back_without_overwrite(self):
        self.seed()
        before_db = self.source.read_bytes()
        plan = self.preview()
        original_move = MediaExecution.move_exclusive
        calls = 0
        def failing(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PermissionError("fixture second file denied")
            return original_move(source, target)
        with patch.object(MediaExecution, "move_exclusive", side_effect=failing):
            result = self.execute(plan)
        self.assertEqual(result["results"][0]["status"], "failed", result)
        self.assertEqual((self.child / "Demo 01.mkv").read_bytes(), b"episode-one")
        self.assertEqual((self.child / "Demo 02.mkv").read_bytes(), b"episode-two")
        self.assertEqual(self.source.read_bytes(), before_db)

    def test_db_save_failure_rolls_back_media_but_unknown_commit_does_not(self):
        self.seed()
        plan = self.preview()
        with patch("directory_organizer.service.catalog.apply_catalog_edits", side_effect=OSError("fixture db unavailable")):
            result = self.execute(plan)
        self.assertEqual(result["results"][0]["status"], "failed")
        self.assertTrue((self.child / "Demo 01.mkv").exists())
        replacement = self.preview()
        error = OSError("fixture rollback uncertain")
        error.db_committed = None
        with patch("directory_organizer.service.catalog.apply_catalog_edits", side_effect=error):
            uncertain = self.execute(replacement)
        self.assertEqual(uncertain["results"][0]["status"], "warning")
        self.assertTrue(all(Path(row["target_path"]).exists() for row in replacement["files"]))

    def test_batch_same_work_merges_distinct_press_edits_and_keeps_all_versions(self):
        other = self.root / "Demo_DVDRip(Jsum)"
        other.mkdir()
        (other / "Demo 01.mkv").write_bytes(b"dvd")
        self.seed([record("Demo", self.root, [self.child.name, other.name])])
        plans = []
        for position, child in enumerate((self.child, other)):
            initial = self.preview(child)
            draft = deepcopy(initial["draft"])
            release = child.name + "_revised"
            draft["release_name"] = release
            collection(draft["records"][0]["record"])["collectioned"][position]["press_path"] = release
            plan = self.preview(child, draft=draft)
            self.assertTrue(plan["can_execute"], plan["issues"])
            plans.append(plan)
        result = self.execute(*plans)
        self.assertEqual([row["status"] for row in result["results"]], ["succeeded", "succeeded"], result)
        saved = catalog_records(self.settings)[0]["record"]
        self.assertEqual([press["press_path"] for press in collection(saved)["collectioned"]], [plan["draft"]["release_name"] for plan in plans])
        self.assertFalse(self.child.exists())
        self.assertFalse(other.exists())

    def test_batch_conflicting_field_stops_before_any_media_move(self):
        other = self.root / "Demo_DVDRip(Jsum)"
        other.mkdir()
        (other / "Demo 01.mkv").write_bytes(b"dvd")
        self.seed([record("Demo", self.root, [self.child.name, other.name])])
        plans = []
        for position, child in enumerate((self.child, other)):
            initial = self.preview(child)
            draft = deepcopy(initial["draft"])
            next(item for item in draft["records"][0]["record"]["attributes"] if item["type"] == "name")["data"] = f"Conflict {position}"
            plans.append(self.preview(child, draft=draft))
        with self.assertRaisesRegex(ValueError, "不同修改"):
            self.execute(*plans)
        self.assertTrue((self.child / "Demo 01.mkv").exists())
        self.assertTrue((other / "Demo 01.mkv").exists())

    def test_shortcuts_are_previewed_after_db_and_require_separate_confirmation(self):
        self.seed()
        plan = self.preview()
        with patch("directory_organizer.service.shortcuts.preview_shortcuts", return_value={"plan_id": "link-preview", "items": []}) as preview, patch("directory_organizer.service.shortcuts.apply_shortcuts") as apply:
            result = self.execute(plan, generate_shortcuts=True)
            preview.assert_called_once()
            apply.assert_not_called()
        self.assertEqual(result["results"][0]["shortcut_preview"]["plan_id"], "link-preview")
        with self.assertRaises(ValueError):
            self.service.shortcut_action({"refs": result["results"][0]["shortcut_refs"], "plan_id": "link-preview"}, settings=self.settings)

    def test_three_way_merge_preserves_unedited_fields_and_rejects_conflicts(self):
        self.assertEqual(merge_confirmed_changes({"a": 1, "b": 2}, {"a": 3, "b": 2}, {"a": 1, "b": 4}), {"a": 3, "b": 4})
        self.assertEqual(merge_confirmed_changes({"x": [1, 2]}, {"x": [3, 2]}, {"x": [1, 4]}), {"x": [3, 4]})
        with self.assertRaises(ValueError):
            merge_confirmed_changes({"a": 1}, {"a": 2}, {"a": 3})

    def test_unique_db_press_with_different_format_is_not_automatically_bound(self):
        self.seed([record("Demo", self.root, ["Demo_DVDRip(Jsum)"])])
        plan = self.preview()
        self.assertFalse(plan["can_execute"])
        self.assertEqual(plan["draft"]["records"][0]["press_keys"], [])
        self.assertTrue(any(issue["code"] == "press-selection" for issue in plan["issues"]))
        self.assertEqual(collection(plan["draft"]["records"][0]["record"])["collectioned"][0]["press_path"], "Demo_DVDRip(Jsum)")

    def test_moving_work_root_cannot_silently_retarget_unselected_press(self):
        self.seed([record("Demo", self.root, [self.child.name, "Demo_DVDRip(Jsum)"])])
        plan = self.preview()
        draft = deepcopy(plan["draft"])
        draft["work_root"] = str(self.base / "new-media")
        collection(draft["records"][0]["record"])["path"] = draft["work_root"]
        updated = self.preview(draft=draft)
        self.assertFalse(updated["can_execute"])
        self.assertTrue(any(issue["code"] == "unselected-press-target" for issue in updated["issues"]))

    def test_stale_press_key_and_reordered_raw_rows_need_manual_reselection(self):
        self.seed([record("Demo", self.root, [self.child.name, "Demo_DVDRip(Jsum)"])])
        plan = self.preview()
        draft = deepcopy(plan["draft"])
        draft["records"][0]["press_keys"] = ["0:not-the-original-key"]
        updated = self.preview(draft=draft)
        self.assertFalse(updated["can_execute"])
        self.assertTrue(any(issue["code"] == "press-key-stale" for issue in updated["issues"]))
        draft = deepcopy(plan["draft"])
        collection(draft["records"][0]["record"])["collectioned"].reverse()
        updated = self.preview(draft=draft)
        self.assertTrue(any(issue["code"] == "press-order-changed" for issue in updated["issues"]))

    def test_two_new_release_directories_must_not_create_duplicate_work_records(self):
        other = self.root / "Demo_BDRip(VCB)"
        other.mkdir()
        (other / "01.mkv").write_bytes(b"other release")
        plans = []
        for child in (self.child, other):
            first = self.preview(child)
            draft = deepcopy(first["draft"])
            draft["records"][0]["confirmed"] = True
            plans.append(self.preview(child, draft=draft))
        with self.assertRaisesRegex(ValueError, "新增同一作品"):
            self.execute(*plans)
        self.assertEqual(catalog_records(self.settings), [])
        self.assertTrue((self.child / "Demo 01.mkv").exists())

    def test_multiple_existing_works_can_share_one_release_and_get_distinct_shortcut_refs(self):
        first = record("Demo part one", self.root, [self.child.name])
        second = record("Demo part two", self.root, [self.child.name])
        self.seed([first, second])
        plan = self.preview()
        self.assertTrue(plan["can_execute"], plan["issues"])
        self.assertEqual(len(plan["draft"]["records"]), 2)
        result = self.execute(plan)
        refs = result["results"][0]["shortcut_refs"]
        self.assertEqual(len(refs), 2)
        self.assertNotEqual(refs[0]["work_key"], refs[1]["work_key"])
        self.assertEqual(len(catalog_records(self.settings)), 2)

    def test_changed_db_record_blocks_batch_before_media_changes(self):
        self.seed()
        plan = self.preview()
        changed = record("Changed", self.root, [self.child.name])
        self.source.write_text(dump_yaml_string([changed]), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "DB 预览已失效"):
            self.execute(plan)
        self.assertTrue((self.child / "Demo 01.mkv").exists())

    def test_destination_created_after_preview_is_never_overwritten(self):
        self.seed()
        plan = self.preview()
        target = Path(plan["files"][0]["target_path"])
        target.parent.mkdir(parents=True)
        target.write_bytes(b"external file")
        result = self.execute(plan)
        self.assertEqual(result["results"][0]["status"], "failed")
        self.assertEqual(target.read_bytes(), b"external file")
        self.assertTrue((self.child / "Demo 01.mkv").exists())

    def test_auto_completed_can_be_manually_unchecked(self):
        self.seed()
        self.execute(self.preview())
        plan = self.preview()
        self.assertTrue(plan["draft"]["completed"])
        draft = deepcopy(plan["draft"])
        draft["completed"] = False
        updated = self.preview(draft=draft)
        self.assertFalse(updated["draft"]["completed"])

    def mark_completed(self, completed=True, service=None, child=None):
        return (service or self.service).set_completion({"root": str(self.root),
            "child": (child or self.child).name, "completed": completed}, settings=self.settings)

    def test_completion_mark_persists_without_touching_media_or_catalog(self):
        self.seed()
        before_media, before_db = snapshot(self.child), self.source.read_bytes()
        marked = self.mark_completed()
        self.assertTrue(marked["completion_marked"])
        restarted = OrganizerService(journal_root=self.base / "receipts")
        scan = restarted.scan({"root": str(self.root)}, settings=self.settings)
        self.assertTrue(scan["children"][0]["completion_marked"])
        with patch("directory_organizer.service.propose_layout", side_effect=AssertionError("must not classify")):
            plan = restarted.preview({"root": str(self.root), "child": self.child.name}, settings=self.settings)
        self.assertTrue(plan["can_execute"], plan["issues"])
        self.assertTrue(plan["draft"]["completed"])
        self.assertEqual(plan["counts"]["moves"], 0)
        self.assertEqual(plan["database_status"], "matched")
        self.assertEqual(snapshot(self.child), before_media)
        self.assertEqual(self.source.read_bytes(), before_db)
        self.assertFalse((self.base / "receipts").exists())

    def test_marked_missing_db_still_requires_information_and_confirmation(self):
        self.mark_completed()
        with patch("directory_organizer.service.propose_layout", side_effect=AssertionError("must not classify")):
            plan = self.preview()
        self.assertEqual(plan["media_status"], "complete")
        self.assertEqual(plan["database_status"], "unresolved")
        self.assertFalse(plan["can_execute"])
        self.assertTrue(any(issue["code"] == "new-record-confirmation" for issue in plan["issues"]))
        self.assertEqual(plan["counts"]["moves"], 0)
        draft = deepcopy(plan["draft"])
        draft["records"][0]["confirmed"] = True
        confirmed = self.preview(draft=draft)
        before = snapshot(self.child)
        result = self.execute(confirmed)
        self.assertEqual(result["results"][0]["status"], "succeeded", result)
        self.assertEqual(len(catalog_records(self.settings)), 1)
        self.assertEqual(snapshot(self.child), before)

    def test_completion_marker_overrides_stale_draft_and_manual_file_targets(self):
        self.seed()
        draft = deepcopy(self.preview()["draft"])
        draft.update(completed=False, work_root=str(self.base / "different"), release_name="renamed",
                     file_targets={"missing.mkv": "bad-target", "Demo 01.mkv": "manual/renamed.mkv"})
        self.mark_completed()
        with patch("directory_organizer.service.propose_layout", side_effect=AssertionError("must not classify")):
            plan = self.preview(draft=draft, strategy="removed-old-class")
        self.assertTrue(plan["can_execute"], plan["issues"])
        self.assertEqual(Path(plan["target_path"]), self.child)
        self.assertEqual(plan["draft"]["file_targets"], {})
        self.assertTrue(all(not row["changed"] and row["target_rel"] == row["source_rel"] for row in plan["files"]))
        self.assertEqual(self.execute(plan)["results"][0]["moved"], 0)

    def test_marked_media_allows_shared_db_edit_and_shortcut_precheck(self):
        self.seed()
        self.mark_completed()
        draft = deepcopy(self.preview()["draft"])
        draft["records"][0]["record"]["completion_test"] = "saved via common DB service"
        plan = self.preview(draft=draft)
        self.assertEqual(plan["database_status"], "needs_sync")
        before = snapshot(self.child)
        with patch("directory_organizer.service.shortcuts.preview_shortcuts", return_value={"plan_id": "fixture"}) as shortcut:
            result = self.execute(plan, generate_shortcuts=True)
        row = result["results"][0]
        self.assertEqual(row["status"], "succeeded", result)
        self.assertEqual(row["moved"], 0)
        self.assertEqual(len(row["shortcut_refs"]), 1)
        shortcut.assert_called_once_with(row["shortcut_refs"], settings=self.settings)
        self.assertEqual(catalog_records(self.settings)[0]["record"]["completion_test"], "saved via common DB service")
        self.assertEqual(snapshot(self.child), before)

    def test_unmark_restores_classification_and_invalidates_protected_plan(self):
        self.seed()
        self.mark_completed()
        protected = self.preview()
        self.mark_completed(False)
        with self.assertRaisesRegex(ValueError, "预览已过期"):
            self.execute(protected)
        normal = self.preview()
        self.assertFalse(normal["draft"]["completed"])
        self.assertFalse(normal["completion_marked"])
        self.assertGreater(normal["counts"]["moves"], 0)

    def test_mark_invalidates_old_move_plan_in_this_and_other_service_instances(self):
        self.seed()
        moving = self.preview()
        self.mark_completed()
        with self.assertRaisesRegex(ValueError, "预览已过期"):
            self.execute(moving)
        self.mark_completed(False)
        moving = self.preview()
        other = OrganizerService(journal_root=self.base / "receipts")
        self.mark_completed(service=other)
        self.mark_completed(False, service=other)
        with self.assertRaisesRegex(ValueError, "标记已变化"):
            self.execute(moving)
        self.assertTrue((self.child / "Demo 01.mkv").is_file())

    def test_other_window_unmark_overrides_stale_completed_draft(self):
        self.seed()
        self.mark_completed()
        draft = deepcopy(self.preview()["draft"])
        other = OrganizerService(journal_root=self.base / "receipts")
        self.mark_completed(False, service=other)
        plan = self.preview(draft=draft)
        self.assertFalse(plan["completion_marked"])
        self.assertFalse(plan["draft"]["completed"])
        self.assertGreater(plan["counts"]["moves"], 0)

    def test_marked_db_only_execution_allows_hardlinks_and_later_added_files(self):
        self.seed()
        linked = self.child / "linked episode.mkv"
        linked.hardlink_to(self.child / "Demo 01.mkv")
        self.mark_completed()
        draft = deepcopy(self.preview()["draft"])
        draft["records"][0]["record"]["db_only"] = True
        plan = self.preview(draft=draft)
        self.assertTrue(plan["can_execute"], plan["issues"])
        (self.child / "later.txt").write_text("external addition", encoding="utf-8")
        result = self.execute(plan)
        self.assertEqual(result["results"][0]["status"], "succeeded", result)
        self.assertEqual(result["results"][0]["moved"], 0)
        self.assertTrue(catalog_records(self.settings)[0]["record"]["db_only"])
        self.assertEqual(linked.stat().st_ino, (self.child / "Demo 01.mkv").stat().st_ino)
        self.assertEqual((self.child / "later.txt").read_text(encoding="utf-8"), "external addition")

    def test_marked_directory_db_target_mismatch_does_not_move_media(self):
        self.seed()
        self.mark_completed()
        draft = deepcopy(self.preview()["draft"])
        collection(draft["records"][0]["record"])["collectioned"][0]["press_path"] = "other_BDRip"
        plan = self.preview(draft=draft)
        self.assertFalse(plan["can_execute"])
        self.assertTrue(any(issue["code"] == "binding-target-mismatch" for issue in plan["issues"]))
        self.assertEqual(plan["counts"]["moves"], 0)

    def test_marked_exact_binding_preserves_equivalent_db_roots_and_other_releases(self):
        raw = record("Demo", self.base, ["media/" + self.child.name, "elsewhere/Other_BDRip"])
        self.seed([raw])
        self.mark_completed()
        plan = self.preview()
        self.assertTrue(plan["can_execute"], plan["issues"])
        self.assertEqual(plan["draft"]["records"][0]["record"], raw)
        self.assertEqual(plan["counts"]["db_changes"], 0)
        result = self.execute(plan)
        self.assertEqual(result["results"][0]["status"], "succeeded", result)
        self.assertEqual(catalog_records(self.settings)[0]["record"], raw)

    def test_batch_completion_change_is_checked_before_any_directory_moves(self):
        other_child = self.root / "Other_BDRip"
        other_child.mkdir()
        (other_child / "Other 01.mkv").write_bytes(b"other episode")
        self.seed([record("Demo", self.root, [self.child.name]), record("Other", self.root, [other_child.name])])
        first, second = self.preview(), self.preview(child=other_child)
        other_service = OrganizerService(journal_root=self.base / "receipts")
        self.mark_completed(service=other_service, child=other_child)
        with self.assertRaisesRegex(ValueError, "标记已变化"):
            self.execute(first, second)
        self.assertTrue((self.child / "Demo 01.mkv").is_file())
        self.assertTrue((other_child / "Other 01.mkv").is_file())

    def test_completion_action_rejects_invalid_values_and_nested_paths(self):
        for value in (None, 1, "true", []):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "布尔值"):
                self.mark_completed(value)
        with self.assertRaises(ValueError):
            self.service.set_completion({"root": str(self.root), "child": "../outside", "completed": True}, settings=self.settings)
        self.assertFalse(self.service.completion.path.exists())

    def test_legacy_relative_db_root_uses_shared_resolver_and_keeps_other_press_targets(self):
        raw = record("Demo", self.root, [self.child.name, "Demo_DVDRip(Jsum)"])
        collection(raw)["path"] = "media"
        self.seed([raw])
        with patch("collection_detail.link_index._legacy_media_root", return_value=self.base):
            plan = self.preview()
            self.assertTrue(plan["can_execute"], plan["issues"])
            self.assertEqual(Path(plan["target_path"]), self.child)
            result = self.execute(plan)
        self.assertEqual(result["results"][0]["status"], "succeeded", result)
        self.assertEqual(len(result["results"][0]["shortcut_refs"]), 1)
        saved = collection(catalog_records(self.settings)[0]["record"])
        self.assertEqual(saved["collectioned"][1]["press_path"], "Demo_DVDRip(Jsum)")

    def test_legacy_root_config_change_invalidates_a_relative_binding_before_move(self):
        self.seed()
        with patch("collection_detail.link_index._legacy_media_root", return_value=self.base):
            initial = self.preview()
            draft = deepcopy(initial["draft"])
            collection(draft["records"][0]["record"])["path"] = "media"
            plan = self.preview(draft=draft)
        self.assertTrue(plan["can_execute"], plan["issues"])
        with patch("collection_detail.link_index._legacy_media_root", return_value=self.base / "changed-root"):
            result = self.execute(plan)
        self.assertEqual(result["results"][0]["status"], "failed", result)
        self.assertIn("目录解析配置已变化", result["results"][0]["message"])
        self.assertTrue((self.child / "Demo 01.mkv").exists())

    def test_malformed_manual_records_have_actionable_validation_errors(self):
        self.seed()
        initial = self.preview()
        for field in ("collectioned", "continuations"):
            draft = deepcopy(initial["draft"])
            collection(draft["records"][0]["record"])[field] = None
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                self.preview(draft=draft)
        draft = deepcopy(initial["draft"])
        draft["records"][0]["record"]["attributes"] = None
        with self.assertRaisesRegex(ValueError, "attributes"):
            self.preview(draft=draft)
        draft = deepcopy(initial["draft"])
        draft["completed"] = "false"
        with self.assertRaisesRegex(ValueError, "布尔"):
            self.preview(draft=draft)

    def test_parent_series_name_does_not_bind_movie_to_tv_record(self):
        root = self.base / "Demo"
        child = root / "Demo Movie_BDRip"
        (child / "Demo Movie_BDRip_Disc").mkdir(parents=True)
        (child / "Demo Movie_BDRip_Disc" / "movie.mkv").write_bytes(b"movie")
        self.seed([record("Demo", root, ["Demo_BDRip"])])
        plan = self.service.preview({"root": str(root), "child": child.name}, settings=self.settings)
        self.assertEqual(Path(plan["target_path"]), child)
        self.assertIsNone(plan["draft"]["records"][0]["ref"])
        self.assertEqual(plan["catalog_candidates"][0]["name"], "Demo")
        self.assertTrue(any(issue["code"] == "new-record-confirmation" for issue in plan["issues"]))

    def test_format_only_child_can_use_parent_name_without_conflicting_child_title(self):
        root = self.base / "Demo"
        child = root / "BDRip"
        child.mkdir(parents=True)
        (child / "01.mkv").write_bytes(b"episode")
        self.seed([record("Demo", root, ["Demo_BDRip"])])
        plan = self.service.preview({"root": str(root), "child": child.name}, settings=self.settings)
        self.assertIsNotNone(plan["draft"]["records"][0]["ref"])
        self.assertTrue(plan["can_execute"], plan["issues"])

    def test_preview_summary_counts_match_actual_move_plan(self):
        self.seed()
        plan = self.preview()
        self.assertEqual(plan["counts"]["files"], len(plan["files"]))
        self.assertEqual(plan["counts"]["moves"], sum(row["changed"] for row in plan["files"]))
        self.assertEqual(plan["counts"]["unchanged"] + plan["counts"]["moves"], len(plan["files"]))
        self.assertEqual(plan["counts"]["db_changes"], len(plan["db_changes"]))

    def test_media_and_database_states_are_independent(self):
        sources = [row["relative_path"] for row in snapshot(self.child)["files"]]
        plan = self.preview(strategy="generic", draft={"file_targets": {path: path for path in sources}})
        self.assertEqual(plan["media_status"], "complete")
        self.assertEqual(plan["database_status"], "unresolved")
        self.assertFalse(plan["can_execute"])
        self.assertEqual(plan["counts"]["moves"], 0)
        self.seed()
        initial = self.preview()
        draft = deepcopy(initial["draft"])
        draft["file_targets"] = {row["source_rel"]: "same.mkv" for row in initial["files"]}
        collision = self.preview(draft=draft)
        self.assertEqual(collision["media_status"], "unresolved")
        self.assertEqual(collision["database_status"], "matched")

    def test_existing_prefixed_categories_and_episode_directories_need_no_move(self):
        episode = self.child / "Demo_BDRip_Disc" / "Demo_BDRip_01"
        episode.mkdir(parents=True)
        for file in list(self.child.glob("*.mkv")):
            file.rename(episode / file.name)
        booklet = self.child / "Demo_BDRip_Booklet"
        booklet.mkdir()
        (booklet / "cover.jpg").write_bytes(b"scan")
        self.seed()
        before = snapshot(self.child)
        plan = self.preview()
        self.assertTrue(plan["can_execute"], plan["issues"])
        self.assertEqual(plan["media_status"], "complete")
        self.assertEqual(plan["counts"]["moves"], 0)
        self.assertEqual(plan["layout_assessment"]["kind"], "organized")
        self.assertEqual(snapshot(self.child), before)
        draft = deepcopy(plan["draft"])
        draft["completed"] = False
        first = plan["files"][0]["source_rel"]
        draft["file_targets"] = {first: "custom/manual.jpg"}
        changed = self.preview(draft=draft)
        self.assertEqual(changed["counts"]["moves"], 1)
        self.assertEqual(changed["media_status"], "needs_work")

    def test_normalized_existing_categories_block_real_collisions_until_manual_resolution(self):
        for category in ("_Disc", self.child.name + "_Disc"):
            directory = self.child / category
            directory.mkdir()
            (directory / "Demo 03.mkv").write_bytes(b"existing")
        self.seed()
        plan = self.preview()
        self.assertFalse(plan["can_execute"])
        self.assertEqual(plan["media_status"], "unresolved")
        self.assertFalse(plan["draft"]["completed"])
        self.assertTrue(any(issue["code"] == "target-conflict" and issue["blocking"] for issue in plan["issues"]))
        draft = deepcopy(plan["draft"])
        draft["file_targets"] = {self.child.name + "_Disc/Demo 03.mkv": "Demo_BDRip_Disc/version-b/Demo 03.mkv"}
        resolved = self.preview(draft=draft)
        self.assertTrue(resolved["can_execute"], resolved["issues"])
        self.assertEqual(resolved["counts"]["moves"], 4)
        draft = deepcopy(plan["draft"])
        draft["completed"] = True
        preserved = self.preview(draft=draft)
        self.assertTrue(preserved["can_execute"], preserved["issues"])
        self.assertEqual(preserved["counts"]["moves"], 0)

    def test_ambiguous_episode_destinations_do_not_auto_complete(self):
        for file in list(self.child.glob("*.mkv")):
            file.unlink()
        for episode in ("_01", "Demo_01"):
            directory = self.child / "_Disc" / episode
            directory.mkdir(parents=True)
            (directory / "Demo 01.mkv").write_bytes(b"existing")
        (self.child / "Demo 01.ass").write_text("subtitle", encoding="utf-8")
        self.seed()
        plan = self.preview()
        self.assertFalse(plan["can_execute"])
        self.assertFalse(plan["draft"]["completed"])
        self.assertTrue(any(issue["code"] == "subtitle-video-ambiguous" and issue["blocking"] for issue in plan["issues"]))
        draft = deepcopy(plan["draft"])
        draft["completed"] = True
        preserved = self.preview(draft=draft)
        self.assertTrue(preserved["can_execute"], preserved["issues"])
        self.assertEqual(preserved["counts"]["moves"], 0)

    def test_preview_exposes_complete_read_only_structure_including_empty_directories(self):
        empty = self.child / "_Image" / "reserved"
        empty.mkdir(parents=True)
        self.seed()
        before = snapshot(self.child)
        plan = self.preview()
        self.assertEqual(plan["source_directories"], before["directories"])
        self.assertIn("_Image/reserved", plan["source_directories"])
        self.assertTrue(plan["source_structure_complete"])
        self.assertEqual(snapshot(self.child), before)
        plan["source_directories"].append("client-only")
        self.assertNotIn("client-only", self.service.plans.get(plan["id"])["source_directories"])

    def test_incomplete_source_scan_prevents_directory_move_summary_claim(self):
        self.seed()
        facts = snapshot(self.child)
        facts["issues"].append({"code": "unsafe-link", "path": str(self.child / "external-link"),
                                "message": "fixture non-ordinary object", "blocking": True})
        with patch("directory_organizer.service.snapshot", return_value=facts):
            plan = self.preview()
        self.assertFalse(plan["source_structure_complete"])
        self.assertFalse(plan["can_execute"])

    def test_unmatched_extracted_subtitle_requires_manual_decision(self):
        subtitles = self.child / "_Subs"
        subtitles.mkdir()
        (subtitles / "Demo 99.ass").write_text("unmatched", encoding="utf-8")
        self.seed()
        plan = self.preview()
        self.assertFalse(plan["can_execute"])
        self.assertEqual(plan["media_status"], "unresolved")
        self.assertTrue(any(issue["code"] == "subtitle-video-unresolved" and issue["blocking"] for issue in plan["issues"]))
        draft = deepcopy(plan["draft"])
        draft["completed"] = True
        preserved = self.preview(draft=draft)
        self.assertTrue(preserved["can_execute"], preserved["issues"])
        self.assertEqual(preserved["counts"]["moves"], 0)
        draft = deepcopy(plan["draft"])
        draft["file_targets"] = {"_Subs/Demo 99.ass": "_Other/unmatched/Demo 99.ass"}
        assigned = self.preview(draft=draft)
        self.assertTrue(assigned["can_execute"], assigned["issues"])


if __name__ == "__main__":
    unittest.main()
