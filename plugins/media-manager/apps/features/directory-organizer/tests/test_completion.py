from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from directory_organizer.completion import CompletionStore
from directory_organizer.snapshots import path_key


class CompletionStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "media" / "Demo_BDRip"
        self.source.mkdir(parents=True)
        self.path = self.base / "state" / "organizer" / "completion.json"
        self.store = CompletionStore(self.path)

    def test_missing_reads_do_not_create_files_or_directories(self):
        self.assertEqual(self.store.read(), {})
        self.assertEqual(self.store.get(self.source), {"completion_marked": False, "completion_revision": ""})
        self.assertFalse(self.path.parent.exists())
        self.path.parent.mkdir(parents=True)
        self.assertEqual(self.store.read(), {})
        self.assertEqual(list(self.path.parent.iterdir()), [])
        self.assertEqual(list(self.source.iterdir()), [])

    def test_persistence_and_idempotence_and_cancellation_revisions(self):
        first = self.store.set(self.source, True)
        self.assertTrue(first["completion_marked"])
        self.assertRegex(first["completion_revision"], r"^[0-9a-f]{32}$")
        reopened = CompletionStore(self.path)
        self.assertEqual(reopened.get(self.source), first)
        before = self.path.stat().st_mtime_ns
        self.assertEqual(reopened.set(self.source, True), first)
        self.assertEqual(self.path.stat().st_mtime_ns, before)
        cancelled = reopened.set(self.source, False)
        self.assertFalse(cancelled["completion_marked"])
        self.assertNotEqual(cancelled["completion_revision"], first["completion_revision"])
        self.assertEqual(reopened.set(self.source, False), cancelled)
        again = reopened.set(self.source, True)
        self.assertNotIn(again["completion_revision"], {first["completion_revision"], cancelled["completion_revision"]})
        self.assertEqual(list(self.source.iterdir()), [])

    def test_false_without_existing_entry_is_saved(self):
        result = self.store.set(self.source, False)
        self.assertFalse(result["completion_marked"])
        self.assertTrue(result["completion_revision"])
        self.assertEqual(CompletionStore(self.path).get(self.source), result)

    def test_new_directory_at_same_path_does_not_inherit_old_identity(self):
        first = self.store.set(self.source, True)
        self.source.rename(self.source.with_name("old-directory"))
        self.source.mkdir()
        self.assertEqual(self.store.get(self.source), {"completion_marked": False, "completion_revision": ""})
        replacement = self.store.set(self.source, True)
        self.assertNotEqual(replacement["completion_revision"], first["completion_revision"])
        self.assertEqual(self.store.get(self.source), replacement)

    def test_preloaded_entries_avoid_reading_again(self):
        result = self.store.set(self.source, True)
        entries = self.store.read()
        self.path.unlink()
        self.assertEqual(self.store.get(self.source, entries=entries), result)
        self.assertFalse(self.store.get(self.source)["completion_marked"])

    def test_corruption_is_reported_and_never_overwritten(self):
        self.path.parent.mkdir(parents=True)
        invalid = ["{", "[]", '{"version":true,"directories":{}}',
                   '{"version":1,"version":1,"directories":{}}',
                   '{"version":2,"directories":{}}',
                   '{"version":1,"directories":{"relative":{}}}']
        valid = {"completed": True, "revision": "a" * 32, "identity": [1, 2]}
        for field, value in [("completed", "true"), ("revision", "invalid"), ("identity", [True, 2])]:
            invalid.append(json.dumps({"version": 1, "directories": {path_key(self.source): {**valid, field: value}}}))
        for content in invalid:
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "无法读取整理完成状态文件") as error:
                    self.store.get(self.source)
                self.assertIn(str(self.path), str(error.exception))
                with self.assertRaises(ValueError):
                    self.store.set(self.source, True)
                self.assertEqual(self.path.read_text(encoding="utf-8"), content)

    def test_non_boolean_completion_is_rejected_without_writes(self):
        for value in [1, "true", None]:
            with self.assertRaises(ValueError):
                self.store.set(self.source, value)
        self.assertFalse(self.path.parent.exists())

    def test_non_file_state_is_rejected(self):
        self.path.mkdir(parents=True)
        with self.assertRaises(ValueError):
            self.store.read()
        with self.assertRaises(ValueError):
            self.store.set(self.source, True)

    def test_hardlinked_state_and_lock_are_rejected(self):
        self.store.set(self.source, True)
        other = self.path.parent / "other.json"
        os.link(self.path, other)
        with self.assertRaises(ValueError):
            self.store.read()
        with self.assertRaises(ValueError):
            self.store.set(self.source, False)
        other.unlink()
        lock = self.path.parent / ".nimda-catalog.lock"
        os.link(lock, self.path.parent / "other.lock")
        with self.assertRaises(ValueError):
            self.store.set(self.source, False)

    def test_symlinked_state_and_parent_are_rejected(self):
        self.store.set(self.source, True)
        actual = self.path.with_name("actual.json")
        self.path.rename(actual)
        try:
            self.path.symlink_to(actual)
        except OSError as exc:
            self.skipTest(f"当前环境不允许创建符号链接：{exc}")
        with self.assertRaises(ValueError):
            self.store.read()
        with self.assertRaises(ValueError):
            self.store.set(self.source, False)
        alias = self.base / "linked-state"
        alias.symlink_to(self.path.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            CompletionStore(alias / "actual.json").read()

    def test_reparse_state_or_parent_is_rejected_without_link_privileges(self):
        self.store.set(self.source, True)
        original_lstat = Path.lstat
        for unsafe in [self.path, self.path.parent]:
            with self.subTest(unsafe=unsafe):
                def lstat(path, *args, **kwargs):
                    metadata = original_lstat(path, *args, **kwargs)
                    if path == unsafe:
                        return SimpleNamespace(st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                                               st_file_attributes=0x400)
                    return metadata

                with patch.object(Path, "lstat", lstat):
                    with self.assertRaises(ValueError):
                        self.store.read()
                    with self.assertRaises(ValueError):
                        self.store.set(self.source, False)
        self.assertTrue(self.store.get(self.source)["completion_marked"])

    def test_thread_writers_keep_all_directory_entries(self):
        sources = [self.source.with_name(f"work-{index}") for index in range(12)]
        for source in sources:
            source.mkdir()
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(lambda source: CompletionStore(self.path).set(source, True), sources))
        self.assertEqual(len(self.store.read()), len(sources))
        for source, expected in zip(sources, results):
            self.assertEqual(self.store.get(source), expected)

    def test_transaction_is_reentrant_and_blocks_other_thread(self):
        started, finished = threading.Event(), threading.Event()

        def write():
            started.set()
            CompletionStore(self.path).set(self.source, False)
            finished.set()

        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.store.transaction():
                with self.store.transaction():
                    self.store.set(self.source, True)
                pending = executor.submit(write)
                self.assertTrue(started.wait(2))
                self.assertFalse(finished.wait(0.1))
                self.assertTrue(self.store.get(self.source)["completion_marked"])
            pending.result(timeout=5)
        self.assertFalse(self.store.get(self.source)["completion_marked"])

    def test_process_writers_keep_all_directory_entries(self):
        sources = [self.source.with_name(f"process-{index}") for index in range(8)]
        for source in sources:
            source.mkdir()
        script = ("from pathlib import Path; import sys; "
                  "from directory_organizer.completion import CompletionStore; "
                  "store = CompletionStore(Path(sys.argv[1])); "
                  "[store.set(Path(source), True) for source in sys.argv[2:]]")
        processes = [subprocess.Popen([sys.executable, "-c", script, str(self.path), *map(str, sources[index::2])],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for index in range(2)]
        try:
            for process in processes:
                stdout, stderr = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, stdout + stderr)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        self.assertEqual(len(self.store.read()), len(sources))
        self.assertTrue(all(self.store.get(source)["completion_marked"] for source in sources))


if __name__ == "__main__":
    unittest.main()
