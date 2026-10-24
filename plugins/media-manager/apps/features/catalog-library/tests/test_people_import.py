from copy import deepcopy
import unittest
from unittest.mock import patch

from catalog_library import people, people_import, providers, service, wikipedia_people
from people_catalog import localization
from people_catalog.model import source_identity
from work_catalog_yaml.yaml_io import dump_yaml_string
import test_people as fixture


def choices(preview, *, relations=True):
    result = []
    for entry in preview["entries"]:
        owner = entry.get("identity_owner_id")
        target = next((row for row in entry["targets"] if row["id"] == owner), None)
        diff = target["diff"] if owner else entry["creation"]["diff"]
        fields = [row["field"] for row in diff if row["selectable"] and row["after"] and not row.get("must_override")]
        result.append({"external_id": entry["external_id"], "action": "reuse" if owner else "create",
            **({"target_id": owner} if owner else {}), "fields": fields, "overrides": {},
            "relations": [row["id"] for row in entry["relations"]] if relations else []})
    return {preview["snapshot_id"]: result}


def default_choices(preview):
    return {preview["snapshot_id"]: [{"external_id": row["external_id"], "action": row["action"],
            "target_id": row["target_id"], "fields": row["selected_fields"],
            "relations": row["selected_relations"], "overrides": row["overrides"]}
            for row in preview["entries"]]}


