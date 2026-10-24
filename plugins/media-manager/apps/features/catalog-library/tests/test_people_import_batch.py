"""Offline main/supplement plans reuse pending cards without preview writes."""
from copy import deepcopy
import time
import unittest
from unittest.mock import patch

from catalog_library import people, people_import, people_import_batch, providers, service
from people_catalog.model import source_identity
import test_people as fixtures


class PeopleImportBatchTest(unittest.TestCase):
    setUp = fixtures.CatalogPeopleTest.setUp
    ref = fixtures.CatalogPeopleTest.ref
    current = fixtures.CatalogPeopleTest.current

    def entry(self, provider, external_id, *, role="primary", summary="Main profile", work_role=None, localized=None, infobox=None, aliases=None):
        item = service._records(self.settings)[0]
        candidate = {"id": source_identity(provider, "person", external_id), "type": "person", "kind": "individual",
            "name": "Shared Actor", "summary": summary, "taxonomy": {"category": "person", "reality": "real", "subtype": "individual"},
            "source": {"provider": provider, "entity_type": "person", "external_id": str(external_id)}}
        if localized is not None:
            candidate["localized"] = deepcopy(localized)
        if infobox is not None:
            candidate["infobox"] = deepcopy(infobox)
        if aliases is not None:
            candidate["aliases"] = deepcopy(aliases)
        snapshot = {"schema_version": 1, "provider": provider, "subject_id": 100,
            "import_entries": [{"external_id": "person:" + str(external_id), "candidate": candidate, "relations": []}]}
        if work_role:
            snapshot["import_entries"][0]["relations"] = [{"id": "relation:" + str(external_id), "kind": "work",
                "role": work_role, "required_entity_keys": [], "ref": {"id": candidate["id"], "type": "person",
                    "role": work_role, "source": {"provider": provider, "subject_id": "100"}}}]
        snapshot_id, _ = providers._save_snapshot(snapshot, self.people_root / "sources" / provider)
        display, versions = people_import.preview(snapshot, snapshot_id, item["record"], self.repo)
        proposal = {"ref": item["ref"], "provider": provider, "subject_id": 100, "snapshot_id": snapshot_id,
            "scope": providers._catalog_scope(self.settings), "people_scope": people._root_scope(self.repo),
            "selection_only": True, "inventory_sha256": people_import._hash(versions),
            "selection_plan_sha256": people_import._hash(display)}
        if provider == "wikipedia":
            proposal["independent_source"] = True
        return {"binding": {"provider": provider, "role": role}, "preview": {"people_preview": display,
            "preview_token": providers._sign({"people_proposal": proposal, "expires": int(time.time()) + 600})}}

    def preview_batch(self, *, same_source=False):
        entries = [self.entry("bangumi", "1"), self.entry("bangumi" if same_source else "wikipedia", "1" if same_source else "77",
                   role="supplement", summary="Supplement profile")]
        item = service._records(self.settings)[0]
        people_import_batch.rebase(entries, item, people_root=self.people_root)
        return entries

    def proposals(self, entries, *, update=True):
        proposals = []
        first = entries[0]["preview"]["people_preview"]["entries"][0]
        for index, entry in enumerate(entries):
            proposal = providers._verify_token(entry["preview"]["preview_token"])["people_proposal"]
            row = entry["preview"]["people_preview"]["entries"][0]
            proposal["_people_selections"] = [{"external_id": row["external_id"], "action": "create" if index == 0 else "reuse",
                **({"target_id": first["candidate"]["id"]} if index else {}),
                "fields": first["selected_fields"] if index == 0 else ["summary"] if update else [], "relations": []}]
            proposals.append(proposal)
        return proposals

    def apply(self, proposals):
        item = service._records(self.settings)[0]
        with patch.object(providers, "_request_json", side_effect=AssertionError("offline confirmation")):
            return people_import.apply_proposals(proposals, item, item["record"], settings=self.settings,
                                                people_root=self.people_root, data_root=self.library)

    def default_proposals(self, entries):
        result = []
        for entry in entries:
            proposal = providers._verify_token(entry["preview"]["preview_token"])["people_proposal"]
            row = entry["preview"]["people_preview"]["entries"][0]
            proposal["_people_selections"] = [{"external_id": row["external_id"], "action": row["action"],
                "target_id": row["target_id"], "fields": row["selected_fields"], "relations": []}]
            result.append(proposal)
        return result

    def filling_batch(self, *, existing=False):
        if existing:
            identity = source_identity("bangumi", "person", "1")
            self.repo.apply_batch([{"id": identity, "schema_version": 1, "type": "person", "kind": "individual",
                "name": "Shared Actor", "summary": "", "source_refs": [{"provider": "bangumi", "entity_type": "person", "external_id": "1"}]}],
                expected_revision=self.repo.revision([identity]))
        entries = [self.entry("bangumi", "1", summary=""),
            self.entry("bangumi", "1", role="supplement", summary="First fill"),
            self.entry("bangumi", "1", role="supplement", summary="Later alternative")]
        people_import_batch.rebase(entries, service._records(self.settings)[0], people_root=self.people_root)
        return entries

    def test_later_sources_compare_with_previous_default_fills_on_new_cards(self):
        entries = self.filling_batch()
        first, fill, later = [entry["preview"]["people_preview"]["entries"][0] for entry in entries]
        self.assertEqual(first["action"], "create")
        self.assertEqual(fill["selected_fields"], ["summary"])
        self.assertEqual(later["selected_fields"], ["summary"])
        diff = next(row for row in later["targets"][0]["diff"] if row["field"] == "summary")
        self.assertEqual((diff["before"], diff["after"], diff["merge_action"]),
                         ("First fill", "Later alternative", "merge"))
        self.assertFalse((self.people_root / "db").exists())
        self.apply(self.default_proposals(entries))
        self.assertEqual(self.repo.read(first["candidate"]["id"])["summary"], "Later alternative")

    def test_ordered_sources_restore_only_confirmed_links_on_one_existing_card(self):
        identity = source_identity("bangumi", "person", "1")
        self.repo.apply_batch([{"id": identity, "schema_version": 1, "type": "person", "kind": "individual",
            "name": "Shared Actor", "summary": "Main profile", "source_refs": [
                {"provider": "bangumi", "entity_type": "person", "external_id": "1"},
                {"provider": "wikipedia", "entity_type": "person", "external_id": "77"}]}],
            expected_revision=self.repo.revision([identity]))
        entries = [self.entry("bangumi", "1", work_role="导演"),
                   self.entry("wikipedia", "77", role="supplement", work_role="编剧")]
        before = self.source.read_bytes(), self.repo.read(identity)
        people_import_batch.rebase(entries, service._records(self.settings)[0], people_root=self.people_root)
        proposals = self.default_proposals(entries)
        for entry, proposal in zip(entries, proposals):
            row = entry["preview"]["people_preview"]["entries"][0]
            self.assertEqual(row["action"], "reuse")
            self.assertEqual(row["target_id"], identity)
            self.assertEqual(len(row["selected_relations"]), 1)
            proposal["_people_selections"][0]["relations"] = list(row["selected_relations"])
        self.assertEqual((self.source.read_bytes(), self.repo.read(identity)), before)
        proposals[0]["_people_selections"][0]["relations"] = []
        self.apply(proposals)
        self.assertEqual([ref["role"] for ref in self.current()["people_refs"]], ["编剧"])
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 1)

    def localized_filling_batch(self):
        identity = source_identity("bangumi", "person", "1")
        self.repo.apply_batch([{"id": identity, "schema_version": 1, "type": "person", "kind": "individual",
            "name": "Shared Actor", "localized": {"name": {"en": "Shared Actor"}, "summary": {"ko": "기존 소개"}},
            "source_refs": [{"provider": "bangumi", "entity_type": "person", "external_id": "1"}]}],
            expected_revision=self.repo.revision([identity]))
        entries = [self.entry("bangumi", "1", summary="", localized={"name": {"en": "Shared Actor"},
                        "summary": {"zh-Hans": "主来源中文介绍"}, "aliases": {"zh-Hans": ["主来源别名"]}}),
                   self.entry("bangumi", "1", summary="", role="supplement", localized={"name": {"en": "Shared Actor"},
                        "summary": {"zh-Hans": "辅助来源中文介绍"}, "aliases": {"zh-Hans": ["辅助来源别名"]}})]
        people_import_batch.rebase(entries, service._records(self.settings)[0], people_root=self.people_root)
        return entries

    def test_localized_batch_compares_same_language_against_ordered_default_fills(self):
        entries = self.localized_filling_batch()
        first, second = [entry["preview"]["people_preview"]["entries"][0] for entry in entries]
        self.assertEqual(set(first["selected_fields"]), {"localized.summary.zh-Hans", "localized.aliases.zh-Hans"})
        self.assertEqual(set(second["selected_fields"]), {"localized.summary.zh-Hans", "localized.aliases.zh-Hans"})
        diff = {row["field"]: row for row in second["targets"][0]["diff"]}
        self.assertEqual(diff["localized.summary.zh-Hans"]["before"], "主来源中文介绍")
        self.assertEqual(diff["localized.summary.zh-Hans"]["merge_action"], "merge")
        self.assertEqual(diff["localized.aliases.zh-Hans"]["before"], ["主来源别名"])
        self.assertEqual(diff["localized.summary.ko"]["merge_action"], "keep")
        self.apply(self.default_proposals(entries))
        localized = self.repo.read(first["target_id"])["localized"]
        self.assertEqual(localized["summary"], {"ko": "기존 소개", "zh-Hans": "辅助来源中文介绍"})
        self.assertEqual(localized["aliases"]["zh-Hans"], ["主来源别名", "辅助来源别名"])

    def test_cancelled_alias_fill_does_not_block_same_identity_supplement_union(self):
        entries = self.localized_filling_batch()
        proposals = self.default_proposals(entries)
        proposals[0]["_people_selections"][0]["fields"] = []
        proposals[1]["_people_selections"][0]["fields"] = ["localized.aliases.zh-Hans"]
        self.apply(proposals)
        localized = self.repo.read(source_identity("bangumi", "person", "1"))["localized"]
        self.assertEqual(localized["aliases"]["zh-Hans"], ["辅助来源别名"])

    def test_earlier_manual_alias_changes_are_retained_by_same_identity_union(self):
        entries = self.localized_filling_batch()
        proposals = self.default_proposals(entries)
        proposals[0]["_people_selections"][0]["overrides"] = {"localized.aliases.zh-Hans": ["手填别名"]}
        self.apply(proposals)
        localized = self.repo.read(source_identity("bangumi", "person", "1"))["localized"]
        self.assertEqual(localized["aliases"]["zh-Hans"], ["手填别名", "辅助来源别名"])

    def test_later_explicit_alias_replacement_still_requires_expected_baseline(self):
        entries = self.localized_filling_batch()
        proposals = self.default_proposals(entries)
        proposals[0]["_people_selections"][0]["overrides"] = {"localized.aliases.zh-Hans": ["手填别名"]}
        proposals[1]["_people_selections"][0]["overrides"] = {"localized.aliases.zh-Hans": ["后续替换"]}
        with self.assertRaisesRegex(ValueError, "更新前值不再一致"):
            self.apply(proposals)

    def test_cancelled_localized_default_fill_blocks_stale_later_overwrite(self):
        entries = self.localized_filling_batch()
        proposals = self.default_proposals(entries)
        proposals[0]["_people_selections"][0]["fields"] = []
        proposals[1]["_people_selections"][0]["fields"] = ["localized.summary.zh-Hans"]
        with self.assertRaisesRegex(ValueError, "更新前值不再一致"):
            self.apply(proposals)
        self.assertNotIn("zh-Hans", self.repo.read(source_identity("bangumi", "person", "1"))["localized"]["summary"])

    def test_later_sources_compare_with_previous_default_fills_on_existing_cards(self):
        entries = self.filling_batch(existing=True)
        first, fill, later = [entry["preview"]["people_preview"]["entries"][0] for entry in entries]
        self.assertTrue(all(row["action"] == "reuse" for row in (first, fill, later)))
        self.assertEqual(fill["selected_fields"], ["summary"])
        self.assertEqual(later["selected_fields"], ["summary"])
        self.assertEqual(self.repo.read(first["target_id"])["summary"], "")
        self.apply(self.default_proposals(entries))
        self.assertEqual(self.repo.read(first["target_id"])["summary"], "Later alternative")

    def test_cancelled_earlier_fill_rejects_later_selected_stale_conflict(self):
        entries = self.filling_batch(existing=True)
        proposals = self.default_proposals(entries)
        proposals[1]["_people_selections"][0]["fields"] = []
        proposals[2]["_people_selections"][0]["fields"] = ["summary"]
        with self.assertRaisesRegex(ValueError, "更新前值不再一致"):
            self.apply(proposals)
        self.assertEqual(self.repo.read(source_identity("bangumi", "person", "1"))["summary"], "")

    def test_existing_card_rejects_later_overwrite_after_unplanned_confirmed_update(self):
        entries = self.filling_batch(existing=True)
        proposals = self.default_proposals(entries)
        proposals[1]["_people_selections"][0]["overrides"] = {"summary": "Manual first value"}
        proposals[2]["_people_selections"][0]["fields"] = ["summary"]
        with self.assertRaisesRegex(ValueError, "更新前值不再一致"):
            self.apply(proposals)
        self.assertEqual(self.repo.read(source_identity("bangumi", "person", "1"))["summary"], "")

    def test_repeated_explicit_same_value_is_idempotent_across_scopes(self):
        entries = self.filling_batch(existing=True)
        entries[2] = self.entry("bangumi", "1", role="supplement", summary="First fill")
        people_import_batch.rebase(entries, service._records(self.settings)[0], people_root=self.people_root)
        proposals = self.default_proposals(entries)
        proposals[0]["_people_selections"][0]["fields"] = ["summary"]
        proposals[0]["_people_selections"][0]["overrides"] = {"summary": "First fill"}
        proposals[2]["_people_selections"][0]["fields"] = ["summary"]
        self.apply(proposals)
        self.assertEqual(self.repo.read(source_identity("bangumi", "person", "1"))["summary"], "First fill")

    def test_supplement_searches_main_plan_and_displays_diff_without_writing_entities(self):
        before = self.source.read_bytes()
        entries = self.preview_batch()
        first, second = [entry["preview"]["people_preview"] for entry in entries]
        self.assertEqual([first["source_role"], second["source_role"]], ["primary", "supplement"])
        self.assertEqual(first["entries"][0]["action"], "create")
        self.assertEqual(second["entries"][0]["action"], "skip")
        target = second["entries"][0]["targets"][0]
        self.assertTrue(target["planned"])
        self.assertEqual(target["planned_snapshot_id"], first["snapshot_id"])
        self.assertEqual(target["planned_external_id"], "person:1")
        self.assertEqual(target["planned_provider"], "bangumi")
        self.assertEqual(next(row for row in target["diff"] if row["field"] == "summary")["before"], "Main profile")
        self.assertFalse((self.people_root / "db").exists())
        self.assertEqual(self.source.read_bytes(), before)
        self.assertLess(len(second["selection_token"]), 8192)
        result = people_import.target_preview({"snapshot_id": second["snapshot_id"], "preview_token": second["selection_token"],
            "external_id": second["entries"][0]["external_id"], "target_id": target["id"]},
            settings=self.settings, people_root=self.people_root)
        self.assertTrue(result["target"]["planned"])

    def test_selected_supplement_updates_only_main_card_and_only_chosen_field(self):
        entries = self.preview_batch()
        result = self.apply(self.proposals(entries))
        self.assertTrue(result["people_updated"])
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 1)
        row = self.repo.read(source_identity("bangumi", "person", "1"))
        self.assertEqual(row["summary"], "Supplement profile")
        self.assertEqual(row["name"], "Shared Actor")
        self.assertEqual({source["provider"] for source in row["source_refs"]}, {"bangumi", "wikipedia"})

    def test_same_source_in_later_scope_defaults_to_reuse_not_second_create(self):
        entries = self.preview_batch(same_source=True)
        second = entries[1]["preview"]["people_preview"]["entries"][0]
        self.assertEqual(second["action"], "reuse")
        self.assertEqual(second["selected_fields"], ["summary"])
        self.assertFalse(second["creation"]["allowed"])
        self.apply(self.proposals(entries, update=False))
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 1)
        self.assertEqual(self.repo.read(second["target_id"])["summary"], "Main profile")

    def test_cancelled_main_dependency_rejects_every_write(self):
        proposals = self.proposals(self.preview_batch())
        proposals[0]["_people_selections"] = []
        with patch.object(service, "apply_edits", side_effect=AssertionError("must not write")), self.assertRaisesRegex(ValueError, "前序人物已取消"):
            self.apply(proposals)
        self.assertFalse(list((self.people_root / "db").glob("*.yaml")))

    def test_changed_main_before_value_rejects_selected_supplement_update(self):
        for field in ("name", "summary"):
            with self.subTest(field=field):
                proposals = self.proposals(self.preview_batch())
                proposals[0]["_people_selections"][0]["overrides"] = {field: "Manual change"}
                with self.assertRaisesRegex(ValueError, "更新前值不再一致"):
                    self.apply(proposals)
                self.assertFalse(list((self.people_root / "db").glob("*.yaml")))

    def test_planned_same_source_cannot_be_submitted_as_second_create(self):
        entries = self.preview_batch(same_source=True)
        proposals = self.proposals(entries)
        proposals[1]["_people_selections"][0]["action"] = "create"
        with self.assertRaisesRegex(ValueError, "重复创建"):
            self.apply(proposals)

    def test_tampered_batch_context_rejects_before_any_write(self):
        entries = self.preview_batch()
        proposals = self.proposals(entries)
        context = proposals[1]["people_batch_context"]
        path = self.people_root / "sources" / "_batch" / (context["snapshot_id"] + ".json")
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "快照已变化"):
            self.apply(proposals)
        self.assertFalse(list((self.people_root / "db").glob("*.yaml")))

    def test_exact_source_link_only_reuse_allows_unselected_main_profile_edits(self):
        entries = self.preview_batch(same_source=True)
        proposals = self.proposals(entries, update=False)
        proposals[0]["_people_selections"][0]["overrides"] = {"name": "Manual name"}
        self.apply(proposals)
        self.assertEqual(self.repo.read(source_identity("bangumi", "person", "1"))["name"], "Manual name")

    def test_later_selected_update_rejects_stale_before_after_intermediate_update(self):
        entries = self.preview_batch()
        entries.append(self.entry("wikipedia", "78", role="supplement", summary="Third profile"))
        people_import_batch.rebase(entries, service._records(self.settings)[0], people_root=self.people_root)
        proposals = self.proposals(entries)
        with self.assertRaisesRegex(ValueError, "更新前值不再一致"):
            self.apply(proposals)
        self.assertFalse(list((self.people_root / "db").glob("*.yaml")))


if __name__ == "__main__":
    unittest.main()
