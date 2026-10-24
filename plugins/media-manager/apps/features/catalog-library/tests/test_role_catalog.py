from copy import deepcopy
import unittest
from unittest.mock import patch

from catalog_library import people, role_catalog, service
from people_catalog.model import source_identity, validate_entity, validate_people_refs
from work_catalog_yaml.yaml_io import dump_yaml_string
import test_people as fixture


class RoleCatalogTest(unittest.TestCase):
    ref = fixture.CatalogPeopleTest.ref
    current = fixture.CatalogPeopleTest.current
    save_entity = fixture.CatalogPeopleTest.save_entity

    def setUp(self):
        fixture.CatalogPeopleTest.setUp(self)
        self.actor_id = source_identity("tmdb", "person", "1")
        self.actor = {"id": self.actor_id, "schema_version": 1, "type": "person", "kind": "individual", "name": "演员",
                      "avatar": {"asset_id": "a" * 64 + ".jpg", "source_url": "https://image.tmdb.org/t/p/original/a.jpg"},
                      "source_refs": [{"provider": "tmdb", "entity_type": "person", "external_id": "1"}]}
        self.save_entity(self.actor)
        self.record["source_refs"] = [{"provider": "tmdb", "external_id": "100", "season_number": 1, "scope": "season:1"}]
        self.record["people_refs"] = [{"id": self.actor_id, "type": "person", "role": "演员", "character_name": "김도기",
            "character_name_cn": "金道奇", "credit_id": "credit-1", "source": {"provider": "tmdb", "subject_id": "100", "season_number": 1}}]
        self.write()

    def write(self):
        self.source.write_text(dump_yaml_string([self.record]), encoding="utf-8")
        with service._CACHE_LOCK:
            service._CACHE.clear()

    def preview(self):
        return role_catalog.preview({"ref": self.ref()}, settings=self.settings, people_root=self.people_root)

    def apply(self, preview, identities=None):
        return role_catalog.apply({"ref": preview["ref"], "preview_token": preview["preview_token"],
            "character_ids": identities if identities is not None else [row["id"] for row in preview["entries"]]},
            settings=self.settings, people_root=self.people_root, data_root=self.library)

    def test_materialize_creates_character_without_actor_portrait_and_preserves_text(self):
        evidence = {(self.actor_id, "김도기", "金道奇"): {"name": {"ko": "김도기", "zh-Hans": "金道奇"}}}
        updated, entities, changes = role_catalog.materialize(self.record, [], self.repo, _name_evidence=evidence)
        role = entities[0]
        self.assertEqual(role["type"], "character")
        self.assertEqual(role["kind"], "character")
        self.assertEqual(role["name"], "김도기")
        self.assertEqual(role["localized"]["name"]["ko"], "김도기")
        self.assertEqual(role["localized"]["name"]["zh-Hans"], "金道奇")
        self.assertEqual(role["avatar"], {})
        self.assertNotEqual(role["id"], self.actor_id)
        self.assertEqual(updated["people_refs"][0]["character_id"], role["id"])
        self.assertEqual(updated["people_refs"][0]["character_name"], "김도기")
        self.assertEqual(role["role_origins"][0]["actor_id"], self.actor_id)
        self.assertFalse(changes[0]["selected"])
        self.assertIsNone(self.repo.read(role["id"]))

    def test_role_keeps_proven_chinese_variants_and_never_guesses_legacy_chinese(self):
        ref = self.record["people_refs"][0]
        ref["character_name_cn"] = "金部長"
        evidence = {(self.actor_id, "김도기", "金部長"): {
            "name": {"ko": "김도기", "zh-Hans": "金部长", "zh-Hant": "金部長"}}}
        _record, entities, _changes = role_catalog.materialize(self.record, [], self.repo, _name_evidence=evidence)
        self.assertEqual(entities[0]["localized"]["name"], evidence[next(iter(evidence))]["name"])
        _record, legacy, _changes = role_catalog.materialize(self.record, [], self.repo)
        self.assertNotIn("zh-Hans", legacy[0]["localized"]["name"])
        self.assertEqual(legacy[0]["localized"]["name"]["und"], "金部長")

    def test_refresh_translation_and_legacy_new_work_id_do_not_duplicate(self):
        updated, entities, _ = role_catalog.materialize(self.record, [], self.repo)
        original = entities[0]
        self.save_entity(original)
        updated["id"] = "work_later_assigned"
        updated["people_refs"][0].update(character_name="Kim Do-gi", character_name_cn="金道基")
        next_record, next_entities, _ = role_catalog.materialize(updated, [], self.repo)
        self.assertEqual(next_record["people_refs"][0]["character_id"], original["id"])
        self.assertEqual(next_entities[0]["source_refs"], original["source_refs"])
        self.assertEqual(len(next_entities[0]["role_origins"]), 1)

    def test_no_credit_ref_reuse_survives_original_name_change(self):
        self.record["people_refs"][0].pop("credit_id")
        updated, entities, _ = role_catalog.materialize(self.record, [], self.repo)
        self.save_entity(entities[0])
        updated["people_refs"][0]["character_name"] = "Different translation"
        _record, values, _ = role_catalog.materialize(updated, [], self.repo)
        self.assertEqual(values[0]["id"], entities[0]["id"])
        self.assertEqual(len(values), 1)
        self.assertEqual(len(values[0]["source_refs"]), 1)

    def test_same_name_different_work_or_credit_never_auto_merges(self):
        first, entities, _ = role_catalog.materialize(self.record, [], self.repo)
        self.save_entity(entities[0])
        other = deepcopy(self.record)
        other["people_refs"][0]["source"]["subject_id"] = "101"
        _updated, others, _ = role_catalog.materialize(other, [], self.repo)
        self.assertNotEqual(entities[0]["id"], others[0]["id"])
        other = deepcopy(self.record)
        other["people_refs"][0]["credit_id"] = "credit-2"
        self.assertNotEqual(entities[0]["id"], role_catalog.materialize(other, [], self.repo)[1][0]["id"])

    def test_episode_subset_does_not_split_source_role_identity(self):
        _record, first, _ = role_catalog.materialize(self.record, [], self.repo)
        self.record["people_refs"][0]["source"]["episode_range"] = {"start": 1, "end": 2}
        self.assertEqual(first[0]["id"], role_catalog.materialize(self.record, [], self.repo)[1][0]["id"])

    def test_duplicate_credit_id_for_different_actors_cannot_merge_roles(self):
        actor = deepcopy(self.actor)
        actor["id"] = source_identity("tmdb", "person", "2")
        actor["source_refs"][0]["external_id"] = "2"
        self.save_entity(actor)
        ref = deepcopy(self.record["people_refs"][0])
        ref["id"] = actor["id"]
        self.record["people_refs"].append(ref)
        record, entities, _ = role_catalog.materialize(self.record, [], self.repo)
        self.assertEqual(len(entities), 2)
        self.assertNotEqual(record["people_refs"][0]["character_id"], record["people_refs"][1]["character_id"])

    def test_manual_role_values_survive_refresh(self):
        updated, entities, _ = role_catalog.materialize(self.record, [], self.repo)
        manual = entities[0]
        manual.update(name="手工角色", name_cn="手工中文", summary="人工介绍")
        self.save_entity(manual)
        updated["people_refs"][0].update(character_name="Source update", character_name_cn="来源中文")
        _updated, entities, _ = role_catalog.materialize(updated, [], self.repo)
        self.assertEqual((entities[0]["name"], entities[0]["name_cn"], entities[0]["summary"]), ("手工角色", "手工中文", "人工介绍"))

    def test_role_materialization_rejects_organization_and_unknown_actor_types(self):
        before = self.source.read_bytes()
        for value in ({"kind": "corporation"}, {"kind": "association"}, {"kind": ""},
                      {"taxonomy": {"category": "organization", "reality": "real", "subtype": "studio"}},
                      {"taxonomy": {"category": "person", "reality": "fictional", "subtype": "character"}}):
            actor = {**self.actor, **value}
            self.save_entity(actor)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "现实人物"):
                role_catalog.materialize(self.record, [], self.repo)
            self.assertEqual(self.source.read_bytes(), before)
        self.save_entity(self.actor)

    def test_existing_role_must_not_be_a_company_mechanic_or_unclassified_entity(self):
        updated, entities, _ = role_catalog.materialize(self.record, [], self.repo)
        original = entities[0]
        for changes in ({"kind": "organization"}, {"kind": "mechanic"}, {"kind": ""},
                        {"taxonomy": {"category": "organization", "reality": "fictional", "subtype": "organization"}}):
            role = {**original, **changes}
            self.save_entity(role)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                role_catalog.materialize(updated, [], self.repo)
        manual = {**original, "kind": "organization", "taxonomy": {"category": "person", "reality": "fictional", "subtype": "character"}}
        self.save_entity(manual)
        _record, refreshed, _ = role_catalog.materialize(updated, [], self.repo)
        self.assertEqual(refreshed[0]["taxonomy"], manual["taxonomy"])

    def test_work_local_role_override_and_clear_do_not_pollute_shared_character(self):
        from people_catalog.localization import set_field
        ref = self.record["people_refs"][0]
        ref["source_values"] = {"character_name": ref["character_name"], "character_name_cn": ref["character_name_cn"]}
        updated, entities, _ = role_catalog.materialize(self.record, [], self.repo)
        self.save_entity(entities[0])
        role_id = entities[0]["id"]
        unchanged_work = deepcopy(updated)
        updated["people_refs"][0].update(character_name="本作品手工角色", character_name_cn="")
        updated, entities, _ = role_catalog.materialize(updated, [], self.repo)
        self.assertEqual((entities[0]["name"], entities[0]["name_cn"]), ("김도기", "金道奇"))
        cast = people.project(updated, people_root=self.people_root, entities=entities)["cast"][0]
        self.assertEqual((cast["character_name"], cast["character_name_cn"]), ("本作品手工角色", ""))
        self.assertEqual(cast["character_name_overrides"], ["character_name", "character_name_cn"])
        # Editing the shared entity still updates work refs that were not
        # explicitly overridden; the edited work keeps its own override.
        set_field(entities[0], "localized.name.ko", "수동 역할")
        set_field(entities[0], "localized.name.zh-Hans", "全局中文名")
        for record, expected in ((updated, ("本作品手工角色", "")), (unchanged_work, ("수동 역할", "全局中文名"))):
            before = deepcopy(record)
            cast = people.project(record, people_root=self.people_root, entities=entities)["cast"][0]
            self.assertEqual((cast["character_name"], cast["character_name_cn"]), expected)
            self.assertEqual(cast["character"]["id"], role_id)
            self.assertEqual(cast["character"]["localized"]["name"]["ko"], "수동 역할")
            self.assertEqual(cast["character_name_overrides"], [] if record is unchanged_work else ["character_name", "character_name_cn"])
            self.assertEqual(record, before)

    def test_legacy_explicit_empty_role_translation_remains_empty_in_projection(self):
        updated, entities, _ = role_catalog.materialize(self.record, [], self.repo)
        updated["people_refs"][0]["character_name_cn"] = ""
        cast = people.project(updated, people_root=self.people_root, entities=entities)["cast"][0]
        self.assertEqual(cast["character_name_cn"], "")
        self.assertIn("character_name_cn", cast["character_name_overrides"])

    def test_retired_role_source_resolves_target_with_and_without_old_ref(self):
        updated, entities, _ = role_catalog.materialize(self.record, [], self.repo)
        donor = entities[0]
        self.save_entity(donor)
        target = deepcopy(donor)
        target["id"] = source_identity("manual", "character", "target")
        target["name"] = "明确保留的角色"
        target.setdefault("source_values", {}).setdefault("tmdb", {})["name"] = target["name"]
        self.save_entity({**target, "source_refs": []})
        retired = {**donor, "merged_into": target["id"], "source_refs": []}
        self.repo.apply_batch([target, retired], expected_revision=self.repo.revision([target["id"], donor["id"]]))
        for record in (updated, self.record):
            result, values, _ = role_catalog.materialize(record, [], self.repo)
            self.assertEqual(result["people_refs"][0]["character_id"], target["id"])
            self.assertEqual(values[0]["id"], target["id"])
            self.assertEqual(values[0]["name"], "明确保留的角色")
        self.assertEqual(self.repo.read(donor["id"]), retired)

    def test_explicit_reference_to_person_or_missing_character_rejected(self):
        for identity in (self.actor_id, source_identity("manual", "character", "missing")):
            self.record["people_refs"][0]["character_id"] = identity
            with self.assertRaises(ValueError):
                role_catalog.materialize(self.record, [], self.repo)

    def test_preview_read_only_and_apply_requires_explicit_selection(self):
        before = self.source.read_bytes()
        preview = self.preview()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 1)
        self.assertFalse((self.people_root / "sources").exists())
        with self.assertRaises(ValueError):
            self.apply(preview, [])
        result = self.apply(preview)
        self.assertTrue(result["ok"])
        self.assertEqual(self.current()["people_refs"][0]["character_id"], preview["entries"][0]["id"])
        self.assertEqual(self.repo.read(self.actor_id), self.actor)
        self.assertNotEqual(result["records"][0]["ref"], preview["ref"])

    def test_partial_choice_does_not_create_or_link_unselected_role(self):
        second = deepcopy(self.record["people_refs"][0])
        second.update(character_name="第二角色", credit_id="credit-2")
        self.record["people_refs"].append(second)
        self.write()
        preview = self.preview()
        self.apply(preview, [preview["entries"][0]["id"]])
        self.assertIn("character_id", self.current()["people_refs"][0])
        self.assertNotIn("character_id", self.current()["people_refs"][1])
        self.assertIsNone(self.repo.read(preview["entries"][1]["id"]))

    def test_any_concurrent_owner_inventory_change_blocks_apply(self):
        preview = self.preview()
        before = self.source.read_bytes()
        other = {"id": source_identity("manual", "character", "concurrent"), "schema_version": 1, "type": "character", "name": "并发人物"}
        self.save_entity(other)
        with self.assertRaises(ValueError):
            self.apply(preview)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertIsNone(self.repo.read(preview["entries"][0]["id"]))

    def test_work_failure_rolls_back_new_roles_and_preserves_actor_bytes(self):
        preview = self.preview()
        before = self.repo._bytes(self.actor_id)
        with patch.object(service, "apply_edits", side_effect=ValueError("stale work")), self.assertRaises(ValueError):
            self.apply(preview)
        self.assertEqual(self.repo._bytes(self.actor_id), before)
        self.assertIsNone(self.repo.read(preview["entries"][0]["id"]))

    def test_changed_selected_entity_after_plan_is_not_overwritten(self):
        self.apply(self.preview())
        identity = self.current()["people_refs"][0]["character_id"]
        preview = self.preview()
        original_plan = role_catalog._plan
        concurrent = self.repo.read(identity)
        concurrent["summary"] = "外部编辑，必须保留"

        def concurrent_plan(*args):
            result = original_plan(*args)
            # Deliberately emulate an external non-cooperating editor between
            # planning and the repository's locked compare-and-swap.
            self.repo.path(identity).write_text(dump_yaml_string(concurrent), encoding="utf-8")
            return result

        with patch.object(role_catalog, "_plan", side_effect=concurrent_plan), self.assertRaises(ValueError):
            self.apply(preview)
        self.assertEqual(self.repo.read(identity)["summary"], concurrent["summary"])

    def test_bounded_model_and_role_reference_constraints(self):
        role = role_catalog.materialize(self.record, [], self.repo)[1][0]
        self.assertEqual(validate_entity(role), role)
        bad = deepcopy(role)
        bad["role_origins"][0]["actor_id"] = "../unsafe"
        with self.assertRaises(ValueError):
            validate_entity(bad)
        bad = deepcopy(role)
        bad["type"] = "person"
        with self.assertRaises(ValueError):
            validate_entity(bad)
        with self.assertRaises(ValueError):
            validate_people_refs([{ "id": role["id"], "type": "character", "character_id": self.actor_id}])


if __name__ == "__main__":
    unittest.main()