class PeopleImportTest(unittest.TestCase):
    setUp = fixture.CatalogPeopleTest.setUp
    ref = fixture.CatalogPeopleTest.ref
    current = fixture.CatalogPeopleTest.current
    request = fixture.CatalogPeopleTest.request
    save_entity = fixture.CatalogPeopleTest.save_entity
    preview = fixture.CatalogPeopleTest.preview

    def apply(self, preview, selections=None):
        return people.apply({"ref": preview["ref"], "snapshot_id": preview["snapshot_id"],
            "preview_token": preview["preview_token"], "people_selections": choices(preview) if selections is None else selections},
            settings=self.settings, people_root=self.people_root, data_root=self.library)

    def test_preview_defaults_new_cards_and_safe_relationships_without_entity_writes(self):
        before = self.source.read_bytes()
        preview = self.preview()
        self.assertEqual(preview["mode"], "people-import")
        self.assertEqual({row["external_id"] for row in preview["entries"]}, {"character:1", "person:1", "person:2"})
        self.assertTrue(all(row["action"] == "create" and row["match_status"] == "new" for row in preview["entries"]))
        for row in preview["entries"]:
            self.assertEqual(row["selected_fields"], row["creation"]["selected_fields"])
            self.assertTrue(set(row["creation"]["required_fields"]).issubset(row["selected_fields"]))
            self.assertEqual(row["selected_relations"], [relation["id"] for relation in row["relations"]])
            self.assertTrue(all(relation["selected"] for relation in row["relations"]))
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse((self.people_root / "db").exists())
        with patch.object(service, "apply_edits", side_effect=AssertionError("default must not write")):
            self.assertFalse(self.apply(preview, {})["people_updated"])

    def test_default_new_cards_can_be_saved_together_without_per_card_confirmation(self):
        preview = self.preview()
        with patch.object(providers, "_request_json", side_effect=AssertionError("offline apply")):
            result = self.apply(preview, default_choices(preview))
        self.assertTrue(result["ok"])
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 3)
        self.assertEqual(len(result["people"]["characters"][0]["actors"]), 2)

    def test_bound_source_defaults_reuse_and_leaves_existing_fields_and_relations_unselected(self):
        preview = self.preview()
        self.apply(preview, default_choices(preview))
        identity = source_identity("bangumi", "person", "1")
        row = self.repo.read(identity)
        row["summary"] = "本地手工资料"
        localization.set_field(row, "localized.summary.und", "本地手工资料")
        self.save_entity(row)
        self.details["person:1"]["summary"] = "更新后的来源资料"
        preview = self.preview()
        for entry in preview["entries"]:
            self.assertEqual(entry["action"], "reuse")
            self.assertEqual(entry["match_status"], "source_bound")
            self.assertEqual(entry["target_id"], entry["identity_owner_id"])
            self.assertEqual(entry["selected_fields"], [])
            self.assertEqual(entry["selected_relations"], [])
            self.assertFalse(entry["creation"]["allowed"])
        entry = next(row for row in preview["entries"] if row["external_id"] == "person:1")
        target = next(row for row in entry["targets"] if row["id"] == identity)
        summary = next(row for row in target["diff"] if row["field"] == "localized.summary.und")
        self.assertEqual((summary["before"], summary["after"]), ("本地手工资料", "更新后的来源资料"))
        self.assertFalse(summary["selected"])
        self.apply(preview, default_choices(preview))
        self.assertEqual(self.repo.read(identity)["summary"], "本地手工资料")

    def test_cancelled_display_fills_preserve_manual_values_but_save_confirmed_source_versions(self):
        preview = self.preview()
        self.apply(preview, default_choices(preview))
        identity = source_identity("bangumi", "person", "1")
        actor = self.repo.read(identity)
        actor.update(name="本地姓名", summary="", aliases=[])
        self.save_entity(actor)
        self.details["person:1"].update(name="新的来源姓名", summary="新的来源简介")
        before = {path.name: path.read_bytes() for path in (self.people_root / "db").glob("*.yaml")}
        work_before = self.source.read_bytes()
        preview = self.preview()
        selection = default_choices(preview)
        actor_choice = next(row for row in selection[preview["snapshot_id"]] if row["target_id"] == identity)
        self.assertIn("localized.summary.und", actor_choice["fields"])
        for row in selection[preview["snapshot_id"]]:
            row["fields"] = []
        result = self.apply(preview, selection)
        self.assertTrue(result["people_updated"])
        self.assertNotEqual({path.name: path.read_bytes() for path in (self.people_root / "db").glob("*.yaml")}, before)
        self.assertEqual(self.source.read_bytes(), work_before)
        self.assertEqual(self.repo.read(identity)["summary"], "")
        self.assertEqual(self.repo.read(identity)["name"], "本地姓名")
        self.assertEqual(self.repo.read(identity)["profile_variants"]["summary"]["und"]["bangumi"]["value"], "新的来源简介")

    def restoration_snapshot(self, provider):
        entries = []
        for external_id, role in (("301", "导演"), ("302", "编剧")):
            identity = source_identity(provider, "person", external_id)
            source = {"provider": provider, "entity_type": "person", "external_id": external_id}
            entity = {"schema_version": 1, "id": identity, "type": "person", "kind": "individual",
                      "name": provider + role, "taxonomy": {"category": "person", "reality": "real", "subtype": "individual"},
                      "source_refs": [source]}
            self.save_entity(entity)
            candidate = {**self.repo.read(identity), "source": source}
            ref = {"id": identity, "type": "person", "role": role,
                   "source": {"provider": provider, "subject_id": "100"}}
            entries.append({"external_id": "person:" + external_id, "candidate": candidate,
                            "relations": [{"id": "relation:" + external_id, "kind": "work", "ref": ref,
                                           "role": role, "required_entity_keys": []}]})
        return {"provider": provider, "subject_id": 100, "import_entries": entries}

    def test_bound_profiles_restore_missing_work_links_for_every_provider_only_on_confirmation(self):
        for provider in ("bangumi", "tmdb", "wikipedia"):
            with self.subTest(provider=provider):
                snapshot = self.restoration_snapshot(provider)
                before = {path.name: path.read_bytes() for path in (self.people_root / "db").glob("*.yaml")}
                preview, _ = people_import.preview(snapshot, provider, {"people_refs": []}, self.repo)
                for entry in preview["entries"]:
                    self.assertEqual(entry["action"], "reuse")
                    self.assertEqual(entry["selected_fields"], [])
                    self.assertEqual(entry["selected_relations"], [entry["relations"][0]["id"]])
                    self.assertEqual(entry["relations"][0]["work_relation_status"], "missing")
                    self.assertTrue(entry["relations"][0]["restore_missing"])
                choices = default_choices(preview)[provider]
                restored, pending, _ = people_import.compose(snapshot, choices, {"people_refs": []}, self.repo)
                self.assertEqual({row["role"] for row in restored["people_refs"]}, {"导演", "编剧"})
                self.assertEqual(set(pending), {entry["target_id"] for entry in preview["entries"]})
                self.assertEqual({path.name: path.read_bytes() for path in (self.people_root / "db").glob("*.yaml")}, before)
                repeated, _ = people_import.preview(snapshot, provider, restored, self.repo)
                self.assertTrue(all(not entry["selected_relations"] for entry in repeated["entries"]))
                self.assertTrue(all(entry["relations"][0]["work_relation_status"] == "existing" for entry in repeated["entries"]))
                again, _, _ = people_import.compose(snapshot, choices, restored, self.repo)
                self.assertEqual(again["people_refs"], restored["people_refs"])
                for choice in choices:
                    choice["relations"] = []
                cancelled, _, _ = people_import.compose(snapshot, choices, {"people_refs": []}, self.repo)
                self.assertEqual(cancelled["people_refs"], [])

    def test_restoration_does_not_default_conflicting_credits_or_unresolved_dependencies(self):
        snapshot = self.restoration_snapshot("tmdb")
        director = snapshot["import_entries"][0]
        director["relations"][0]["ref"]["credit_id"] = "confirmed-credit"
        previous = deepcopy(director["relations"][0]["ref"])
        previous["role"] = "制片人"
        writer = snapshot["import_entries"][1]
        writer["relations"][0]["required_entity_keys"] = ["character:unresolved"]
        preview, _ = people_import.preview(snapshot, "snapshot", {"people_refs": [previous]}, self.repo)
        self.assertTrue(all(not entry["selected_relations"] for entry in preview["entries"]))
        self.assertEqual(preview["entries"][0]["relations"][0]["work_relation_status"], "conflict")
        self.assertTrue(all(not entry["relations"][0]["restore_missing"] for entry in preview["entries"]))

    def test_existing_credit_translation_is_not_mistaken_for_a_missing_work_link(self):
        snapshot = self.restoration_snapshot("tmdb")
        relation = snapshot["import_entries"][0]["relations"][0]
        relation["ref"].update(credit_id="same-credit", character_name="Original Role", role="演员")
        previous = deepcopy(relation["ref"])
        previous["character_name"] = "用户保留的中文角色名"
        preview, _ = people_import.preview(snapshot, "snapshot", {"people_refs": [previous]}, self.repo)
        director = preview["entries"][0]
        self.assertEqual(director["selected_relations"], [])
        self.assertEqual(director["relations"][0]["work_relation_status"], "existing")

    def test_unique_legacy_creditless_link_is_existing_and_explicit_refresh_updates_in_place(self):
        snapshot = self.restoration_snapshot("tmdb")
        director = snapshot["import_entries"][0]
        legacy = deepcopy(director["relations"][0]["ref"])
        director["relations"][0]["ref"]["credit_id"] = "new-native-credit"
        record = {"people_refs": [legacy]}
        preview, _ = people_import.preview(snapshot, "snapshot", record, self.repo)
        self.assertEqual(preview["entries"][0]["relations"][0]["work_relation_status"], "existing")
        self.assertEqual(preview["entries"][0]["selected_relations"], [])
        confirmed = default_choices(preview)["snapshot"]
        confirmed[0]["relations"] = [director["relations"][0]["id"]]
        updated, _, _ = people_import.compose(snapshot, confirmed, record, self.repo)
        links = [row for row in updated["people_refs"] if row["role"] == "导演"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["credit_id"], "new-native-credit")
        self.assertEqual(record["people_refs"], [legacy])

    def test_distinct_known_credits_and_different_legacy_characters_are_not_merged(self):
        snapshot = self.restoration_snapshot("tmdb")
        relation = snapshot["import_entries"][0]["relations"][0]
        relation["ref"]["credit_id"] = "new-credit"
        previous = {**deepcopy(relation["ref"]), "credit_id": "different-known-credit"}
        preview, _ = people_import.preview(snapshot, "snapshot", {"people_refs": [previous]}, self.repo)
        self.assertEqual(preview["entries"][0]["relations"][0]["work_relation_status"], "missing")
        updated, _, _ = people_import.compose(snapshot, default_choices(preview)["snapshot"], {"people_refs": [previous]}, self.repo)
        self.assertEqual({row.get("credit_id") for row in updated["people_refs"] if row["role"] == "导演"},
                         {"new-credit", "different-known-credit"})
        relation["ref"].update(role="演员", character_name="Role A")
        previous = {key: deepcopy(value) for key, value in relation["ref"].items() if key != "credit_id"}
        previous["character_name"] = "Role B"
        preview, _ = people_import.preview(snapshot, "snapshot", {"people_refs": [previous]}, self.repo)
        self.assertEqual(preview["entries"][0]["relations"][0]["work_relation_status"], "missing")
        updated, _, _ = people_import.compose(snapshot, default_choices(preview)["snapshot"], {"people_refs": [previous]}, self.repo)
        self.assertEqual({row.get("character_name") for row in updated["people_refs"] if row["role"] == "演员"}, {"Role A", "Role B"})
        previous["character_name"] = "Role A"
        previous["character_id"] = "old-character"
        incoming = {**relation["ref"], "character_id": "different-character"}
        self.assertIsNone(people_import._find_work_relation([previous], incoming, [incoming]))

    def test_ambiguous_legacy_credit_mapping_requires_confirmation_in_both_directions(self):
        snapshot = self.restoration_snapshot("tmdb")
        director = snapshot["import_entries"][0]
        legacy = deepcopy(director["relations"][0]["ref"])
        director["relations"][0]["ref"]["credit_id"] = "credit-one"
        second = deepcopy(director["relations"][0])
        second.update(id="second-director-credit", ref={**second["ref"], "credit_id": "credit-two"})
        director["relations"].append(second)
        preview, _ = people_import.preview(snapshot, "snapshot", {"people_refs": [legacy]}, self.repo)
        self.assertEqual(preview["entries"][0]["selected_relations"], [])
        self.assertTrue(all(row["work_relation_status"] == "conflict" for row in preview["entries"][0]["relations"]))
        director["relations"].pop()
        preview, _ = people_import.preview(snapshot, "snapshot", {"people_refs": [legacy, {**legacy, "eps": "2"}]}, self.repo)
        self.assertEqual(preview["entries"][0]["selected_relations"], [])
        self.assertEqual(preview["entries"][0]["relations"][0]["work_relation_status"], "conflict")

    def test_sparse_legacy_roles_never_match_by_empty_names_or_one_sided_character_id(self):
        snapshot = self.restoration_snapshot("tmdb")
        relation = snapshot["import_entries"][0]["relations"][0]
        relation["ref"].update(credit_id="new-credit", role="演员", character_name="")
        character_id = source_identity("tmdb", "character", "legacy-sparse-role")
        for character_on_new in (False, True):
            with self.subTest(character_on_new=character_on_new):
                incoming = deepcopy(relation["ref"])
                previous = {key: deepcopy(value) for key, value in incoming.items() if key != "credit_id"}
                (incoming if character_on_new else previous)["character_id"] = character_id
                self.assertIsNone(people_import._find_work_relation([previous], incoming, [incoming]))
                relation["ref"] = incoming
                preview, _ = people_import.preview(snapshot, "snapshot", {"people_refs": [previous]}, self.repo)
                self.assertEqual(preview["entries"][0]["selected_relations"], [])
                self.assertEqual(preview["entries"][0]["relations"][0]["work_relation_status"], "conflict")

        incoming = {**relation["ref"], "character_name": "Known role"}
        incoming.pop("character_id", None)
        previous = {key: deepcopy(value) for key, value in incoming.items() if key != "credit_id"}
        sparse_old = {**previous, "character_name": ""}
        sparse_new = {**incoming, "credit_id": "another-credit", "character_name": ""}
        self.assertIsNone(people_import._find_work_relation([previous, sparse_old], incoming, [incoming]))
        self.assertIsNone(people_import._find_work_relation([previous], incoming, [incoming, sparse_new]))

    def test_reuse_without_fields_still_adds_an_explicit_new_source_binding(self):
        identity = source_identity("manual", "person", "new-source")
        self.save_entity({"schema_version": 1, "id": identity, "type": "person", "kind": "individual",
                          "name": "Same Name", "summary": "本地简介"})
        preview = self.preview()
        result = self.apply(preview, {preview["snapshot_id"]: [{"external_id": "person:1", "action": "reuse",
            "target_id": identity, "fields": [], "relations": [], "overrides": {}}]})
        actor = self.repo.read(identity)
        self.assertTrue(result["people_updated"])
        self.assertEqual(actor["summary"], "本地简介")
        self.assertEqual(actor["source_refs"][0]["snapshot_id"], preview["snapshot_id"])
        self.assertEqual(actor["source_refs"][0]["external_id"], "1")

    def test_default_reuse_fills_empty_fields_but_keeps_local_only_and_conflicting_values(self):
        preview = self.preview()
        self.apply(preview, default_choices(preview))
        identity = source_identity("bangumi", "person", "1")
        actor = self.repo.read(identity)
        actor.update(summary="", aliases=["Local alias"], career=["Local career"])
        localization.set_field(actor, "localized.summary.und-Latn", "")
        self.save_entity(actor)
        self.details["person:1"]["summary"] = "New profile"
        preview = self.preview()
        entry = next(row for row in preview["entries"] if row["external_id"] == "person:1")
        target = next(row for row in entry["targets"] if row["id"] == identity)
        diff = {row["field"]: row for row in target["diff"]}
        self.assertEqual(entry["selected_fields"], ["localized.summary.und-Latn"])
        self.assertEqual(target["selected_fields"], ["localized.summary.und-Latn"])
        self.assertEqual(diff["localized.summary.und-Latn"]["merge_action"], "fill")
        self.assertEqual(diff["localized.aliases.und-Latn"]["merge_action"], "keep")
        self.assertEqual(diff["career"]["merge_action"], "conflict")
        self.assertFalse(diff["localized.aliases.und-Latn"]["changed"])
        self.assertTrue(diff["career"]["requires_confirmation"])
        self.assertEqual(self.repo.read(identity)["summary"], "")
        self.apply(preview, default_choices(preview))
        actor = self.repo.read(identity)
        self.assertEqual(localization.field_value(actor, "localized.summary.und-Latn"), "New profile")
        self.assertEqual(actor["aliases"], ["Local alias"])
        self.assertEqual(actor["career"], ["Local career"])

    def test_selected_list_conflict_adopts_exact_source_field_only(self):
        preview = self.preview()
        self.apply(preview, default_choices(preview))
        identity = source_identity("bangumi", "person", "1")
        actor = self.repo.read(identity)
        actor.update(career=["Local career"], aliases=["Local alias"])
        self.save_entity(actor)
        preview = self.preview()
        self.apply(preview, {preview["snapshot_id"]: [{"external_id": "person:1", "action": "reuse",
            "target_id": identity, "fields": ["career"], "overrides": {}, "relations": []}]})
        actor = self.repo.read(identity)
        self.assertEqual(actor["career"], ["seiyu"])
        self.assertEqual(actor["aliases"], ["Local alias"])

    def test_explicit_relation_selection_can_refresh_evidence_without_profile_fields(self):
        self.apply(self.preview())
        identity = source_identity("bangumi", "person", "2")
        previous = self.repo.read(identity)
        self.details["person:2"]["summary"] = "新的未选简介"
        preview = self.preview()
        entry = next(row for row in preview["entries"] if row["external_id"] == "person:2")
        self.apply(preview, {preview["snapshot_id"]: [{"external_id": "person:2", "action": "reuse",
            "target_id": identity, "fields": [], "relations": [entry["relations"][0]["id"]], "overrides": {}}]})
        actor = self.repo.read(identity)
        self.assertEqual(actor["summary"], previous["summary"])
        self.assertEqual(actor["source_refs"][0]["snapshot_id"], preview["snapshot_id"])
        self.assertNotEqual(actor["source_refs"][0]["snapshot_id"], previous["source_refs"][0]["snapshot_id"])

    def test_full_library_alias_matches_require_identity_confirmation(self):
        identity = source_identity("manual", "person", "alias")
        self.save_entity({"schema_version": 1, "id": identity, "type": "person", "kind": "individual",
                          "name": "Different Name", "aliases": [" Ｓａｍｅ Ｎａｍｅ "]})
        preview = self.preview()
        entry = next(row for row in preview["entries"] if row["external_id"] == "person:2")
        self.assertEqual(entry["action"], "skip")
        self.assertEqual(entry["match_status"], "name_match")
        self.assertEqual([target["id"] for target in entry["targets"]], [identity])
        self.assertEqual(entry["selected_relations"], [])
        self.assertTrue(any(row["selected"] for row in entry["relations"]))

    def test_multiple_name_matches_and_blocked_matches_never_default_to_duplicate_creation(self):
        for key in ("first", "second"):
            self.save_entity({"schema_version": 1, "id": source_identity("manual", "person", key),
                              "type": "person", "kind": "individual", "name": "Same Name"})
        preview = self.preview()
        entry = next(row for row in preview["entries"] if row["external_id"] == "person:2")
        self.assertEqual((entry["action"], entry["match_status"]), ("skip", "ambiguous"))
        self.assertEqual(len(entry["targets"]), 2)
        for key in ("first", "second"):
            row = self.repo.read(source_identity("manual", "person", key))
            row["taxonomy"] = {"category": "organization", "reality": "real", "subtype": "corporation"}
            self.save_entity(row)
        preview = self.preview()
        entry = next(row for row in preview["entries"] if row["external_id"] == "person:2")
        self.assertEqual((entry["action"], entry["match_status"]), ("skip", "conflict"))
        self.assertTrue(all(target["blocked"] for target in entry["targets"]))

    def test_default_relationships_wait_for_unresolved_dependencies(self):
        self.save_entity({"schema_version": 1, "id": source_identity("manual", "person", "same"),
                          "type": "person", "kind": "individual", "name": "Same Name"})
        preview = self.preview()
        character = next(row for row in preview["entries"] if row["entity_type"] == "character")
        self.assertEqual(character["action"], "create")
        for relation in character["relations"]:
            self.assertEqual(relation["selected"], not relation["required_entity_keys"])
        self.apply(preview, default_choices(preview))
        self.assertFalse(self.repo.read(character["candidate"]["id"]).get("voice_actors"))

    def test_creation_recommendations_never_select_empty_source_fields(self):
        preview = self.wiki_preview()
        person = next(row for row in preview["entries"] if row["entity_type"] == "person")
        self.assertNotIn("summary", person["creation"]["selected_fields"])
        for row in person["creation"]["diff"]:
            self.assertEqual(row["field"] in person["creation"]["selected_fields"],
                             bool(row["selectable"] and row["after"] and not row["must_override"]))

    def test_conflicting_source_owner_is_not_automatically_reused_or_recreated(self):
        identity = source_identity("bangumi", "person", "1")
        self.save_entity({"schema_version": 1, "id": identity, "type": "person", "kind": "corporation",
                          "name": "Renamed locally", "source_refs": [{"provider": "bangumi", "entity_type": "person",
                                                                      "external_id": "1"}]})
        previous = self.repo.read(identity)
        preview = self.preview()
        entry = next(row for row in preview["entries"] if row["external_id"] == "person:1")
        self.assertEqual((entry["action"], entry["match_status"]), ("skip", "conflict"))
        self.assertFalse(entry["creation"]["allowed"])
        self.assertEqual(entry["selected_fields"], [])
        self.assertEqual(self.repo.read(identity), previous)
        character = next(row for row in preview["entries"] if row["entity_type"] == "character")
        actor_relation = next(row for row in character["relations"] if "person:1" in row["required_entity_keys"])
        self.assertFalse(actor_relation["selected"])

    def test_existing_canonical_card_without_explicit_source_defaults_only_empty_field_fills(self):
        identity = source_identity("bangumi", "person", "1")
        self.save_entity({"schema_version": 1, "id": identity, "type": "person", "kind": "individual",
                          "name": "Renamed locally"})
        preview = self.preview()
        entry = next(row for row in preview["entries"] if row["external_id"] == "person:1")
        self.assertEqual((entry["action"], entry["match_status"]), ("reuse", "source_bound"))
        self.assertFalse(entry["creation"]["allowed"])
        self.assertEqual(entry["selected_fields"], ["localized.summary.und-Latn", "career"])
        self.assertIn(identity, [target["id"] for target in entry["targets"]])

    def test_explicit_fields_relations_apply_once_offline(self):
        preview = self.preview()
        with patch.object(providers, "_request_json", side_effect=AssertionError("offline apply")), \
             patch.object(service, "apply_edits", wraps=service.apply_edits) as save:
            result = self.apply(preview)
        self.assertTrue(result["ok"])
        self.assertEqual(save.call_count, 1)
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 3)
        self.assertEqual(len(result["people"]["characters"][0]["actors"]), 2)
        self.assertNotEqual(result["records"][0]["ref"], preview["ref"])

    def test_profile_only_does_not_create_work_or_voice_relationships(self):
        preview = self.preview()
        self.apply(preview, choices(preview, relations=False))
        self.assertFalse(self.current().get("people_refs"))
        self.assertFalse(self.repo.read(source_identity("bangumi", "character", "1")).get("voice_actors"))

    def test_relation_dependency_must_be_selected_or_exactly_bound(self):
        preview = self.preview()
        selection = choices(preview)
        selection[preview["snapshot_id"]] = [row for row in selection[preview["snapshot_id"]] if row["external_id"] == "character:1"]
        with self.assertRaisesRegex(ValueError, "依赖"):
            self.apply(preview, selection)
        self.assertFalse(list((self.people_root / "db").glob("*.yaml")))

    def test_same_name_only_suggests_and_full_library_non_work_target_is_supported(self):
        identity = source_identity("manual", "person", "outside")
        self.save_entity({"schema_version": 1, "id": identity, "type": "person", "kind": "individual", "name": "Same Name", "summary": "手填"})
        preview = self.preview()
        entry = next(row for row in preview["entries"] if row["external_id"] == "person:2")
        self.assertEqual(entry["action"], "skip")
        self.assertEqual(entry["recommended_target_id"], "")
        self.assertIn(identity, [row["id"] for row in preview["targets"]])
        target = people_import.target_preview({"snapshot_id": preview["snapshot_id"], "preview_token": preview["selection_token"],
            "external_id": entry["external_id"], "target_id": identity}, settings=self.settings, people_root=self.people_root)["target"]
        self.assertFalse(target["blocked"])
        self.apply(preview, {preview["snapshot_id"]: [{"external_id": "person:2", "action": "reuse", "target_id": identity,
            "fields": [], "relations": [entry["relations"][0]["id"]], "overrides": {}}]})
        self.assertEqual(self.repo.read(identity)["summary"], "手填")
        self.assertEqual(self.current()["people_refs"][0]["id"], identity)

    def test_manual_field_overrides_and_explicit_clear_are_bounded(self):
        preview = self.preview()
        selection = choices(preview, relations=False)
        first = selection[preview["snapshot_id"]][0]
        name_field = preview["entries"][0]["creation"]["required_fields"][0]
        summary_field = next(field for field in first["fields"] if field.startswith("localized.summary."))
        first["overrides"] = {name_field: "人工核对名称", summary_field: ""}
        self.apply(preview, selection)
        row = self.repo.read(source_identity("bangumi", "character", "1"))
        self.assertEqual(row["name"], "人工核对名称")
        self.assertEqual(localization.field_value(row, summary_field), "")
        next_preview = self.preview()
        self.apply(next_preview, {})
        self.assertEqual(localization.field_value(self.repo.read(row["id"]), summary_field), "")

    def test_overrides_cannot_change_identity_or_unselected_field(self):
        preview = self.preview()
        entry = next(row for row in preview["entries"] if row["external_id"] == "person:1")
        for overrides in ({"id": "attacker"}, {"avatar": {}}, {"summary": "not selected"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.apply(preview, {preview["snapshot_id"]: [{"external_id": "person:1", "action": "create", "fields": entry["creation"]["required_fields"], "overrides": overrides}]})

    def test_bound_source_owner_cannot_be_transferred(self):
        self.apply(self.preview())
        other = source_identity("manual", "person", "other")
        self.save_entity({"schema_version": 1, "id": other, "type": "person", "kind": "individual", "name": "Other"})
        preview = self.preview()
        with self.assertRaisesRegex(ValueError, "所有者"):
            self.apply(preview, {preview["snapshot_id"]: [{"external_id": "person:1", "action": "reuse", "target_id": other, "fields": []}]})

    def test_stale_full_inventory_and_work_version_rejected(self):
        preview = self.preview()
        self.save_entity({"schema_version": 1, "id": source_identity("manual", "person", "late"), "type": "person", "name": "Late"})
        with self.assertRaisesRegex(ValueError, "变化"):
            self.apply(preview)

    def test_definite_work_failure_rolls_back_all_new_entities(self):
        preview = self.preview()
        before = self.source.read_bytes()
        with patch.object(service, "apply_edits", side_effect=ValueError("failed")), self.assertRaisesRegex(ValueError, "failed"):
            self.apply(preview)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(list((self.people_root / "db").glob("*.yaml")), [])

    def test_wikipedia_person_page_does_not_require_tmdb_or_work_binding(self):
        profile = {"provider": "wikipedia", "external_id": "77", "name": "演员", "name_cn": "演员", "aliases": [], "summary": "简介", "infobox": [],
            "source_url": "https://zh.wikipedia.org/wiki/演员", "fetched_at": "2026-10-07T00:00:00+00:00", "source_metadata": {}, "raw": {}}
        with patch.object(wikipedia_people, "fetch_person_profile", return_value=profile):
            preview = people_import.wikipedia_preview({"ref": self.ref(), "pages": [{"page": "演员", "role": "演员", "character_name": "角色"}]},
                settings=self.settings, people_root=self.people_root)
        self.assertEqual(len(preview["entries"]), 2)
        with patch.object(wikipedia_people, "fetch_person_profile", side_effect=AssertionError("offline")):
            result = self.apply(preview)
        self.assertEqual(len(result["people"]["cast"]), 1)
        self.assertTrue(result["people"]["cast"][0]["character"]["id"])

    def wiki_preview(self, name="Role", name_cn="角色"):
        profile = {"provider": "wikipedia", "external_id": "77", "name": "演员", "name_cn": "演员",
            "source_url": "https://zh.wikipedia.org/wiki/演员", "fetched_at": "2026-10-07T00:00:00+00:00", "source_metadata": {}, "raw": {}}
        with patch.object(wikipedia_people, "fetch_person_profile", return_value=profile):
            return people_import.wikipedia_preview({"ref": self.ref(), "pages": [{"page": "演员", "role": "演员",
                "character_name": name, "character_name_cn": name_cn}]}, settings=self.settings, people_root=self.people_root)

    def test_role_profile_can_be_created_without_actor_or_relationship(self):
        preview = self.wiki_preview()
        selection = choices(preview, relations=False)
        selection[preview["snapshot_id"]] = [row for row in selection[preview["snapshot_id"]] if row["external_id"].startswith("character:")]
        self.apply(preview, selection)
        self.assertIsNone(self.repo.read(source_identity("wikipedia", "person", "77")))
        self.assertFalse(self.current().get("people_refs"))
        identity = next(row["candidate"]["id"] for row in preview["entries"] if row["entity_type"] == "character")
        self.assertFalse(self.repo.read(identity).get("role_origins"))

    def test_explicit_taxonomy_edit_works_on_new_and_existing_profiles(self):
        preview = self.preview()
        entry = next(row for row in preview["entries"] if row["external_id"] == "person:2")
        selection = {preview["snapshot_id"]: [{"external_id": "person:2", "action": "create", "fields": [*entry["creation"]["required_fields"], "taxonomy"],
            "overrides": {"taxonomy": {"category": "person", "reality": "real", "subtype": "individual"}}, "relations": []}]}
        self.apply(preview, selection)
        identity = source_identity("bangumi", "person", "2")
        preview = self.preview()
        self.apply(preview, {preview["snapshot_id"]: [{"external_id": "person:2", "action": "reuse", "target_id": identity,
            "fields": ["taxonomy"], "overrides": {"taxonomy": {"category": "person", "reality": "unknown", "subtype": "unknown"}}, "relations": []}]})
        self.assertEqual(self.repo.read(identity)["taxonomy"]["reality"], "unknown")

    def test_selected_actor_relationship_rejects_non_character_taxonomy(self):
        preview = self.wiki_preview()
        selection = choices(preview)
        role = next(row for row in selection[preview["snapshot_id"]] if row["external_id"].startswith("character:"))
        role["overrides"]["taxonomy"] = {"category": "organization", "reality": "fictional", "subtype": "organization"}
        with self.assertRaisesRegex(ValueError, "虚构人物角色"):
            self.apply(preview, selection)
        self.assertFalse(list((self.people_root / "db").glob("*.yaml")))

    def test_explicit_relationship_refresh_updates_translation_but_keeps_manual_clear(self):
        self.apply(self.wiki_preview())
        preview = self.wiki_preview(name_cn="来源新译名")
        self.apply(preview)
        self.assertEqual(self.current()["people_refs"][0]["character_name_cn"], "来源新译名")
        record = self.current()
        record["people_refs"][0]["character_name"] = ""
        record["people_refs"][0]["character_name_cn"] = ""
        self.source.write_text(dump_yaml_string([record]), encoding="utf-8")
        self.apply(self.wiki_preview(name_cn="再次更新"))
        self.assertEqual(self.current()["people_refs"][0]["character_name_cn"], "")
        self.assertEqual(self.current()["people_refs"][0]["character_name"], "")
        self.assertEqual(len(self.current()["people_refs"]), 1)
        self.assertEqual(self.current()["people_refs"][0]["source_values"]["character_name_cn"], "再次更新")

    def test_empty_source_fields_require_override_and_safe_fills_are_selected(self):
        preview = self.wiki_preview()
        entry = next(row for row in preview["entries"] if row["entity_type"] == "person")
        summary = next(row for row in entry["creation"]["diff"] if row["field"] == "summary")
        self.assertTrue(summary["must_override"])
        self.assertFalse(summary["selected"])
        self.assertEqual([row["field"] for row in entry["creation"]["diff"] if row["selected"]], entry["selected_fields"])
        with self.assertRaisesRegex(ValueError, "来源缺失"):
            self.apply(preview, {preview["snapshot_id"]: [{"external_id": entry["external_id"], "action": "create",
                "fields": ["name", "summary"], "overrides": {}, "relations": []}]})

    def test_one_failed_wikipedia_page_does_not_block_other_explicit_page(self):
        profile = {"external_id": "77", "name": "演员", "source_url": "https://zh.wikipedia.org/wiki/演员", "fetched_at": "now"}
        with patch.object(wikipedia_people, "fetch_person_profile", side_effect=[ValueError("页面不可用"), profile]):
            preview = people_import.wikipedia_preview({"ref": self.ref(), "pages": [{"page": "bad"}, {"page": "good"}]},
                                                     settings=self.settings, people_root=self.people_root)
        self.assertEqual(len(preview["entries"]), 1)
        self.assertTrue(preview["warnings"])
        self.apply(preview)
        self.assertEqual(self.repo.read(source_identity("wikipedia", "person", "77"))["name"], "演员")


if __name__ == "__main__":
    unittest.main()
