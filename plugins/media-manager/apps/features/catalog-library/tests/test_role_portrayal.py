"""Explicit source portrayal groups; never merge roles by stripped names."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from catalog_library import character_merge, people, people_import, role_catalog
from people_catalog import portrayal
from people_catalog.model import source_identity, validate_entity, validate_people_refs
from people_catalog.repository import PeopleRepository
from test_people_import import default_choices
import test_character_merge as merge_fixture
import test_role_catalog as fixture
import test_tmdb_people as tmdb_fixture


def age(value):
    return {"kind": "age", "value": value, "label": value, "language": "en", "explicit": True}


class PortrayalModelTest(unittest.TestCase):
    def test_exact_stage_validation_and_suffix_only_detection(self):
        for value in ("child", "teen", "young", "adult", "elder"):
            self.assertEqual(portrayal.validate_qualifier(age(value)), age(value))
        for value in ({}, {**age("adult"), "explicit": False}, age("unknown"), {**age("adult"), "extra": True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                portrayal.validate_qualifier(value)
        self.assertIsNone(portrayal.parse_qualifier("Young Kim"))
        self.assertEqual(portrayal.parse_qualifier("金部长（少年）")["value"], "teen")
        self.assertEqual(portrayal.parse_qualifier("Kim (child)")["value"], "child")
        self.assertEqual(portrayal.base_name("Kim (child)", age("child")), "Kim")
        self.assertEqual(portrayal.base_name("Kim (child)", age("adult")), "Kim (child)")

    def test_stage_compatibility_does_not_guess_child_vs_teen_or_default_adult(self):
        self.assertTrue(portrayal.compatible_stage({"qualifier": age("young")}, {"qualifier": age("child")}))
        self.assertTrue(portrayal.compatible_stage({"qualifier": age("young")}, {"qualifier": age("teen")}))
        self.assertFalse(portrayal.compatible_stage({"qualifier": age("child")}, {"qualifier": age("teen")}))
        self.assertFalse(portrayal.compatible_stage({}, {"qualifier": age("adult")}))
        for value in ("", "bad group", "x" * 201, "../escape"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                portrayal.validate_role_group(value)


class RolePortrayalTest(unittest.TestCase):
    setUp = fixture.RoleCatalogTest.setUp
    write = fixture.RoleCatalogTest.write
    save_entity = fixture.RoleCatalogTest.save_entity

    def portrayals(self, *, grouped=True):
        second = deepcopy(self.actor)
        second.update(id=source_identity("tmdb", "person", "2"), name="Child Actor")
        second["source_refs"][0]["external_id"] = "2"
        self.save_entity(second)
        record = deepcopy(self.record)
        record["id"] = "work_age_stages"
        adult = record["people_refs"][0]
        adult.update(character_name="Hero", character_name_cn="英雄", qualifier=age("adult"))
        child = {**deepcopy(adult), "id": second["id"], "character_name": "Hero (child)",
                 "character_name_cn": "英雄（童年）", "credit_id": "child-credit", "qualifier": age("child")}
        if grouped:
            adult["role_group"] = child["role_group"] = "wiki-cast:explicit-row-1"
        record["people_refs"] = [child, adult]
        return record, [deepcopy(self.actor), second]

    def normalize(self, record, actors, *, target=None):
        target = deepcopy(target) if target is not None else {**deepcopy(record), "people_refs": []}

        def build(_snapshot, _snapshot_id, seed, repository, **_kwargs):
            seed["people_refs"] = deepcopy(record["people_refs"])
            return role_catalog.materialize(seed, actors, repository, _kwargs.get("warnings"))

        with patch.object(people, "_build", side_effect=build), \
             patch.object(PeopleRepository, "apply_batch", side_effect=AssertionError("preview must not write")):
            snapshot = people_import.normalize({"provider": "tmdb", "subject_id": 100}, target, self.repo)
            preview, _ = people_import.preview(snapshot, "snapshot", target, self.repo)
        return target, snapshot, preview

    def test_explicit_group_creates_one_card_with_each_actor_and_stage(self):
        record, _actors = self.portrayals()
        updated, entities, _ = role_catalog.materialize(record, [], self.repo)
        self.assertEqual(len(entities), 1)
        role = entities[0]
        self.assertEqual(role["name"], "Hero")
        self.assertEqual(role["name_cn"], "英雄")
        self.assertEqual({row["character_id"] for row in updated["people_refs"]}, {role["id"]})
        self.assertEqual({row["qualifier"]["value"] for row in role["role_origins"]}, {"adult", "child"})
        self.assertEqual(len({row["actor_id"] for row in role["role_origins"]}), 2)
        self.assertEqual(len(role["source_refs"]), 2)
        self.assertEqual(validate_entity(role), role)
        self.assertEqual(validate_people_refs(updated["people_refs"]), updated["people_refs"])

    def test_similar_age_names_without_explicit_source_group_remain_separate(self):
        record, _ = self.portrayals(grouped=False)
        updated, entities, _ = role_catalog.materialize(record, [], self.repo)
        self.assertEqual(len(entities), 2)
        self.assertEqual(len({row["character_id"] for row in updated["people_refs"]}), 2)

    def test_unqualified_group_main_actor_is_not_assumed_adult(self):
        record, _ = self.portrayals()
        record["people_refs"][1].pop("qualifier")
        record["people_refs"][1]["character_name"] = "Main Hero"
        updated, entities, _ = role_catalog.materialize(record, [], self.repo)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0]["name"], "Main Hero")
        self.assertNotIn("qualifier", updated["people_refs"][1])
        main_origin = next(row for row in entities[0]["role_origins"] if row["actor_id"] == self.actor_id)
        self.assertNotIn("qualifier", main_origin)
        self.assertEqual({row["character_id"] for row in updated["people_refs"]}, {entities[0]["id"]})

    def test_group_is_isolated_by_work_and_season(self):
        record, _ = self.portrayals()
        _, entities, _ = role_catalog.materialize(record, [], self.repo)
        self.save_entity(entities[0])
        for change in ("work", "season"):
            with self.subTest(change=change):
                other = deepcopy(record)
                if change == "work":
                    other["id"] = "work_another"
                else:
                    for ref in other["people_refs"]:
                        ref["source"]["season_number"] = 2
                _, proposals, _ = role_catalog.materialize(other, [], self.repo)
                self.assertEqual(len(proposals), 1)
                self.assertNotEqual(proposals[0]["id"], entities[0]["id"])

    def test_existing_two_role_cards_are_not_merged_by_later_group_evidence(self):
        record, actors = self.portrayals(grouped=False)
        _, initial, _ = role_catalog.materialize(record, [], self.repo)
        for entity in initial:
            self.save_entity(entity)
        for ref in record["people_refs"]:
            ref["role_group"] = "wiki-cast:explicit-row-1"
        warnings = []
        updated, proposed, _ = role_catalog.materialize(record, [], self.repo, warnings)
        self.assertEqual({row["id"] for row in proposed}, {row["id"] for row in initial})
        self.assertEqual(len({row["character_id"] for row in updated["people_refs"]}), 2)
        self.assertTrue(any("不会自动合并" in warning for warning in warnings))
        _target, snapshot, preview = self.normalize(record, actors)
        roles = [row for row in preview["entries"] if row["entity_type"] == "character"]
        self.assertEqual(len(roles), 2)
        self.assertEqual({row["target_id"] for row in roles}, {row["id"] for row in initial})
        self.assertTrue(any("不会自动合并" in warning for warning in snapshot["warnings"]))

    def test_group_normalization_and_default_compose_keep_both_portrayals(self):
        record, actors = self.portrayals()
        target, snapshot, preview = self.normalize(record, actors)
        roles = [row for row in preview["entries"] if row["entity_type"] == "character"]
        self.assertEqual(len(roles), 1)
        updated, pending, _ = people_import.compose(snapshot, default_choices(preview)["snapshot"], target, self.repo)
        refs = updated["people_refs"]
        self.assertEqual(len(refs), 2)
        self.assertEqual(len({row["character_id"] for row in refs}), 1)
        role = pending[refs[0]["character_id"]]
        self.assertEqual({row["qualifier"]["value"] for row in refs}, {"adult", "child"})
        self.assertEqual({row["qualifier"]["value"] for row in role["role_origins"]}, {"adult", "child"})

    def test_legacy_two_cards_are_not_joined_when_work_gains_uuid_and_group(self):
        record, actors = self.portrayals(grouped=False)
        legacy = {key: value for key, value in record.items() if key != "id"}
        _, initial, _ = role_catalog.materialize(legacy, [], self.repo)
        for entity in initial:
            self.save_entity(entity)
        for ref in record["people_refs"]:
            ref["role_group"] = "wiki-cast:new-age-evidence"
        _target, _snapshot, preview = self.normalize(record, actors)
        roles = [row for row in preview["entries"] if row["entity_type"] == "character"]
        self.assertEqual(len(roles), 2)
        self.assertEqual({row["target_id"] for row in roles}, {row["id"] for row in initial})

    def test_one_existing_role_is_extended_without_losing_either_origin(self):
        record, actors = self.portrayals()
        seed = {**deepcopy(record), "people_refs": [deepcopy(record["people_refs"][1])]}
        _, initial, _ = role_catalog.materialize(seed, [], self.repo)
        self.save_entity(initial[0])
        target, snapshot, preview = self.normalize(record, actors)
        roles = [row for row in preview["entries"] if row["entity_type"] == "character"]
        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0]["target_id"], initial[0]["id"])
        updated, pending, _ = people_import.compose(snapshot, default_choices(preview)["snapshot"], target, self.repo)
        self.assertEqual({row["character_id"] for row in updated["people_refs"]}, {initial[0]["id"]})
        self.assertEqual(len(pending[initial[0]["id"]]["role_origins"]), 2)

    def test_independent_wiki_import_keeps_shared_stage_metadata(self):
        snapshot = {"provider": "wikipedia", "subject_id": 99, "raw": {
            "profiles": [{"external_id": value, "name": name, "kind": "individual",
                          "source_url": "https://zh.wikipedia.org/wiki/" + name, "fetched_at": "2026-10-11T00:00:00+00:00"}
                         for value, name in (("77", "成年演员"), ("78", "童年演员"))],
            "relations": [{"external_id": value, "role": "演员", "character_name": "영웅", "character_name_cn": "英雄",
                           "qualifier": age(stage), "role_group": "wiki-cast:explicit-row", "localized": {"name": {"ko": "영웅", "zh-Hans": "英雄"}}}
                          for value, stage in (("77", "adult"), ("78", "child"))]}}
        target = {"id": "work_wiki_stage", "people_refs": []}
        people_import.normalize(snapshot, target, self.repo)
        preview, _ = people_import.preview(snapshot, "snapshot", target, self.repo)
        updated, pending, _ = people_import.compose(snapshot, default_choices(preview)["snapshot"], target, self.repo)
        refs = updated["people_refs"]
        self.assertEqual(len(refs), 2)
        self.assertEqual(len({row["character_id"] for row in refs}), 1)
        self.assertEqual({row["qualifier"]["value"] for row in refs}, {"adult", "child"})
        self.assertEqual(len(pending[refs[0]["character_id"]]["role_origins"]), 2)

    def test_existing_origin_gains_stage_once_not_duplicate(self):
        record, actors = self.portrayals()
        record["people_refs"] = [record["people_refs"][1]]
        seed = deepcopy(record)
        seed["people_refs"][0].pop("qualifier")
        seed["people_refs"][0].pop("role_group")
        _, initial, _ = role_catalog.materialize(seed, [], self.repo)
        self.save_entity(initial[0])
        target, snapshot, preview = self.normalize(record, actors)
        _, pending, _ = people_import.compose(snapshot, default_choices(preview)["snapshot"], target, self.repo)
        origins = pending[initial[0]["id"]]["role_origins"]
        self.assertEqual(len(origins), 1)
        self.assertEqual(origins[0]["qualifier"]["value"], "adult")

    def test_different_explicit_stage_is_unselected_until_confirmed_then_both_preserved(self):
        record, actors = self.portrayals()
        record["people_refs"] = [record["people_refs"][1]]
        seed = deepcopy(record)
        seed["people_refs"][0]["qualifier"] = age("child")
        _, initial, _ = role_catalog.materialize(seed, [], self.repo)
        self.save_entity(initial[0])
        target, snapshot, preview = self.normalize(record, actors)
        actor = next(row for row in preview["entries"] if row["candidate"]["id"] == self.actor_id)
        relation = actor["relations"][0]
        self.assertEqual(relation["work_relation_status"], "conflict")
        self.assertEqual(relation["stage_conflict"]["after"]["value"], "adult")
        self.assertIn("不覆盖原阶段", relation["reason"])
        self.assertNotIn(relation["id"], actor["selected_relations"])
        choices = default_choices(preview)["snapshot"]
        selected = next(row for row in choices if row["external_id"] == actor["external_id"])
        selected["relations"] = [relation["id"]]
        updated, pending, _ = people_import.compose(snapshot, choices, target, self.repo)
        self.assertEqual(updated["people_refs"][0]["qualifier"]["value"], "adult")
        self.assertEqual({origin["qualifier"]["value"] for origin in pending[initial[0]["id"]]["role_origins"]}, {"child", "adult"})

    def test_existing_work_relation_stage_conflict_retains_old_ref_on_confirmed_add(self):
        record, actors = self.portrayals()
        record["people_refs"] = [record["people_refs"][1]]
        seed = deepcopy(record)
        seed["people_refs"][0]["qualifier"] = age("child")
        old_work, initial, _ = role_catalog.materialize(seed, [], self.repo)
        self.save_entity(initial[0])
        target, snapshot, preview = self.normalize(record, actors, target=old_work)
        actor = next(row for row in preview["entries"] if row["candidate"]["id"] == self.actor_id)
        relation = actor["relations"][0]
        self.assertEqual(relation["work_relation_status"], "conflict")
        self.assertEqual(actor["selected_relations"], [])
        choices = default_choices(preview)["snapshot"]
        next(row for row in choices if row["external_id"] == actor["external_id"])["relations"] = [relation["id"]]
        updated, pending, _ = people_import.compose(snapshot, choices, target, self.repo)
        self.assertEqual({ref["qualifier"]["value"] for ref in updated["people_refs"]}, {"child", "adult"})
        self.assertEqual({origin["qualifier"]["value"] for origin in pending[initial[0]["id"]]["role_origins"]}, {"child", "adult"})

    def test_same_actor_and_credit_with_two_explicit_stages_is_not_collapsed(self):
        record, _ = self.portrayals()
        adult = record["people_refs"][1]
        record["people_refs"][0] = {**deepcopy(adult), "qualifier": age("child")}
        actors = [self.actor]
        target, snapshot, preview = self.normalize(record, actors)
        updated, pending, _ = people_import.compose(snapshot, default_choices(preview)["snapshot"], target, self.repo)
        roles = [value for value in pending.values() if value["type"] == "character"]
        self.assertEqual(len(roles), 1)
        self.assertEqual({ref["qualifier"]["value"] for ref in updated["people_refs"]}, {"child", "adult"})
        self.assertEqual({origin["qualifier"]["value"] for origin in roles[0]["role_origins"]}, {"child", "adult"})
        for entity in pending.values():
            self.save_entity(entity)
        _, _, repeated = self.normalize(record, actors, target=updated)
        actor = next(row for row in repeated["entries"] if row["candidate"]["id"] == self.actor_id)
        self.assertTrue(all(relation["work_relation_status"] == "existing" for relation in actor["relations"]))
        self.assertFalse(any(relation.get("stage_conflict") for relation in actor["relations"]))


class ManualAgeRoleMergeTest(unittest.TestCase):
    setUp = merge_fixture.CharacterMergeTest.setUp
    save_entity = merge_fixture.CharacterMergeTest.save_entity
    write_works = merge_fixture.CharacterMergeTest.write_works
    preview = merge_fixture.CharacterMergeTest.preview
    apply = merge_fixture.CharacterMergeTest.apply

    def test_manual_merge_keeps_each_actor_and_phase_in_work_and_provenance(self):
        self.donor["role_origins"][0]["qualifier"] = age("child")
        self.donor["role_origins"][0]["role_group"] = "wiki-cast:row"
        self.target["role_origins"] = [{**deepcopy(self.donor["role_origins"][0]), "actor_id": self.cv_id,
            "external_id": "role:" + "b" * 64, "qualifier": age("adult")}]
        self.target["source_refs"].append({"provider": "tmdb", "entity_type": "character", "external_id": "role:" + "b" * 64})
        self.record["people_refs"][1].update(qualifier=age("child"), role_group="wiki-cast:row")
        self.record["people_refs"].append({**deepcopy(self.actor_ref), "id": self.cv_id,
            "character_id": self.target_id, "qualifier": age("adult"), "credit_id": "adult-credit"})
        for person in (self.donor, self.target):
            self.save_entity(person)
        self.write_works()
        preview = self.preview()
        self.assertEqual(preview["donor"]["portrayals"][0]["qualifier"], age("child"))
        self.apply(preview)
        origins = self.repo.read(self.target_id)["role_origins"]
        self.assertEqual({(row["actor_id"], row["qualifier"]["value"]) for row in origins},
                         {(self.actor_id, "child"), (self.cv_id, "adult")})
        from work_catalog_yaml.yaml_io import load_yaml_string
        refs = load_yaml_string(self.source.read_text(encoding="utf-8"))[0]["people_refs"]
        self.assertEqual({row["qualifier"]["value"] for row in refs if row.get("character_id")}, {"adult", "child"})
        self.assertEqual({row["character_id"] for row in refs if row.get("character_id")}, {self.target_id})


class TmdbPortrayalPipelineTest(unittest.TestCase):
    setUp = tmdb_fixture.TmdbPeopleTest.setUp
    write_record = tmdb_fixture.TmdbPeopleTest.write_record
    request = tmdb_fixture.TmdbPeopleTest.request

    def test_explicit_wiki_age_group_survives_real_tmdb_builder_and_import(self):
        from catalog_library import tmdb_people
        self.credits["crew"] = []
        self.credits["cast"] = [
            {"id": 1, "name": "成年演员", "original_name": "Adult Actor", "roles": [{"character": "Hero", "credit_id": "adult-credit"}]},
            {"id": 2, "name": "童年演员", "original_name": "Child Actor", "roles": [{"character": "Hero (young)", "credit_id": "child-credit"}]},
        ]
        snapshot = tmdb_people.fetch_snapshot(100, season_number=1, original_language="ko", _requester=self.request)
        snapshot["raw"]["wikipedia_people"] = {"people": {}, "relations": [
            {"person_id": 1, "original_value": "Hero", "character_name": "영웅", "character_name_cn": "英雄",
             "qualifier": age("adult"), "role_group": "wiki-cast:table-row", "localized": {"name": {"ko": "영웅", "zh-Hans": "英雄"}}},
            {"person_id": 2, "original_value": "Hero (young)", "character_name": "영웅", "character_name_cn": "英雄（童年）",
             "qualifier": age("child"), "role_group": "wiki-cast:table-row", "localized": {"name": {"ko": "영웅", "zh-Hans": "英雄"}}},
        ]}
        target = {**deepcopy(self.record), "id": "work_tmdb_age", "people_refs": []}
        with patch("socket.socket.connect", side_effect=AssertionError("normalization stays offline")), \
             patch.object(PeopleRepository, "apply_batch", side_effect=AssertionError("no real or fixture writes")):
            people_import.normalize(snapshot, target, self.repo)
            preview, _ = people_import.preview(snapshot, "snapshot", target, self.repo)
            updated, pending, _ = people_import.compose(snapshot, default_choices(preview)["snapshot"], target, self.repo)
        roles = [row for row in pending.values() if row["type"] == "character"]
        self.assertEqual(len(roles), 1)
        self.assertEqual(len(updated["people_refs"]), 2)
        self.assertEqual({row["qualifier"]["value"] for row in updated["people_refs"]}, {"adult", "child"})
        self.assertEqual({row["character_id"] for row in updated["people_refs"]}, {roles[0]["id"]})
        self.assertEqual(len(roles[0]["role_origins"]), 2)


if __name__ == "__main__":
    unittest.main()
