from __future__ import annotations

import unittest
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest.mock import patch

from collection_detail import link_index, shortcut_retirement, shortcut_service
from collection_detail.catalog_repository import CatalogRepository
from collection_detail.save import browse_save_yaml_from_ui_body
from work_catalog_yaml.yaml_io import dump_yaml_string, load_yaml
import test_incremental_shortcuts as fixtures


class ShortcutRetirementTest(unittest.TestCase):
    preview = fixtures.IncrementalShortcutTest.preview
    apply = fixtures.IncrementalShortcutTest.apply
    create = staticmethod(fixtures.IncrementalShortcutTest.create)

    def setUp(self) -> None:
        fixtures.IncrementalShortcutTest.setUp(self)
        docs = load_yaml(self.source)
        docs[0]["id"] = "work_" + "a" * 32
        self.source.write_text(dump_yaml_string(docs), encoding="utf-8")
        self.writer = patch.object(link_index, "_create_windows_shortcut", side_effect=self.create)
        self.writer.start()
        self.addCleanup(self.writer.stop)

    def initial(self) -> Path:
        plan = self.preview()
        self.assertEqual(self.apply(plan)["created"], 1)
        return Path(next(row["shortcut_path"] for row in plan["items"] if row["status"] == "planned"))

    def change_date(self, *, index: int = 0, start: str = "20980201") -> None:
        browse_save_yaml_from_ui_body({"rows": [{"yaml_source_rel": self.source.name, "index_in_file": index,
            "date": {"start": start, "end": "20980331"}}]}, settings=self.settings)

    @property
    def receipt_path(self) -> Path:
        return self.root / "feature" / "db" / "shortcuts" / "managed-shortcuts.yaml"

    def test_date_edit_retires_owned_link_and_only_empty_directories(self) -> None:
        old = self.initial()
        self.change_date()
        plan = self.preview()
        self.assertEqual(plan["retirement_count"], 1)
        self.assertTrue(old.exists(), "Preview is read-only")
        result = self.apply(plan)
        self.assertEqual(result["removed_count"], 1)
        self.assertEqual(result["removed_directory_count"], 1)
        self.assertFalse(old.parent.exists())
        self.assertTrue(Path(plan["retirements"][0]["replacement_path"]).is_file())
        self.assertEqual(self.keep.read_bytes(), b"keep")
        self.assertEqual(self.apply(self.preview())["removed_count"], 0)

    def test_scoped_api_uses_same_retirement_without_replacing_full_index_cache(self) -> None:
        old = self.initial()
        index_before = (self.root / "index.yaml").read_bytes()
        self.change_date()
        work = CatalogRepository(self.settings).load_works()[0]
        refs = [{"work_key": work["work_key"], "press_key": work["press"][0]["press_key"]}]
        plan = shortcut_service.preview_shortcuts(refs, settings=self.settings)
        result = shortcut_service.apply_shortcuts(refs, plan["plan_id"], settings=self.settings)
        self.assertEqual(result["removed_count"], 1)
        self.assertFalse(old.exists())
        self.assertEqual((self.root / "index.yaml").read_bytes(), index_before)

    def test_new_link_failure_retains_old_link_and_retries_safely(self) -> None:
        old = self.initial()
        self.change_date()
        with patch.object(link_index, "_create_windows_shortcut", side_effect=PermissionError("denied")):
            result = self.apply(self.preview())
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["removed_count"], 0)
        self.assertTrue(old.is_file())
        self.assertTrue(any(row["code"] == "shortcut-retirement-replacement-missing" for row in result["issues"]))
        self.assertEqual(self.apply(self.preview())["removed_count"], 1)

    def test_changed_owned_link_is_not_deleted(self) -> None:
        old = self.initial()
        self.change_date()
        old.write_text(str(self.target) + " ", encoding="utf-8")
        plan = self.preview()
        self.assertEqual(plan["retirement_count"], 0)
        self.assertTrue(any(row["code"] == "shortcut-retirement-conflict" for row in plan["issues"]))
        self.assertEqual(self.apply(plan)["created"], 1)
        self.assertTrue(old.exists())

    def test_change_after_preview_requires_repreview(self) -> None:
        old = self.initial()
        self.change_date()
        plan = self.preview()
        old.write_text("manual target", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "重新预检"):
            self.apply(plan)
        self.assertTrue(old.exists())

    def test_manual_content_keeps_old_directory(self) -> None:
        old = self.initial()
        note = old.parent / "manual.txt"
        note.write_text("keep", encoding="utf-8")
        self.change_date()
        result = self.apply(self.preview())
        self.assertEqual(result["removed_count"], 1)
        self.assertEqual(result["removed_directory_count"], 0)
        self.assertTrue(note.is_file())

    def test_other_work_still_using_old_destination_prevents_retirement(self) -> None:
        old = self.initial()
        docs = load_yaml(self.source)
        import copy
        shared = copy.deepcopy(docs[0])
        shared["id"] = "work_" + "b" * 32
        docs.append(shared)
        self.source.write_text(dump_yaml_string(docs), encoding="utf-8")
        self.change_date()
        result = self.apply(self.preview())
        self.assertEqual(result["removed_count"], 0)
        self.assertTrue(old.is_file())

    def test_reused_row_with_same_target_different_stable_work_is_not_retired(self) -> None:
        old = self.initial()
        docs = load_yaml(self.source)
        docs[0]["id"] = "work_" + "b" * 32
        self.source.write_text(dump_yaml_string(docs), encoding="utf-8")
        self.change_date()
        self.assertEqual(self.apply(self.preview())["removed_count"], 0)
        self.assertTrue(old.is_file())

    def test_reordered_row_same_stable_identity_still_retires_safely(self) -> None:
        old = self.initial()
        docs = load_yaml(self.source)
        docs[0], docs[1] = docs[1], docs[0]
        self.source.write_text(dump_yaml_string(docs), encoding="utf-8")
        self.change_date(index=1)
        self.assertEqual(self.apply(self.preview())["removed_count"], 1)
        self.assertFalse(old.exists())

    def test_missing_receipts_never_adopts_or_deletes_historical_links(self) -> None:
        old = self.initial()
        self.receipt_path.unlink()
        self.change_date()
        self.assertEqual(self.apply(self.preview())["removed_count"], 0)
        self.assertTrue(old.is_file())

    def test_receipt_save_failure_preserves_old_and_retry_recovers(self) -> None:
        old = self.initial()
        self.change_date()
        with patch.object(shortcut_retirement, "atomic_write_bytes", side_effect=PermissionError("manifest denied")):
            result = self.apply(self.preview())
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["removed_count"], 0)
        self.assertTrue(old.is_file())
        self.assertTrue(any(row["code"] == "shortcut-receipt-save-failed" for row in result["issues"]))
        result = self.apply(self.preview())
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["removed_count"], 1)
        self.assertFalse(old.exists())
        self.change_date(start="20980301")
        self.assertEqual(self.apply(self.preview())["removed_count"], 1, "Retry retains the new managed ownership chain")

    def test_legacy_work_without_stable_id_is_reported_but_never_retired(self) -> None:
        docs = load_yaml(self.source)
        docs[0].pop("id")
        self.source.write_text(dump_yaml_string(docs), encoding="utf-8")
        old = self.initial()
        self.change_date()
        result = self.apply(self.preview())
        self.assertEqual(result["removed_count"], 0)
        self.assertTrue(old.exists())
        self.assertTrue(any(row["code"] == "shortcut-retirement-identity-missing" for row in result["issues"]))

    def test_rebound_target_never_retires_prior_resource(self) -> None:
        old = self.initial()
        docs = load_yaml(self.source)
        replacement = self.target.parent / "Replacement"
        replacement.mkdir()
        coll = next(a["data"] for a in docs[0]["attributes"] if a["type"] == "collection-type")
        coll["collectioned"][0]["press_path"] = "Replacement"
        self.source.write_text(dump_yaml_string(docs), encoding="utf-8")
        self.change_date()
        self.assertEqual(self.apply(self.preview())["removed_count"], 0)
        self.assertTrue(old.exists())

    def test_external_db_change_during_creation_preserves_old_shortcut(self) -> None:
        old = self.initial()
        self.change_date()

        def create_then_external_edit(path, target):
            self.create(path, target)
            self.source.write_text(self.source.read_text(encoding="utf-8") + "\n# external edit\n", encoding="utf-8")

        with patch.object(link_index, "_create_windows_shortcut", side_effect=create_then_external_edit):
            result = self.apply(self.preview())
        self.assertEqual(result["removed_count"], 0)
        self.assertTrue(old.exists())
        self.assertTrue(any(row["code"] == "shortcut-retirement-db-changed" for row in result["issues"]))

    def test_receipt_file_and_parent_reparse_points_are_rejected(self) -> None:
        self.initial()
        original = Path.lstat
        for unsafe_path, mode in ((self.receipt_path, stat.S_IFLNK), (self.receipt_path.parent, stat.S_IFDIR)):
            def unsafe_metadata(path, *args, **kwargs):
                if path == unsafe_path:
                    return SimpleNamespace(st_mode=mode, st_file_attributes=0x400)
                return original(path, *args, **kwargs)
            with self.subTest(path=unsafe_path), patch.object(Path, "lstat", autospec=True, side_effect=unsafe_metadata):
                with self.assertRaisesRegex(ValueError, "联接/符号链接|联接或符号链接"):
                    shortcut_retirement.load_receipts(self.receipt_path, str(self.db))

    def test_receipt_and_shortcut_size_limits_preserve_old_files(self) -> None:
        old = self.initial()
        with patch.object(shortcut_retirement, "MAX_RECEIPT_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "安全读取上限"):
                self.preview()
        self.change_date()
        with patch.object(shortcut_retirement, "MAX_SHORTCUT_BYTES", 1):
            plan = self.preview()
        self.assertEqual(plan["retirement_count"], 0)
        self.assertTrue(any(row["code"] == "shortcut-retirement-unsafe" for row in plan["issues"]))
        self.assertTrue(old.is_file())

    def test_receipts_live_outside_catalog_discovery(self) -> None:
        self.initial()
        self.assertTrue(self.receipt_path.is_file())
        self.assertEqual(CatalogRepository(self.settings).catalog_paths(), [self.source])

    def test_new_catalog_file_during_creation_preserves_old_shortcut(self) -> None:
        old = self.initial()
        shared = load_yaml(self.source)[0]
        shared["id"] = "work_" + "b" * 32
        self.change_date()

        def create_then_external_catalog(path, target):
            self.create(path, target)
            (self.db / "[JP][TVInfo][2099].yaml").write_text(dump_yaml_string([shared]), encoding="utf-8")

        with patch.object(link_index, "_create_windows_shortcut", side_effect=create_then_external_catalog):
            result = self.apply(self.preview())
        self.assertEqual(result["removed_count"], 0)
        self.assertTrue(old.exists())
        self.assertTrue(any(row["code"] == "shortcut-retirement-db-changed" for row in result["issues"]))

    def test_failed_full_rebuild_clears_receipts_for_cleared_roots(self) -> None:
        self.source.write_text(dump_yaml_string(load_yaml(self.source)[:1]), encoding="utf-8")
        old = self.initial()
        plan = link_index.generate_link_index_files_from_ui_body({"preview": True}, settings=self.settings)["file_generation"]
        with patch.object(link_index, "_create_windows_shortcut", side_effect=PermissionError("failed rebuild")):
            result = link_index.generate_link_index_files_from_ui_body({"plan_id": plan["plan_id"],
                "confirm_clear": True, "confirm_clear_twice": True}, settings=self.settings)["file_generation"]
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["failed_count"], 1)
        self.assertFalse(old.exists())
        self.assertEqual(load_yaml(self.receipt_path)["items"], [])

    def _change_link_during_successor_receipt_save(self, *, change_replacement: bool) -> None:
        old = self.initial()
        self.change_date()
        with patch.object(shortcut_retirement, "atomic_write_bytes", side_effect=PermissionError("first receipt failed")):
            self.apply(self.preview())
        plan = self.preview()
        replacement = Path(plan["retirements"][0]["replacement_path"])
        original = shortcut_retirement.atomic_write_bytes
        calls = 0

        def persist_then_modify(path, data):
            nonlocal calls
            original(path, data)
            calls += 1
            if calls == 2:
                (replacement if change_replacement else old).write_text("manual changed target", encoding="utf-8")

        with patch.object(shortcut_retirement, "atomic_write_bytes", side_effect=persist_then_modify):
            result = self.apply(plan)
        self.assertEqual(result["removed_count"], 0)
        self.assertTrue(old.exists())
        self.assertTrue(any(row["code"] == "shortcut-retirement-failed" for row in result["issues"]))

    def test_old_link_change_during_receipt_save_is_preserved(self) -> None:
        self._change_link_during_successor_receipt_save(change_replacement=False)

    def test_replacement_change_during_receipt_save_preserves_old_link(self) -> None:
        self._change_link_during_successor_receipt_save(change_replacement=True)

    def test_multiple_resources_cleanup_only_successful_replacements(self) -> None:
        docs = load_yaml(self.source)
        coll = next(a["data"] for a in docs[0]["attributes"] if a["type"] == "collection-type")
        other_target = self.target.parent / "DVDRip"
        other_target.mkdir()
        coll["collectioned"].append({"press_format": "DVDRip", "press_group": "", "press_path": "DVDRip"})
        self.source.write_text(dump_yaml_string(docs), encoding="utf-8")
        plan = self.preview()
        self.assertEqual(self.apply(plan)["created"], 2)
        old_paths = [Path(row["shortcut_path"]) for row in plan["items"] if row["status"] == "planned"]
        self.change_date()

        def selective_create(path, target):
            if target == other_target:
                raise PermissionError("second resource denied")
            self.create(path, target)

        with patch.object(link_index, "_create_windows_shortcut", side_effect=selective_create):
            result = self.apply(self.preview())
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["removed_count"], 1)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(sum(path.exists() for path in old_paths), 1)
        self.assertTrue(old_paths[0].parent.exists())


if __name__ == "__main__":
    unittest.main()
