"""Translation evidence preserves roles; cross-source identity remains a choice."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from catalog_library import people, people_import, role_catalog
from people_catalog.model import source_identity
from people_catalog.repository import PeopleRepository
from test_people_import import default_choices
import test_role_catalog as fixture


class RoleIdentityImportTest(unittest.TestCase):
    setUp = fixture.RoleCatalogTest.setUp
    write = fixture.RoleCatalogTest.write
    save_entity = fixture.RoleCatalogTest.save_entity

    def wiki_actor(self, *, link_tmdb=False):
        source = {"provider": "wikipedia", "entity_type": "person", "external_id": "77"}
        if link_tmdb:
            self.actor["source_refs"].append(source)
            self.save_entity(self.actor)
            return self.actor
        actor = {**deepcopy(self.actor), "id": source_identity("wikipedia", "person", "77"), "source_refs": [source]}
        self.save_entity(actor)
        return actor

    def seed_wiki_role(self, actor, *, name="金部長", work_id="work_kim", other_name=None):
        record = {"id": work_id, "people_refs": [{"id": actor["id"], "type": "person", "role": "演员",
            "character_name": name, "character_name_cn": name,
            "source": {"provider": "wikipedia", "subject_id": "9263078"}}]}
        if other_name:
            record["people_refs"].append({**record["people_refs"][0], "character_name": other_name, "character_name_cn": other_name})
        _, entities, _ = role_catalog.materialize(record, [], self.repo)
        for entity in entities:
            self.save_entity(entity)
        return entities[0]

    def incoming(self, *, name="김부장", chinese="金部長", include_tmdb=False, second_role=False, record=None, role_cell=None):
        record = record or {"id": "work_kim", "people_refs": []}
        profile = {"external_id": "77", "name": "소지섭", "name_cn": "苏志燮", "kind": "individual",
            "source_url": "https://zh.wikipedia.org/wiki/蘇志燮", "fetched_at": "2026-10-11T00:00:00+00:00"}
        if include_tmdb:
            profile["source_refs"] = [{"provider": "tmdb", "entity_type": "person", "external_id": "1"}]
        relation = {"external_id": "77", "role": "演员", "character_name": name, "character_name_cn": chinese}
        if role_cell is not None:
            relation["evidence"] = {"role_cell": role_cell}
        snapshot = {"provider": "wikipedia", "subject_id": 9263078,
                    "raw": {"profiles": [profile], "relations": [relation]}}
        if second_role:
            snapshot["raw"]["relations"].append({**relation, "character_name": "다른사람", "character_name_cn": "另一角色"})
        with patch.object(PeopleRepository, "apply_batch", side_effect=AssertionError("preview must not write")):
            people_import.normalize(snapshot, record, self.repo)
            preview, _ = people_import.preview(snapshot, "snapshot", record, self.repo)
        return record, snapshot, preview

    def role(self, preview):
        return next(row for row in preview["entries"] if row["entity_type"] == "character")

    def test_cleared_wiki_role_uses_same_card_when_chinese_gains_korean_name(self):
        actor = self.wiki_actor()
        old = self.seed_wiki_role(actor)
        before = {path.name: path.read_bytes() for path in (self.people_root / "db").glob("*.yaml")}
        record, snapshot, preview = self.incoming()
        role = self.role(preview)
        self.assertEqual((role["action"], role["target_id"]), ("reuse", old["id"]))
        self.assertFalse(role["creation"]["allowed"])
        updated, pending, _ = people_import.compose(snapshot, default_choices(preview)["snapshot"], record, self.repo)
        self.assertEqual(updated["people_refs"][0]["character_id"], old["id"])
        self.assertEqual(len(pending), 2)
        self.assertEqual({path.name: path.read_bytes() for path in (self.people_root / "db").glob("*.yaml")}, before)

    def test_translation_recovery_rejects_different_work_actor_and_chinese_evidence(self):
        actor = self.wiki_actor()
        old = self.seed_wiki_role(actor)
        for kwargs in ({"record": {"id": "work_other", "people_refs": []}}, {"chinese": "不同角色"}, {"name": "Unconfirmed rename"}):
            with self.subTest(kwargs=kwargs):
                self.assertNotEqual(self.role(self.incoming(**kwargs)[2])["identity_owner_id"], old["id"])

    def test_simplified_conversion_uses_retained_exact_traditional_source_cell(self):
        actor = self.wiki_actor()
        old = self.seed_wiki_role(actor)
        role = self.role(self.incoming(chinese="金部长", role_cell="金部長（73號）")[2])
        self.assertEqual((role["action"], role["target_id"]), ("reuse", old["id"]))
        self.assertEqual(self.role(self.incoming(chinese="金部长", role_cell="別人（73號）")[2])["identity_owner_id"], "")

    def test_profile_without_actor_provenance_is_suggested_not_recreated_or_bound(self):
        actor = self.wiki_actor()
        old = self.seed_wiki_role(actor)
        old.pop("role_origins")
        self.save_entity(old)
        role = self.role(self.incoming(chinese="金部长", role_cell="金部長（73號）")[2])
        self.assertEqual((role["action"], role["identity_owner_id"]), ("skip", ""))
        self.assertEqual(role["targets"][0]["id"], old["id"])
        self.assertNotIn("73號", role["candidate"].get("aliases", []))

    def test_two_prior_or_two_incoming_roles_never_use_unique_translation_fallback(self):
        actor = self.wiki_actor()
        old = self.seed_wiki_role(actor)
        self.assertEqual(self.role(self.incoming(second_role=True)[2])["identity_owner_id"], "")
        self.seed_wiki_role(actor, other_name="別人")
        self.assertEqual(self.role(self.incoming()[2])["identity_owner_id"], "")
        self.assertEqual(self.repo.read(old["id"])["name"], "金部長")

    def seed_tmdb_role(self, *, legacy=False):
        record = deepcopy(self.record)
        if not legacy:
            record["id"] = "work_kim"
        record["people_refs"][0].update(character_name="Manager Kim", character_name_cn="")
        _, entities, _ = role_catalog.materialize(record, [], self.repo)
        self.save_entity(entities[0])
        return entities[0]

    def test_cross_source_unique_work_actor_role_defaults_to_reuse(self):
        old = self.seed_tmdb_role()
        record, snapshot, preview = self.incoming(include_tmdb=True)
        actor = next(row for row in preview["entries"] if row["entity_type"] == "person")
        self.assertEqual(actor["target_id"], self.actor_id)
        role = self.role(preview)
        self.assertEqual((role["action"], role["match_status"], role["identity_owner_id"]), ("reuse", "role_evidence", ""))
        self.assertEqual(role["target_id"], old["id"])
        self.assertEqual(role["recommended_target_id"], old["id"])
        self.assertEqual(role["targets"][0]["id"], old["id"])
        self.assertIn("tmdb", {source["provider"] for source in snapshot["import_entries"][0]["candidate"]["source_refs"]})
        self.assertEqual(self.repo.read(old["id"])["name"], "Manager Kim")
        updated, pending, _ = people_import.compose(snapshot, default_choices(preview)["snapshot"], record, self.repo)
        self.assertEqual(updated["people_refs"][0]["character_id"], old["id"])
        self.assertEqual({source["provider"] for source in pending[old["id"]]["source_refs"]}, {"tmdb", "wikipedia"})

    def test_cross_source_needs_confirmed_actor_and_work_scope(self):
        self.seed_tmdb_role()
        for kwargs in ({}, {"include_tmdb": True, "record": {"id": "work_unrelated", "people_refs": []}},
                       {"include_tmdb": True, "second_role": True}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.role(self.incoming(**kwargs)[2])["recommended_target_id"], "")

    def test_same_work_multiple_seasons_or_ranges_needs_scope_confirmation(self):
        old = self.seed_tmdb_role()
        for bindings in (
            [{"provider": "tmdb", "external_id": "100", "season_number": season, "scope": f"season:{season}"}
             for season in (1, 2)],
            [{"provider": "tmdb", "external_id": "100", "season_number": 1, "scope": f"season:1:episodes:{start}-{end}",
              "episode_range": {"start": start, "end": end}} for start, end in ((1, 8), (9, 16))],
        ):
            with self.subTest(bindings=bindings):
                record = {"id": "work_kim", "people_refs": [], "source_refs": bindings}
                role = self.role(self.incoming(include_tmdb=True, record=record)[2])
                self.assertEqual(role["action"], "skip")
                self.assertEqual(role["recommended_target_id"], "")
                self.assertEqual(role["targets"][0]["id"], old["id"])

    def test_different_age_stage_is_not_automatic_role_evidence(self):
        from people_catalog import portrayal
        old = self.seed_tmdb_role()
        old["role_origins"][0]["qualifier"] = portrayal.parse_qualifier("Manager Kim (child)")
        self.save_entity(old)
        role = self.role(self.incoming(include_tmdb=True, name="김부장 (adult)", chinese="金部长")[2])
        self.assertEqual(role["recommended_target_id"], "")
        self.assertNotEqual(role["target_id"], old["id"])

    def test_existing_both_source_cards_keep_their_owners(self):
        tmdb = self.seed_tmdb_role()
        actor = self.wiki_actor(link_tmdb=True)
        wiki = self.seed_wiki_role(actor)
        role = self.role(self.incoming(include_tmdb=True)[2])
        self.assertEqual(role["target_id"], wiki["id"])
        self.assertNotEqual(wiki["id"], tmdb["id"])
        self.assertEqual(self.repo.read(tmdb["id"])["name"], "Manager Kim")

    def test_legacy_other_source_role_requires_explicit_work_binding(self):
        old = self.seed_tmdb_role(legacy=True)
        self.assertEqual(self.role(self.incoming(include_tmdb=True)[2])["recommended_target_id"], "")
        record = {"id": "work_kim", "people_refs": [], "source_refs": deepcopy(self.record["source_refs"])}
        self.assertEqual(self.role(self.incoming(include_tmdb=True, record=record)[2])["recommended_target_id"], old["id"])

    def test_wiki_adoption_preserves_existing_tmdb_snapshot_and_metadata(self):
        self.actor["source_refs"][0].update(snapshot_id="a" * 64, fetched_at="2026-10-10T00:00:00+00:00",
                                            source_url="https://www.themoviedb.org/person/1")
        self.save_entity(self.actor)
        record, snapshot, preview = self.incoming(include_tmdb=True)
        snapshot["_snapshot_id"] = "b" * 64
        selected = next(row for row in default_choices(preview)["snapshot"] if row["external_id"].startswith("person:"))
        selected["relations"] = []
        _, pending, _ = people_import.compose(snapshot, [selected], record, self.repo)
        sources = pending[self.actor_id]["source_refs"]
        self.assertEqual(next(row for row in sources if row["provider"] == "tmdb"), self.actor["source_refs"][0])
        self.assertEqual(next(row for row in sources if row["provider"] == "wikipedia")["snapshot_id"], "b" * 64)

    def tmdb_with_wiki_identity(self, tmdb_id="200"):
        actor = {**deepcopy(self.actor), "id": source_identity("tmdb", "person", tmdb_id),
                 "source_refs": [{"provider": "tmdb", "entity_type": "person", "external_id": tmdb_id}]}
        record = {"id": "work_kim", "people_refs": []}
        snapshot = {"provider": "tmdb", "subject_id": 100,
                    "raw": {"wikipedia_people": {"actor_matches": [{"tmdb_person_id": tmdb_id, "wikipedia_pageid": "77"}]}}}

        def build(_snapshot, _snapshot_id, seed, repository, **_kwargs):
            return seed, [actor], []

        with patch.object(people, "_build", side_effect=build):
            people_import.normalize(snapshot, record, self.repo)
            preview, _ = people_import.preview(snapshot, "snapshot", record, self.repo)
        return record, snapshot, preview

    def test_tmdb_first_import_reuses_verified_existing_wiki_actor(self):
        actor = self.wiki_actor()
        actor["source_refs"][0]["snapshot_id"] = "c" * 64
        self.save_entity(actor)
        record, snapshot, preview = self.tmdb_with_wiki_identity()
        self.assertEqual(preview["entries"][0]["target_id"], actor["id"])
        snapshot["_snapshot_id"] = "d" * 64
        _, pending, _ = people_import.compose(snapshot, default_choices(preview)["snapshot"], record, self.repo)
        sources = pending[actor["id"]]["source_refs"]
        self.assertEqual(next(row for row in sources if row["provider"] == "wikipedia"), actor["source_refs"][0])
        self.assertEqual(next(row for row in sources if row["provider"] == "tmdb")["snapshot_id"], "d" * 64)

    def test_verified_cross_source_pair_with_two_existing_owners_remains_separate(self):
        wiki = self.wiki_actor()
        _, snapshot, preview = self.tmdb_with_wiki_identity("1")
        self.assertEqual(preview["entries"][0]["target_id"], self.actor_id)
        self.assertNotEqual(wiki["id"], self.actor_id)
        self.assertTrue(any("分别关联不同资料卡" in warning for warning in snapshot["warnings"]))
        self.assertEqual({row["provider"] for row in preview["entries"][0]["candidate"]["source_refs"]}, {"tmdb"})

    def test_source_stamp_never_attributes_new_cross_provider_identity_to_current_snapshot(self):
        candidate = {"source": {"provider": "tmdb", "entity_type": "person", "external_id": "200"},
                     "source_refs": [{"provider": "tmdb", "entity_type": "person", "external_id": "200"},
                                     {"provider": "wikipedia", "entity_type": "person", "external_id": "77"}]}
        people_import._stamp_sources(candidate, "e" * 64)
        self.assertEqual(candidate["source_refs"][0]["snapshot_id"], "e" * 64)
        self.assertNotIn("snapshot_id", candidate["source_refs"][1])


if __name__ == "__main__":
    unittest.main()
