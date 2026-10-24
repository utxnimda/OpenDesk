from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from catalog_library.model import classification_definition, new_work_id, validate_extensions


def playback_fixture(*, identity=None, assignments=None):
    identity = identity or {"press_path": "作品_BDRip", "press_format": "BDRip", "press_group": "",
                            "segment": "main", "continuation_index": None}
    key = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"version": 1, "resources": {key: {"identity": identity, "assignments": assignments if assignments is not None else {"bangumi:35": "_Disc/第01集/episode.mkv"}}}}


def scan_fixture():
    return {"root_path": "E:/Media/作品_BDRip", "root_identity": [1, 2, 3],
            "scanned_at": "2026-10-05T03:04:05.123456Z",
            "files": [{"relative_path": "_Disc/第01集/episode.mkv", "episode": "01",
                       "size": 1024, "mtime_ns": 20, "signature": [1, 4, 10, 1024, 20],
                       "duration_seconds": None}]}


def cached_playback_fixture(*, selected=True):
    playback = playback_fixture()
    key = next(iter(playback["resources"]))
    playback["resources"][key]["scan"] = scan_fixture()
    if selected:
        playback["selected_resource_key"] = key
    return playback


class CatalogLibraryModelTest(unittest.TestCase):
    def test_people_references_use_shared_validator_without_embedding_profiles(self):
        refs = [{"id": "person_" + "1" * 32, "type": "character", "role": "主角",
                 "source": {"provider": "bangumi", "subject_id": "12"}}]
        record = {"people_refs": refs, "custom": "keep"}
        result = validate_extensions(record)
        self.assertEqual(result, record)
        result["people_refs"][0]["role"] = "Changed"
        self.assertEqual(refs[0]["role"], "主角")
        for value in (None, "bad", [{**refs[0], "name": "Embedded profile"}], [{**refs[0], "id": "../escape"}], refs * 2):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_extensions({"people_refs": value})

    def test_work_ids_are_distinct_and_not_derived_from_title(self):
        first, second = new_work_id(), new_work_id()
        self.assertRegex(first, r"^work_[0-9a-f]{32}$")
        self.assertNotEqual(first, second)

    def test_legacy_record_is_not_implicitly_migrated(self):
        record = {"attributes": [], "custom": {"keep": True}}
        self.assertEqual(validate_extensions(record), record)
        self.assertNotIn("id", record)

    def test_invalid_extension_values_are_rejected(self):
        for record in ({"id": "bangumi:1"}, {"schema_version": True}, {"schema_version": 2},
                       {"metadata": []}, {"metadata": {"aliases": [1]}},
                       {"metadata": {"episodes": [1]}}, {"source_refs": ["bad"]},
                       {"source_refs": [{"provider": "bangumi", "external_id": None}]},
                       {"classifications": [{"type": "series", "value_id": "x"}]},
                       {"classifications": [{"type": "series", "value_id": "classification_" + "a" * 32, "order": float("inf")}]},
                       {"classifications": [{"type": "series", "value_id": "classification_" + "a" * 32, "order": True}]}):
            with self.subTest(record=record), self.assertRaises(ValueError):
                validate_extensions(record)

    def test_provider_metadata_can_extend_without_schema_lock_in(self):
        record = {"id": new_work_id(), "metadata": {"summary": "Local", "episodes": [{"name": "One"}],
                   "tracks": [{"title": "Track"}], "field_sources": {"summary": {"provider": "example"}}},
                   "source_refs": [{"provider": "example", "external_id": "123", "scope": "01-12"}]}
        self.assertEqual(validate_extensions(record), record)

    def test_local_playback_is_preserved_without_rewriting_paths_or_metadata(self):
        record = {"metadata": {"episodes": [{"id": "bangumi:35", "title": "One"}]},
                  "local_playback": playback_fixture(), "custom": {"keep": True}}
        result = validate_extensions(record)
        self.assertEqual(result, record)
        resource_key = next(iter(result["local_playback"]["resources"]))
        result["local_playback"]["resources"][resource_key]["assignments"]["bangumi:35"] = "Other.mkv"
        self.assertEqual(next(iter(record["local_playback"]["resources"].values()))["assignments"]["bangumi:35"], "_Disc/第01集/episode.mkv")
        identity = {"press_path": "Extra", "press_format": "WEB", "press_group": "Custom",
                    "segment": "continuation", "continuation_index": 0}
        continuation = {"local_playback": playback_fixture(identity=identity, assignments={"type:1:1": "_Disc\\special.mp4"})}
        self.assertEqual(validate_extensions(continuation), continuation)
        self.assertEqual(validate_extensions({"local_playback": {"version": 1, "resources": {}}}), {"local_playback": {"version": 1, "resources": {}}})

    def test_local_playback_rejects_invalid_schema_identity_hashes_and_chapter_keys(self):
        valid = playback_fixture()
        key, resource = next(iter(valid["resources"].items()))
        bad = [None, [], {}, {"version": True, "resources": {}}, {"version": 2, "resources": {}},
               {"version": 1, "resources": []}, {**valid, "unknown": True},
               {"version": 1, "resources": {key.upper(): resource}},
               {"version": 1, "resources": {"a" * 64: resource}},
               {"version": 1, "resources": {key: None}},
               {"version": 1, "resources": {key: {"identity": resource["identity"]}}},
               {"version": 1, "resources": {key: {**resource, "assignments": []}}}]
        for field, value in (("press_format", None), ("press_group", 1), ("segment", "other"),
                             ("continuation_index", True), ("continuation_index", 0), ("press_path", "")):
            identity = {**resource["identity"], field: value}
            bad.append(playback_fixture(identity=identity))
        for value in (None, True, -1, 1.5, "0"):
            bad.append(playback_fixture(identity={**resource["identity"], "segment": "continuation", "continuation_index": value}))
        for chapter in ("", "   ", "a" * 201, "bangumi:35\n", 35):
            bad.append(playback_fixture(assignments={chapter: "episode.mkv"}))
        missing = deepcopy(valid)
        del missing["resources"][key]["identity"]["press_group"]
        bad.append(missing)
        for playback in bad:
            with self.subTest(playback=playback), self.assertRaises(ValueError):
                validate_extensions({"local_playback": playback})

    def test_playback_numbering_mode_is_optional_and_strictly_validated(self):
        legacy = playback_fixture()
        self.assertEqual(validate_extensions({"local_playback": legacy})["local_playback"], legacy)
        for mode in ("local", "continuous"):
            state = deepcopy(legacy)
            next(iter(state["resources"].values()))["numbering_mode"] = mode
            self.assertEqual(validate_extensions({"local_playback": state})["local_playback"], state)
        for mode in (None, True, 1, [], {}, "", "auto", "14", "CONTINUOUS"):
            state = deepcopy(legacy)
            next(iter(state["resources"].values()))["numbering_mode"] = mode
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "numbering_mode"):
                validate_extensions({"local_playback": state})

    def test_local_playback_rejects_unsafe_paths_without_inspecting_files(self):
        valid = playback_fixture()
        identity = next(iter(valid["resources"].values()))["identity"]
        paths = (None, "", "/outside.mkv", "C:/outside.mkv", "C:relative.mkv", "\\\\server\\share\\episode.mkv",
                 "../outside.mkv", "_Disc/../outside.mkv", "./episode.mkv", "_Disc//episode.mkv",
                 "_Disc/", "_Disc/episode.mkv:stream", "NUL.mkv", "_Disc/COM1.txt", "LPT¹.mkv", "CONIN$",
                 "_Disc/folder./episode.mkv", "_Disc/folder /episode.mkv", "_Disc/episode.mkv ",
                 "_Disc/line\nbreak.mkv", "_Disc/zero\x00byte.mkv", "_Disc/control\x7f.mkv", "_Disc/a?.mkv")
        for value in paths:
            with self.subTest(path=value):
                for playback in (playback_fixture(assignments={"bangumi:35": value}),
                                 playback_fixture(identity={**identity, "press_path": value})):
                    with self.assertRaises(ValueError):
                        validate_extensions({"local_playback": playback})

    def test_local_playback_resource_and_assignment_limits_are_enforced(self):
        resources = {}
        for index in range(201):
            identity = {"press_path": "Release-" + str(index), "press_format": "BDRip", "press_group": "",
                        "segment": "main", "continuation_index": None}
            resources.update(playback_fixture(identity=identity, assignments={})["resources"])
        with self.assertRaisesRegex(ValueError, "200"):
            validate_extensions({"local_playback": {"version": 1, "resources": resources}})
        resources.pop(next(iter(resources)))
        validate_extensions({"local_playback": {"version": 1, "resources": resources}})
        assignments = {f"bangumi:{index}": "_Disc/episode.mkv" for index in range(5000)}
        validate_extensions({"local_playback": playback_fixture(assignments=assignments)})
        assignments["bangumi:extra"] = "extra.mp4"
        with self.assertRaisesRegex(ValueError, "5000"):
            validate_extensions({"local_playback": playback_fixture(assignments=assignments)})

    def test_playback_scan_cache_round_trips_without_disk_access_or_normalization(self):
        for root in ("E:/Media/作品_BDRip", "E:\\Media\\作品_BDRip", "/media/作品_BDRip",
                     "\\\\server\\share\\作品_BDRip"):
            for selected in (False, True):
                for duration in (None, 1, 1422.125):
                    with self.subTest(root=root, selected=selected, duration=duration):
                        playback = cached_playback_fixture(selected=selected)
                        scan = next(iter(playback["resources"].values()))["scan"]
                        scan["root_path"] = root
                        scan["files"][0]["duration_seconds"] = duration
                        record = {"local_playback": playback}
                        with patch.object(Path, "stat", side_effect=AssertionError("model must not inspect disk")), \
                                patch.object(Path, "lstat", side_effect=AssertionError("model must not inspect disk")), \
                                patch("builtins.open", side_effect=AssertionError("model must not open files")):
                            result = validate_extensions(record)
                        self.assertEqual(result, record)
                        result_scan = next(iter(result["local_playback"]["resources"].values()))["scan"]
                        result_scan["files"][0]["episode"] = "edited"
                        result_scan["root_identity"][0] = 99
                        self.assertEqual(scan["files"][0]["episode"], "01")
                        self.assertEqual(scan["root_identity"], [1, 2, 3])

    def test_selected_resource_requires_an_existing_valid_scan_cache(self):
        valid = cached_playback_fixture()
        key = valid["selected_resource_key"]
        for selected in (None, "", "a" * 64, key.upper(), "not-a-key", 1, []):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                validate_extensions({"local_playback": {**valid, "selected_resource_key": selected}})
        legacy = playback_fixture()
        legacy["selected_resource_key"] = key
        with self.assertRaisesRegex(ValueError, "扫描缓存"):
            validate_extensions({"local_playback": legacy})
        valid["resources"][key]["scan"]["files"] = []
        with self.assertRaisesRegex(ValueError, "非空"):
            validate_extensions({"local_playback": valid})

    def test_scan_cache_requires_exact_schema_and_complete_nonempty_files(self):
        for value in (None, [], {}, {**scan_fixture(), "warnings": []},
                      {**scan_fixture(), "incomplete": False}, {**scan_fixture(), "files": []},
                      {**scan_fixture(), "files": None}, {**scan_fixture(), "files": {}},
                      {key: value for key, value in scan_fixture().items() if key != "scanned_at"}):
            playback = cached_playback_fixture()
            next(iter(playback["resources"].values()))["scan"] = value
            with self.subTest(scan=value), self.assertRaises(ValueError):
                validate_extensions({"local_playback": playback})
        for field in tuple(scan_fixture()["files"][0]):
            playback = cached_playback_fixture()
            del next(iter(playback["resources"].values()))["scan"]["files"][0][field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                validate_extensions({"local_playback": playback})
        playback = cached_playback_fixture()
        next(iter(playback["resources"].values()))["scan"]["files"][0]["unknown"] = True
        with self.assertRaises(ValueError):
            validate_extensions({"local_playback": playback})

    def test_scan_root_and_timestamp_are_validated_by_syntax_only(self):
        invalid = {
            "root_path": (None, "", " ", "relative/path", "E:relative", "\\relative",
                          "/media/../escape", "E:\\media\\..\\escape", "/media/zero\x00", "E:/control\x85"),
            "scanned_at": (None, "", "2026-10-05", "2026-10-05T03:04:05", "2026-10-05T03:04:05+08:00",
                           "2026-02-30T03:04:05Z", "2026-10-05T25:00:00Z", "2026-10-05T03:04:05Z\n"),
            "root_identity": (None, [], [1, 2], [1, 2, 3, 4], [True, 2, 3], [1, -1, 3], [1, 2.0, 3], (1, 2, 3)),
        }
        for field, values in invalid.items():
            for value in values:
                playback = cached_playback_fixture()
                next(iter(playback["resources"].values()))["scan"][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_extensions({"local_playback": playback})
        playback = cached_playback_fixture()
        next(iter(playback["resources"].values()))["scan"]["scanned_at"] = "2026-10-05T03:04:05+00:00"
        self.assertEqual(validate_extensions({"local_playback": playback})["local_playback"], playback)

    def test_scan_video_paths_reject_unsafe_names_and_executable_extensions(self):
        for value in ("../outside.mkv", "/outside.mkv", "C:/outside.mkv", "_Disc/file.mkv:stream",
                      "_Disc/NUL.mkv", "_Disc/CONOUT$.mkv", "_Disc/control\x9f.mkv", "_Disc/file.mkv ",
                      "_Disc/run.exe", "_Disc/run.ps1", "_Disc/file.mkv.lnk", "_Disc/image.iso", "_Disc/subtitle.ass"):
            playback = cached_playback_fixture()
            next(iter(playback["resources"].values()))["scan"]["files"][0]["relative_path"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_extensions({"local_playback": playback})

    def test_scan_signatures_numbers_and_optional_durations_are_strict(self):
        invalid = {
            "size": (-1, True, 1.5, "1"), "mtime_ns": (-1, False, 1.5, "1"),
            "episode": (None, 1, False, []),
            "signature": (None, [], [1, 2, 3, 4], [1, 2, 3, 1024, 20, 0], [1, 2, 3, 999, 20],
                          [1, 2, 3, 1024, 21], [True, 2, 3, 1024, 20], [1, -1, 3, 1024, 20],
                          [1, 2, 3.0, 1024, 20]),
            "duration_seconds": (0, -1, True, "1", float("nan"), float("inf"), float("-inf")),
        }
        for field, values in invalid.items():
            for value in values:
                playback = cached_playback_fixture()
                next(iter(playback["resources"].values()))["scan"]["files"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_extensions({"local_playback": playback})

    def test_scan_files_enforce_limits_and_windows_equivalent_path_uniqueness(self):
        playback = cached_playback_fixture()
        scan = next(iter(playback["resources"].values()))["scan"]
        template = scan["files"][0]
        scan["files"] = [{**deepcopy(template), "relative_path": f"Disc/{index}.mkv"} for index in range(2000)]
        self.assertEqual(validate_extensions({"local_playback": playback})["local_playback"], playback)
        scan["files"].append({**deepcopy(template), "relative_path": "Disc/2000.mkv"})
        with self.assertRaisesRegex(ValueError, "2000"):
            validate_extensions({"local_playback": playback})
        for duplicate in ("Disc/01.mkv", "disc/01.MKV", "Disc\\01.mkv"):
            scan["files"] = [{**deepcopy(template), "relative_path": "Disc/01.mkv"},
                             {**deepcopy(template), "relative_path": duplicate}]
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ValueError, "重复"):
                validate_extensions({"local_playback": playback})

    def test_classification_rename_keeps_identity_and_type(self):
        original = classification_definition({"name": "Fate"})
        renamed = classification_definition({"name": "Fate Series"}, existing=original)
        self.assertEqual(renamed["id"], original["id"])
        with self.assertRaises(ValueError):
            classification_definition({"name": "Fate", "type": "topic"}, existing=original)


if __name__ == "__main__":
    unittest.main()
