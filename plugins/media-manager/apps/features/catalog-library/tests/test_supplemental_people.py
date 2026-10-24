"""A selected Wiki source explicitly supplements or creates local people atomically.

Only transport is mocked: real Wiki zh/ko discovery, cast parsing, enrichment,
snapshot signing, local rebuilding and the shared work writer all run.
"""
from copy import deepcopy
from contextlib import ExitStack
import base64
import unittest
from unittest.mock import patch

from catalog_library import people, providers, service, source_sync, supplemental_people, tmdb_source
from catalog_library import wikipedia_people, wikipedia_source
from catalog_library.source_bindings import normalized_refs
from people_catalog.model import source_identity
from work_catalog_yaml.yaml_io import dump_yaml_string
import test_people as fixtures
from people_import_test_helpers import selections as explicit_selections


def page(title, text, pageid, **extra):
    return {"query": {"pages": [{"pageid": pageid, "ns": 0, "title": title,
        "revisions": [{"revid": pageid + 1000, "slots": {"main": {"content": text}}}], **extra}]}}


class SupplementalPeopleTest(unittest.TestCase):
    ref = fixtures.CatalogPeopleTest.ref
    current = fixtures.CatalogPeopleTest.current
    save_entity = fixtures.CatalogPeopleTest.save_entity

    def setUp(self):
        fixtures.CatalogPeopleTest.setUp(self)
        self.record["source_refs"] = [{"provider": "tmdb", "external_id": "100", "season_number": 1, "scope": "season:1"}]
        self.record["metadata"] = {"summary": "Original local summary"}
        self.write_record()
        self.sources = self.library / "sources"
        self.tmdb_calls, self.wiki_calls = [], []
        self.fail_tmdb = set()
        self.fail_korean = False
        self.network = ExitStack()
        self.addCleanup(self.network.close)
        self.tmdb_http = self.network.enter_context(patch.object(tmdb_source, "_request_json", side_effect=self.tmdb_request))
        self.wiki_http = self.network.enter_context(patch.object(wikipedia_source, "_request_json", side_effect=self.wiki_request))
        self.network.enter_context(patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("No actual network in tests")))

    def write_record(self):
        self.source.write_text(dump_yaml_string([self.record]), encoding="utf-8")
        with service._CACHE_LOCK:
            service._CACHE.clear()

    def tmdb_request(self, path, **kwargs):
        self.tmdb_calls.append((path, deepcopy(kwargs)))
        parts = path.strip("/").split("/")
        if parts[0] == "person":
            return {"id": int(parts[1]), "name": "Ha Seok-Jin", "also_known_as": ["하석진"], "biography": "Source biography"}
        subject = int(parts[1])
        if subject in self.fail_tmdb:
            raise RuntimeError("fixture optional TMDB unavailable")
        if len(parts) == 2:
            return {"id": subject, "name": "Test series", "original_name": "Test series", "original_language": "ko"}
        season = int(parts[3])
        identity = subject * 10 + season
        if len(parts) == 4:
            return {"id": identity, "season_number": season, "episodes": [{"id": identity * 10 + 1,
                "show_id": subject, "season_number": season, "episode_number": 1, "name": "Episode"}]}
        if parts[-1] == "aggregate_credits":
            return {"id": identity, "cast": [{"id": 1, "name": "Ha Seok-Jin", "original_name": "Ha Seok-Jin",
                "roles": [{"character": "Lee Jae-In", "credit_id": f"credit-{subject}-{season}", "episode_count": 1}]}], "crew": []}
        raise AssertionError(path)

    def wiki_request(self, query, **kwargs):
        self.wiki_calls.append((deepcopy(query), deepcopy(kwargs)))
        if kwargs.get("action") == "parse":
            return {"parse": {"text": query["text"]}}
        language = kwargs.get("language", "zh")
        if language == "ko":
            if self.fail_korean:
                raise ValueError("fixture Korean endpoint unavailable")
            self.assertEqual(query.get("titles"), "새 드라마")
            return page("새 드라마", "== 등장인물 ==\n* [[하석진]] : 이재인 역", 789, pageprops={"wikibase_item": "Q123"})
        if "langlinks" in query.get("prop", ""):
            identity = int(query["pageids"])
            return {"query": {"pages": [{"pageid": identity, "ns": 0, "title": "新作品" if identity == 123 else "旧作品",
                "pageprops": {"wikibase_item": "Q123"}, "langlinks": [{"lang": "ko", "title": "새 드라마"}]}]}}
        if query.get("titles") == "河錫辰":
            return page("河錫辰", "{{藝人|姓名=河錫辰|外文名=하석진|外文=ko|羅馬拼音=Ha Seok-Jin}}", 456)
        identity = int(query.get("pageids", 0))
        if identity in {123, 124}:
            name, role = ("新作品", "李宰仁") if identity == 123 else ("旧作品", "不应混入的旧角色")
            return page(name, "{{Infobox television|name=" + name + "|language=韓語}}\n== 演員 ==\n* [[河錫辰]] 飾演 " + role,
                identity, extract="Selected Wiki work description", pageprops={"wikibase_item": "Q123"})
        raise AssertionError(query)

    def seed_tmdb(self):
        for binding in normalized_refs(self.current()):
            if binding["provider"] != "tmdb" or not binding["enabled"]:
                continue
            result = people.preview({"ref": self.ref(), "provider": "tmdb", "binding_id": binding["binding_id"]},
                settings=self.settings, data_root=self.library, people_root=self.people_root)
            people.apply({"ref": result["ref"], "preview_token": result["preview_token"],
                "people_selections": explicit_selections(result, self.repo)}, settings=self.settings,
                data_root=self.library, people_root=self.people_root)
        self.tmdb_calls.clear()
        self.wiki_calls.clear()

    def preview(self, **extra):
        return providers.preview_provider_import({"ref": self.ref(), "provider": "wikipedia", "subject_id": 123, **extra},
            settings=self.settings, source_root=self.sources, people_root=self.people_root)

    def choices(self, preview, *, preserve=False):
        """Simulate choosing existing TMDB identities; names never decide this in production."""
        if not preview.get("people_preview"):
            return {}
        result = explicit_selections(preview)
        actor_id = source_identity("tmdb", "person", 1)
        actor = self.repo.read(actor_id)
        refs = self.current().get("people_refs", [])
        role_id = next((row.get("character_id") for row in refs if row["id"] == actor_id and row.get("character_id")), None)
        sources = (preview.get("people_preview") or {}).get("sources", [])
        for source in sources:
            if preserve:
                for choice in result[source["snapshot_id"]]:
                    choice.update(fields=[], relations=[], overrides={})
            if source["provider"] != "wikipedia":
                continue
            entries = {entry["external_id"]: entry for entry in source["entries"]}
            for choice in result[source["snapshot_id"]]:
                entry = entries[choice["external_id"]]
                target = actor_id if entry["entity_type"] == "person" and actor else role_id
                if target:
                    choice.update(action="reuse", target_id=target, relations=[])
                    # Existing entity classifications and manual edits are not
                    # silently overwritten by a new supplementary source.
                    choice["fields"] = [] if preserve else [key for key in choice["fields"] if key != "taxonomy"]
                    choice["overrides"] = {}
        return result

    def preview_cast(self, preview):
        source = next(row for row in preview["people_preview"]["sources"] if row["entries"])
        actor = next(row for row in source["entries"] if row["entity_type"] == "person")
        relation = next(row for row in actor["relations"] if row["kind"] == "work")
        role = next(row for row in source["entries"] if row["external_id"] in relation["required_entity_keys"])
        return {**actor["candidate"], "character_name": role["candidate"]["name"],
                "character_name_cn": role["candidate"].get("name_cn", "")}

    def apply(self, preview, selected=None, *, preserve=False):
        return providers.apply_provider_import({"ref": preview["ref"], "provider": "wikipedia",
            "snapshot_id": preview["snapshot_id"], "preview_token": preview["preview_token"],
            "selected_fields": ["metadata.summary"] if selected is None else selected,
            "people_selections": self.choices(preview, preserve=preserve)}, settings=self.settings,
            source_root=self.sources, data_root=self.library, people_root=self.people_root)

    def database_state(self):
        return self.source.read_bytes(), {path.name: path.read_bytes() for path in (self.people_root / "db").glob("*.yaml")}

    def forbid_network(self):
        self.tmdb_http.side_effect = AssertionError("TMDB apply must be offline")
        self.wiki_http.side_effect = AssertionError("Wiki apply must be offline")

    def test_new_wiki_preview_uses_real_enrich_and_shows_both_languages_without_database_writes(self):
        self.seed_tmdb()
        before = self.database_state()
        self.assertEqual(people.project(self.current(), people_root=self.people_root)["cast"][0]["character_name"], "Lee Jae-In")
        with patch.object(wikipedia_people, "fetch_people_snapshot", wraps=wikipedia_people.fetch_people_snapshot) as fetch:
            preview = self.preview()
        self.assertEqual(self.database_state(), before)
        fetch.assert_called_once()
        self.assertNotIn("wikipedia", [ref["provider"] for ref in self.current()["source_refs"]])
        self.assertEqual(fetch.call_args.args[0], 123)
        cast = self.preview_cast(preview)
        self.assertEqual((cast["name"], cast["name_cn"]), ("하석진", "河錫辰"))
        self.assertEqual((cast["character_name"], cast["character_name_cn"]), ("이재인", "李宰仁"))
        signed = providers._verify_token(preview["preview_token"])
        child = signed["supplement_people_proposals"][0]
        self.assertEqual(child["ref"], preview["ref"])
        self.assertEqual(child["supplement_dependency"]["external_id"], "123")
        self.assertEqual(child["provider"], "tmdb")
        snapshot = providers._read_snapshot(child["snapshot_id"], child["snapshot_id"], self.people_root / "sources" / "tmdb", provider="tmdb")
        self.assertEqual(snapshot["subject_id"], 100)
        self.assertEqual(snapshot["raw"]["wikipedia_people"]["work_pages"][0]["pageid"], 123)
        self.assertTrue(all(row["action"] == "reuse" for row in preview["people_preview"]["sources"][0]["entries"]))
        self.assertTrue(snapshot["import_entries"])
        self.assertTrue(any(options.get("language") == "ko" for _, options in self.wiki_calls))
        self.assertEqual(sum(query.get("titles") == "河錫辰" for query, _ in self.wiki_calls), 1)
        self.assertEqual(sum(options.get("language") == "ko" for _, options in self.wiki_calls), 1)

    def test_wiki_only_actor_and_role_remain_in_combined_preview(self):
        self.seed_tmdb()
        original = self.wiki_request
        def request(query, **options):
            if query.get("titles") == "新演员":
                return page("新演员", "{{藝人|姓名=新演员|外文名=다른배우|外文=ko}}", 457)
            result = original(query, **options)
            if query.get("pageids") == 123 and "revisions" in query.get("prop", ""):
                result["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"] += "\n* [[新演员]] 飾演 新角色"
            return result
        self.wiki_http.side_effect = request
        preview = self.preview()
        source = next(row for row in preview["people_preview"]["sources"] if row["provider"] == "wikipedia")
        actors = [row for row in source["entries"] if row["entity_type"] == "person"]
        existing = next(row for row in actors if row["external_id"] == "person:456")
        added = next(row for row in actors if row["external_id"] == "person:457")
        self.assertEqual(existing["action"], "reuse")
        self.assertEqual(existing["target_id"], source_identity("tmdb", "person", 1))
        self.assertFalse(existing["relations"])
        self.assertEqual(added["action"], "create")
        roles = [row for row in source["entries"] if row["entity_type"] == "character"]
        self.assertEqual([row["candidate"]["name_cn"] for row in roles], ["新角色"])
        self.forbid_network()
        providers.apply_provider_import({"ref": preview["ref"], "provider": "wikipedia", "snapshot_id": preview["snapshot_id"],
            "preview_token": preview["preview_token"], "selected_fields": [], "people_selections": explicit_selections(preview)},
            settings=self.settings, source_root=self.sources, data_root=self.library, people_root=self.people_root)
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 4)
        self.assertEqual(len(people.project(self.current(), people_root=self.people_root)["cast"]), 2)

    def test_confirm_combined_preview_is_offline_and_saves_work_exactly_once(self):
        self.seed_tmdb()
        preview = self.preview()
        self.forbid_network()
        with patch.object(service, "apply_edits", wraps=service.apply_edits) as save:
            result = self.apply(preview)
        save.assert_called_once()
        self.assertTrue(result["people_updated"])
        record = self.current()
        self.assertEqual(record["metadata"]["summary"], "Selected Wiki work description")
        self.assertEqual({row["provider"] for row in record["source_refs"]}, {"tmdb", "wikipedia"})
        cast = people.project(record, people_root=self.people_root)["cast"]
        self.assertEqual(len(cast), 1)
        self.assertEqual((cast[0]["character_name"], cast[0]["character_name_cn"]), ("이재인", "李宰仁"))

    def test_new_wiki_enriches_prior_tmdb_snapshot_without_repeating_main_requests(self):
        self.seed_tmdb()
        item = providers._catalog_item(self.ref(), self.settings)
        prior = people.preview_for_import(item, 100, settings=self.settings, people_root=self.people_root,
            provider="tmdb", season_number=1)
        cached = self.people_root / "sources" / "tmdb" / (prior["proposal"]["snapshot_id"] + ".json")
        before = cached.read_bytes()
        self.tmdb_calls.clear()
        preview = providers.preview_provider_import({"ref": self.ref(), "provider": "wikipedia", "subject_id": 123},
            settings=self.settings, source_root=self.sources, people_root=self.people_root,
            _prior_people_proposals=[prior["proposal"]])
        self.assertFalse(self.tmdb_calls)
        self.assertEqual(cached.read_bytes(), before)
        self.assertEqual(self.preview_cast(preview)["character_name_cn"], "李宰仁")
        self.assertNotIn("wikipedia", [row["provider"] for row in self.current()["source_refs"]])
        self.forbid_network()
        self.apply(preview)
        self.assertEqual(people.project(self.current(), people_root=self.people_root)["cast"][0]["character_name_cn"], "李宰仁")

    def test_empty_tmdb_people_result_preserves_independent_wiki_candidates(self):
        with patch.object(people, "preview_for_import", return_value={"proposal": None, "preview": None}):
            preview = self.preview()
        self.assertEqual([row["provider"] for row in preview["people_preview"]["sources"]], ["wikipedia"])
        self.assertEqual(self.preview_cast(preview)["character_name_cn"], "李宰仁")

    def test_multiple_wiki_sources_do_not_discard_earlier_tmdb_enrichment(self):
        self.seed_tmdb()
        self.record = self.current()
        self.record["source_refs"].extend({"provider": "wikipedia", "external_id": str(identity)} for identity in (123, 124))
        self.write_record()
        preview = source_sync.preview({"ref": self.ref()}, settings=self.settings, source_root=self.sources, people_root=self.people_root)
        sources = preview["people_preview"]["sources"]
        tmdb = [row for row in sources if row["provider"] == "tmdb"]
        self.assertEqual(len(tmdb), 1)
        self.assertEqual(next(row for row in tmdb[0]["entries"] if row["entity_type"] == "character")["candidate"]["name_cn"], "李宰仁")
        later = next(row for row in sources if row["provider"] == "wikipedia" and row["subject_id"] == 124)
        self.assertTrue(any(row["candidate"].get("name_cn") == "不应混入的旧角色" for row in later["entries"]))
        self.assertTrue(any("保留前项" in warning for warning in preview["warnings"]))

    def test_separate_actor_source_owners_warn_without_dropping_wiki_profiles(self):
        self.seed_tmdb()
        wiki_id = source_identity("wikipedia", "person", 456)
        self.save_entity({"id": wiki_id, "schema_version": 1, "type": "person", "kind": "individual",
            "name": "河錫辰", "name_cn": "河錫辰", "avatar": {},
            "taxonomy": {"category": "person", "reality": "real", "subtype": "individual"},
            "source_refs": [{"provider": "wikipedia", "entity_type": "person", "external_id": "456"}]})
        before = self.database_state()
        preview = self.preview()
        source = next(row for row in preview["people_preview"]["sources"] if row["provider"] == "wikipedia")
        actor = next(row for row in source["entries"] if row["entity_type"] == "person")
        self.assertEqual(actor["target_id"], wiki_id)
        self.assertTrue(any("分别属于" in warning for warning in preview["warnings"]))
        self.assertEqual(before, self.database_state())

    def test_empty_work_field_selection_still_confirms_binding_and_people_once(self):
        self.seed_tmdb()
        preview = self.preview()
        self.forbid_network()
        with patch.object(service, "apply_edits", wraps=service.apply_edits) as save:
            self.apply(preview, [])
        save.assert_called_once()
        self.assertEqual(self.current()["metadata"]["summary"], "Original local summary")
        self.assertEqual(people.project(self.current(), people_root=self.people_root)["cast"][0]["character_name_cn"], "李宰仁")

    def test_replace_wiki_only_reads_new_binding_and_preserves_original_before_confirmation(self):
        self.seed_tmdb()
        self.record = self.current()
        self.record["source_refs"].append({"provider": "wikipedia", "external_id": "124"})
        self.write_record()
        old = next(row for row in normalized_refs(self.current()) if row["provider"] == "wikipedia")
        before = self.database_state()
        preview = self.preview(replace_binding_id=old["binding_id"])
        self.assertEqual(self.database_state(), before)
        self.assertFalse(any(query.get("pageids") == 124 for query, _ in self.wiki_calls))
        cast = self.preview_cast(preview)
        self.assertEqual(cast["character_name_cn"], "李宰仁")
        self.forbid_network()
        self.apply(preview)
        refs = [row for row in normalized_refs(self.current()) if row["provider"] == "wikipedia"]
        self.assertEqual([(row["binding_id"], row["external_id"]) for row in refs], [(old["binding_id"], "123")])

    def test_optional_people_failure_preserves_work_candidates_and_existing_people(self):
        self.seed_tmdb()
        old_people = self.database_state()[1]
        old_refs = deepcopy(self.current()["people_refs"])
        with patch.object(people, "preview_for_import", side_effect=RuntimeError("fixture optional people unavailable")), \
                patch.object(wikipedia_people, "fetch_people_snapshot", side_effect=RuntimeError("fixture optional people unavailable")):
            preview = self.preview()
        self.assertIsNone(preview["people_preview"])
        self.assertTrue(any("作品资料仍可采用" in warning for warning in preview["warnings"]))
        self.forbid_network()
        self.apply(preview)
        self.assertEqual(self.current()["metadata"]["summary"], "Selected Wiki work description")
        self.assertEqual(self.current()["people_refs"], old_refs)
        self.assertEqual(self.database_state()[1], old_people)

    def test_tmdb_network_failure_does_not_block_independent_wiki_confirmation(self):
        self.seed_tmdb()
        old_refs = deepcopy(self.current()["people_refs"])
        old_entities = self.database_state()[1]
        self.fail_tmdb.add(100)
        preview = self.preview()
        self.assertTrue(self.tmdb_calls)
        self.assertTrue(any("fixture optional TMDB unavailable" in warning for warning in preview["warnings"]))
        self.assertEqual(self.preview_cast(preview)["character_name_cn"], "李宰仁")
        self.forbid_network()
        self.apply(preview)
        self.assertEqual(self.current()["metadata"]["summary"], "Selected Wiki work description")
        self.assertEqual(self.current()["people_refs"], old_refs)
        self.assertNotEqual(self.database_state()[1], old_entities)

    def test_korean_network_failure_keeps_chinese_supplement_in_same_preview(self):
        self.seed_tmdb()
        self.fail_korean = True
        preview = self.preview()
        cast = self.preview_cast(preview)
        self.assertEqual(cast["character_name_cn"], "李宰仁")
        # No Korean evidence means no invented native role; existing English
        # text can be retained by declining that field explicitly.
        self.assertEqual(cast["character_name"], "Lee Jae-In")
        self.assertTrue(any("Korean endpoint" in row for row in preview["warnings"]))
        self.forbid_network()
        self.apply(preview)
        self.assertEqual(people.project(self.current(), people_root=self.people_root)["cast"][0]["character_name_cn"], "李宰仁")

    def test_no_tmdb_binding_still_previews_and_defaults_to_create_wiki_people(self):
        self.record["source_refs"] = []
        self.write_record()
        preview = self.preview()
        self.assertEqual(preview["people_preview"]["sources"][0]["provider"], "wikipedia")
        for row in preview["people_preview"]["sources"][0]["entries"]:
            self.assertEqual(row["action"], "create")
            self.assertTrue(any(field.startswith("localized.name.") for field in row["selected_fields"]))
            self.assertEqual(row["selected_fields"], [field["field"] for field in row["creation"]["diff"]
                if field["selectable"] and field["after"] and not field["must_override"]])
        self.assertFalse((self.people_root / "db").exists())
        self.assertEqual(self.tmdb_calls, [])
        self.forbid_network()
        self.apply(preview)
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 2)

    def test_disabled_tmdb_is_not_fetched_or_enriched(self):
        self.record["source_refs"][0]["enabled"] = False
        self.write_record()
        preview = self.preview()
        self.assertEqual(preview["people_preview"]["sources"][0]["provider"], "wikipedia")
        self.assertEqual(self.tmdb_calls, [])
        self.assertFalse((self.people_root / "db").exists())

    def test_disabled_selected_wiki_stays_disabled_and_does_not_fetch_tmdb(self):
        self.record["source_refs"].append({"provider": "wikipedia", "external_id": "123", "enabled": False})
        self.write_record()
        preview = self.preview()
        self.assertIsNone(preview["people_preview"])
        self.assertEqual(self.tmdb_calls, [])
        self.forbid_network()
        self.apply(preview)
        selected = next(row for row in normalized_refs(self.current()) if row["provider"] == "wikipedia")
        self.assertFalse(selected["enabled"])

    def test_modified_signed_child_is_rejected_before_work_or_people_write(self):
        self.seed_tmdb()
        preview = self.preview()
        before = self.database_state()
        payload = providers._verify_token(preview["preview_token"])
        payload["supplement_people_proposals"][0]["supplement_dependency"]["external_id"] = "124"
        encoded = base64.urlsafe_b64encode(providers._json_bytes(payload)).rstrip(b"=").decode("ascii")
        preview["preview_token"] = encoded + "." + preview["preview_token"].split(".")[1]
        self.forbid_network()
        with self.assertRaisesRegex(ValueError, "修改|失效"):
            self.apply(preview)
        self.assertEqual(self.database_state(), before)

    def test_modified_display_is_not_authority_for_confirmed_people(self):
        self.seed_tmdb()
        preview = self.preview()
        role = next(row for row in preview["people_preview"]["sources"][0]["entries"] if row["entity_type"] == "character")
        role["candidate"]["name_cn"] = "Forged display"
        self.forbid_network()
        self.apply(preview)
        self.assertEqual(people.project(self.current(), people_root=self.people_root)["cast"][0]["character_name_cn"], "李宰仁")

    def test_changed_work_rejects_entire_confirmation_without_people_writes(self):
        self.seed_tmdb()
        preview = self.preview()
        self.record = self.current()
        self.record["metadata"]["summary"] = "Concurrent work edit"
        self.write_record()
        before = self.database_state()
        self.forbid_network()
        with self.assertRaisesRegex(ValueError, "变化|预览"):
            self.apply(preview)
        self.assertEqual(self.database_state(), before)

    def test_changed_person_rejects_entire_confirmation_without_work_write(self):
        self.seed_tmdb()
        preview = self.preview()
        actor = self.repo.read(source_identity("tmdb", "person", 1))
        actor["summary"] = "Concurrent manual biography"
        self.save_entity(actor)
        before = self.database_state()
        self.forbid_network()
        with self.assertRaisesRegex(ValueError, "变化|预览"):
            self.apply(preview)
        self.assertEqual(self.database_state(), before)

    def test_multiple_tmdb_bindings_are_preserved_without_automatic_cross_scope_updates(self):
        self.record["source_refs"].extend([
            {"provider": "tmdb", "external_id": "200", "season_number": 1, "scope": "season:1"},
            {"provider": "tmdb", "external_id": "100", "season_number": 2, "scope": "season:2"}])
        self.write_record()
        self.seed_tmdb()
        original_refs = deepcopy(self.current()["people_refs"])
        preview = self.preview()
        sources = preview["people_preview"]["sources"]
        self.assertEqual([(row["provider"], row["subject_id"]) for row in sources], [("wikipedia", 123)])
        self.assertEqual(self.tmdb_calls, [])
        self.assertEqual(len(providers._verify_token(preview["preview_token"])["supplement_people_proposals"]), 1)
        self.forbid_network()
        with patch.object(service, "apply_edits", wraps=service.apply_edits) as save:
            self.apply(preview)
        save.assert_called_once()
        refs = self.current()["people_refs"]
        self.assertEqual(len(refs), 3)
        self.assertEqual({(row["source"]["subject_id"], row["source"]["season_number"]) for row in refs}, {("100", 1), ("100", 2), ("200", 1)})
        self.assertEqual(refs, original_refs)

    def test_unavailable_tmdb_scope_cannot_discard_independent_wiki_candidates(self):
        self.record["source_refs"].append({"provider": "tmdb", "external_id": "200", "season_number": 1, "scope": "season:1"})
        self.write_record()
        self.seed_tmdb()
        original = people.preview_for_import
        def preview_people(item, identity, **options):
            if identity == 100:
                raise RuntimeError("fixture one scope unavailable")
            return original(item, identity, **options)
        with patch.object(people, "preview_for_import", side_effect=preview_people):
            preview = self.preview()
        self.assertEqual([row["subject_id"] for row in preview["people_preview"]["sources"]], [123])
        self.assertEqual(self.tmdb_calls, [])
        self.forbid_network()
        self.apply(preview)
        self.assertEqual(len(self.current()["people_refs"]), 2)
        scopes = {row["source"]["subject_id"]: row for row in self.current()["people_refs"]}
        self.assertEqual(scopes["100"]["character_name"], "Lee Jae-In")
        self.assertEqual(scopes["200"]["character_name"], "Lee Jae-In")

    def test_four_binding_limit_remains_confirmable(self):
        self.record["source_refs"] = [{"provider": "tmdb", "external_id": str(identity), "season_number": 1, "scope": "season:1"}
            for identity in (100, 200, 300, 400)]
        self.write_record()
        preview = self.preview()
        self.assertLessEqual(len(preview["preview_token"]), 8192)
        self.assertEqual(len(providers._verify_token(preview["preview_token"])["supplement_people_proposals"]), 1)
        self.assertEqual(self.tmdb_calls, [])
        self.forbid_network()
        self.apply(preview)
        self.assertEqual(len(self.current()["people_refs"]), 1)
        self.assertEqual(self.current()["people_refs"][0]["source"]["provider"], "wikipedia")

    def test_dependency_validator_rejects_absent_disabled_or_other_wiki_binding(self):
        preview = self.preview()
        children = providers._verify_token(preview["preview_token"])["supplement_people_proposals"]
        dependency = children[0]["supplement_dependency"]
        for refs in ([], [{**dependency, "enabled": False}], [{**dependency, "external_id": "124"}]):
            with self.subTest(refs=refs), self.assertRaisesRegex(ValueError, "未关联|停用"):
                supplemental_people.validate(children, provider="wikipedia", subject_id=123, final_record={"source_refs": refs})
        with self.assertRaisesRegex(ValueError, "不一致"):
            supplemental_people.validate(children, provider="wikipedia", subject_id=124)

    def test_existing_other_wiki_is_not_mixed_into_selected_page_and_remains_bound(self):
        self.seed_tmdb()
        self.record = self.current()
        self.record["source_refs"].append({"provider": "wikipedia", "external_id": "124"})
        self.write_record()
        preview = self.preview()
        self.assertFalse(any(query.get("pageids") == 124 for query, _ in self.wiki_calls))
        self.assertEqual(self.preview_cast(preview)["character_name_cn"], "李宰仁")
        self.forbid_network()
        self.apply(preview)
        self.assertEqual({row["external_id"] for row in self.current()["source_refs"] if row["provider"] == "wikipedia"}, {"123", "124"})

    def test_four_old_wiki_bindings_cannot_exclude_or_contaminate_new_selection(self):
        self.seed_tmdb()
        self.record = self.current()
        self.record["source_refs"].extend({"provider": "wikipedia", "external_id": str(identity)} for identity in (124, 125, 126, 127))
        self.write_record()
        original = self.database_state()
        preview = self.preview()
        self.assertEqual(self.database_state(), original)
        requested_ids = {query["pageids"] for query, _ in self.wiki_calls if "pageids" in query}
        self.assertEqual(requested_ids, {123})
        cast = self.preview_cast(preview)
        self.assertEqual((cast["character_name_cn"], cast["character_name"]), ("李宰仁", "이재인"))
        self.assertFalse(any("已关联作品条目超过" in warning for warning in preview["warnings"]))
        self.forbid_network()
        self.apply(preview)
        self.assertEqual({row["external_id"] for row in self.current()["source_refs"] if row["provider"] == "wikipedia"},
            {"123", "124", "125", "126", "127"})

    def test_people_snapshot_tampering_blocks_both_databases(self):
        self.seed_tmdb()
        preview = self.preview()
        child = providers._verify_token(preview["preview_token"])["supplement_people_proposals"][0]
        snapshot = self.people_root / "sources" / child["provider"] / (child["snapshot_id"] + ".json")
        snapshot.write_text("{}", encoding="utf-8")
        before = self.database_state()
        self.forbid_network()
        with self.assertRaisesRegex(ValueError, "校验"):
            self.apply(preview)
        self.assertEqual(self.database_state(), before)

    def test_manual_names_and_explicit_clears_remain_protected(self):
        self.seed_tmdb()
        self.apply(self.preview())
        actor = self.repo.read(source_identity("tmdb", "person", 1))
        actor.update(name="Manual official name", name_cn="")
        from people_catalog.localization import set_field
        set_field(actor, "localized.name.und-Latn", "Manual official name")
        set_field(actor, "localized.name.zh-Hans", "")
        self.save_entity(actor)
        self.record = self.current()
        self.record["people_refs"][0].update(character_name="Manual role", character_name_cn="")
        self.write_record()
        preview = self.preview()
        entry = next(row for row in preview["people_preview"]["sources"][0]["entries"] if row["entity_type"] == "person")
        target = next(row for row in entry["targets"] if row["id"] == actor["id"])
        before_fields = {row["field"]: row["before"] for row in target["diff"]}
        self.assertEqual((before_fields["localized.name.und-Latn"], before_fields["localized.name.zh-Hans"]),
                         ("Manual official name", ""))
        original_entities = self.database_state()[1]
        self.forbid_network()
        self.apply(preview, preserve=True)
        self.assertEqual(self.current()["people_refs"][0]["character_name_cn"], "")
        self.assertEqual(self.database_state()[1], original_entities)

    def test_more_than_four_tmdb_bindings_do_not_limit_independent_wiki_source(self):
        self.record["source_refs"] = [{"provider": "tmdb", "external_id": str(identity), "season_number": 1, "scope": "season:1"}
            for identity in (100, 200, 300, 400, 500)]
        self.write_record()
        preview = self.preview()
        self.assertEqual(len(preview["people_preview"]["sources"]), 1)
        self.assertEqual(self.tmdb_calls, [])
        self.assertEqual(preview["people_preview"]["sources"][0]["provider"], "wikipedia")
        self.forbid_network()
        self.apply(preview)
        self.assertEqual(self.current()["metadata"]["summary"], "Selected Wiki work description")

    def test_source_sync_wiki_only_flattens_supplements_and_commits_offline_once(self):
        self.seed_tmdb()
        self.record = self.current()
        self.record["source_refs"].append({"provider": "wikipedia", "external_id": "123"})
        self.write_record()
        selected = next(row for row in normalized_refs(self.current()) if row["provider"] == "wikipedia")
        preview = source_sync.preview({"ref": self.ref(), "binding_ids": [selected["binding_id"]]},
            settings=self.settings, source_root=self.sources, people_root=self.people_root)
        children = preview["people_preview"]["sources"]
        self.assertEqual(len(children), 2)
        self.assertEqual(children[0]["provider"], "tmdb")
        self.assertTrue(all(row["entity_type"] == "person" and row["action"] == "reuse" for row in children[1]["entries"]))
        self.assertEqual(self.preview_cast(preview)["character_name_cn"], "李宰仁")
        self.forbid_network()
        with patch.object(service, "apply_edits", wraps=service.apply_edits) as save:
            source_sync.apply({"ref": preview["ref"], "preview_token": preview["preview_token"], "selected_fields": [],
                "people_selections": self.choices(preview)},
                settings=self.settings, source_root=self.sources, people_root=self.people_root, data_root=self.library)
        save.assert_called_once()
        self.assertEqual(people.project(self.current(), people_root=self.people_root)["cast"][0]["character_name_cn"], "李宰仁")

    def test_source_sync_tmdb_and_wiki_keep_one_enriched_confirmable_people_plan(self):
        self.record["source_refs"].append({"provider": "wikipedia", "external_id": "123"})
        self.write_record()
        preview = source_sync.preview({"ref": self.ref()}, settings=self.settings, source_root=self.sources, people_root=self.people_root)
        self.assertEqual(len(preview["people_preview"]["sources"]), 2)
        self.assertEqual({row["provider"] for row in preview["people_preview"]["sources"]}, {"tmdb", "wikipedia"})
        wiki_entries = next(row["entries"] for row in preview["people_preview"]["sources"] if row["provider"] == "wikipedia")
        self.assertTrue(all(row["entity_type"] == "person" and row["action"] == "reuse" for row in wiki_entries))
        self.assertFalse(any("重复人物补充已跳过" in warning or "单独刷新该维基来源" in warning for warning in preview["warnings"]))
        self.assertEqual(len([path for path, _ in self.tmdb_calls if path.endswith("aggregate_credits")]), 2)
        self.forbid_network()
        with patch.object(service, "apply_edits", wraps=service.apply_edits) as save:
            source_sync.apply({"ref": preview["ref"], "preview_token": preview["preview_token"], "selected_fields": [],
                "people_selections": self.choices(preview)},
                settings=self.settings, source_root=self.sources, people_root=self.people_root, data_root=self.library)
        save.assert_called_once()
        self.assertEqual(len(self.current()["people_refs"]), 1)
        self.assertEqual({row["source"]["provider"] for row in self.current()["people_refs"]}, {"tmdb"})
        self.assertEqual(people.project(self.current(), people_root=self.people_root)["cast"][0]["character_name_cn"], "李宰仁")


if __name__ == "__main__":
    unittest.main()
