from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from work_catalog_yaml.common import media_files


class MediaFilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        media_files._cached_duration.cache_clear()
        self.addCleanup(media_files._cached_duration.cache_clear)

    def file(self, relative, content=b"not real media"):
        path = self.root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_scan_prefers_all_direct_disc_trees_without_writes(self):
        selected = ["Disc/Work [01].mkv", "_Disc/02/02.MP4",
                    "Work_BDRip_Disc/Work_03/[Work][03][1080P][X264].mkv"]
        ignored = ["Movie.mkv", "Work_04/04.mkv", "Others/04.mkv", "Work_Others/05.mkv", "CM/CM01.mkv",
                   "Unknown/Disc/06.mkv", "CD/track.flac", "Disc/01.ass",
                   "Disc/run.exe", "Disc/movie.mkv.lnk", "Disc/script.ps1", "Disc/image.iso"]
        for name in selected + ignored:
            self.file(name)
        before = {path: path.stat() for path in self.root.rglob("*") if path.is_file()}
        with patch("builtins.open", side_effect=AssertionError("scan must not read or write media")), \
                patch.object(Path, "open", side_effect=AssertionError("scan must not open files")):
            result = media_files.scan_media_files(self.root)
        by_path = {row["relative_path"]: row for row in result["files"]}
        self.assertEqual(set(by_path), set(selected))
        self.assertEqual(result["warnings"], [])
        self.assertFalse(result["truncated"])
        self.assertEqual(by_path[selected[0]]["episode"], "01")
        self.assertEqual(by_path[selected[1]]["episode"], "02")
        self.assertEqual(by_path[selected[2]]["episode"], "03")
        for path, metadata in before.items():
            self.assertEqual(path.stat().st_mtime_ns, metadata.st_mtime_ns)
            if path.relative_to(self.root).as_posix() in by_path:
                row = by_path[path.relative_to(self.root).as_posix()]
                self.assertEqual(row["size"], metadata.st_size)
                self.assertEqual(row["mtime_ns"], metadata.st_mtime_ns)
                self.assertEqual(row["signature"], [metadata.st_dev, metadata.st_ino,
                                                    metadata.st_ctime_ns, metadata.st_size, metadata.st_mtime_ns])

    def test_no_disc_uses_resource_as_disc_with_flat_and_episode_directories(self):
        expected = {"Work_01.mkv": "01", "Work_02/main.mp4": "02",
                    "第一幕/Work_EP03.mkv": "03", "Work.Name_04/untitled.mkv": "04",
                    "Movie.mkv": ""}
        ignored = ["Others/05.mkv", "Image/06.mkv", "Work_Subtitles/07.mkv", "Work_Music/08.mkv", "Booklet/09.mkv",
                   "Audio/08.mkv", "Work_02/NCOP01.mkv", "Work_02/Fonts/09.mkv"]
        for name in [*expected, *ignored]:
            self.file(name)
        result = media_files.scan_media_files(self.root)
        self.assertEqual({row["relative_path"]: row["episode"] for row in result["files"]}, expected)
        for name in expected:
            self.assertEqual(media_files.safe_media_path(self.root, name), self.root.joinpath(*name.split("/")))
        for name in ignored:
            with self.subTest(name=name), self.assertRaises(ValueError):
                media_files.safe_media_path(self.root, name)

    def test_disc_flat_layout_and_plain_episode_folders_match_same_rules(self):
        expected = {"_Disc/Work_BDRip_01.mkv": "01", "_Disc/Work_02/main.mp4": "02",
                    "_Disc/自定义名/Work EP03.mkv": "03"}
        for name in expected:
            self.file(name)
        for name in ("_Disc/OP/04.mkv", "_Disc/Work_02/Subtitles/02.mkv", "_Disc/CM01.mkv"):
            self.file(name)
        result = media_files.scan_media_files(self.root)
        self.assertEqual({row["relative_path"]: row["episode"] for row in result["files"]}, expected)
        self.assertEqual(result["warnings"], [])

    def test_disc_precedence_is_rechecked_before_opening_existing_root_video(self):
        target = self.file("Work_01/main.mkv")
        self.assertEqual(media_files.safe_media_path(self.root, "Work_01/main.mkv"), target)
        self.file("Disc/02.mkv")
        with patch.object(os, "startfile", create=True) as launch:
            with self.assertRaises(ValueError):
                media_files.open_media_file(self.root, "Work_01/main.mkv")
            launch.assert_not_called()
        self.assertEqual([row["relative_path"] for row in media_files.scan_media_files(self.root)["files"]], ["Disc/02.mkv"])

    def test_folder_fallback_conflicts_and_multiple_versions_are_conservative(self):
        expected = {
            "Disc/Work_01/main.mkv": "01",
            "Disc/Work_02/EP03.mkv": "",
            "Disc/Work_04/EP04 [05].mkv": "",
            "Disc/Work_06/version-a.mkv": "",
            "Disc/Work_06/version-b.mp4": "",
            "Disc/Work_07/Work_07.mkv": "07",
            "Disc/Work_07/Work_07v2.mp4": "07",
            "Disc/Work_08/EP08.mkv": "08",
            "Disc/Work_08/main.mkv": "",
            "Disc/Work_S02E09/Work_EP09.mkv": "09",
            "Disc/Work_S02E10/Work_S01E10.mkv": "",
            "Disc/Work_11/[01-12].mkv": "",
            "Disc/Work_2020/main.mkv": "",
            "Disc/Work_1080/main.mkv": "",
            "Disc/Work_12/Version_02/EP12.mkv": "12",
            "Disc/Work_13/Version_01/main.mkv": "",
            "Disc/Work_13/Version_02/main.mp4": "",
            "Disc/Work_14/Version_02/main.mkv": "14",
            "Disc/custom/Version_15/main.mkv": "",
        }
        for name in expected:
            self.file(name)
        self.file("Disc/Work_01/NCOP01.mp4")
        result = media_files.scan_media_files(self.root)
        self.assertEqual({row["relative_path"]: row["episode"] for row in result["files"]}, expected)
        self.assertEqual(result["warnings"], [])

    def test_scan_enumerates_resource_root_once_not_once_per_video(self):
        for number in range(1, 12):
            self.file(f"Disc/Work_{number:02}/Work_{number:02}.mkv")
        original = os.scandir
        with patch.object(os, "scandir", wraps=original) as scan:
            self.assertEqual(len(media_files.scan_media_files(self.root)["files"]), 11)
        self.assertEqual(sum(Path(call.args[0]) == self.root for call in scan.call_args_list), 1)

    def test_scan_signature_detects_replacement_with_same_name_size_and_mtime(self):
        target = self.file("Disc/01.mkv", b"old fixture")
        before = media_files.scan_media_files(self.root)["files"][0]
        metadata = target.stat()
        replacement = self.file("replacement.tmp", b"new fixture")
        os.utime(replacement, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        os.replace(replacement, target)
        after = media_files.scan_media_files(self.root)["files"][0]
        self.assertEqual(after["relative_path"], before["relative_path"])
        self.assertEqual(after["size"], before["size"])
        self.assertEqual(after["mtime_ns"], before["mtime_ns"])
        self.assertNotEqual(after["signature"], before["signature"])

    def test_episode_detection_is_filename_only_and_conservative(self):
        cases = {
            "[BECK][Live_05][v2][BDRip][1080P][X264_FLAC].mkv": "05",
            "[Work][01v2][BDRip][1080P].mkv": "01", "Work E02.mkv": "02",
            "Work EP1000.mkv": "1000", "Work Episode 3.mkv": "03",
            "Work S02E03v2.mkv": "S02E03", "第 12 话.mkv": "12",
            "Work - 07 [1080p].mkv": "07", "08.mkv": "08",
            "Work_BDRip_01.mkv": "01", "Work_01.mkv": "01",
            "作品 01 [1080p].mkv": "01", "作品 - 01.mkv": "01",
            "Work_01v2.mkv": "01", "Work_EP01_AAC2.0.mkv": "01",
            "Work EP01 AAC2.0.mkv": "01", "Work EP01 AAC5.1.mkv": "01",
            "Work_01_[1080p][10bit][AAC2.0].mkv": "01",
            "Work AAC2.0.mkv": "", "Work AAC5.1.mkv": "",
            "001 成为死神的这天.mkv": "01", "002 死神的工作.mkv": "02",
            "366 变迁的历史，不变的心.mkv": "366", "01 单集标题.mkv": "01",
            "014话-背靠背的死斗！.mkv": "14", "053市丸银的诱惑，崩溃的精神准备.mkv": "53",
            "326两个雏森，日番谷的决心！.mkv": "326", "366改变的历史、不变的心.mkv": "366",
            "001はじまり.mkv": "01", "002시작.mkv": "02",
            "86数字作品名.mkv": "", "1080高清.mkv": "", "2020年作品.mkv": "",
            "001H264.mkv": "", "001AAC2.0.mkv": "", "176&177合辑.mkv": "",
            "86 数字作品名.mkv": "", "2020 数字作品名.mkv": "",
            "1080 高清.mkv": "", "720 清晰.mkv": "",
            "[Gun x Sword][17(O.A. Ver.)][BDRIP][1080P][H264_FLAC_THD].mkv": "17",
            "Work [17 (O.A. Ver.)].mkv": "17", "Work [17(18)].mkv": "",
            "Work [17-18(O.A. Ver.)].mkv": "",
            "Work EP01 [1080p][10bit][AAC2.0].mkv": "01",
            "86 [1080p][10bit][AAC2.0].mkv": "",
            "86 - 01 [1080p].mkv": "",
            "Disc/Work_09/Untitled.mkv": "", "Work_2020.mkv": "",
            "[BECK][MOON ON THE WATER][BDRip][1080P][X264_FLAC].mkv": "",
            "[BECK][SLIP OUT][BDRip][1080P][X264_FLAC].mkv": "",
            "Movie [2004][1080P][X264].mkv": "", "Movie [1080].mkv": "",
            "Work E1080p.mkv": "", "Work [01][02].mkv": "",
            "Work E01 E02.mkv": "", "Work S01E01 S01E02.mkv": "",
            "Work E01-02.mkv": "", "Work [01-12].mkv": "",
            "Work S01E01-E02.mkv": "", "NCOP01.mkv": "", "CM01.mkv": "",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(media_files.media_episode_key(name), expected)

    def test_conflicting_marker_families_do_not_silently_choose_one_episode(self):
        for name in ("S01E01 E02.mkv", "Episode01 [02].mkv", "[02] Episode01.mkv",
                     "S01E01 [02].mkv", "S01E01 第2集.mkv", "Live_01 [02].mkv",
                     "Episode01 - 02.mkv", "S01E01 S02E01.mkv", "第1话 [02].mkv"):
            with self.subTest(name=name):
                self.assertEqual(media_files.media_episode_key(name), "")

    def test_agreeing_markers_keep_episode_and_ignore_year_resolution_tags(self):
        cases = {"S01E01 [01].mkv": "S01E01", "S01E01 E01 [01].mkv": "S01E01",
                 "EP01 [1080].mkv": "01", "Episode01 [2004][720].mkv": "01",
                 "S01E01 [2020][2160].mkv": "S01E01", "第1话 [01].mkv": "01",
                 "EP2011 [1080].mkv": "2011", "[Live_01][01].mkv": "01",
                 "Foo_01.mkv": "01"}
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(media_files.media_episode_key(name), expected)

    def test_safe_path_normalizes_relative_separators_and_keeps_absolute_root(self):
        target = self.file("Work_Disc/01/Work [01].mkv")
        self.assertEqual(media_files.safe_media_path(self.root, "Work_Disc\\01\\Work [01].mkv"), target)

    def test_unsafe_windows_paths_and_nonmedia_are_rejected_before_disk_access(self):
        bad = ["", "../file.mkv", "Disc/../file.mkv", "/file.mkv", "C:/file.mkv",
               "C:file.mkv", "\\\\server\\share\\file.mkv", "\\\\?\\C:\\file.mkv",
               "Disc//file.mkv", "./file.mkv", "Disc/file.mkv:stream", "Disc /file.mkv",
               "Disc./file.mkv", "Disc/file.mkv.", "Disc/file.mkv ", "Disc/NUL.mkv",
               "Disc/COM¹.mkv", "Disc/a\x00.mkv", "Disc/*.mkv", "Disc/<file>.mkv",
               "Disc/a\x7f.mkv", "Disc/a\x80.mkv", "Disc/a\x9f.mkv",
               "Disc/CONIN$.mkv", "Disc/conout$.mkv", "Disc/CONIN$/01.mkv",
               "Disc/run.exe", "Disc/run.bat", "Disc/run.ps1", "Disc/video.mkv.lnk",
               "Disc/image.iso", "Others/file.mkv", "Unknown/Disc/file.mkv"]
        with patch.object(Path, "lstat", side_effect=AssertionError("invalid input must not access disk")):
            for name in bad:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    media_files.safe_media_path(self.root, name)

    def test_missing_files_and_directories_named_as_video_cannot_open(self):
        (self.root / "folder.mkv").mkdir()
        for name in ("missing.mkv", "folder.mkv"):
            with self.subTest(name=name), patch.object(os, "startfile", create=True) as launch:
                with self.assertRaises((ValueError, OSError)):
                    media_files.open_media_file(self.root, name)
                launch.assert_not_called()

    def test_unsafe_root_or_disc_ancestor_is_rejected_before_child_access(self):
        target = self.file("Disc/01.mkv")
        original = Path.lstat
        for unsafe in (self.root, self.root / "Disc"):
            for mode, attributes in ((stat.S_IFLNK, 0), (stat.S_IFDIR, 0x400)):
                with self.subTest(unsafe=unsafe, mode=mode):
                    touched = []

                    def lstat(path, *args, **kwargs):
                        touched.append(path)
                        if path == unsafe:
                            return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
                        if path == target:
                            raise AssertionError("unsafe ancestor must be rejected before file probe")
                        return original(path, *args, **kwargs)

                    with patch.object(Path, "lstat", autospec=True, side_effect=lstat):
                        with self.assertRaises(ValueError):
                            media_files.safe_media_path(self.root, "Disc/01.mkv")
                    self.assertNotIn(target, touched)

    def test_scan_skips_link_files_and_junction_subtrees(self):
        safe = self.file("Disc/01.mkv")
        linked = self.file("Disc/linked.mkv")
        junction = self.root / "Disc" / "Junction"
        junction.mkdir()
        self.file("Disc/Junction/02.mkv")
        original = Path.lstat

        def lstat(path, *args, **kwargs):
            if path == linked:
                return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
            if path == junction:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            if junction in path.parents:
                raise AssertionError("junction content must not be inspected")
            return original(path, *args, **kwargs)

        with patch.object(Path, "lstat", autospec=True, side_effect=lstat):
            result = media_files.scan_media_files(self.root)
        self.assertEqual([row["relative_path"] for row in result["files"]], [safe.relative_to(self.root).as_posix()])

    def test_scan_limits_file_count_entries_and_depth(self):
        self.file("Disc/01.mkv")
        self.file("Disc/02.mkv")
        self.file("Disc/nested/03.mkv")
        result = media_files.scan_media_files(self.root, max_files=1)
        self.assertEqual(len(result["files"]), 1)
        self.assertTrue(result["truncated"])
        result = media_files.scan_media_files(self.root, max_entries=1)
        self.assertLessEqual(len(result["files"]), 1)
        self.assertTrue(result["truncated"])
        result = media_files.scan_media_files(self.root, max_depth=1)
        self.assertEqual(len(result["files"]), 2)
        self.assertTrue(result["truncated"])
        for options in ({"max_files": 0}, {"max_entries": True}, {"max_depth": 33}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                media_files.scan_media_files(self.root, **options)

    def test_partial_root_inventory_cannot_fall_back_to_root_media(self):
        self.file("01.mkv")
        self.file("Disc/02.mkv")
        result = media_files.scan_media_files(self.root, max_entries=1)
        self.assertEqual(result["files"], [])
        self.assertTrue(result["truncated"])

    def test_truncated_episode_folder_cannot_make_versions_look_unique(self):
        self.file("Disc/Work_01/main-a.mkv")
        self.file("Disc/Work_01/main-b.mkv")
        result = media_files.scan_media_files(self.root, max_files=1)
        self.assertEqual(len(result["files"]), 1)
        self.assertEqual(result["files"][0]["episode"], "")
        self.assertTrue(result["truncated"])

    def test_no_disc_link_episode_directory_and_link_media_are_rejected(self):
        target = self.file("Work_01/main.mkv")
        original = Path.lstat
        for unsafe in (target.parent, target):
            def lstat(path, *args, **kwargs):
                if path == unsafe:
                    return SimpleNamespace(st_mode=stat.S_IFDIR if path == target.parent else stat.S_IFREG,
                                           st_file_attributes=0x400)
                if unsafe == target.parent and path == target:
                    raise AssertionError("junction content must not be inspected")
                return original(path, *args, **kwargs)
            with self.subTest(unsafe=unsafe), patch.object(Path, "lstat", autospec=True, side_effect=lstat):
                self.assertEqual(media_files.scan_media_files(self.root)["files"], [])
                with self.assertRaises(ValueError):
                    media_files.safe_media_path(self.root, "Work_01/main.mkv")

    def test_unreadable_disc_reports_incomplete_inventory(self):
        self.file("Disc/01.mkv")
        original = os.scandir

        def scandir(path):
            if Path(path) == self.root / "Disc":
                raise PermissionError("offline")
            return original(path)

        with patch.object(os, "scandir", side_effect=scandir):
            result = media_files.scan_media_files(self.root)
        self.assertEqual(result["files"], [])
        self.assertTrue(result["warnings"])

    def test_open_only_calls_default_player_with_revalidated_absolute_file(self):
        target = self.file("Disc/01.mkv")
        with patch.object(os, "startfile", create=True) as launch, \
                patch.object(media_files, "safe_media_path", wraps=media_files.safe_media_path) as verify:
            self.assertEqual(media_files.open_media_file(self.root, "Disc/01.mkv"), target)
        self.assertEqual(verify.call_count, 2)
        launch.assert_called_once_with(str(target), "open")

    def test_replacement_during_open_recheck_does_not_launch(self):
        target = self.file("Disc/01.mkv")
        with patch.object(media_files, "safe_media_path", side_effect=[target, ValueError("replaced")]), \
                patch.object(os, "startfile", create=True) as launch:
            with self.assertRaises(ValueError):
                media_files.open_media_file(self.root, "Disc/01.mkv")
        launch.assert_not_called()

    def test_probe_is_bounded_local_cached_and_invalidated_by_file_change(self):
        target = self.file("01.mkv")
        with patch.object(media_files.shutil, "which", return_value="C:/tools/ffprobe.exe"), \
                patch.object(media_files.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="1422.125\n")) as run:
            self.assertEqual(media_files.probe_media_duration(target), 1422.125)
            self.assertEqual(media_files.probe_media_duration(target), 1422.125)
            self.assertEqual(run.call_count, 1)
            command = run.call_args.args[0]
            self.assertEqual(command[-2:], ["-i", str(target)])
            self.assertIn("file,pipe", command)
            self.assertEqual(run.call_args.kwargs["timeout"], 3.0)
            self.assertFalse(run.call_args.kwargs.get("shell", False))
            target.write_bytes(b"changed media fixture")
            self.assertEqual(media_files.probe_media_duration(target), 1422.125)
            self.assertEqual(run.call_count, 2)

    def test_absent_probe_and_invalid_file_do_not_spawn_processes(self):
        target = self.file("01.mkv")
        bad = self.file("run.exe")
        with patch.object(media_files.shutil, "which", return_value=None), \
                patch.object(media_files.subprocess, "run") as run:
            for path in (target, bad, self.root / "missing.mkv", "relative.mkv"):
                self.assertIsNone(media_files.probe_media_duration(path))
            run.assert_not_called()

    def test_failed_duration_probe_can_retry_without_file_change_and_caches_success(self):
        target = self.file("01.mkv")
        for failure in (subprocess.TimeoutExpired("ffprobe", 3), OSError("temporary I/O failure"),
                        SimpleNamespace(returncode=1, stdout="")):
            media_files._cached_duration.cache_clear()
            with self.subTest(failure=failure), patch.object(media_files.shutil, "which", return_value="ffprobe"), \
                    patch.object(media_files.subprocess, "run", side_effect=[failure, SimpleNamespace(returncode=0, stdout="42.5")]) as run:
                self.assertIsNone(media_files.probe_media_duration(target))
                self.assertEqual(media_files._cached_duration.cache_info().currsize, 0)
                self.assertEqual(media_files.probe_media_duration(target), 42.5)
                self.assertEqual(media_files.probe_media_duration(target), 42.5)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(media_files._cached_duration.cache_info().currsize, 1)

    def test_duration_availability_only_checks_local_executable(self):
        with patch.object(media_files.subprocess, "run", side_effect=AssertionError("must not launch a probe")), \
                patch.object(Path, "lstat", side_effect=AssertionError("must not inspect media")):
            for executable, expected in ((None, False), ("C:/tools/ffprobe.exe", True)):
                with self.subTest(executable=executable), patch.object(media_files.shutil, "which", return_value=executable):
                    self.assertEqual(media_files.duration_available(), expected)

    def test_probe_rejects_link_file_and_invalid_timeout_without_process(self):
        target = self.file("01.mkv")
        for timeout in (0, -1, 11, float("nan"), "invalid"):
            with self.subTest(timeout=timeout), patch.object(media_files.subprocess, "run") as run:
                self.assertIsNone(media_files.probe_media_duration(target, timeout=timeout))
                run.assert_not_called()
        original = Path.lstat

        def lstat(path, *args, **kwargs):
            if path == target:
                return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)
            return original(path, *args, **kwargs)

        with patch.object(Path, "lstat", autospec=True, side_effect=lstat), \
                patch.object(media_files.subprocess, "run") as run:
            self.assertIsNone(media_files.probe_media_duration(target))
            run.assert_not_called()

    def test_probe_nonzero_exit_never_exposes_partial_duration(self):
        target = self.file("01.mkv")
        with patch.object(media_files.shutil, "which", return_value="ffprobe"), \
                patch.object(media_files.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="42")):
            self.assertIsNone(media_files.probe_media_duration(target))

    def test_probe_failures_and_invalid_durations_are_optional(self):
        target = self.file("01.mkv")
        for value in ("N/A", "nan", "inf", "-1", "0", "", "1\n2"):
            media_files._cached_duration.cache_clear()
            with self.subTest(value=value), patch.object(media_files.shutil, "which", return_value="ffprobe"), \
                    patch.object(media_files.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=value)):
                self.assertIsNone(media_files.probe_media_duration(target))
        for error in (subprocess.TimeoutExpired("ffprobe", 3), OSError("unavailable")):
            media_files._cached_duration.cache_clear()
            with self.subTest(error=error), patch.object(media_files.shutil, "which", return_value="ffprobe"), \
                    patch.object(media_files.subprocess, "run", side_effect=error):
                self.assertIsNone(media_files.probe_media_duration(target))

    def test_probe_rejects_changed_file_metadata_after_duration_read(self):
        target = self.file("01.mkv")

        def run(*args, **kwargs):
            target.write_bytes(b"replacement")
            return SimpleNamespace(returncode=0, stdout="10")

        with patch.object(media_files.shutil, "which", return_value="ffprobe"), \
                patch.object(media_files.subprocess, "run", side_effect=run):
            self.assertIsNone(media_files.probe_media_duration(target))


if __name__ == "__main__":
    unittest.main()
