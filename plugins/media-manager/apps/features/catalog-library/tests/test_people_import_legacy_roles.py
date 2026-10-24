"""Cleared work links must not orphan exactly identified pre-UUID role cards."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from catalog_library import people, people_import, role_catalog
from people_catalog.model import source_identity
from people_catalog.repository import PeopleRepository
from test_people_import import default_choices
import test_role_catalog as fixture


class LegacyRoleImportTest(unittest.TestCase):
    setUp = fixture.RoleCatalogTest.setUp
    write = fixture.RoleCatalogTest.write
    save_entity = fixture.RoleCatalogTest.save_entity

    def seed_role(self):
        updated, entities, _ = role_catalog.materialize(self.record, [], self.repo)
        self.save_entity(entities[0])
        return updated, entities[0]

    def normalize(self, record=None, refs=None, actors=None):
        incoming = deepcopy(refs if refs is not None else self.record["people_refs"])
        actors = deepcopy(actors if actors is not None else [self.actor])
        record = deepcopy(record if record is not None else {**self.record, "id": "work_after_import", "people_refs": []})

        def build(_snapshot, _snapshot_id, seed, repository, **_kwargs):
            seed["people_refs"] = incoming
            return role_catalog.materialize(seed, actors, repository)

        with patch.object(people, "_build", side_effect=build), \
             patch.object(PeopleRepository, "apply_batch", side_effect=AssertionError("normalization is read-only")):
            snapshot = people_import.normalize({"provider": "tmdb", "subject_id": 100}, record, self.repo)
            preview, _ = people_import.preview(snapshot, "snapshot", record, self.repo)
        return record, snapshot, preview

    def role(self, preview):
        return next(row for row in preview["entries"] if row["entity_type"] == "character")

    def test_cleared_links_recover_exact_pre_uuid_card_and_default_missing_relationship(self):
        _, old = self.seed_role()
        before = {path.name: path.read_bytes() for path in (self.people_root / "db").glob("*.yaml")}
        work_before = self.source.read_bytes()
        record, snapshot, preview = self.normalize()
        role = self.role(preview)
        self.assertEqual((role["action"], role["match_status"], role["target_id"]), ("reuse", "source_bound", old["id"]))
        self.assertFalse(role["creation"]["allowed"])
        actor = next(row for row in preview["entries"] if row["entity_type"] == "person")
        self.assertEqual(actor["selected_relations"], [actor["relations"][0]["id"]])
        updated, pending, _ = people_import.compose(snapshot, default_choices(preview)["snapshot"], record, self.repo)
        self.assertEqual(updated["people_refs"][0]["character_id"], old["id"])
        self.assertEqual(set(pending), {old["id"], self.actor_id})
        self.assertEqual({path.name: path.read_bytes() for path in (self.people_root / "db").glob("*.yaml")}, before)
        self.assertEqual(self.source.read_bytes(), work_before)

    def test_credit_identity_survives_translation_but_name_change_still_requires_confirmation(self):
        _, old = self.seed_role()
        refs = deepcopy(self.record["people_refs"])
        refs[0]["character_name"] = "다른이름"
        _, _, preview = self.normalize(refs=refs)
        role = self.role(preview)
        self.assertEqual(role["target_id"], old["id"])
        self.assertEqual(role["action"], "reuse")
        name = next(field for field in role["targets"][0]["diff"] if field["field"] == "localized.name.ko")
        self.assertTrue(name["requires_confirmation"])
        self.assertNotIn(name["field"], role["selected_fields"])
        self.assertEqual(self.repo.read(old["id"])["name"], old["name"])

    def test_different_actor_subject_season_or_credit_never_inherits_old_role(self):
        _, old = self.seed_role()
        other_actor = deepcopy(self.actor)
        other_actor["id"] = source_identity("tmdb", "person", "2")
        other_actor["source_refs"][0]["external_id"] = "2"
        self.save_entity(other_actor)
        for case in ("actor", "subject", "season", "credit"):
            with self.subTest(case=case):
                refs = deepcopy(self.record["people_refs"])
                actors = [self.actor]
                if case == "actor":
                    refs[0]["id"] = other_actor["id"]
                    actors = [other_actor]
                elif case == "subject":
                    refs[0]["source"]["subject_id"] = "101"
                elif case == "season":
                    refs[0]["source"]["season_number"] = 2
                else:
                    refs[0]["credit_id"] = "other-credit"
                _, _, preview = self.normalize(refs=refs, actors=actors)
                role = self.role(preview)
                self.assertEqual(role["identity_owner_id"], "")
                self.assertEqual(role["action"], "skip" if case in {"actor", "credit"} else "create")
                # Unrelated subject/season names no longer become global
                # character suggestions. Same-scope ambiguous roles do.
                if case in {"subject", "season"}:
                    self.assertEqual(role["targets"], [])
                self.assertNotEqual(role["candidate"]["id"], old["id"])

    def test_creditless_recovery_requires_exact_original_name(self):
        self.record["people_refs"][0].pop("credit_id")
        _, old = self.seed_role()
        self.assertEqual(self.role(self.normalize()[2])["target_id"], old["id"])
        refs = deepcopy(self.record["people_refs"])
        refs[0]["character_name"] = "Unconfirmed rename"
        self.assertEqual(self.role(self.normalize(refs=refs)[2])["identity_owner_id"], "")

    def test_missing_or_ambiguous_origin_is_not_authority(self):
        _, old = self.seed_role()
        for origins in ([], [old["role_origins"][0], {**old["role_origins"][0], "original_name": "ambiguous"}]):
            with self.subTest(origins=origins):
                self.save_entity({**old, "role_origins": origins})
                self.assertEqual(self.role(self.normalize()[2])["identity_owner_id"], "")

    def test_unbound_canonical_file_without_source_evidence_does_not_authorize_migration(self):
        _, old = self.seed_role()
        no_provenance = [{key: value for key, value in row.items() if key != "evidence_kind"} for row in old["source_refs"]]
        for sources in ([], no_provenance):
            with self.subTest(sources=sources):
                self.save_entity({**old, "source_refs": sources})
                self.assertEqual(self.role(self.normalize()[2])["identity_owner_id"], "")

    def test_another_work_uuid_is_not_legacy_migration_evidence(self):
        self.record["id"] = "work_another_uuid"
        _, old = self.seed_role()
        role = self.role(self.normalize()[2])
        self.assertEqual(role["identity_owner_id"], "")
        self.assertNotEqual(role["candidate"]["id"], old["id"])

    def test_existing_current_uuid_identity_takes_precedence_over_legacy(self):
        _, old = self.seed_role()
        current = {**self.record, "id": "work_after_import"}
        _, values, _ = role_catalog.materialize(current, [], self.repo)
        active = values[0]
        self.save_entity(active)
        self.assertNotEqual(active["id"], old["id"])
        self.assertEqual(self.role(self.normalize()[2])["target_id"], active["id"])

    def test_explicit_work_identity_takes_precedence_over_legacy_fallback(self):
        _, old = self.seed_role()
        current = {**self.record, "id": "work_other_explicit_identity"}
        linked, values, _ = role_catalog.materialize(current, [], self.repo)
        active = values[0]
        self.save_entity(active)
        linked["id"] = "work_after_import"
        self.assertNotEqual(active["id"], old["id"])
        self.assertEqual(self.role(self.normalize(record=linked)[2])["target_id"], active["id"])

    def test_recovery_respects_explicitly_merged_owner_without_reviving_donor(self):
        _, old = self.seed_role()
        target = {**old, "id": source_identity("manual", "character", "merged-role"), "name_cn": "手工角色名"}
        retired = {**old, "merged_into": target["id"], "source_refs": []}
        self.save_entity({**target, "source_refs": []})
        self.repo.apply_batch([target, retired], expected_revision=self.repo.revision([target["id"], old["id"]]))
        record, snapshot, preview = self.normalize()
        self.assertEqual(self.role(preview)["target_id"], target["id"])
        updated, pending, _ = people_import.compose(snapshot, default_choices(preview)["snapshot"], record, self.repo)
        self.assertEqual(updated["people_refs"][0]["character_id"], target["id"])
        self.assertNotIn(old["id"], pending)
        self.assertEqual(self.repo.read(old["id"]), retired)

    def test_recovered_identity_keeps_existing_taxonomy_block(self):
        _, old = self.seed_role()
        self.save_entity({**old, "taxonomy": {"category": "organization", "reality": "fictional", "subtype": "organization"}})
        role = self.role(self.normalize()[2])
        self.assertEqual(role["identity_owner_id"], old["id"])
        self.assertEqual(role["action"], "skip")
        self.assertTrue(role["targets"][0]["blocked"])
        self.assertFalse(role["creation"]["allowed"])

    def test_merged_multiple_origins_preserve_exact_legacy_evidence_despite_matching_other_name(self):
        _, old = self.seed_role()
        refs = deepcopy(self.record["people_refs"])
        refs[0]["character_name"] = "다른이름"
        other_origin = role_catalog._origin({"id": "work_another_uuid"}, refs[0])
        target = {**old, "id": source_identity("manual", "character", "merged-multiple-origins"),
                  "role_origins": [*old["role_origins"], other_origin],
                  "source_refs": [*old["source_refs"], {**old["source_refs"][0], "external_id": other_origin["external_id"]}]}
        self.save_entity({**target, "source_refs": []})
        retired = {**old, "merged_into": target["id"], "source_refs": []}
        self.repo.apply_batch([target, retired], expected_revision=self.repo.revision([target["id"], old["id"]]))
        _, snapshot, preview = self.normalize(refs=refs)
        role = self.role(preview)
        self.assertEqual(role["target_id"], target["id"])
        self.assertEqual(role["candidate"]["source"]["external_id"], old["role_origins"][0]["external_id"])
        incoming = next(row for row in snapshot["import_entries"] if row["candidate"]["type"] == "character")
        self.assertEqual(incoming["role_origins"], old["role_origins"])


if __name__ == "__main__":
    unittest.main()
