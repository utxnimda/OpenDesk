"""Source cards merge independent detail keys without overwriting local ones."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from catalog_library import people_import, people_infobox as details, people_library as library, providers, service
from catalog_library.people_field_merge import field_merge
from people_catalog.model import source_identity
import test_people as fixtures
import test_people_library as library_fixtures
import test_people_import_batch as batch_fixtures


class InfoboxFieldsTest(unittest.TestCase):
    def test_paths_are_stable_reversible_and_not_dot_sensitive(self):
        for key in ("身高", "別名", "https://example.test/a.b", "a/b", "__proto__"):
            path = details.field_path(key)
            self.assertTrue(details.is_field(path))
            self.assertEqual(details.field_key(path), key)
            self.assertEqual(path.count("."), 1)
        for path in ("infobox.", "infobox.!!!", "infobox.bmFtZQ==", "infobox.Li4v"):
            # Encoded punctuation is a key, never a filesystem or object path.
            if path == "infobox.Li4v":
                self.assertEqual(details.field_key(path), "../")
            else:
                self.assertFalse(details.is_field(path))

    def test_values_are_grouped_without_losing_unknown_legacy_rows(self):
        entity = {"infobox": [{"key": "身高", "value": "180"}, {"key": "身高", "value": "181"},
                               {"unexpected": "legacy"}, {"key": "生日", "value": "2000"}]}
        before = deepcopy(entity)
        path = details.field_path("身高")
        self.assertEqual(details.field_value(entity, path), ["180", "181"])
        self.assertIn(details.LEGACY_FIELD, details.field_paths(entity))
        self.assertEqual(entity, before)
        details.set_field(entity, path, "182")
        self.assertEqual(entity["infobox"], [{"key": "身高", "value": "182"},
                                            {"unexpected": "legacy"}, {"key": "生日", "value": "2000"}])

    def test_different_row_metadata_uses_complete_json_conflict_without_flattening(self):
        local = {"infobox": [{"key": "名称", "value": "Original", "lang": "en"},
                              {"key": "名称", "value": "본명", "lang": "ko"},
                              {"key": "本地资料", "value": "keep"}]}
        incoming = {"infobox": [{"key": "名称", "value": "新名字", "lang": "zh-Hans"}]}
        original = deepcopy(local)
        full = details.field_path("名称", complete_rows=True)
        self.assertIn(full, details.field_paths(local, incoming))
        self.assertTrue(details.is_field(full))
        self.assertEqual(details.field_key(full), "名称")
        self.assertEqual(details.field_value(local, full), local["infobox"][:2])
        decision = field_merge(full, details.field_value(local, full), details.field_value(incoming, full))
        self.assertTrue(decision["requires_confirmation"])
        self.assertFalse(decision["default_selected"])
        self.assertEqual(local, original)
        with self.assertRaisesRegex(ValueError, "完整条目"):
            details.set_field(local, details.field_path("名称"), "unsafe")
        self.assertEqual(local, original)
        details.set_field(local, full, incoming["infobox"])
        self.assertEqual(local["infobox"], incoming["infobox"] + [original["infobox"][-1]])
        with self.assertRaises(ValueError):
            details.set_field(local, full, [{"key": "其他", "value": "invalid"}])

    def test_same_metadata_can_stay_keyed_and_source_changes_consider_target_metadata(self):
        local = {"infobox": [{"key": "名称", "value": "A1", "lang": "en"},
                              {"key": "名称", "value": "A2", "lang": "en"}]}
        self.assertEqual(details.field_paths(local), (details.field_path("名称"),))
        incoming = {"infobox": [{"key": "名称", "value": "A3"}, {"key": "身高", "value": "180"}]}
        changes = details.source_changes(incoming, local)
        self.assertEqual(set(changes), {details.field_path("名称", complete_rows=True), details.field_path("身高")})
        self.assertEqual(changes[details.field_path("名称", complete_rows=True)], [incoming["infobox"][0]])
        self.assertEqual(details.edit_type(local["infobox"], changes[details.field_path("名称", complete_rows=True)]), "json")

    def test_structured_alias_rows_with_different_metadata_are_not_blindly_unioned(self):
        local = {"infobox": [{"key": "别名", "value": ["A1"], "language": "en"},
                              {"key": "别名", "value": ["A2"], "language": "ko"}]}
        incoming = {"infobox": [{"key": "别名", "value": ["A3"], "language": "zh-Hans"}]}
        path = details.field_paths(local, incoming)[0]
        self.assertFalse(details.is_alias_field(path))
        decision = field_merge(path, details.field_value(local, path), details.field_value(incoming, path))
        self.assertTrue(decision["requires_confirmation"])
        self.assertEqual(decision["merged_value"], incoming["infobox"])

    def test_alias_detail_rows_union_strings_and_structured_values(self):
        path = details.field_path("别名")
        result = field_merge(path, ["A1", {"v": "A2"}], ["A1", {"v": "A3"}])
        self.assertEqual(result["merged_value"], ["A1", {"v": "A2"}, {"v": "A3"}])
        self.assertEqual(result["merge_action"], "merge")
        self.assertFalse(result["requires_confirmation"])
        self.assertEqual(field_merge(path, "A1", "A1")["merge_action"], "same")

    def test_alias_detail_empty_placeholders_do_not_pollute_autofill(self):
        path = details.field_path("别名")
        for empty in ({}, [], {"v": ""}, [{}, [], {"v": [None, " "]}]):
            with self.subTest(empty=empty):
                result = field_merge(path, empty, ["A1"])
                self.assertEqual(result["merge_action"], "fill")
                self.assertTrue(result["default_selected"])
                self.assertEqual(result["merged_value"], ["A1"])

    def test_alias_detail_union_preserves_populated_structures_while_removing_empty_items(self):
        path = details.field_path("aliases")
        structured = {"k": "original", "v": "A2", "evidence": {"language": "en"}}
        before = ["A1", {}, [], {"v": ""}, structured]
        incoming = [None, " ", {"v": [None, ""]}, deepcopy(structured), {"v": "A3"}, {"v": 0}]
        original = deepcopy((before, incoming))
        result = field_merge(path, before, incoming)
        self.assertEqual(result["merged_value"], ["A1", structured, {"v": "A3"}, {"v": 0}])
        self.assertFalse(result["requires_confirmation"])
        self.assertEqual((before, incoming), original)


class InfoboxImportTest(unittest.TestCase):
    setUp = fixtures.CatalogPeopleTest.setUp
    save_entity = fixtures.CatalogPeopleTest.save_entity

    def snapshot(self):
        identity = source_identity("tmdb", "person", "55")
        source = {"provider": "tmdb", "entity_type": "person", "external_id": "55"}
        before = {"schema_version": 1, "id": identity, "type": "person", "kind": "individual", "name": "Actor",
                  "aliases": ["A1", "A2"], "summary": "", "source_refs": [source], "infobox": [
                      {"key": "身高", "value": "180"}, {"key": "生日", "value": ""},
                      {"key": "本地独有", "value": "保留"}, {"key": "相同", "value": "same"}]}
        self.save_entity(before)
        candidate = {**deepcopy(before), "source": source, "summary": "Source profile", "aliases": ["A1", "A3"], "infobox": [
            {"key": "身高", "value": "181"}, {"key": "生日", "value": "2000"},
            {"key": "来源独有", "value": "补齐"}, {"key": "相同", "value": "same"}]}
        snapshot = {"provider": "tmdb", "subject_id": 100, "import_entries": [
            {"external_id": "person:55", "candidate": candidate, "relations": []}]}
        return snapshot, before

    def test_default_adoption_adds_new_and_empty_keys_despite_one_conflict(self):
        snapshot, before = self.snapshot()
        display, _ = people_import.preview(snapshot, "keyed", {}, self.repo)
        entry = display["entries"][0]
        diff = {row["field"]: row for row in entry["targets"][0]["diff"]}
        height, birth, extra = [details.field_path(key) for key in ("身高", "生日", "来源独有")]
        self.assertTrue(diff[height]["requires_confirmation"])
        self.assertNotIn(height, entry["selected_fields"])
        self.assertTrue({birth, extra, "aliases", "summary"}.issubset(entry["selected_fields"]))
        self.assertEqual(diff[height]["label"], "详细资料 · 身高")
        self.assertEqual(diff[height]["edit_type"], "text")
        self.assertFalse(any(row["requires_confirmation"] for row in diff.values() if row["field"] != height))
        choice = {"external_id": entry["external_id"], "action": "reuse", "target_id": before["id"],
                  "fields": entry["selected_fields"], "relations": []}
        _, pending, _ = people_import.compose(snapshot, [choice], {}, self.repo)
        saved = pending[before["id"]]
        self.assertEqual(saved["aliases"], ["A1", "A2", "A3"])
        self.assertEqual(saved["summary"], "Source profile")
        self.assertEqual({row["key"]: row["value"] for row in saved["infobox"]}, {
            "身高": "180", "生日": "2000", "本地独有": "保留", "相同": "same", "来源独有": "补齐"})
        self.assertEqual(self.repo.read(before["id"]), before)
        choice["fields"] = [height]
        choice["overrides"] = {height: "182"}
        _, pending, _ = people_import.compose(snapshot, [choice], {}, self.repo)
        self.assertEqual(details.field_value(pending[before["id"]], height), "182")
        self.assertEqual(details.field_value(pending[before["id"]], birth), "")
        choice["fields"] = ["infobox"]
        choice["overrides"] = {}
        with self.assertRaises(ValueError):
            people_import.compose(snapshot, [choice], {}, self.repo)

    def test_metadata_conflict_preserves_all_original_rows_while_other_keys_fill(self):
        snapshot, before = self.snapshot()
        before["infobox"] = [{"key": "名称", "value": "Name", "lang": "en"},
                              {"key": "名称", "value": "본명", "lang": "ko"}]
        self.save_entity(before)
        candidate = snapshot["import_entries"][0]["candidate"]
        candidate["infobox"] = [{"key": "名称", "value": "名字", "lang": "zh-Hans"},
                                 {"key": "新增", "value": "safe"}]
        preview, _ = people_import.preview(snapshot, "metadata", {}, self.repo)
        entry = preview["entries"][0]
        path = details.field_path("名称", complete_rows=True)
        row = next(row for row in entry["targets"][0]["diff"] if row["field"] == path)
        self.assertTrue(row["requires_confirmation"])
        self.assertEqual(row["edit_type"], "json")
        self.assertNotIn(path, entry["selected_fields"])
        self.assertIn(details.field_path("新增"), entry["selected_fields"])
        choice = {"external_id": entry["external_id"], "action": "reuse", "target_id": before["id"],
                  "fields": entry["selected_fields"], "relations": []}
        _, pending, _ = people_import.compose(snapshot, [choice], {}, self.repo)
        self.assertEqual(pending[before["id"]]["infobox"], before["infobox"] + [candidate["infobox"][-1]])
        choice["fields"] = [path]
        _, pending, _ = people_import.compose(snapshot, [choice], {}, self.repo)
        self.assertEqual(pending[before["id"]]["infobox"], [candidate["infobox"][0]])
        self.assertEqual(self.repo.read(before["id"]), before)


class InfoboxIndependentLibraryTest(unittest.TestCase):
    setUp = library_fixtures.PeopleLibraryTest.setUp
    save_entity = fixtures.CatalogPeopleTest.save_entity
    detail = library_fixtures.PeopleLibraryTest.detail
    preview = library_fixtures.PeopleLibraryTest.preview
    apply = library_fixtures.PeopleLibraryTest.apply

    def test_source_defaults_are_keyed_but_manual_edit_and_clear_stay_whole(self):
        raw = {"id": 11, "type": 1, "name": "Original Name", "infobox": [
            {"key": "出生地", "value": "New"}, {"key": "身高", "value": "180"}]}
        work_before = self.source.read_bytes()
        with patch.object(providers, "_request_json", return_value=raw):
            preview = self.preview("source", provider="bangumi")
        birth, height = [details.field_path(key) for key in ("出生地", "身高")]
        self.assertIn(height, preview["selected_fields"])
        self.assertNotIn(birth, preview["selected_fields"])
        self.assertNotIn("infobox", [row["field"] for row in preview["diff"]])
        self.apply(preview)
        self.assertEqual(self.repo.read(self.identity)["infobox"], [
            {"key": "出生地", "value": "Local"}, {"key": "身高", "value": "180"}])
        self.assertEqual(self.source.read_bytes(), work_before)
        edit = self.preview(changes={"infobox": [{"key": "手工", "value": "only"}]})
        self.assertEqual(edit["selected_fields"], ["infobox"])
        self.apply(edit)
        self.assertEqual(self.repo.read(self.identity)["infobox"], [{"key": "手工", "value": "only"}])
        clear = self.preview("clear")
        self.apply(clear, ["infobox"])
        self.assertEqual(self.repo.read(self.identity)["infobox"], [])

    def test_independent_source_compares_complete_metadata_rows_and_keeps_others_incremental(self):
        self.person["infobox"] = [{"key": "出生地", "value": "Seoul", "lang": "en"},
                                  {"key": "出生地", "value": "서울", "lang": "ko"}]
        self.save_entity(self.person)
        raw = {"id": 11, "type": 1, "name": "Original Name", "infobox": [
            {"key": "出生地", "value": "首尔", "lang": "zh-Hans"}, {"key": "身高", "value": "180"}]}
        full = details.field_path("出生地", complete_rows=True)
        with patch.object(providers, "_request_json", return_value=raw):
            preview = self.preview("source", provider="bangumi")
        row = next(row for row in preview["diff"] if row["field"] == full)
        self.assertTrue(row["requires_confirmation"])
        self.assertNotIn(full, preview["selected_fields"])
        self.apply(preview)
        self.assertEqual(self.repo.read(self.identity)["infobox"], self.person["infobox"] + [raw["infobox"][-1]])
        with patch.object(providers, "_request_json", return_value=raw):
            preview = self.preview("source", provider="bangumi")
        self.apply(preview, [full])
        self.assertEqual(self.repo.read(self.identity)["infobox"], raw["infobox"])


class InfoboxBatchTest(unittest.TestCase):
    setUp = fixtures.CatalogPeopleTest.setUp
    entry = batch_fixtures.PeopleImportBatchTest.entry
    default_proposals = batch_fixtures.PeopleImportBatchTest.default_proposals
    apply = batch_fixtures.PeopleImportBatchTest.apply

    def test_main_supplement_keys_and_aliases_share_pending_card(self):
        from catalog_library import people_import_batch
        main = self.entry("bangumi", "1", aliases=["A1", "A2"], infobox=[{"key": "身高", "value": "180"}])
        supplement = self.entry("bangumi", "1", role="supplement", aliases=["A1", "A3"], infobox=[
            {"key": "身高", "value": "181"}, {"key": "出生地", "value": "Seoul"}])
        entries = [main, supplement]
        people_import_batch.rebase(entries, service._records(self.settings)[0], people_root=self.people_root)
        row = entries[1]["preview"]["people_preview"]["entries"][0]
        self.assertEqual(set(row["selected_fields"]), {"aliases", details.field_path("出生地")})
        self.assertFalse((self.people_root / "db").exists())
        self.apply(self.default_proposals(entries))
        saved = self.repo.read(source_identity("bangumi", "person", "1"))
        self.assertEqual(saved["aliases"], ["A1", "A2", "A3"])
        self.assertEqual(saved["infobox"], [{"key": "身高", "value": "180"}, {"key": "出生地", "value": "Seoul"}])

    def test_cross_provider_confirmed_identity_uses_same_keyed_merge(self):
        from catalog_library import people_import_batch
        entries = [self.entry("bangumi", "1", aliases=["A1", "A2"], infobox=[{"key": "身高", "value": "180"}]),
                   self.entry("wikipedia", "77", role="supplement", aliases=["A1", "A3"], infobox=[
                       {"key": "身高", "value": "181"}, {"key": "出生地", "value": "Seoul"}])]
        people_import_batch.rebase(entries, service._records(self.settings)[0], people_root=self.people_root)
        first = entries[0]["preview"]["people_preview"]["entries"][0]
        second = entries[1]["preview"]["people_preview"]["entries"][0]
        target = next(row for row in second["targets"] if row["id"] == first["candidate"]["id"])
        self.assertNotIn(details.field_path("身高"), target["selected_fields"])
        proposals = self.default_proposals(entries)
        choice = proposals[1]["_people_selections"][0]
        choice.update(action="reuse", target_id=target["id"], fields=target["selected_fields"])
        self.apply(proposals)
        saved = self.repo.read(target["id"])
        self.assertEqual(saved["aliases"], ["A1", "A2", "A3"])
        self.assertEqual(saved["infobox"], [{"key": "身高", "value": "180"}, {"key": "出生地", "value": "Seoul"}])
        self.assertEqual({row["provider"] for row in saved["source_refs"]}, {"bangumi", "wikipedia"})

    def test_pending_card_metadata_conflict_does_not_block_other_supplement_keys(self):
        from catalog_library import people_import_batch
        first_rows = [{"key": "名称", "value": "Name", "lang": "en"},
                      {"key": "名称", "value": "본명", "lang": "ko"}]
        entries = [self.entry("bangumi", "1", infobox=first_rows),
                   self.entry("bangumi", "1", role="supplement", infobox=[
                       {"key": "名称", "value": "名字", "lang": "zh-Hans"}, {"key": "身高", "value": "180"}])]
        people_import_batch.rebase(entries, service._records(self.settings)[0], people_root=self.people_root)
        second = entries[1]["preview"]["people_preview"]["entries"][0]
        self.assertEqual(second["selected_fields"], [details.field_path("身高")])
        self.apply(self.default_proposals(entries))
        saved = self.repo.read(source_identity("bangumi", "person", "1"))
        self.assertEqual(saved["infobox"], first_rows + [{"key": "身高", "value": "180"}])


if __name__ == "__main__":
    unittest.main()
