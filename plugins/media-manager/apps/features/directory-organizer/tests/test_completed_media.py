from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from directory_organizer.execution import MediaExecution
from directory_organizer.snapshots import snapshot


@contextmanager
def scanned_entries(entries):
    yield iter(entries)


class CompletedMediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "release"
        self.source.mkdir()
        (self.source / "episode.mkv").write_bytes(b"original episode")

    def media(self):
        plan = {"id": str(uuid4()), "source_path": str(self.source), "target_path": str(self.source),
                "draft": {"completed": True}, "_moves": [], "_snapshot": snapshot(self.source, preserve_media=True)}
        return MediaExecution(plan, self.base / "receipts")

    def test_preserve_snapshot_accepts_hardlinks_without_exclusive_signature(self):
        os.link(self.source / "episode.mkv", self.source / "episode-copy.mkv")
        with self.assertRaisesRegex(ValueError, "普通独占文件"):
            snapshot(self.source)
        with patch("directory_organizer.snapshots.signature", side_effect=AssertionError("must not require movable files")):
            facts = snapshot(self.source, preserve_media=True)
        self.assertEqual([row["relative_path"] for row in facts["files"]], ["episode-copy.mkv", "episode.mkv"])
        self.assertEqual(facts["files"][0]["signature"], facts["files"][1]["signature"])
        self.assertEqual(facts["issues"], [])
        self.assertEqual(set(facts), {"files", "directories", "identity", "issues", "fingerprint"})

    def test_preserve_snapshot_skips_links_without_following_them(self):
        link = SimpleNamespace(name="external-link", path=str(self.source / "external-link"),
                               stat=Mock(return_value=SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)))
        with patch("directory_organizer.snapshots.os.scandir", side_effect=lambda path: scanned_entries([link])) as scan:
            facts = snapshot(self.source, preserve_media=True)
        self.assertEqual(scan.call_count, 1)
        link.stat.assert_called_once_with(follow_symlinks=False)
        self.assertEqual(facts["files"], [])
        self.assertEqual(facts["directories"], [])
        self.assertEqual(facts["issues"][0]["code"], "unsafe-link")
        self.assertFalse(facts["issues"][0]["blocking"])
        with patch("directory_organizer.snapshots.os.scandir", side_effect=lambda path: scanned_entries([link])):
            strict = snapshot(self.source)
        self.assertTrue(strict["issues"][0]["blocking"])

    def test_preserve_snapshot_skips_junctions_and_special_resources(self):
        junction = SimpleNamespace(name="junction", path=str(self.source / "junction"),
                                   stat=Mock(return_value=SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)))
        special = SimpleNamespace(name="special", path=str(self.source / "special"),
                                  stat=Mock(return_value=SimpleNamespace(st_mode=stat.S_IFIFO, st_file_attributes=0)))
        with patch("directory_organizer.snapshots.os.scandir", side_effect=lambda path: scanned_entries([junction, special])) as scan:
            facts = snapshot(self.source, preserve_media=True)
        self.assertEqual(scan.call_count, 1)
        self.assertEqual({issue["code"] for issue in facts["issues"]}, {"unsafe-link", "special-file"})
        self.assertTrue(all(not issue["blocking"] for issue in facts["issues"]))

    def test_unreadable_nested_directory_is_only_a_structure_warning(self):
        unreadable = self.source / "private"
        unreadable.mkdir()
        real_scan = os.scandir

        def scan(path):
            if Path(path) == unreadable:
                raise PermissionError("fixture access denied")
            return real_scan(path)

        with patch("directory_organizer.snapshots.os.scandir", side_effect=scan):
            facts = snapshot(self.source, preserve_media=True)
            with self.assertRaises(PermissionError):
                snapshot(self.source)
        self.assertEqual(facts["directories"], ["private"])
        self.assertEqual(len(facts["files"]), 1)
        self.assertFalse(facts["issues"][0]["blocking"])
        self.assertEqual(facts["issues"][0]["path"], str(unreadable))
        self.assertIn("fixture access denied", facts["issues"][0]["message"])

    def test_unreadable_resource_metadata_does_not_block_db_only(self):
        missing = SimpleNamespace(name="gone", path=str(self.source / "gone"),
                                  stat=Mock(side_effect=PermissionError("fixture missing metadata")))
        with patch("directory_organizer.snapshots.os.scandir", side_effect=lambda path: scanned_entries([missing])):
            facts = snapshot(self.source, preserve_media=True)
        self.assertEqual(facts["files"], [])
        self.assertFalse(facts["issues"][0]["blocking"])
        self.assertEqual(facts["issues"][0]["code"], "structure-unreadable")

    def test_preserved_snapshot_file_and_directory_limits_are_nonblocking(self):
        for name in ("second.mkv", "third.mkv"):
            (self.source / name).write_bytes(b"keep")
        with patch("directory_organizer.snapshots.MAX_FILES", 1):
            facts = snapshot(self.source, preserve_media=True)
            with self.assertRaisesRegex(ValueError, "超过"):
                snapshot(self.source)
        self.assertLessEqual(len(facts["files"]), 1)
        self.assertTrue(facts["issues"])
        self.assertTrue(all(issue["code"] == "structure-limit" and not issue["blocking"] for issue in facts["issues"]))
        for name in ("folder-a", "folder-b", "folder-c"):
            (self.source / name).mkdir()
        with patch("directory_organizer.snapshots.MAX_FILES", 1):
            facts = snapshot(self.source, preserve_media=True)
        self.assertLessEqual(len(facts["directories"]), 1)
        self.assertTrue(facts["issues"])
        self.assertTrue(all(not issue["blocking"] for issue in facts["issues"]))

    def test_nonordinary_source_root_is_always_rejected(self):
        for preserve in (False, True):
            with self.subTest(preserve=preserve), self.assertRaisesRegex(ValueError, "普通目录"):
                snapshot(self.source / "episode.mkv", preserve_media=preserve)
            with self.subTest(preserve=preserve), patch("directory_organizer.snapshots.ordinary_directory", return_value=False):
                with self.assertRaisesRegex(ValueError, "普通目录"):
                    snapshot(self.source, preserve_media=preserve)

    def test_completed_execution_accepts_content_changes_and_keeps_empty_directories(self):
        empty = self.source / "empty-category"
        empty.mkdir()
        media = self.media()
        (self.source / "episode.mkv").write_bytes(b"changed externally")
        (self.source / "new.mkv").write_bytes(b"added after preview")
        os.link(self.source / "episode.mkv", self.source / "linked.mkv")
        with patch("directory_organizer.execution.snapshot", side_effect=AssertionError("must not rescan resources")), \
                patch.object(media, "mkdir", side_effect=AssertionError("must not create media directories")), \
                patch.object(media, "move_exclusive", side_effect=AssertionError("must not move media")):
            media.run()
            self.assertEqual(media.complete({"db_committed": True}), [])
        self.assertTrue(empty.is_dir())
        self.assertEqual((self.source / "episode.mkv").read_bytes(), b"changed externally")
        self.assertTrue((self.source / "new.mkv").is_file())
        self.assertTrue((self.source / "linked.mkv").is_file())
        self.assertEqual(media.moved, [])
        self.assertEqual(media.created, [])
        events = [json.loads(line) for line in media.journal.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["event"] for row in events], ["execution-start", "media-complete", "database-committed", "execution-complete"])
        self.assertTrue(events[0]["media_preserved"])

    def test_completed_execution_rejects_moves_or_target_rename(self):
        media = self.media()
        media.plan["_moves"] = [{"source": "unused", "target": "unused"}]
        with self.assertRaisesRegex(ValueError, "不允许移动或改名"):
            media.run()
        self.assertFalse(media.journal.exists())
        media.plan["_moves"] = []
        media.plan["target_path"] = str(self.base / "renamed")
        with self.assertRaisesRegex(ValueError, "不允许移动或改名"):
            media.run()
        self.assertFalse(media.journal.exists())

    def test_completed_execution_rejects_replaced_root(self):
        media = self.media()
        previous = self.base / "previous-release"
        self.source.rename(previous)
        self.source.mkdir()
        with self.assertRaisesRegex(ValueError, "身份已变化"):
            media.run()
        self.assertFalse(media.journal.exists())
        self.assertEqual((previous / "episode.mkv").read_bytes(), b"original episode")

    def test_completed_execution_rejects_missing_or_nonordinary_root(self):
        media = self.media()
        self.source.rename(self.base / "previous-release")
        with self.assertRaisesRegex(ValueError, "普通目录"):
            media.run()
        self.source.write_bytes(b"foreign file")
        with self.assertRaisesRegex(ValueError, "普通目录"):
            media.run()
        self.assertFalse(media.journal.exists())

    def test_unmarked_execution_keeps_strict_content_fingerprint_check(self):
        media = self.media()
        media.plan["draft"]["completed"] = False
        (self.source / "new.mkv").write_bytes(b"added after preview")
        with self.assertRaisesRegex(ValueError, "来源目录已变化"):
            media.run()
        self.assertFalse(media.journal.exists())


if __name__ == "__main__":
    unittest.main()
