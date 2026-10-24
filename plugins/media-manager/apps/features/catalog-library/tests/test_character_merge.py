"""Character merge exercises synthetic temporary repositories only."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from catalog_library import assets, character_merge as merge, people_assets, providers, service
from people_catalog.model import source_identity
from people_catalog import localization
from people_catalog.repository import PeopleRepository
from people_catalog.source_links import build_source_index
from work_catalog_yaml.persistence import PersistenceRollbackError
from work_catalog_yaml.yaml_io import dump_yaml_string, load_yaml_string
import test_people as fixtures
from test_people_assets import image_bytes


class CharacterMergeTest(unittest.TestCase):
    ref = fixtures.CatalogPeopleTest.ref
    current = fixtures.CatalogPeopleTest.current
    save_entity = fixtures.CatalogPeopleTest.save_entity

    def setUp(self):
        fixtures.CatalogPeopleTest.setUp(self)
        self.target_id = source_identity("bangumi", "character", "101")
        self.donor_id = source_identity("tmdb", "character", "role:" + "a" * 64)
        self.actor_id = source_identity("tmdb", "person", "7")
        self.cv_id = source_identity("bangumi", "person", "8")
        self.scope = {"provider": "tmdb", "subject_id": "100", "season_number": 1}
        self.actor = {"schema_version": 1, "id": self.actor_id, "type": "person", "name": "Actor"}
        self.cv = {"schema_version": 1, "id": self.cv_id, "type": "person", "name": "Voice Actor"}
        for person in (self.actor, self.cv):
            self.save_entity(person)
        self.target = {"schema_version": 1, "id": self.target_id, "type": "character", "kind": "character",
            "name": "Hero", "name_cn": "英雄", "aliases": ["Local Hero"], "summary": "手动简介", "avatar": {},
            "gender": "男性", "birth_date": {"year": 1990}, "infobox": [{"key": "武器", "value": "剑"}],
            "source_refs": [{"provider": "bangumi", "entity_type": "character", "external_id": "101"}],
            "voice_actors": [{"person_id": self.cv_id, "scope": {"provider": "bangumi", "subject_id": "33"}, "role": "CV"}]}
        self.donor = {"schema_version": 1, "id": self.donor_id, "type": "character", "kind": "character",
            "name": "ＨＥＲＯ", "name_cn": "英雄新版", "aliases": ["演员版角色"], "summary": "演员版简介",
            "gender": "男性", "birth_date": {"month": 5}, "infobox": [{"key": "武器", "value": "剑"}, {"key": "年龄", "value": "25"}],
            "source_refs": [{"provider": "tmdb", "entity_type": "character", "external_id": "role:" + "a" * 64}],
            "role_origins": [{"work_key": "work:test", "actor_id": self.actor_id, "scope": self.scope,
                              "external_id": "role:" + "a" * 64, "original_name": "Hero", "credit_id": "credit-1"}],
            "voice_actors": [{"person_id": self.actor_id, "scope": self.scope, "role": "饰演"}]}
        self.save_entity(self.target)
        self.save_entity(self.donor)
        self.character_ref = {"id": self.donor_id, "type": "character", "role": "主角", "source": self.scope}
        self.actor_ref = {"id": self.actor_id, "type": "person", "role": "演员", "character_name": "Hero",
                          "character_name_cn": "英雄", "character_id": self.donor_id, "credit_id": "credit-1",
                          "source": self.scope, "source_values": {"character_name": "Hero"}}
        self.record["people_refs"] = [deepcopy(self.character_ref), deepcopy(self.actor_ref)]
        self.write_works()

    def write_works(self, records=None):
        self.source.write_text(dump_yaml_string(records or [self.record]), encoding="utf-8")
        with service._CACHE_LOCK:
            service._CACHE.clear()

    def preview(self, **changes):
        with patch("socket.socket.connect", side_effect=AssertionError("merge is offline")):
            return merge.preview({"person_id": self.donor_id, "target_id": self.target_id, **changes},
                                 settings=self.settings, people_root=self.people_root, data_root=self.library)

    def apply(self, preview, fields=None, **changes):
        with patch("socket.socket.connect", side_effect=AssertionError("merge is offline")):
            return merge.apply({"person_id": self.donor_id, "target_id": self.target_id,
                "preview_token": preview["preview_token"], "selected_fields": [] if fields is None else fields, **changes},
                settings=self.settings, people_root=self.people_root, data_root=self.library)

    def bytes(self):
        return self.source.read_bytes(), {p.name: p.read_bytes() for p in (self.people_root / "db").glob("*.yaml")}

    def test_candidates_only_suggest_matching_names_or_aliases_without_writing(self):
        wrong = source_identity("bangumi", "person", "101")
        self.save_entity({"schema_version": 1, "id": wrong, "type": "person", "name": "Hero"})
        alias_id = source_identity("manual", "character", "alias")
        self.save_entity({"schema_version": 1, "id": alias_id, "type": "character", "kind": "character", "name": "Other", "aliases": ["hero"]})
        before = self.bytes()
        result = merge.candidates({"person_id": self.donor_id}, people_root=self.people_root)
        self.assertEqual({row["id"] for row in result["candidates"]}, {self.target_id, alias_id})
        self.assertTrue(all(row["matched_names"] == ["hero"] for row in result["candidates"]))
        self.assertEqual(self.bytes(), before)

    def test_merge_candidates_exclude_organizations_objects_and_unclassified_entries(self):
        excluded = []
        for kind in ("organization", "mechanic", "ship", ""):
            identity = source_identity("manual", "character", "excluded:" + kind)
            excluded.append(identity)
            self.save_entity({"schema_version": 1, "id": identity, "type": "character", "kind": kind, "name": "Hero"})
        manual_id = source_identity("manual", "character", "manual-organization")
        self.save_entity({"schema_version": 1, "id": manual_id, "type": "character", "kind": "character", "name": "Hero",
                          "taxonomy": {"category": "organization", "reality": "fictional", "subtype": "organization"}})
        excluded.append(manual_id)
        before = self.bytes()
        result = merge.candidates({"person_id": self.donor_id}, people_root=self.people_root)
        self.assertEqual([row["id"] for row in result["candidates"]], [self.target_id])
        for identity in excluded:
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, "分类"):
                self.preview(target_id=identity)
            with self.subTest(donor=identity), self.assertRaisesRegex(ValueError, "分类"):
                merge.candidates({"person_id": identity}, people_root=self.people_root)
        self.assertEqual(self.bytes(), before)

    def test_merge_respects_manual_person_classification_and_preserves_target_taxonomy(self):
        self.target["taxonomy"] = {"category": "person", "reality": "fictional", "subtype": "character"}
        self.target["kind"] = "organization"
        self.save_entity(self.target)
        preview = self.preview()
        self.assertEqual(preview["target"]["taxonomy"], self.target["taxonomy"])
        self.apply(preview)
        self.assertEqual(self.repo.read(self.target_id)["taxonomy"], self.target["taxonomy"])
        self.assertEqual(self.repo.read(self.target_id)["kind"], "organization")

    def test_candidate_scan_reuses_content_cache_and_rechecks_bytes_without_reparsing(self):
        from catalog_library import people_library
        merge.candidates({"person_id": self.donor_id}, people_root=self.people_root)
        with patch.object(people_library, "load_yaml_string", side_effect=AssertionError("cached YAML must not reparse")):
            candidates = merge.candidates({"person_id": self.donor_id}, people_root=self.people_root)
            preview = self.preview()
        self.assertEqual(candidates["candidates"][0]["id"], self.target_id)
        self.assertEqual(preview["target_id"], self.target_id)
        self.donor.update(name="Another", name_cn="另一个", aliases=[])
        self.save_entity(self.donor)
        self.assertEqual(merge.candidates({"person_id": self.donor_id}, people_root=self.people_root)["candidates"], [])

    def test_cached_scan_retains_retired_records_and_detects_changes_during_scan(self):
        retired_id = source_identity("manual", "character", "cache-retired")
        self.save_entity({"schema_version": 1, "id": retired_id, "type": "character", "name": "Hero", "merged_into": self.donor_id, "source_refs": []})
        rows, _versions = merge._people_state(self.repo)
        self.assertIn(retired_id, rows)
        original = merge._people_versions

        def race(repository):
            self.save_entity({**self.target, "summary": "concurrent cached change"})
            return original(repository)

        with patch.object(merge, "_people_versions", side_effect=race), self.assertRaisesRegex(ValueError, "变化"):
            merge.candidates({"person_id": self.donor_id}, people_root=self.people_root)

    def test_preview_is_read_only_and_all_fields_default_to_keep_target(self):
        before = self.bytes()
        preview = self.preview()
        self.assertEqual(self.bytes(), before)
        self.assertEqual(preview["affected_work_count"], 1)
        self.assertEqual(preview["affected_works"][0]["relation_count"], 1)
        self.assertEqual(preview["affected_works"][0]["actor_relation_count"], 1)
        self.assertTrue(all(row["selected"] is False for row in preview["diff"]))
        self.assertEqual(preview["selected_fields"], [])

    def test_empty_field_list_only_transfers_identity_and_unions_relationships(self):
        result = self.apply(self.preview())
        target, donor = self.repo.read(self.target_id), self.repo.read(self.donor_id)
        for field in ("name", "name_cn", "summary", "aliases", "birth_date", "avatar", "infobox"):
            self.assertEqual(target[field], self.target[field])
        self.assertEqual(target["voice_actors"], self.target["voice_actors"] + self.donor["voice_actors"])
        self.assertEqual(target["role_origins"], self.donor["role_origins"])
        self.assertEqual(target["source_refs"], self.target["source_refs"] + self.donor["source_refs"])
        self.assertEqual(donor, {**self.donor, "merged_into": self.target_id, "source_refs": []})
        self.assertEqual(result["person"]["id"], self.target_id)
        self.assertEqual(build_source_index(self.repo).resolve("tmdb", "character", "role:" + "a" * 64), self.target_id)
        self.assertTrue(list((self.people_root / "history").glob("*.yaml")))

    def test_explicit_fields_merge_nonempty_data_and_keep_exact_provenance(self):
        self.apply(self.preview(), ["name", "summary", "aliases", "infobox", "birth_date"])
        target = self.repo.read(self.target_id)
        self.assertEqual(target["name"], self.donor["name"])
        self.assertEqual(target["summary"], self.donor["summary"])
        self.assertEqual(target["name_cn"], self.target["name_cn"])
        self.assertEqual(target["aliases"], self.target["aliases"] + self.donor["aliases"])
        self.assertEqual(target["infobox"], self.donor["infobox"])
        self.assertEqual(target["birth_date"], {"year": 1990, "month": 5})
        self.assertEqual(target["field_sources"]["summary"]["donor_id"], self.donor_id)
        self.assertEqual(target["source_values"]["character_merge"][self.donor_id]["summary"], self.donor["summary"])

    def test_all_works_retarget_character_and_actor_refs_preserving_scope_credit_and_media(self):
        other = deepcopy(self.record)
        other["attributes"][0]["data"] = "Second Work"
        other["people_refs"] = [{**self.actor_ref, "source": {**self.scope, "season_number": 2}, "eps": "3-7"}]
        untouched = deepcopy(self.record)
        untouched["attributes"][0]["data"] = "Third Work"
        untouched["people_refs"] = [{**self.character_ref, "id": self.target_id}]
        self.write_works([self.record, other, untouched])
        result = self.apply(self.preview())
        works = load_yaml_string(self.source.read_text(encoding="utf-8"))
        self.assertEqual(works[0]["people_refs"], [{**self.character_ref, "id": self.target_id}, {**self.actor_ref, "character_id": self.target_id}])
        self.assertEqual(works[1]["people_refs"], [{**other["people_refs"][0], "character_id": self.target_id}])
        for original, updated in zip([self.record, other, untouched], works):
            self.assertEqual(updated["attributes"], original["attributes"])
        self.assertEqual(result["affected_work_count"], 2)
        self.assertEqual(works[2], untouched)

    def test_language_fields_merge_only_confirmed_slots_and_preserve_other_languages(self):
        self.target["localized"] = {"name": {"zh-Hans": "原中文角色", "ko": "주인공"},
                                    "summary": {"zh-Hans": "原中文简介"}}
        self.donor["localized"] = {"name": {"zh-Hans": "新中文角色", "ko": "주인공", "en": "Hero"},
                                   "summary": {"ko": "한국어 소개"}}
        self.save_entity(self.target)
        self.save_entity(self.donor)
        before = self.bytes()
        preview = self.preview()
        rows = {row["field"]: row for row in preview["diff"]}
        self.assertNotIn("name", rows)
        self.assertFalse(rows["localized.name.ko"]["selectable"])
        self.assertEqual(preview["selected_fields"], [])
        self.assertEqual(self.bytes(), before)
        self.apply(preview, ["localized.name.zh-Hans", "localized.summary.ko"])
        saved = self.repo.read(self.target_id)
        self.assertEqual(localization.project(saved)["name"], {**localization.project(self.target)["name"], "zh-Hans": "新中文角色"})
        self.assertEqual(localization.project(saved)["summary"], {**localization.project(self.target)["summary"], "ko": "한국어 소개"})
        self.assertEqual(saved["field_sources"]["localized.name.zh-Hans"]["donor_id"], self.donor_id)
        self.assertEqual(self.current()["people_refs"][1]["character_id"], self.target_id)

    def test_duplicate_character_refs_dedupe_but_distinct_roles_and_scopes_remain(self):
        self.record["people_refs"].extend([{**self.character_ref, "id": self.target_id}, {**self.character_ref, "role": "客串"}])
        self.write_works()
        self.apply(self.preview())
        refs = self.current()["people_refs"]
        self.assertEqual(len(refs), 3)
        self.assertEqual({ref["role"] for ref in refs}, {"主角", "演员", "客串"})

    def test_avatar_requires_explicit_choice_and_local_cached_asset(self):
        image = image_bytes()
        info = assets.store_image(image, "image/png", root=self.people_root / "assets", label="人物图片")
        self.donor["avatar"] = {"asset_id": info["sha256"] + "." + info["extension"], "source_url": "https://lain.bgm.tv/pic/crt/m/a.png"}
        self.save_entity(self.donor)
        before_asset = (self.people_root / "assets" / self.donor["avatar"]["asset_id"]).read_bytes()
        self.apply(self.preview(), ["avatar"])
        self.assertEqual(self.repo.read(self.target_id)["avatar"], self.donor["avatar"])
        self.assertEqual((self.people_root / "assets" / self.donor["avatar"]["asset_id"]).read_bytes(), before_asset)

    def test_missing_avatar_asset_rejects_selection_but_not_identity_only_merge(self):
        self.donor["avatar"] = {"asset_id": "b" * 64 + ".png", "source_url": "https://example.invalid/avatar.png"}
        self.save_entity(self.donor)
        preview, before = self.preview(), self.bytes()
        with self.assertRaises((ValueError, OSError)):
            self.apply(preview, ["avatar"])
        self.assertEqual(self.bytes(), before)
        self.apply(preview)
        self.assertEqual(self.repo.read(self.target_id)["avatar"], {})

    def test_self_person_missing_retired_and_chain_targets_are_rejected(self):
        for target_id in (self.donor_id, self.actor_id, source_identity("manual", "character", "missing")):
            with self.subTest(target_id=target_id), self.assertRaises(ValueError):
                self.preview(target_id=target_id)
        self.apply(self.preview())
        with self.assertRaises(ValueError):
            self.preview()
        with self.assertRaises(ValueError):
            self.preview(person_id=self.target_id, target_id=self.donor_id)

    def test_donor_with_retired_inbound_link_is_blocked_to_avoid_a_merge_chain(self):
        retired_id = source_identity("manual", "character", "retired")
        self.save_entity({"schema_version": 1, "id": retired_id, "type": "character", "name": "Hero", "merged_into": self.donor_id, "source_refs": []})
        before = self.bytes()
        choices = merge.candidates({"person_id": self.donor_id}, people_root=self.people_root)
        self.assertTrue(all(row["blocked"] for row in choices["candidates"]))
        with self.assertRaisesRegex(ValueError, "合并链"):
            self.preview()
        self.assertEqual(self.bytes(), before)

    def test_invalid_duplicate_empty_unavailable_fields_and_target_tampering_fail(self):
        preview, before = self.preview(), self.bytes()
        for fields in (["id"], ["source_refs"], ["gender"], ["career"], ["summary", "summary"], "summary"):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.apply(preview, fields)
        with self.assertRaises(ValueError):
            self.apply(preview, target_id=self.actor_id)
        with self.assertRaises(ValueError):
            self.apply(preview, selected_fields=None)
        self.assertEqual(self.bytes(), before)

    def test_stale_full_people_inventory_rejects_unrelated_new_record(self):
        preview = self.preview()
        self.save_entity({"schema_version": 1, "id": source_identity("manual", "person", "other"), "type": "person", "name": "Unrelated"})
        before = self.bytes()
        with self.assertRaisesRegex(ValueError, "变化"):
            self.apply(preview)
        self.assertEqual(self.bytes(), before)

    def test_stale_full_work_version_rejects_unrelated_edits(self):
        preview = self.preview()
        self.record["metadata"] = {"summary": "Concurrent work edit"}
        self.write_works()
        before = self.bytes()
        with self.assertRaisesRegex(ValueError, "变化"):
            self.apply(preview)
        self.assertEqual(self.bytes(), before)

    def test_concurrent_target_change_after_plan_is_not_adopted_as_a_fresh_cas_baseline(self):
        preview = self.preview()
        original = merge._merged
        concurrent = {**self.target, "summary": "并发用户修改，必须保留"}

        def race(target, donor, selected, diff):
            result = original(target, donor, selected, diff)
            if selected:
                self.save_entity(concurrent)
            return result

        before_work = self.source.read_bytes()
        with patch.object(merge, "_merged", side_effect=race), self.assertRaisesRegex(ValueError, "变化"):
            self.apply(preview, ["summary"])
        self.assertEqual(self.repo.read(self.target_id), concurrent)
        self.assertEqual(self.repo.read(self.donor_id), self.donor)
        self.assertEqual(self.source.read_bytes(), before_work)

    def test_late_identity_collision_rolls_back_only_our_updates(self):
        preview, before = self.preview(), self.bytes()
        original = PeopleRepository.apply_batch
        late_id = source_identity("manual", "character", "late")
        late = {**self.donor, "id": late_id}

        def race(repository, records, *, expected_revision, after_write=None, **options):
            def late_owner():
                original(repository, [late], expected_revision=repository.revision([late_id]))
                return after_write()
            return original(repository, records, expected_revision=expected_revision, after_write=late_owner, **options)

        with patch.object(PeopleRepository, "apply_batch", new=race), self.assertRaisesRegex(ValueError, "并发"):
            self.apply(preview)
        self.assertEqual(self.source.read_bytes(), before[0])
        for filename, content in before[1].items():
            self.assertEqual((self.people_root / "db" / filename).read_bytes(), content)
        self.assertEqual(self.repo.read(late_id), late)

    def test_work_save_failure_rolls_back_target_and_retired_donor(self):
        preview, before = self.preview(), self.bytes()
        with patch.object(service, "apply_edits", side_effect=ValueError("synthetic save failure")), self.assertRaises(ValueError):
            self.apply(preview, ["summary"])
        self.assertEqual(self.bytes(), before)

    def test_successful_merge_invalidates_old_work_record_version(self):
        old_ref = self.ref()
        self.apply(self.preview())
        current_item = service._records(self.settings)[0]
        self.assertNotEqual(current_item["ref"]["record_sha256"], old_ref["record_sha256"])
        self.assertNotEqual(current_item["ref"]["source_sha256"], old_ref["source_sha256"])
        with self.assertRaisesRegex(ValueError, "作品记录已变化"):
            providers._check_version(current_item, old_ref)

    def _assert_commit_failure_retains_dependencies(self, committed):
        preview = self.preview()
        writer = service.apply_edits

        def commit_then_fail(*args, **kwargs):
            writer(*args, **kwargs)
            error = PersistenceRollbackError("fixture: post-commit failure")
            error.db_committed = committed
            raise error

        with patch.object(service, "apply_edits", side_effect=commit_then_fail), self.assertRaises(PersistenceRollbackError) as raised:
            self.apply(preview, ["summary"])
        self.assertIs(raised.exception.db_committed, committed)
        self.assertTrue(raised.exception.people_records_retained)
        self.assertEqual(self.repo.read(self.donor_id)["merged_into"], self.target_id)
        self.assertEqual(self.repo.read(self.target_id)["summary"], self.donor["summary"])
        self.assertEqual(self.current()["people_refs"][0]["id"], self.target_id)
        self.assertEqual(self.current()["people_refs"][1]["character_id"], self.target_id)
        self.assertEqual(build_source_index(self.repo).resolve("tmdb", "character", "role:" + "a" * 64), self.target_id)

    def test_known_committed_work_failure_preserves_merged_dependencies(self):
        self._assert_commit_failure_retains_dependencies(True)

    def test_unknown_work_commit_failure_preserves_merged_dependencies(self):
        self._assert_commit_failure_retains_dependencies(None)

    def test_source_identity_split_blocks_before_any_write(self):
        conflicting = {**self.target, "id": source_identity("manual", "character", "split")}
        self.save_entity(conflicting)
        before = self.bytes()
        with self.assertRaisesRegex(ValueError, "多个"):
            self.preview()
        self.assertEqual(self.bytes(), before)

    def test_token_scope_expiry_and_injected_fields_are_rejected(self):
        preview, before = self.preview(), self.bytes()
        payload = providers._verify_token(preview["preview_token"])
        for changes in ({"kind": "people-library-edit"}, {"expires": 1}, {"people_scope": "other"}, {"scope": "other"}):
            fake = {**preview, "preview_token": providers._sign({**payload, **changes})}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.apply(fake)
        with self.assertRaises(ValueError):
            self.preview(extra="untrusted")
        self.assertEqual(self.bytes(), before)

    def test_unreferenced_characters_merge_offline_without_writing_any_work(self):
        self.record["people_refs"] = []
        self.write_works()
        before = self.source.read_bytes()
        with patch.object(service, "apply_edits", side_effect=AssertionError("no work update")):
            result = self.apply(self.preview())
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(result["records"], [])


if __name__ == "__main__":
    unittest.main()
