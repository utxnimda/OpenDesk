"""Offline aggregate-source fixtures exercise one work + people transaction."""
from __future__ import annotations

from copy import deepcopy
from contextlib import ExitStack
import time
import unittest
from unittest.mock import patch

from catalog_library import people, people_import, providers, service, source_sync
from people_catalog.model import source_identity
from people_catalog.repository import PeopleRepository
from work_catalog_yaml.persistence import PersistenceRollbackError
from work_catalog_yaml.yaml_io import dump_yaml_string
import test_provider_people_import as fixtures
from people_import_test_helpers import selections


class AggregateSourcePeopleTest(unittest.TestCase):
    ref = fixtures.CombinedProviderPeopleTest.ref
    current = fixtures.CombinedProviderPeopleTest.current

    def setUp(self):
        # This fixture explicitly stops the provider-only people_preview mock.
        fixtures.CombinedProviderPeopleTest.setUp(self)
        self.record["source_refs"] = [
            {"provider": "bangumi", "external_id": str(subject_id), "scope": "",
             "binding_id": "sb_" + f"{subject_id:024x}", "enabled": True, "role": "primary"}
            for subject_id in (12, 13)]
        self.file.write_text(dump_yaml_string([self.record]), encoding="utf-8")
        self.details["person:3"] = {"id": 3, "name": "Second Director", "summary": "Second director biography",
                                    "type": 1, "career": []}
        self.addCleanup(service._CACHE.clear)

    def request(self, path, **kwargs):
        if path == "/v0/episodes":
            subject_id = int(kwargs["query"]["subject_id"])
            self.assertIn(subject_id, (12, 13))
            page = deepcopy(self.page)
            page["data"][0].update(id=subject_id + 23, subject_id=subject_id)
            return page
        for subject_id in (12, 13):
            prefix = f"/v0/subjects/{subject_id}"
            if path == prefix:
                return {**deepcopy(self.subject), "id": subject_id, "name_cn": f"来源名称 {subject_id}"}
            if path == prefix + "/characters":
                # Both scopes intentionally share one character and actor entity.
                return deepcopy(self.characters)
            if path == prefix + "/persons":
                director = deepcopy(self.persons)
                director[0]["id"] = 2 if subject_id == 12 else 3
                director[0]["name"] = self.details[f"person:{director[0]['id']}"]["name"]
                return director
        parts = path.split("/")
        if len(parts) == 4 and parts[2] in {"characters", "persons"}:
            key = f"{'character' if parts[2] == 'characters' else 'person'}:{parts[3]}"
            if key in self.details:
                return deepcopy(self.details[key])
        raise AssertionError(f"Unexpected fixture network request: {path}")

    def preview_sync(self):
        with patch.object(providers, "_request_json", side_effect=self.request), \
                patch.object(providers.assets, "acquire_cover", side_effect=ValueError("fixture: no cover")):
            return source_sync.preview({"ref": self.ref()}, settings=self.settings,
                source_root=self.sources, assets_root=self.root / "assets", people_root=self.people_root)

    def apply_sync(self, preview):
        with patch.object(providers, "_request_json", side_effect=AssertionError("confirmation must remain offline")):
            return source_sync.apply({"ref": preview["ref"], "preview_token": preview["preview_token"],
                "selected_fields": ["metadata.summary"], "people_selections": selections(preview, self.repo)}, settings=self.settings, source_root=self.sources,
                assets_root=self.root / "assets", people_root=self.people_root, data_root=self.library)

    def entity_bytes(self):
        return {path.name: path.read_bytes() for path in (self.people_root / "db").glob("*.yaml")}

    def assert_both_scopes(self):
        refs = self.current()["people_refs"]
        character_id = source_identity("bangumi", "character", 1)
        actor_id = source_identity("bangumi", "person", 1)
        self.assertEqual(len(refs), 4)
        self.assertEqual({(ref["source"]["subject_id"], ref["id"]) for ref in refs}, {
            ("12", character_id), ("12", source_identity("bangumi", "person", 2)),
            ("13", character_id), ("13", source_identity("bangumi", "person", 3))})
        self.assertEqual(set(self.entity_bytes()), {identity + ".yaml" for identity in (
            character_id, actor_id, source_identity("bangumi", "person", 2), source_identity("bangumi", "person", 3))})
        character = self.repo.read(character_id)
        self.assertEqual({(ref["scope"]["subject_id"], ref["person_id"]) for ref in character["voice_actors"]},
                         {("12", actor_id), ("13", actor_id)})
        self.assertEqual(people.project(self.current(), people_root=self.people_root)["warnings"], [])

    def test_preview_collects_two_people_proposals_without_writing_databases(self):
        before = self.file.read_bytes()
        preview = self.preview_sync()
        self.assertTrue(preview["can_apply"])
        self.assertEqual([row["status"] for row in preview["sources"]], ["ok", "ok"])
        self.assertEqual([row["subject_id"] for row in preview["people_preview"]["sources"]], [12, 13])
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.entity_bytes(), {})

    def test_one_work_commit_keeps_both_scopes_and_deduplicates_shared_entities(self):
        preview = self.preview_sync()
        with patch.object(service, "apply_edits", wraps=service.apply_edits) as writer, \
                patch.object(people, "apply_with_work", side_effect=AssertionError("must aggregate both proposals")), \
                patch.object(people, "apply", side_effect=AssertionError("no stale-ref second work write")):
            result = self.apply_sync(preview)
        writer.assert_called_once()
        self.assertTrue(result["db_committed"])
        self.assertTrue(result["people_updated"])
        self.assertEqual(self.current()["metadata"]["summary"], "Source description")
        self.assertEqual(len(self.current()["source_refs"]), 2)
        self.assertEqual(self.current()["attributes"], self.record["attributes"])
        self.assert_both_scopes()

    def test_tampered_second_people_snapshot_blocks_every_database_write(self):
        preview = self.preview_sync()
        second = preview["people_preview"]["sources"][1]
        self.assertEqual(second["subject_id"], 13)
        path = self.people_root / "sources" / "bangumi" / (second["snapshot_id"] + ".json")
        path.write_bytes(path.read_bytes() + b" ")
        before = self.file.read_bytes()
        with patch.object(service, "apply_edits", side_effect=AssertionError("must not write work")), \
                patch.object(PeopleRepository, "apply_batch", side_effect=AssertionError("must not write people")), \
                self.assertRaisesRegex(ValueError, "快照|变化|预览"):
            self.apply_sync(preview)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.entity_bytes(), {})

    def test_invalid_second_prepared_proposal_blocks_first_valid_proposal_too(self):
        preview = self.preview_sync()
        before = self.file.read_bytes()
        prepare = providers.apply_provider_import
        prepared_subjects = []

        def altered_proposal(*args, **kwargs):
            result = prepare(*args, **kwargs)
            proposal = result["people_proposal"]
            prepared_subjects.append(proposal["subject_id"])
            if proposal["subject_id"] == 13:
                proposal["selection_plan_sha256"] = "0" * 64
            return result

        with patch.object(providers, "apply_provider_import", side_effect=altered_proposal), \
                patch.object(service, "apply_edits", side_effect=AssertionError("must not write work")), \
                patch.object(PeopleRepository, "apply_batch", side_effect=AssertionError("must not write people")), \
                self.assertRaisesRegex(ValueError, "变化|预览"):
            self.apply_sync(preview)
        self.assertEqual(prepared_subjects, [12, 13])
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.entity_bytes(), {})

    def test_stale_entity_unique_to_second_proposal_blocks_all_changes(self):
        preview = self.preview_sync()
        identity = source_identity("bangumi", "person", 3)
        self.repo.apply_batch([{"id": identity, "schema_version": 1, "type": "person",
                                "name": "Manual edit while aggregate preview was open"}],
                              expected_revision=self.repo.revision([identity]))
        before, people_before = self.file.read_bytes(), self.entity_bytes()
        with patch.object(service, "apply_edits", side_effect=AssertionError("must not write work")), \
                patch.object(PeopleRepository, "apply_batch", side_effect=AssertionError("must not write people")), \
                self.assertRaisesRegex(ValueError, "变化|预览"):
            self.apply_sync(preview)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.entity_bytes(), people_before)

    def test_external_edit_after_overlay_plan_is_never_accepted_as_fresh_revision(self):
        self.apply_sync(self.preview_sync())
        self.details["person:3"]["summary"] = "Updated provider biography"
        preview = self.preview_sync()
        identity = source_identity("bangumi", "person", 3)
        concurrent = self.repo.read(identity)
        concurrent["summary"] = "Concurrent manual edit must survive"
        before_work = self.file.read_bytes()
        before_people = self.entity_bytes()
        original_build = people_import.compose
        injected = []

        def build_with_external_edit(snapshot, choices, record, repository, pending=None):
            result = original_build(snapshot, choices, record, repository, pending)
            if snapshot["subject_id"] == 13:
                # The directory lock does not constrain an external editor.
                self.repo.path(identity).write_text("# external editor comment\n" + dump_yaml_string(concurrent), encoding="utf-8")
                injected.append(identity)
            return result

        with patch.object(people_import, "compose", side_effect=build_with_external_edit), \
                patch.object(service, "apply_edits", side_effect=AssertionError("must not save work")), \
                self.assertRaisesRegex(ValueError, "变化|预览"):
            self.apply_sync(preview)
        self.assertEqual(injected, [identity])
        self.assertEqual(self.file.read_bytes(), before_work)
        self.assertEqual(self.repo.read(identity)["summary"], concurrent["summary"])
        self.assertTrue(self.repo._bytes(identity).startswith(b"# external editor comment"))
        after_people = self.entity_bytes()
        before_people.pop(identity + ".yaml")
        after_people.pop(identity + ".yaml")
        self.assertEqual(after_people, before_people)

    def test_new_unrelated_owner_after_overlay_plan_blocks_aggregate(self):
        preview = self.preview_sync()
        identity = source_identity("manual", "person", "concurrent-owner")
        concurrent = {"id": identity, "schema_version": 1, "type": "person", "name": "Concurrent owner"}
        original_build = people_import.compose

        def build_with_external_owner(snapshot, choices, record, repository, pending=None):
            result = original_build(snapshot, choices, record, repository, pending)
            if snapshot["subject_id"] == 13:
                self.repo.path(identity).parent.mkdir(parents=True, exist_ok=True)
                self.repo.path(identity).write_text(dump_yaml_string(concurrent), encoding="utf-8")
            return result

        before_work = self.file.read_bytes()
        with patch.object(people_import, "compose", side_effect=build_with_external_owner), \
                patch.object(service, "apply_edits", side_effect=AssertionError("must not save work")), \
                self.assertRaisesRegex(ValueError, "变化|预览"):
            self.apply_sync(preview)
        self.assertEqual(self.file.read_bytes(), before_work)
        self.assertEqual(set(self.entity_bytes()), {identity + ".yaml"})

    def test_work_failure_rolls_back_all_new_shared_and_unique_entities(self):
        preview = self.preview_sync()
        before = self.file.read_bytes()

        def fail_work(*args, **kwargs):
            self.assertEqual(len(self.entity_bytes()), 4)
            raise ValueError("fixture work CAS failed")

        with patch.object(service, "apply_edits", side_effect=fail_work) as writer, \
                self.assertRaisesRegex(ValueError, "CAS"):
            self.apply_sync(preview)
        writer.assert_called_once()
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.entity_bytes(), {})

    def test_work_failure_restores_existing_entities_byte_for_byte(self):
        self.apply_sync(self.preview_sync())
        for detail in self.details.values():
            detail["summary"] += " updated by source"
        preview = self.preview_sync()
        before, people_before = self.file.read_bytes(), self.entity_bytes()

        def fail_work(*args, **kwargs):
            self.assertNotEqual(self.entity_bytes(), people_before)
            raise ValueError("fixture work CAS failed")

        with patch.object(service, "apply_edits", side_effect=fail_work) as writer, \
                self.assertRaisesRegex(ValueError, "CAS"):
            self.apply_sync(preview)
        writer.assert_called_once()
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.entity_bytes(), people_before)
        self.assert_both_scopes()

    def test_unknown_work_commit_retains_both_scopes_people_dependencies(self):
        preview = self.preview_sync()
        write_work = service.apply_edits

        def commit_then_fail(*args, **kwargs):
            write_work(*args, **kwargs)
            error = PersistenceRollbackError("fixture aggregate rollback incomplete")
            error.db_committed = None
            raise error

        with patch.object(service, "apply_edits", side_effect=commit_then_fail) as writer, \
                self.assertRaisesRegex(PersistenceRollbackError, "提交状态不确定.*人物数据已保留") as raised:
            self.apply_sync(preview)
        writer.assert_called_once()
        self.assertIsNone(raised.exception.db_committed)
        self.assert_both_scopes()


