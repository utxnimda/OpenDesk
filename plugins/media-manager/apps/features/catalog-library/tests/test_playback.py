from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from catalog_library import playback, service
from work_catalog_yaml.jp_tv.browse_settings import JpTvBrowseSettings
from work_catalog_yaml.yaml_io import dump_yaml_string, load_yaml_string


class PlaybackTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "db"
        self.db.mkdir()
        self.media = self.root / "media"
        self.press = self.media / "Demo_BDRip"
        self.disc = self.press / "Demo_BDRip_Disc"
        self.disc.mkdir(parents=True)
        (self.disc / "[Demo][01].mkv").write_bytes(b"fixture-video-one")
        (self.disc / "[Demo][02].mkv").write_bytes(b"fixture-video-two")
        (self.disc / "[Demo][01].ass").write_text("subtitle", encoding="utf-8")
        other = self.press / "Demo_BDRip_Others"
        other.mkdir()
        (other / "[01].mkv").write_bytes(b"bonus")
        self.record = {"attributes": [
            {"type": "name", "data": "Demo"}, {"type": "country", "data": "japan"},
            {"type": "date", "data": {"start": "20050101", "end": "20050301"}},
            {"type": "collection-type", "data": {"domain": "animation", "release_type": "tv", "path": str(self.media),
             "collectioned": [{"press_format": "BDRip", "press_group": "", "press_path": self.press.name}]}},
        ], "metadata": {"summary": "Keep summary", "episodes": [
            {"id": "bangumi:1", "ep": 1, "sort": 1, "type": 0, "title": "One", "air_date": "20050101"},
            {"id": "bangumi:2", "ep": 2, "sort": 2, "type": 0, "title": "Two"}]}}
        self.source = self.db / "[JP][TVInfo][2005].yaml"
        self.write_record()
        self.settings = JpTvBrowseSettings(version=1, filesystem_root=self.db, resolved_default_readable=str(self.source),
            resolved_catalog_yaml_paths=(str(self.source),), enum_options={}, enum_labels={}, enum_section_labels={}, app_features=())
        self.library = self.root / "library"
        availability = patch.object(playback.media_files, "duration_available", return_value=False)
        availability.start()
        self.addCleanup(availability.stop)

    def write_record(self):
        self.source.write_text(dump_yaml_string([self.record]), encoding="utf-8")

    def call(self, name, body):
        return getattr(playback, name)(body, settings=self.settings, data_root=self.library)

    def ref(self):
        return service.detail({"ref": service._records(self.settings)[0]["ref"]}, settings=self.settings, data_root=self.library)["ref"]

    def scan(self, action="select"):
        return self.call(action, {"ref": self.ref(), "resource_index": 0})

    def body(self, scan):
        return {key: scan[key] for key in ("ref", "resource_index", "revision")}

    def assignment(self, scan, path, index=0):
        return {**self.body(scan), "assignments": [{"chapter_key": scan["chapters"][index]["chapter_key"], "relative_path": path}]}

    def open_body(self, scan, index=0):
        chapter = scan["chapters"][index]
        return {**self.body(scan), "chapter_key": chapter["chapter_key"], "relative_path": chapter["relative_path"]}

    def use_media_layout(self, relative_paths):
        self.press = self.media / "Layout_BDRip"
        self.disc = self.press / "Disc"
        for relative_path in relative_paths:
            target = self.press / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"fixture-video")
        self.record["attributes"][-1]["data"]["collectioned"][0]["press_path"] = self.press.name
        self.write_record()

    def assert_playback_round_trip(self, expected_paths):
        media_before = {str(path): path.read_bytes() for path in self.press.rglob("*") if path.is_file()}
        selected = self.scan()
        self.assertTrue(selected["db_committed"])
        self.assertEqual([row["status"] for row in selected["chapters"]], ["automatic"] * len(expected_paths))
        self.assertEqual([row["relative_path"] for row in selected["chapters"]], expected_paths)
        self.assertEqual({row["relative_path"] for row in selected["files"]}, set(expected_paths))
        record = load_yaml_string(self.source.read_text(encoding="utf-8"))[0]
        self.assertEqual(record["local_playback"]["selected_resource_key"], selected["resource_key"])
        self.assertEqual(record["local_playback"]["resources"][selected["resource_key"]]["scan"]["files"], selected["files"])
        with service._CACHE_LOCK:
            service._CACHE.clear()
        with patch.object(playback.media_files, "scan_media_files", side_effect=AssertionError("Detail must use DB cache")), \
             patch.object(playback.media_files, "probe_media_duration", side_effect=AssertionError("Detail must not probe")):
            detail = service.detail({"ref": selected["ref"]}, settings=self.settings, data_root=self.library)
        self.assertEqual(detail["playback"]["files"], selected["files"])
        self.assertEqual(detail["playback"]["chapters"], selected["chapters"])
        self.assertEqual(detail["playback"]["revision"], selected["revision"])
        for index, relative_path in enumerate(expected_paths):
            with self.subTest(relative_path=relative_path), patch.object(playback.media_files.os, "startfile", create=True) as launch:
                result = self.call("open_chapter", self.open_body(detail["playback"], index))
                self.assertTrue(result["ok"])
                self.assertEqual(result["relative_path"], relative_path)
                launch.assert_called_once_with(str(self.press / relative_path), "open")
        self.assertEqual({str(path): path.read_bytes() for path in self.press.rglob("*") if path.is_file()}, media_before)
        return selected

    def test_resource_root_flat_videos_round_trip_when_no_disc_exists(self):
        self.use_media_layout(["Work_01.mkv", "Work_02.mp4", "Others/Work_01.mkv", "Scans/Work_02.mkv"])
        self.assert_playback_round_trip(["Work_01.mkv", "Work_02.mp4"])

    def test_resource_root_episode_directories_round_trip_when_no_disc_exists(self):
        self.use_media_layout(["Work_01/feature.mkv", "Work_02/Work_02.mp4", "Subs/Work_01.mkv"])
        self.assert_playback_round_trip(["Work_01/feature.mkv", "Work_02/Work_02.mp4"])

    def test_disc_episode_directories_round_trip_and_exclude_auxiliary_resources(self):
        self.use_media_layout(["Disc/Work_01/feature.mkv", "Disc/Work_02/Work_02.mp4",
                               "Disc/Others/Work_01.mkv", "Disc/Scans/Work_02.mkv", "Others/Work_01.mkv"])
        self.assert_playback_round_trip(["Disc/Work_01/feature.mkv", "Disc/Work_02/Work_02.mp4"])

    def test_direct_disc_prevents_root_files_from_stealing_episode_matches(self):
        self.use_media_layout(["Work_01.mkv", "Work_02.mp4", "Disc/Work_01.mkv", "Disc/Work_02.mp4"])
        selected = self.assert_playback_round_trip(["Disc/Work_01.mkv", "Disc/Work_02.mp4"])
        with self.assertRaises(ValueError):
            self.call("save", self.assignment(selected, "Work_01.mkv"))
        with patch.object(playback.media_files.os, "startfile", create=True) as launch:
            with self.assertRaisesRegex(ValueError, "不一致"):
                self.call("open_chapter", {**self.open_body(selected), "relative_path": "Work_01.mkv"})
        launch.assert_not_called()

    def test_episode_directory_multiple_versions_require_manual_confirmation(self):
        self.use_media_layout(["Disc/Work_01/[Demo][01].mkv", "Disc/Work_01/[Demo][01][v2].mp4",
                               "Disc/Work_02/Work_02.mkv"])
        selected = self.scan()
        self.assertEqual(selected["chapters"][0]["status"], "ambiguous")
        self.assertEqual(len(selected["chapters"][0]["candidates"]), 2)
        with patch.object(playback.media_files.os, "startfile", create=True) as launch:
            with self.assertRaisesRegex(ValueError, "尚未唯一"):
                self.call("open_chapter", self.open_body(selected))
        launch.assert_not_called()
        chosen = "Disc/Work_01/[Demo][01][v2].mp4"
        saved = self.call("save", self.assignment(selected, chosen))
        self.assertEqual(saved["chapters"][0]["status"], "confirmed")
        self.assertEqual(saved["chapters"][0]["relative_path"], chosen)
        record = load_yaml_string(self.source.read_text(encoding="utf-8"))[0]
        self.assertEqual(record["local_playback"]["resources"][saved["resource_key"]]["assignments"],
                         {saved["chapters"][0]["chapter_key"]: chosen})
        with patch.object(playback.media_files.os, "startfile", create=True) as launch:
            self.call("open_chapter", self.open_body(saved))
        launch.assert_called_once_with(str(self.press / chosen), "open")

    def test_conflicting_episode_directory_and_filename_remain_manual_candidates(self):
        conflicting = "Disc/Work_01/Work_02.mkv"
        self.use_media_layout([conflicting, "Disc/Work_02/feature.mkv"])
        selected = self.scan()
        self.assertEqual(len(selected["files"]), 2)
        by_path = {row["relative_path"]: row for row in selected["files"]}
        self.assertEqual(by_path[conflicting]["episode"], "")
        self.assertEqual(selected["chapters"][0]["status"], "unmatched")
        self.assertEqual(selected["chapters"][1]["relative_path"], "Disc/Work_02/feature.mkv")
        with patch.object(playback.media_files.os, "startfile", create=True) as launch:
            with self.assertRaisesRegex(ValueError, "尚未唯一"):
                self.call("open_chapter", self.open_body(selected))
        launch.assert_not_called()
        saved = self.call("save", self.assignment(selected, conflicting))
        self.assertEqual(saved["chapters"][0]["status"], "confirmed")
        with patch.object(playback.media_files.os, "startfile", create=True) as launch:
            self.call("open_chapter", self.open_body(saved))
        launch.assert_called_once_with(str(self.press / conflicting), "open")

    def test_select_caches_complete_inventory_without_modifying_media(self):
        before = self.source.read_bytes()
        media_before = {str(path): path.read_bytes() for path in self.press.rglob("*") if path.is_file()}
        with patch.object(playback.media_files, "probe_media_duration") as probe:
            scan = self.scan()
        self.assertEqual(len(scan["files"]), 2)
        self.assertEqual([row["status"] for row in scan["chapters"]], ["automatic", "automatic"])
        self.assertEqual(scan["chapters"][0]["relative_path"], "Demo_BDRip_Disc/[Demo][01].mkv")
        self.assertNotEqual(self.source.read_bytes(), before)
        self.assertEqual({str(path): path.read_bytes() for path in self.press.rglob("*") if path.is_file()}, media_before)
        record = scan["detail"]["record"]
        self.assertEqual(record["local_playback"]["selected_resource_key"], scan["resource_key"])
        self.assertTrue(scan["detail"]["resources"][0]["is_playback_resource"])
        self.assertEqual(scan["detail"]["playback"]["revision"], scan["revision"])
        self.assertFalse(self.library.exists())
        probe.assert_not_called()

    def test_chapter_projection_prefers_original_title_and_keeps_chinese_tooltip(self):
        chapters = [
            {"number": 1, "title": "はじまり", "name_cn": "开始"},
            {"number": 2, "name": "その先", "name_cn": "未来"},
            {"number": 3, "name_cn": "中文后备"},
        ]
        rows = playback._chapter_rows(chapters)
        self.assertEqual([row["title"] for row in rows], ["はじまり", "その先", "中文后备"])
        self.assertEqual([row["name_cn"] for row in rows], ["开始", "未来", "中文后备"])

    def test_provider_duration_is_exposed_separately_from_local_file_cache(self):
        first = self.record["metadata"]["episodes"][0]
        first.update(title="はじまり", name_cn="开始", duration="00:24:00", duration_seconds=1440)
        self.record["metadata"]["episodes"][1].update(duration="约24分钟", duration_seconds=0)
        self.write_record()
        selected = self.scan()
        chapter = selected["chapters"][0]
        self.assertEqual(chapter["title"], "はじまり")
        self.assertEqual(chapter["name_cn"], "开始")
        self.assertEqual(chapter["source_duration_seconds"], 1440)
        self.assertEqual(chapter["source_duration"], "00:24:00")
        self.assertIsNone(chapter["duration_seconds"])
        self.assertIsNone(selected["chapters"][1]["source_duration_seconds"])
        self.assertEqual(selected["chapters"][1]["source_duration"], "约24分钟")
        self.assertTrue(all(row["duration_seconds"] is None for row in selected["files"]))
        self.assertFalse(selected["duration_available"])
        stored = load_yaml_string(self.source.read_text(encoding="utf-8"))[0]
        self.assertTrue(all(row["duration_seconds"] is None for row in stored["local_playback"]["resources"][selected["resource_key"]]["scan"]["files"]))
        self.assertEqual(stored["metadata"]["episodes"][0]["duration_seconds"], 1440)
        self.assertEqual(selected["detail"]["playback"]["chapters"][0], chapter)

    def test_local_duration_does_not_overwrite_provider_duration(self):
        self.record["metadata"]["episodes"][0].update(duration="00:24:00", duration_seconds=1440)
        self.write_record()
        with patch.object(playback.media_files, "duration_available", return_value=True), \
             patch.object(playback.media_files, "probe_media_duration", return_value=1467):
            selected = self.scan()
        self.assertEqual(selected["chapters"][0]["duration_seconds"], 1467)
        self.assertEqual(selected["chapters"][0]["source_duration_seconds"], 1440)
        self.assertEqual(selected["detail"]["record"]["metadata"]["episodes"][0]["duration_seconds"], 1440)

    def test_provider_zero_or_invalid_numeric_duration_is_not_a_real_duration(self):
        for value in [0, -1, True, "24", float("inf"), float("nan"), None]:
            with self.subTest(value=value):
                row = playback._chapter_rows([{"number": 1, "duration_seconds": value}])[0]
                self.assertIsNone(row["source_duration_seconds"])

    def test_auto_open_uses_only_current_matching_chapter_and_default_player(self):
        scan = self.scan()
        with patch.object(playback.media_files.os, "startfile", create=True) as launch:
            result = self.call("open_chapter", self.open_body(scan))
        self.assertTrue(result["ok"])
        launch.assert_called_once_with(str(self.disc / "[Demo][01].mkv"), "open")
        with self.assertRaisesRegex(ValueError, "不一致"):
            self.call("open_chapter", {**self.open_body(scan), "relative_path": scan["chapters"][1]["relative_path"]})

    def test_ambiguous_versions_require_confirmed_mapping_and_save_reuses_writer(self):
        (self.disc / "[Demo][01][v2].mp4").write_bytes(b"second-version")
        scan = self.scan()
        self.assertEqual(scan["chapters"][0]["status"], "ambiguous")
        with self.assertRaisesRegex(ValueError, "尚未唯一"):
            self.call("open_chapter", self.open_body(scan))
        selected = "Demo_BDRip_Disc/[Demo][01][v2].mp4"
        with patch.object(service, "apply_edits", wraps=service.apply_edits) as writer:
            saved = self.call("save", self.assignment(scan, selected))
        self.assertEqual(writer.call_count, 1)
        self.assertTrue(saved["db_committed"])
        self.assertEqual(saved["chapters"][0]["status"], "confirmed")
        self.assertEqual(saved["chapters"][0]["relative_path"], selected)
        record = load_yaml_string(self.source.read_text(encoding="utf-8"))[0]
        self.assertEqual(record["metadata"], self.record["metadata"])
        self.assertEqual(saved["detail"]["record"]["local_playback"], record["local_playback"])
        with patch.object(playback.media_files.os, "startfile", create=True) as launch:
            self.call("open_chapter", self.open_body(saved))
        launch.assert_called_once()
        restored = self.call("save", self.assignment(saved, ""))
        self.assertEqual(restored["chapters"][0]["status"], "ambiguous")

    def test_missing_confirmed_file_never_silently_falls_back_to_other_candidate(self):
        scan = self.scan()
        saved = self.call("save", self.assignment(scan, scan["chapters"][0]["relative_path"]))
        (self.disc / "[Demo][01].mkv").rename(self.disc / "[Demo][01] new.mkv")
        current = self.scan()
        self.assertEqual(current["chapters"][0]["status"], "missing")
        with self.assertRaises(ValueError):
            self.call("open_chapter", self.open_body(current))

    def test_stale_db_ref_rejects_resource_reorder_even_with_stable_id_supplied(self):
        scan = self.scan()
        self.record["id"] = "work_" + "a" * 32
        self.write_record()
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.call("scan", {**self.body(scan), "id": self.record["id"]})

    def test_stale_disk_revision_rejects_open_and_save(self):
        scan = self.scan()
        (self.disc / "[Demo][01].mkv").write_bytes(b"changed-video")
        for action, body in (("open_chapter", self.open_body(scan)), ("save", self.assignment(scan, scan["chapters"][0]["relative_path"]))):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "已变化"):
                self.call(action, body)

    def test_changed_episode_recognition_rejects_cached_open_and_save_until_rescan(self):
        selected = self.scan()
        record = load_yaml_string(self.source.read_text(encoding="utf-8"))[0]
        stored = record["local_playback"]["resources"][selected["resource_key"]]["scan"]
        stored["files"][0]["episode"], stored["files"][1]["episode"] = "02", "01"
        self.source.write_text(dump_yaml_string([record]), encoding="utf-8")
        with service._CACHE_LOCK:
            service._CACHE.clear()
        detail = service.detail({"ref": self.ref()}, settings=self.settings, data_root=self.library)
        cached = detail["playback"]
        current = playback.media_files.scan_media_files(self.press)
        self.assertEqual([row["signature"] for row in stored["files"]], [row["signature"] for row in current["files"]])
        self.assertNotEqual([row["episode"] for row in stored["files"]], [row["episode"] for row in current["files"]])
        self.assertEqual(cached["chapters"][0]["relative_path"], "Demo_BDRip_Disc/[Demo][02].mkv")
        before = self.source.read_bytes()
        with patch.object(playback.media_files.os, "startfile", create=True) as launch, \
             patch.object(service, "apply_edits") as writer:
            for action, body in (("open_chapter", self.open_body(cached)),
                                 ("save", self.assignment(cached, cached["files"][0]["relative_path"]))):
                with self.subTest(action=action), self.assertRaisesRegex(ValueError, "重新识别"):
                    self.call(action, body)
            launch.assert_not_called()
            writer.assert_not_called()
        self.assertEqual(self.source.read_bytes(), before)
        refreshed = self.scan("scan")
        self.assertEqual(refreshed["chapters"][0]["relative_path"], "Demo_BDRip_Disc/[Demo][01].mkv")
        self.assertNotEqual(refreshed["revision"], cached["revision"])
        saved = self.call("save", self.assignment(refreshed, refreshed["chapters"][0]["relative_path"]))
        with patch.object(playback.media_files.os, "startfile", create=True) as launch:
            self.call("open_chapter", self.open_body(saved))
        launch.assert_called_once_with(str(self.disc / "[Demo][01].mkv"), "open")

    def test_rescan_preserves_manual_assignment_and_refreshes_durations(self):
        with patch.object(playback.media_files, "duration_available", return_value=True), \
             patch.object(playback.media_files, "probe_media_duration", return_value=99):
            selected = self.scan()
        chosen = selected["files"][1]["relative_path"]
        saved = self.call("save", self.assignment(selected, chosen))
        with patch.object(playback.media_files, "duration_available", return_value=True), \
             patch.object(playback.media_files, "probe_media_duration", return_value=101) as probe:
            refreshed = self.scan("scan")
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(refreshed["revision"], saved["revision"])
        self.assertEqual(refreshed["chapters"][0]["status"], "confirmed")
        self.assertEqual(refreshed["chapters"][0]["relative_path"], chosen)
        self.assertEqual(refreshed["chapters"][0]["duration_seconds"], 101)
        self.assertTrue(all(row["duration_seconds"] == 101 for row in refreshed["files"]))
        self.assertTrue(refreshed["detail"]["resources"][0]["is_playback_resource"])

    def test_same_size_and_mtime_replacement_invalidates_revision(self):
        scan = self.scan()
        original = self.disc / "[Demo][01].mkv"
        stat = original.stat()
        replacement = self.root / "replacement.mkv"
        replacement.write_bytes(b"x" * stat.st_size)
        os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        os.replace(replacement, original)
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.call("open_chapter", self.open_body(scan))

    def test_arbitrary_paths_scripts_and_non_disc_files_cannot_be_bound(self):
        scan = self.scan()
        for path in ("../outside.mkv", "C:/outside.mkv", "Demo_BDRip_Others/[01].mkv", "Demo_BDRip_Disc/[Demo][01].ass", "run.exe"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.call("save", self.assignment(scan, path))

    def test_scan_persists_durations_but_does_not_implicitly_select_resource(self):
        with patch.object(playback.media_files, "duration_available", return_value=True), patch.object(playback.media_files, "probe_media_duration", return_value=1441.5) as probe:
            scan = self.scan("scan")
        self.assertTrue(scan["db_committed"])
        self.assertEqual(probe.call_count, 2)
        self.assertTrue(all(call.kwargs["timeout"] <= 1 for call in probe.call_args_list))
        self.assertTrue(all(row["duration_seconds"] == 1441.5 for row in scan["files"]))
        self.assertEqual(scan["chapters"][0]["duration_seconds"], 1441.5)
        self.assertIsNone(scan["detail"]["playback"])
        self.assertFalse(scan["detail"]["resources"][0]["is_playback_resource"])
        with self.assertRaisesRegex(ValueError, "当前有效"):
            self.call("open_chapter", self.open_body(scan))

    def test_duration_failures_are_warnings_and_refresh_retries_missing_values(self):
        with patch.object(playback.media_files, "duration_available", return_value=True), \
             patch.object(playback.media_files, "probe_media_duration", side_effect=[subprocess.TimeoutExpired("ffprobe", 1), None]) as probe:
            selected = self.scan()
        self.assertTrue(selected["db_committed"])
        self.assertTrue(selected["warnings"])
        self.assertTrue(all(row["duration_seconds"] is None for row in selected["files"]))
        self.assertEqual(probe.call_count, 2)
        with patch.object(playback.media_files, "duration_available", return_value=True), \
             patch.object(playback.media_files, "probe_media_duration", return_value=80.0) as retry:
            refreshed = self.scan("scan")
        self.assertEqual(retry.call_count, 2)
        self.assertTrue(all(row["duration_seconds"] == 80 for row in refreshed["files"]))
        self.assertEqual(refreshed["revision"], selected["revision"], "duration and DB version are not disk identity")

    def test_duration_total_budget_leaves_remaining_values_empty_without_failing_selection(self):
        elapsed = [0]
        def duration(*_args, **_kwargs):
            elapsed[0] = 12.1
            return 60
        with patch.object(playback.media_files, "duration_available", return_value=True), \
             patch.object(playback.media_files, "probe_media_duration", side_effect=duration) as probe, \
             patch.object(playback, "time", SimpleNamespace(monotonic=lambda: elapsed[0])):
            selected = self.scan()
        probe.assert_called_once()
        self.assertEqual([row["duration_seconds"] for row in selected["files"]], [60, None])
        self.assertTrue(any("总时间" in warning for warning in selected["warnings"]))
        self.assertTrue(selected["detail"]["resources"][0]["is_playback_resource"])

    def test_disk_changes_during_duration_probe_abort_before_db_write(self):
        before = self.source.read_bytes()
        def change_file(*_args, **_kwargs):
            (self.disc / "[Demo][01].mkv").write_bytes(b"changed during scan")
            return 1
        with patch.object(playback.media_files, "duration_available", return_value=True), \
             patch.object(playback.media_files, "probe_media_duration", side_effect=change_file), \
             patch.object(service, "apply_edits") as writer:
            with self.assertRaisesRegex(ValueError, "已变化"):
                self.scan()
        writer.assert_not_called()
        self.assertEqual(self.source.read_bytes(), before)

    def test_manual_save_preserves_scan_and_durations_without_reprobing(self):
        with patch.object(playback.media_files, "duration_available", return_value=True), \
             patch.object(playback.media_files, "probe_media_duration", return_value=99):
            selected = self.scan()
        cached = deepcopy(selected["detail"]["record"]["local_playback"]["resources"][selected["resource_key"]]["scan"])
        with patch.object(playback.media_files, "probe_media_duration", side_effect=AssertionError("No probes during binding")):
            saved = self.call("save", self.assignment(selected, selected["files"][1]["relative_path"]))
        self.assertEqual(saved["detail"]["record"]["local_playback"]["resources"][saved["resource_key"]]["scan"], cached)
        self.assertEqual(saved["chapters"][0]["duration_seconds"], 99)
        self.assertEqual(saved["revision"], selected["revision"])

    def test_single_season_matches_but_cross_season_duplicates_do_not(self):
        for file in self.disc.glob("*.mkv"):
            file.unlink()
        (self.disc / "Demo S02E01.mkv").write_bytes(b"x")
        self.assertEqual(self.scan()["chapters"][0]["status"], "automatic")
        (self.disc / "Demo S03E01.mkv").write_bytes(b"y")
        self.assertEqual(self.scan()["chapters"][0]["status"], "unmatched")

    def test_non_unique_or_missing_chapter_identity_cannot_be_bound(self):
        self.record["metadata"]["episodes"] = [{"title": "No identity"}, {"number": 1}, {"number": 1}]
        self.write_record()
        scan = self.scan()
        self.assertTrue(all(not row["binding_allowed"] for row in scan["chapters"]))
        with self.assertRaises(ValueError):
            self.call("save", self.assignment(scan, scan["files"][0]["relative_path"]))

    def test_special_chapters_do_not_match_main_episode_and_ep_zero_uses_sort(self):
        self.record["metadata"]["episodes"][0].update(ep=0, sort=1)
        self.record["metadata"]["episodes"][1].update(type=1, ep=1)
        self.write_record()
        scan = self.scan()
        self.assertEqual(scan["chapters"][0]["number"], "1")
        self.assertEqual(scan["chapters"][0]["status"], "automatic")
        self.assertEqual(scan["chapters"][1]["status"], "unmatched")

    def test_empty_incomplete_and_failed_scans_keep_previous_selection_and_db_unchanged(self):
        selected = self.scan()
        before = self.source.read_bytes()
        original = playback.media_files.scan_media_files
        for change in ({"truncated": True}, {"warnings": ["unreadable child"]}, {"files": []}):
            for action in ("scan", "select"):
                with self.subTest(change=change, action=action), \
                     patch.object(playback.media_files, "scan_media_files", side_effect=lambda root: {**original(root), **change}), \
                     patch.object(service, "apply_edits") as writer:
                    with self.assertRaises(ValueError):
                        self.scan(action)
                    writer.assert_not_called()
                    self.assertEqual(self.source.read_bytes(), before)
        with patch.object(playback.media_files, "scan_media_files", side_effect=OSError("offline")), self.assertRaises(OSError):
            self.scan()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(service.detail({"ref": self.ref()}, settings=self.settings, data_root=self.library)["playback"]["resource_key"], selected["resource_key"])

    def test_selected_resource_switch_rejects_old_resource_even_with_current_ref(self):
        other = self.media / "Demo_WEB"
        other.mkdir()
        (other / "01.mkv").write_bytes(b"other release")
        self.record["attributes"][-1]["data"]["collectioned"].append({"press_format": "WEB", "press_group": "", "press_path": other.name})
        self.write_record()
        first = self.scan()
        second = self.call("select", {"ref": first["ref"], "resource_index": 1})
        self.assertEqual([row["is_playback_resource"] for row in second["detail"]["resources"]], [False, True])
        with patch.object(playback.media_files, "open_media_file") as launch:
            with self.assertRaisesRegex(ValueError, "当前有效"):
                self.call("open_chapter", {**self.open_body(first), "ref": second["ref"]})
            launch.assert_not_called()

    def test_saved_cache_survives_reload_without_scan_or_probe(self):
        selected = self.scan()
        with service._CACHE_LOCK:
            service._CACHE.clear()
        with patch.object(playback.media_files, "scan_media_files", side_effect=AssertionError("No media scan in detail")), \
             patch.object(playback.media_files, "probe_media_duration", side_effect=AssertionError("No probe in detail")), \
             patch.object(playback, "ordinary_directory", side_effect=AssertionError("No media stat in detail")):
            detail = service.detail({"ref": selected["ref"]}, settings=self.settings, data_root=self.library)
        self.assertEqual(detail["playback"]["revision"], selected["revision"])
        self.assertEqual(detail["playback"]["files"], selected["files"])

    def test_invalid_saved_mapping_does_not_fall_back_to_auto(self):
        self.record["local_playback"] = {"version": 99, "resources": {}}
        self.write_record()
        with self.assertRaisesRegex(ValueError, "local_playback"):
            self.scan()

    def test_duplicate_resources_are_not_silently_combined(self):
        collection = self.record["attributes"][-1]["data"]
        collection["collectioned"].append(deepcopy(collection["collectioned"][0]))
        self.write_record()
        with self.assertRaisesRegex(ValueError, "重复压制"):
            self.scan()

    def test_committed_save_refresh_failure_is_not_reported_as_failed_write(self):
        scan = self.scan()
        with patch.object(service, "apply_edits", return_value={"db_committed": True, "records": [], "issues": [{"message": "refresh failed"}]}):
            result = self.call("save", self.assignment(scan, scan["chapters"][0]["relative_path"]))
        self.assertTrue(result["db_committed"])
        self.assertTrue(result["requires_rescan"])
        self.assertEqual(result["revision"], "")
        self.assertIn("已保存", result["message"])

    def test_numeric_normalization_is_bounded_and_boolean_safe(self):
        for value in (True, None, "1e-1000000000", "0." + "0" * 100 + "1", float("inf"), -1):
            self.assertEqual(playback._number(value), "")
        self.assertEqual(playback._number("0001.500"), "1.5")

    def continuation_fixture(self):
        self.record["metadata"]["episodes"] = [
            {"id": f"bangumi:{index}", "ep": index, "sort": index + 13, "type": 0, "name": f"Episode {index}"}
            for index in range(1, 14)]
        for index in range(3, 27):
            (self.disc / f"[Demo][{index:02}].mkv").write_bytes(b"fixture-continuation")
        self.write_record()

    def select_numbering(self, mode, action="select"):
        return self.call(action, {"ref": self.ref(), "resource_index": 0, "numbering_mode": mode})

    def replace_scanned_chapters(self, chapters):
        record = load_yaml_string(self.source.read_text(encoding="utf-8"))[0]
        record["metadata"]["episodes"] = chapters
        self.source.write_text(dump_yaml_string([record]), encoding="utf-8")
        with service._CACHE_LOCK:
            service._CACHE.clear()

    def test_continuation_mode_selects_14_through_26_in_shared_26_episode_resource(self):
        self.continuation_fixture()
        original = deepcopy(self.record["metadata"])
        local = self.scan()
        self.assertEqual(local["numbering_mode"], "local")
        self.assertTrue(local["has_continuous_number"])
        self.assertTrue(local["continuous_number_available"])
        self.assertEqual([row["number"] for row in local["chapters"]], [str(index) for index in range(1, 14)])
        self.assertEqual([row["continuous_number"] for row in local["chapters"]], [str(index) for index in range(14, 27)])
        self.assertEqual([row["relative_path"] for row in local["chapters"]],
                         [f"Demo_BDRip_Disc/[Demo][{index:02}].mkv" for index in range(1, 14)])
        continued = self.select_numbering("continuous")
        self.assertEqual(continued["resource_key"], local["resource_key"])
        self.assertNotEqual(continued["revision"], local["revision"])
        self.assertEqual([row["chapter_key"] for row in continued["chapters"]],
                         [row["chapter_key"] for row in local["chapters"]])
        self.assertEqual([row["relative_path"] for row in continued["chapters"]],
                         [f"Demo_BDRip_Disc/[Demo][{index:02}].mkv" for index in range(14, 27)])
        self.assertEqual(continued["detail"]["record"]["metadata"], original)
        self.assertEqual(continued["detail"]["resources"][0]["numbering_mode"], "continuous")
        with patch.object(playback.media_files.os, "startfile", create=True) as launch:
            self.call("open_chapter", self.open_body(continued))
        launch.assert_called_once_with(str(self.disc / "[Demo][14].mkv"), "open")

    def test_continuation_mode_persists_across_cached_load_and_omitted_mode_rescan(self):
        self.continuation_fixture()
        selected = self.select_numbering("continuous")
        with service._CACHE_LOCK:
            service._CACHE.clear()
        with patch.object(playback, "_inventory", side_effect=AssertionError("No media reads for cached detail")), \
             patch.object(playback.media_files, "probe_media_duration", side_effect=AssertionError("No probe")):
            detail = service.detail({"ref": selected["ref"]}, settings=self.settings, data_root=self.library)
        self.assertEqual(detail["playback"]["chapters"], selected["chapters"])
        self.assertEqual(detail["playback"]["numbering_mode"], "continuous")
        refreshed = self.scan("scan")
        self.assertEqual(refreshed["numbering_mode"], "continuous")
        self.assertEqual(refreshed["revision"], selected["revision"])
        self.assertEqual(refreshed["chapters"][0]["relative_path"], "Demo_BDRip_Disc/[Demo][14].mkv")

    def test_numbered_chapter_keys_and_manual_assignments_survive_mode_switches(self):
        self.continuation_fixture()
        for chapter in self.record["metadata"]["episodes"]:
            chapter.pop("id")
        self.write_record()
        selected = self.scan()
        chosen = "Demo_BDRip_Disc/[Demo][20].mkv"
        saved = self.call("save", self.assignment(selected, chosen))
        continued = self.select_numbering("continuous")
        self.assertEqual(continued["chapters"][0]["chapter_key"], "number:0:1")
        self.assertEqual(continued["chapters"][0]["status"], "confirmed")
        self.assertEqual(continued["chapters"][0]["relative_path"], chosen)
        self.assertEqual(continued["chapters"][1]["relative_path"], "Demo_BDRip_Disc/[Demo][15].mkv")
        restored = self.select_numbering("local")
        self.assertEqual(restored["chapters"][0]["relative_path"], chosen)
        self.assertEqual(restored["chapters"][1]["relative_path"], "Demo_BDRip_Disc/[Demo][02].mkv")
        self.assertEqual(restored["detail"]["record"]["local_playback"]["resources"][saved["resource_key"]]["assignments"],
                         {"number:0:1": chosen})

    def test_invalid_numbering_modes_are_rejected_before_scan_or_write(self):
        self.continuation_fixture()
        before = self.source.read_bytes()
        for mode in (None, True, 1, "", "auto", "CONTINUOUS", [], {}):
            for action in ("scan", "select"):
                with self.subTest(mode=mode, action=action), \
                     patch.object(playback, "_inventory") as inventory, patch.object(service, "apply_edits") as writer:
                    with self.assertRaisesRegex(ValueError, "numbering_mode"):
                        self.select_numbering(mode, action)
                    inventory.assert_not_called()
                    writer.assert_not_called()
        self.assertEqual(self.source.read_bytes(), before)

    def test_continuation_choice_requires_distinct_usable_source_numbers(self):
        for chapters in ([{"id": 1, "ep": 1, "sort": 1}], [{"id": 1, "ep": 1}],
                         [{"id": 1, "sort": 14}], [{"id": 1, "ep": 0, "sort": 14}],
                         [{"id": 1, "ep": 1, "sort": 14, "type": 1}],
                         [{"id": 1, "ep": 1, "sort": 14}, {"id": 2, "ep": 2, "sort": 14}]):
            with self.subTest(chapters=chapters):
                self.record["metadata"]["episodes"] = chapters
                self.write_record()
                with patch.object(playback, "_inventory") as inventory:
                    with self.assertRaisesRegex(ValueError, "没有可用"):
                        self.select_numbering("continuous")
                    inventory.assert_not_called()

    def test_missing_continuation_numbers_are_not_filled_by_offset_or_row_index(self):
        self.continuation_fixture()
        self.record["metadata"]["episodes"] = [
            {"id": "first", "ep": 1, "sort": 14}, {"id": "second", "ep": 2}, {"id": "unknown", "name": "Unknown"}]
        self.write_record()
        selected = self.select_numbering("continuous")
        self.assertEqual([row["status"] for row in selected["chapters"]], ["automatic", "unmatched", "unmatched"])
        self.assertEqual([row["matching_number"] for row in selected["chapters"]], ["14", "", ""])
        self.assertEqual(selected["chapters"][2]["number"], "3")
        self.assertTrue(any("部分章节" in warning for warning in selected["warnings"]))
        local = self.select_numbering("local")
        self.assertEqual(local["chapters"][2]["status"], "unmatched")

    def test_duplicate_continuation_numbers_and_specials_never_auto_match(self):
        self.continuation_fixture()
        self.record["metadata"]["episodes"] = [
            {"id": "first", "ep": 1, "sort": 14}, {"id": "second", "ep": 2, "sort": 14},
            {"id": "third", "ep": 3, "sort": 16}, {"id": "special", "ep": 4, "sort": 17, "type": 1}]
        self.write_record()
        selected = self.select_numbering("continuous")
        self.assertEqual([row["status"] for row in selected["chapters"]],
                         ["unmatched", "unmatched", "automatic", "unmatched"])
        self.assertEqual(selected["chapters"][2]["relative_path"], "Demo_BDRip_Disc/[Demo][16].mkv")
        saved = self.call("save", self.assignment(selected, "Demo_BDRip_Disc/[Demo][14].mkv"))
        self.assertEqual(saved["chapters"][0]["status"], "confirmed")

    def test_duplicate_source_identity_cannot_be_bound_even_with_unique_continuation_numbers(self):
        self.continuation_fixture()
        self.record["metadata"]["episodes"][1]["id"] = "bangumi:1"
        self.write_record()
        selected = self.select_numbering("continuous")
        self.assertEqual([row["binding_allowed"] for row in selected["chapters"][:3]], [False, False, True])
        self.assertEqual([row["status"] for row in selected["chapters"][:3]], ["unmatched", "unmatched", "automatic"])
        with self.assertRaisesRegex(ValueError, "章节不存在"):
            self.call("save", self.assignment(selected, "Demo_BDRip_Disc/[Demo][14].mkv"))

    def test_continuation_metadata_loss_keeps_mode_and_manual_override_without_local_fallback(self):
        self.continuation_fixture()
        selected = self.select_numbering("continuous")
        saved = self.call("save", self.assignment(selected, "Demo_BDRip_Disc/[Demo][20].mkv"))
        chapters = deepcopy(self.record["metadata"]["episodes"])
        for chapter in chapters:
            chapter.pop("sort")
        self.replace_scanned_chapters(chapters)
        detail = service.detail({"ref": self.ref()}, settings=self.settings, data_root=self.library)
        current = detail["playback"]
        self.assertEqual(current["numbering_mode"], "continuous")
        self.assertEqual(current["chapters"][0]["status"], "confirmed")
        self.assertEqual(current["chapters"][0]["relative_path"], "Demo_BDRip_Disc/[Demo][20].mkv")
        self.assertTrue(all(row["status"] == "unmatched" for row in current["chapters"][1:]))
        self.assertFalse(current["continuous_number_available"])
        self.assertTrue(any("不会改用本作集数" in warning for warning in current["warnings"]))
        refreshed = self.scan("scan")
        self.assertEqual(refreshed["numbering_mode"], "continuous")
        self.assertEqual(refreshed["chapters"], current["chapters"])
        self.assertEqual(refreshed["resource_key"], saved["resource_key"])

    def test_continuation_distinction_loss_does_not_silently_match_local_numbers(self):
        self.continuation_fixture()
        self.select_numbering("continuous")
        chapters = deepcopy(self.record["metadata"]["episodes"])
        for chapter in chapters:
            chapter["sort"] = chapter["ep"]
        self.replace_scanned_chapters(chapters)
        detail = service.detail({"ref": self.ref()}, settings=self.settings, data_root=self.library)
        self.assertTrue(all(row["status"] == "unmatched" for row in detail["playback"]["chapters"]))
        self.assertEqual(detail["playback"]["numbering_mode"], "continuous")

    def test_mode_changes_reject_stale_ref_and_stale_revision_even_with_current_ref(self):
        self.continuation_fixture()
        local = self.scan()
        continued = self.select_numbering("continuous")
        for action, body in (("open_chapter", self.open_body(local)),
                             ("save", self.assignment(local, local["chapters"][0]["relative_path"]))):
            for current_ref in (False, True):
                with self.subTest(action=action, current_ref=current_ref):
                    request = {**body, **({"ref": continued["ref"]} if current_ref else {})}
                    with patch.object(playback.media_files, "open_media_file") as launch, \
                         patch.object(service, "apply_edits") as writer:
                        with self.assertRaisesRegex(ValueError, "已变化"):
                            self.call(action, request)
                        launch.assert_not_called()
                        writer.assert_not_called()

    def test_source_number_change_invalidates_mapping_revision_without_disk_changes(self):
        self.continuation_fixture()
        selected = self.select_numbering("continuous")
        chapters = deepcopy(self.record["metadata"]["episodes"])
        chapters[0]["sort"], chapters[1]["sort"] = 15, 14
        self.replace_scanned_chapters(chapters)
        ref = self.ref()
        for action, body in (("open_chapter", self.open_body(selected)),
                             ("save", self.assignment(selected, selected["chapters"][0]["relative_path"]))):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "已变化"):
                self.call(action, {**body, "ref": ref})
        detail = service.detail({"ref": ref}, settings=self.settings, data_root=self.library)
        self.assertNotEqual(detail["playback"]["revision"], selected["revision"])
        self.assertEqual(detail["playback"]["chapters"][0]["relative_path"], "Demo_BDRip_Disc/[Demo][15].mkv")

    def test_open_and_save_ignore_request_numbering_override_and_use_persisted_mode(self):
        self.continuation_fixture()
        selected = self.select_numbering("continuous")
        with patch.object(playback.media_files.os, "startfile", create=True) as launch:
            self.call("open_chapter", {**self.open_body(selected), "numbering_mode": "local"})
        launch.assert_called_once_with(str(self.disc / "[Demo][14].mkv"), "open")
        saved = self.call("save", {**self.assignment(selected, "Demo_BDRip_Disc/[Demo][20].mkv"), "numbering_mode": "local"})
        self.assertEqual(saved["numbering_mode"], "continuous")
        self.assertEqual(saved["chapters"][1]["relative_path"], "Demo_BDRip_Disc/[Demo][15].mkv")

    def test_numbering_mode_is_per_work_when_two_works_share_resource_directory(self):
        self.continuation_fixture()
        second = deepcopy(self.record)
        second["attributes"][0]["data"] = "Other work"
        second["metadata"]["episodes"] = [{"id": "other:1", "ep": 1, "sort": 1}]
        self.source.write_text(dump_yaml_string([self.record, second]), encoding="utf-8")
        continued = self.select_numbering("continuous")
        records = service._records(self.settings)
        other = next(item for item in records if item["record"]["attributes"][0]["data"] == "Other work")
        local = self.call("select", {"ref": other["ref"], "resource_index": 0})
        self.assertEqual(local["resource_key"], continued["resource_key"])
        self.assertEqual(local["numbering_mode"], "local")
        self.assertEqual(local["chapters"][0]["relative_path"], "Demo_BDRip_Disc/[Demo][01].mkv")
        first = next(item for item in service._records(self.settings) if item["record"]["attributes"][0]["data"] == "Demo")
        detail = service.detail({"ref": first["ref"]}, settings=self.settings, data_root=self.library)
        self.assertEqual(detail["playback"]["numbering_mode"], "continuous")
        self.assertEqual(detail["playback"]["chapters"][0]["relative_path"], "Demo_BDRip_Disc/[Demo][14].mkv")

    def test_legacy_cache_without_mode_retains_original_local_matching(self):
        self.continuation_fixture()
        selected = self.scan()
        record = load_yaml_string(self.source.read_text(encoding="utf-8"))[0]
        record["local_playback"]["resources"][selected["resource_key"]].pop("numbering_mode")
        self.source.write_text(dump_yaml_string([record]), encoding="utf-8")
        with service._CACHE_LOCK:
            service._CACHE.clear()
        with patch.object(playback, "_inventory", side_effect=AssertionError("Legacy cache must not scan")):
            detail = service.detail({"ref": self.ref()}, settings=self.settings, data_root=self.library)
        self.assertEqual(detail["playback"]["numbering_mode"], "local")
        self.assertEqual(detail["playback"]["chapters"], selected["chapters"])
        self.assertEqual(detail["playback"]["revision"], selected["revision"])

    def test_batch_manual_assignments_commit_once_without_duration_probes(self):
        selected = self.scan()
        changes = [{"chapter_key": row["chapter_key"], "relative_path": selected["files"][index]["relative_path"]}
                   for index, row in enumerate(selected["chapters"])]
        with patch.object(service, "apply_edits", wraps=service.apply_edits) as writer, \
             patch.object(playback.media_files, "probe_media_duration", side_effect=AssertionError("No probes while binding")):
            saved = self.call("save", {**self.body(selected), "assignments": changes})
        writer.assert_called_once()
        self.assertEqual(len(writer.call_args.args[0]["edits"]), 1)
        self.assertTrue(saved["db_committed"])
        self.assertEqual([row["status"] for row in saved["chapters"]], ["confirmed", "confirmed"])
        entry = saved["detail"]["record"]["local_playback"]["resources"][saved["resource_key"]]
        self.assertEqual(entry["assignments"], {row["chapter_key"]: row["relative_path"] for row in changes})

    def test_batch_invalid_later_assignment_does_not_write_earlier_valid_changes(self):
        selected = self.scan()
        before = self.source.read_bytes()
        for invalid in ["../outside.mkv", "Demo_BDRip_Disc/not-in-scan.mkv"]:
            changes = [{"chapter_key": selected["chapters"][0]["chapter_key"], "relative_path": selected["files"][0]["relative_path"]},
                       {"chapter_key": selected["chapters"][1]["chapter_key"], "relative_path": invalid}]
            with self.subTest(invalid=invalid), patch.object(service, "apply_edits") as writer:
                with self.assertRaises(ValueError):
                    self.call("save", {**self.body(selected), "assignments": changes})
                writer.assert_not_called()
                self.assertEqual(self.source.read_bytes(), before)

    def test_number_ambiguity_is_checked_independently_for_selected_numbering_mode(self):
        self.continuation_fixture()
        self.record["metadata"]["episodes"] = [
            {"id": "first", "ep": 1, "sort": 14}, {"id": "second", "ep": 1, "sort": 15}]
        self.write_record()
        local = self.select_numbering("local")
        self.assertEqual([row["status"] for row in local["chapters"]], ["unmatched", "unmatched"])
        continued = self.select_numbering("continuous")
        self.assertEqual([row["status"] for row in continued["chapters"]], ["automatic", "automatic"])
        self.assertEqual([row["relative_path"] for row in continued["chapters"]],
                         ["Demo_BDRip_Disc/[Demo][14].mkv", "Demo_BDRip_Disc/[Demo][15].mkv"])


if __name__ == "__main__":
    unittest.main()