class IndependentPeopleSourceOrderingTest(unittest.TestCase):
    """In-memory orchestration fixtures never open or write either database."""

    def setUp(self):
        self.ref = {"yaml_source_rel": "fixture.yaml", "index_in_file": 0,
                    "source_sha256": "a" * 64, "record_sha256": "b" * 64}
        self.bindings = [
            {"provider": "tmdb", "binding_id": "tmdb-a", "external_id": "50", "season_number": 1,
             "episode_range": {"start": 1, "end": 8}, "scope": "season:1;episodes:1-8", "enabled": True, "role": "primary"},
            {"provider": "tmdb", "binding_id": "tmdb-b", "external_id": "50", "season_number": 1,
             "episode_range": {"start": 9, "end": 16}, "scope": "season:1;episodes:9-16", "enabled": True, "role": "primary"},
            {"provider": "wikipedia", "binding_id": "wiki-a", "external_id": "82", "scope": "", "enabled": True, "role": "supplement"},
            {"provider": "wikipedia", "binding_id": "wiki-b", "external_id": "83", "scope": "", "enabled": True, "role": "supplement"},
        ]
        self.record = {"attributes": [{"type": "name", "data": "Fixture"}], "metadata": {"aliases": []}, "source_refs": self.bindings}
        self.item = {"ref": self.ref, "record": self.record, "name": "Fixture"}
        self.failed_direct = set()
        self.requests, self.candidates, self.snapshot = [], {}, None
        patches = ExitStack()
        self.addCleanup(patches.close)
        for target, name, options in [
            (providers, "_catalog_item", {"return_value": self.item}),
            (providers, "_check_version", {"return_value": None}),
            (providers, "_catalog_scope", {"return_value": "memory-only"}),
            (providers, "_save_snapshot", {"side_effect": self.save_snapshot}),
            (providers, "preview_provider_import", {"side_effect": self.fetch_preview}),
            (source_sync, "report_progress", {"return_value": None}),
            (service, "apply_edits", {"side_effect": AssertionError("no database writes in orchestration fixture")}),
        ]:
            patches.enter_context(patch.object(target, name, **options))

    def save_snapshot(self, value, *args, **kwargs):
        self.snapshot = deepcopy(value)
        return "c" * 64, "c" * 64

    def fetch_preview(self, body, **kwargs):
        owner = next(binding for binding in self.bindings if binding["binding_id"] == body["replace_binding_id"])
        self.assertNotIn("_supplement_excluded_bindings", kwargs)
        self.requests.append(owner["binding_id"])
        if owner["binding_id"] in self.failed_direct:
            raise ValueError("fixture independent source failed")
        children = [{"provider": owner["provider"], "subject_id": int(owner["external_id"]),
                     "season_number": owner.get("season_number"), "episode_range": deepcopy(owner.get("episode_range")),
                     "people": {"cast": []}, "binding_id": owner["binding_id"],
                     "changes": [{"id": owner["binding_id"], "name": owner["binding_id"], "action": "update"}]}]
        people_preview = children[0] if owner["provider"] == "tmdb" else {"sources": children, "changes": [], "warnings": []} if children else None
        candidate = {"ref": deepcopy(self.ref), "provider": owner["provider"], "snapshot_id": owner["binding_id"],
                     "preview_token": providers._sign({"expires": int(time.time()) + 600}),
                     "diff": [], "warnings": [], "people_preview": people_preview}
        self.candidates[owner["binding_id"]] = deepcopy(candidate)
        return candidate

    def preview(self, ids):
        before = deepcopy(self.record)
        result = source_sync.preview({"ref": self.ref, "binding_ids": ids}, settings=object(), source_root="memory-only")
        self.assertEqual(self.record, before)
        return result

    def assert_one_apply_matches_display(self, preview):
        def prepare(body, **kwargs):
            candidate = self.candidates[body["snapshot_id"]]
            owner = next(binding for binding in self.bindings if binding["binding_id"] == body["snapshot_id"])
            proposals = [{"binding_id": child.get("binding_id", owner["binding_id"]), "owner": child["changes"][0]["name"]}
                         for child in source_sync._people_previews(candidate)]
            return {"record": deepcopy(kwargs["_record_override"]), "people_proposal": None, "people_proposals": proposals,
                    "applied_fields": [], "automatic_aliases_added": [], "warnings": []}

        with patch.object(providers, "_read_snapshot", return_value=deepcopy(self.snapshot)), \
                patch.object(providers, "apply_provider_import", side_effect=prepare), \
                patch.object(providers, "preview_provider_import", side_effect=AssertionError("confirmation must be offline")), \
                patch.object(people, "apply_many_with_work", return_value={"db_committed": True, "people_updated": True}) as writer:
            result = source_sync.apply({"ref": self.ref, "preview_token": preview["preview_token"], "selected_fields": []},
                                       settings=object(), source_root="memory-only")
        writer.assert_called_once()
        submitted = writer.call_args.args[0]
        displayed = [{"binding_id": child["changes"][0]["id"], "owner": child["changes"][0]["name"]}
                     for child in preview["people_preview"]["sources"]]
        self.assertEqual(submitted, displayed)
        self.assertEqual(len({row["binding_id"] for row in submitted}), len(submitted))
        self.assertEqual(result["warnings"], preview["warnings"])
        return submitted

    def test_two_wikis_display_and_submit_their_independent_previews(self):
        preview = self.preview(["wiki-a", "wiki-b"])
        self.assertEqual(self.requests, ["wiki-a", "wiki-b"])
        self.assertEqual([row["name"] for row in preview["people_preview"]["changes"]], ["wiki-a", "wiki-b"])
        self.assertEqual(preview["warnings"], [])
        self.assertIsNotNone(self.snapshot["entries"][1]["preview"]["people_preview"])
        self.assertEqual(len(self.assert_one_apply_matches_display(preview)), 2)

    def test_primary_wiki_runs_before_supplement_tmdb_and_preserves_both_previews(self):
        self.bindings[0]["role"] = "supplement"
        self.bindings[2]["role"] = "primary"
        preview = self.preview(["wiki-a", "tmdb-a"])
        self.assertEqual(self.requests, ["wiki-a", "tmdb-a"])
        self.assertEqual([row["name"] for row in preview["people_preview"]["changes"]], ["wiki-a", "tmdb-a"])
        self.assertEqual(preview["warnings"], [])
        self.assertEqual(self.assert_one_apply_matches_display(preview), [
            {"binding_id": "wiki-a", "owner": "wiki-a"}, {"binding_id": "tmdb-a", "owner": "tmdb-a"}])

    def test_failed_tmdb_scope_does_not_discard_independent_wiki_people(self):
        self.failed_direct.add("tmdb-b")
        preview = self.preview(["tmdb-a", "tmdb-b", "wiki-a", "wiki-b"])
        self.assertEqual(self.requests, ["tmdb-a", "tmdb-b", "wiki-a", "wiki-b"])
        self.assertEqual([row["status"] for row in preview["sources"]], ["ok", "error", "ok", "ok"])
        self.assertEqual(self.assert_one_apply_matches_display(preview), [
            {"binding_id": "tmdb-a", "owner": "tmdb-a"}, {"binding_id": "wiki-a", "owner": "wiki-a"},
            {"binding_id": "wiki-b", "owner": "wiki-b"}])

    def test_failed_first_wiki_does_not_discard_second_wiki_people(self):
        self.failed_direct.add("wiki-a")
        preview = self.preview(["wiki-a", "wiki-b"])
        self.assertEqual(self.requests, ["wiki-a", "wiki-b"])
        self.assertEqual(self.assert_one_apply_matches_display(preview), [
            {"binding_id": "wiki-b", "owner": "wiki-b"}])

    def test_refreshing_the_second_wiki_alone_uses_its_own_people_and_has_no_skip_warning(self):
        preview = self.preview(["wiki-b"])
        self.assertEqual(self.requests, ["wiki-b"])
        self.assertEqual(preview["warnings"], [])
        self.assertEqual(self.assert_one_apply_matches_display(preview), [
            {"binding_id": "wiki-b", "owner": "wiki-b"}])

    def test_primary_tmdb_and_supplement_wiki_do_not_emit_false_skip_warning(self):
        preview = self.preview(["wiki-a", "tmdb-a"])
        self.assertEqual(self.requests, ["tmdb-a", "wiki-a"])
        self.assertEqual(preview["warnings"], [])
        self.assertEqual([row["provider"] for row in preview["people_preview"]["sources"]], ["tmdb", "wikipedia"])
        self.assertEqual(len(self.assert_one_apply_matches_display(preview)), 2)


if __name__ == "__main__":
    unittest.main()
