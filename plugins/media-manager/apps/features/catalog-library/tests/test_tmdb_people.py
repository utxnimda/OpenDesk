from copy import deepcopy
import unittest
import time
from unittest.mock import patch

from catalog_library import people, providers, service, source_sync, tmdb_people, tmdb_source, people_assets
from people_catalog.model import source_identity, validate_people_refs
from work_catalog_yaml.yaml_io import dump_yaml_string
import test_people as people_fixture
from people_import_test_helpers import selections, cast


class TmdbPeopleTest(unittest.TestCase):
    ref = people_fixture.CatalogPeopleTest.ref
    current = people_fixture.CatalogPeopleTest.current
    save_entity = people_fixture.CatalogPeopleTest.save_entity

    def setUp(self):
        people_fixture.CatalogPeopleTest.setUp(self)
        self.bangumi_details = deepcopy(self.details)
        self.record["source_refs"] = [{"provider": "tmdb", "external_id": "100", "season_number": 1, "scope": "season:1"}]
        self.write_record()
        self.season = {"id": 1001, "season_number": 1, "episodes": [
            {"id": 101, "season_number": 1, "show_id": 100, "episode_number": 1, "name": "Episode one"},
            {"id": 102, "season_number": 1, "show_id": 100, "episode_number": 2, "name": "Episode two"}]}
        self.credits = {"id": 1001, "cast": [{"id": 1, "name": "金演员", "original_name": "김배우", "roles": [{"character": "男主角", "episode_count": 2}]}],
                        "crew": [{"id": 2, "name": "李导演", "jobs": [{"job": "Director", "episode_count": 2}]}]}
        self.details = {1: {"id": 1, "name": "金演员", "biography": "演员介绍", "also_known_as": ["Kim Actor", "金演员"], "birthday": "1980-02-03", "gender": 2},
                        2: {"id": 2, "name": "李导演", "biography": "导演介绍", "known_for_department": "Directing"}}
        self.calls = []

    def write_record(self):
        self.source.write_text(dump_yaml_string([self.record]), encoding="utf-8")
        with service._CACHE_LOCK:
            service._CACHE.clear()

    def request(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if path == "/tv/100":
            return {"id": 100, "name": "电视剧", "original_name": "TV Show", "original_language": "zh", "first_air_date": "2011-01-01"}
        if path == "/tv/100/season/1":
            return deepcopy(self.season)
        if path == "/tv/100/season/1/aggregate_credits":
            return deepcopy(self.credits)
        if "/episode/" in path:
            number = int(path.split("/")[-2])
            return {"id": 100 + number, "cast": [{"id": number, "name": "金演员" if number == 1 else "李导演", "character": "范围角色"}], "crew": []}
        if path.startswith("/person/"):
            return deepcopy(self.details[int(path.split("/")[-1])])
        raise AssertionError(path)

    def preview(self, **body):
        with patch.object(tmdb_source, "_request_json", side_effect=self.request):
            return people.preview({"ref": self.ref(), "provider": "tmdb", "subject_id": 100, "season_number": 1, **body},
                                  settings=self.settings, data_root=self.library, people_root=self.people_root)

    def apply(self, preview):
        return people.apply({"ref": preview["ref"], "preview_token": preview["preview_token"], "people_selections": selections(preview, self.repo)}, settings=self.settings,
                            data_root=self.library, people_root=self.people_root)

    def test_preview_adapts_real_cast_and_crew_without_database_write(self):
        before = self.source.read_bytes()
        preview = self.preview()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse((self.people_root / "db").exists())
        self.assertTrue(preview["binding_id"])
        actor = cast(preview)[0]
        self.assertEqual((actor["name"], actor["name_cn"], actor["character_name"]), ("김배우", "金演员", "男主角"))
        self.assertEqual(actor["type"], "person")
        self.assertNotIn("actors", actor)
        self.assertTrue(any(row["role"] == "导演" for entry in preview["entries"] for row in entry["relations"]))
        self.assertEqual(len([row for row in preview["entries"] if row["entity_type"] == "character"]), 1)
        self.assertTrue(all(call[1]["deadline"] for call in self.calls))
        self.assertEqual(len(list((self.people_root / "sources" / "tmdb").glob("*.json"))), 1)

    def test_apply_and_projection_are_offline_and_commit_work_once(self):
        preview = self.preview()
        with patch.object(tmdb_source, "_request_json", side_effect=AssertionError("apply must not network")), patch.object(service, "apply_edits", wraps=service.apply_edits) as writer:
            result = self.apply(preview)
            projected = people.project(self.current(), people_root=self.people_root)
        writer.assert_called_once()
        self.assertEqual(projected, result["people"])
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 3)
        actor = self.repo.read(source_identity("tmdb", "person", 1))
        self.assertEqual(actor["birth_date"], {"year": 1980, "month": 2, "day": 3})
        self.assertNotIn("voice_actors", actor)
        self.assertEqual(self.current()["people_refs"][0]["source"]["season_number"], 1)

    def test_refresh_preserves_manual_profile_fields_and_other_season_relations(self):
        from people_catalog.localization import set_field
        self.apply(self.preview())
        actor = self.repo.read(source_identity("tmdb", "person", 1))
        set_field(actor, "localized.summary.zh-Hans", "手工内容")
        self.save_entity(actor)
        self.record = self.current()
        other = deepcopy(self.record["people_refs"][0]); other["source"]["season_number"] = 2
        self.record["people_refs"].append(other); self.write_record()
        self.details[1]["biography"] = "来源更新"
        self.apply(self.preview())
        self.assertEqual(self.repo.read(actor["id"])["summary"], "手工内容")
        self.assertEqual(self.repo.read(actor["id"])["localized"]["summary"]["zh-Hans"], "手工内容")
        self.assertIn(other, self.current()["people_refs"])

    def test_missing_credits_and_profiles_preserve_existing_fields_and_relations(self):
        self.apply(self.preview())
        before = self.current()["people_refs"]
        self.credits.pop("cast")
        self.details[2] = {"id": 2, "biography": {}, "birthday": "not-a-date"}
        preview = self.preview()
        self.assertTrue(preview["warnings"])
        self.apply(preview)
        self.assertEqual(self.current()["people_refs"], before)
        self.assertEqual(self.repo.read(source_identity("tmdb", "person", 2))["summary"], "导演介绍")

    def test_episode_range_uses_only_selected_episode_credits_not_whole_season(self):
        self.record["source_refs"][0].update(scope="season:1;episodes:2-2", episode_range={"start": 2, "end": 2})
        self.write_record()
        preview = self.preview(episode_range={"start": 2, "end": 2})
        self.assertEqual(len(cast(preview)), 1)
        self.assertEqual(cast(preview)[0]["id"], source_identity("tmdb", "person", 2))
        self.assertEqual(cast(preview)[0]["source"]["episode_range"], {"start": 2, "end": 2})
        self.assertIn("/tv/100/season/1/episode/2/credits", [path for path, _ in self.calls])
        self.assertFalse(any("aggregate_credits" in path or "/episode/1/" in path for path, _ in self.calls))

    def test_explicit_binding_identity_and_season_cannot_be_changed_at_apply(self):
        preview = self.preview()
        for patch_body in ({"provider": "bangumi"}, {"season_number": 2}, {"binding_id": "sb_wrong"}):
            with self.subTest(patch_body=patch_body), self.assertRaises(ValueError):
                people.apply({"ref": preview["ref"], "preview_token": preview["preview_token"], **patch_body}, settings=self.settings, people_root=self.people_root)
        with self.assertRaises(ValueError):
            self.preview(season_number=2)
        self.assertFalse((self.people_root / "db").exists())

    def test_failed_work_write_rolls_back_all_new_people(self):
        preview = self.preview()
        before = self.source.read_bytes()
        with patch.object(service, "apply_edits", side_effect=ValueError("作品版本变化")), self.assertRaises(ValueError):
            self.apply(preview)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse(list((self.people_root / "db").glob("*.yaml")))

    def test_source_refresh_reads_people_and_apply_is_one_work_write(self):
        with patch.object(tmdb_source, "_request_json", side_effect=self.request):
            preview = providers.preview_provider_import({"ref": self.ref(), "provider": "tmdb", "subject_id": 100, "season_number": 1}, settings=self.settings,
                source_root=self.library / "sources", assets_root=self.library / "assets", people_root=self.people_root)
        self.assertEqual(len(cast(preview["people_preview"])), 1)
        with patch.object(service, "apply_edits", wraps=service.apply_edits) as writer, patch.object(tmdb_source, "_request_json", side_effect=AssertionError("offline apply")):
            result = providers.apply_provider_import({"ref": preview["ref"], "provider": "tmdb", "snapshot_id": preview["snapshot_id"], "preview_token": preview["preview_token"], "selected_fields": [], "people_selections": selections(preview, self.repo)},
                settings=self.settings, source_root=self.library / "sources", assets_root=self.library / "assets", data_root=self.library, people_root=self.people_root)
        writer.assert_called_once()
        self.assertTrue(result["people_updated"])

    def test_multi_source_refresh_routes_tmdb_people_through_atomic_shared_apply(self):
        with patch.object(tmdb_source, "_request_json", side_effect=self.request):
            preview = source_sync.preview({"ref": self.ref()}, settings=self.settings, source_root=self.library / "sources", assets_root=self.library / "assets", people_root=self.people_root)
        with patch.object(service, "apply_edits", wraps=service.apply_edits) as writer:
            result = source_sync.apply({"ref": preview["ref"], "preview_token": preview["preview_token"], "selected_fields": [], "people_selections": selections(preview, self.repo)}, settings=self.settings,
                                      source_root=self.library / "sources", assets_root=self.library / "assets", data_root=self.library, people_root=self.people_root)
        writer.assert_called_once()
        self.assertTrue(result["people_updated"])

    def test_tmdb_paths_are_fixed_and_character_relations_remain_person_only(self):
        self.assertTrue(tmdb_source._PATH.fullmatch("/tv/100/season/1/aggregate_credits"))
        self.assertTrue(tmdb_source._PATH.fullmatch("/tv/100/season/1/episode/2/credits"))
        self.assertTrue(tmdb_source._PATH.fullmatch("/person/1"))
        for path in ("/person/../config", "https://evil/person/1", "/movie/1/credits", "/tv/100/credits"):
            self.assertFalse(tmdb_source._PATH.fullmatch(path))
        with self.assertRaises(ValueError):
            validate_people_refs([{"id": source_identity("tmdb", "character", 1), "type": "character", "character_name": "role"}])
        with patch.object(people_assets.assets, "download_image") as download, self.assertRaises(ValueError):
            people_assets.acquire_tmdb_avatar({"profile_path": "https://evil/image.jpg"}, user_agent="test", root=self.people_root)
        download.assert_not_called()

    def test_detail_errors_leave_confirmable_credit_names_and_optional_null_dates_are_quiet(self):
        def request(path, **kwargs):
            if path.startswith("/person/"):
                raise ValueError("TMDB 连接失败或超时")
            return self.request(path, **kwargs)
        with patch.object(tmdb_source, "_request_json", side_effect=request):
            preview = people.preview({"ref": self.ref(), "provider": "tmdb", "subject_id": 100, "season_number": 1}, settings=self.settings, people_root=self.people_root)
        self.assertTrue(preview["warnings"])
        self.apply(preview)
        self.assertEqual(people.project(self.current(), people_root=self.people_root)["cast"][0]["character_name"], "男主角")
        self.assertEqual(self.repo.read(source_identity("tmdb", "person", 1)).get("summary", ""), "")
        self.details[1]["birthday"] = None
        preview = self.preview()
        self.assertFalse(any("birth_date" in message for message in preview["warnings"]))

    def test_expired_budget_does_not_start_requests_and_failed_range_cannot_clear_cast(self):
        with patch.object(tmdb_source, "_request_json") as request:
            snapshot = tmdb_people.fetch_snapshot(100, season_number=1, deadline=time.monotonic() - 1)
        request.assert_not_called()
        self.assertEqual(snapshot["raw"]["unavailable_lists"], ["cast", "crew"])
        self.record["source_refs"][0].update(scope="season:1;episodes:1-2", episode_range={"start": 1, "end": 2})
        self.write_record()
        self.apply(self.preview(episode_range={"start": 1, "end": 2}))
        previous = self.current()["people_refs"]
        def request(path, **kwargs):
            if "/episode/2/" in path:
                raise ValueError("TMDB 连接失败或超时")
            return self.request(path, **kwargs)
        with patch.object(tmdb_source, "_request_json", side_effect=request):
            preview = people.preview({"ref": self.ref(), "provider": "tmdb", "subject_id": 100, "season_number": 1}, settings=self.settings, people_root=self.people_root)
        self.apply(preview)
        self.assertEqual(self.current()["people_refs"], previous)

    def test_changed_actor_database_and_tampered_snapshot_reject_before_work_write(self):
        preview = self.preview()
        identity = source_identity("tmdb", "person", 1)
        self.repo.apply_batch([{"id": identity, "schema_version": 1, "type": "person", "name": "concurrent user"}], expected_revision=self.repo.revision([identity]))
        with patch.object(service, "apply_edits") as writer, self.assertRaises(ValueError):
            self.apply(preview)
        writer.assert_not_called()
        preview = self.preview()
        snapshot = self.people_root / "sources" / "tmdb" / (preview["snapshot_id"] + ".json")
        snapshot.write_bytes(b"{}")
        with patch.object(service, "apply_edits") as writer, self.assertRaises(ValueError):
            self.apply(preview)
        writer.assert_not_called()

    def test_tmdb_portrait_uses_fixed_cdn_and_returns_only_local_asset_reference(self):
        image = {"sha256": "a" * 64, "extension": "jpg"}
        with patch.object(people_assets.assets, "download_image", return_value=(b"fixture", "image/jpeg")) as download, patch.object(people_assets.assets, "store_image", return_value=image):
            avatar = people_assets.acquire_tmdb_avatar({"profile_path": "/Portrait.jpg"}, user_agent="fixture", root=self.people_root, deadline=100)
        self.assertEqual(download.call_args.args[0], "https://image.tmdb.org/t/p/w500/Portrait.jpg")
        self.assertIs(download.call_args.kwargs["validate_url"], tmdb_source.validate_cover_url)
        self.assertEqual(people_assets.avatar_url(avatar), "/api/catalog-library/people/assets/" + "a" * 64 + ".jpg")

    def test_manually_cleared_profile_fields_are_not_restored_by_refresh(self):
        from people_catalog.localization import field_paths, set_field
        self.apply(self.preview())
        identity = source_identity("tmdb", "person", 1)
        entity = self.repo.read(identity)
        for path in field_paths(entity):
            if path.startswith("localized.summary.") or path == "localized.name.zh-Hans":
                set_field(entity, path, "")
            elif path.startswith("localized.aliases."):
                set_field(entity, path, [])
        entity["birth_date"] = {}
        self.save_entity(entity)
        self.details[1]["biography"] = "new remote biography"
        self.apply(self.preview())
        after = self.repo.read(identity)
        for key, value in {"summary": "", "name_cn": "", "birth_date": {}}.items():
            self.assertEqual(after[key], value)
        # Confirmed source name versions are additive aliases even when their
        # displayed name slot was not selected for refilling.
        self.assertEqual(after["aliases"], ["金演员"])

    def test_mixed_bangumi_and_tmdb_same_numeric_subject_id_compose_people_once(self):
        self.record["source_refs"].append({"provider": "bangumi", "external_id": "100", "scope": ""})
        self.write_record()
        def bangumi_request(path, **kwargs):
            if path == "/v0/subjects/100/characters":
                return deepcopy(self.characters)
            if path == "/v0/subjects/100/persons":
                return deepcopy(self.persons)
            parts = path.split("/")
            return deepcopy(self.bangumi_details[f"{'character' if parts[2] == 'characters' else 'person'}:{parts[-1]}"])
        snapshot = {"schema_version": 1, "provider": "bangumi", "subject_id": 100, "fetched_at": "2026-01-01T00:00:00Z",
                    "raw": {"subject": {"id": 100, "name": "Bangumi title", "type": 2, "date": "2011-01-01"}, "episode_pages": [{"data": []}]}}
        with patch.object(tmdb_source, "_request_json", side_effect=self.request), patch.object(providers, "_fetch_snapshot", return_value=snapshot), patch.object(providers, "_request_json", side_effect=bangumi_request):
            preview = source_sync.preview({"ref": self.ref()}, settings=self.settings, source_root=self.library / "sources", assets_root=self.library / "assets", people_root=self.people_root)
        self.assertEqual([row["status"] for row in preview["sources"]], ["ok", "ok"])
        with patch.object(service, "apply_edits", wraps=service.apply_edits) as writer, patch.object(tmdb_source, "_request_json", side_effect=AssertionError("offline")), patch.object(providers, "_request_json", side_effect=AssertionError("offline")):
            result = source_sync.apply({"ref": preview["ref"], "preview_token": preview["preview_token"], "selected_fields": [], "people_selections": selections(preview, self.repo)}, settings=self.settings,
                source_root=self.library / "sources", assets_root=self.library / "assets", people_root=self.people_root, data_root=self.library)
        writer.assert_called_once()
        projection = people.project(self.current(), people_root=self.people_root)
        self.assertEqual(len(projection["cast"]), 1)
        self.assertEqual(len(projection["characters"]), 1)
        self.assertEqual({ref["source"]["provider"] for ref in self.current()["people_refs"]}, {"bangumi", "tmdb"})
        self.assertTrue(result["people_updated"])

    def bilingual_snapshot(self, localized=None, original=None):
        localized = localized or {"id": 1001, "cast": [{"id": 1, "name": "金演员", "original_name": "김배우", "roles": [
            {"credit_id": "first-role", "character": "王甲", "episode_count": 2},
            {"credit_id": "second-role", "character": "王乙", "episode_count": 1}]}], "crew": []}
        original = original or {"id": 1001, "cast": [{"id": 1, "name": "김배우", "original_name": "김배우", "roles": [
            {"credit_id": "second-role", "character": "왕을", "episode_count": 1},
            {"credit_id": "first-role", "character": "왕갑", "episode_count": 2}]}], "crew": []}
        def request(path, **kwargs):
            if path.endswith("aggregate_credits"):
                return deepcopy(original if kwargs["query"]["language"] == "ko" else localized)
            return self.request(path, **kwargs)
        with patch.object(tmdb_source, "_request_json", side_effect=request):
            return tmdb_people.fetch_snapshot(100, season_number=1, season_data=self.season, original_language="ko")

    def test_bilingual_roles_match_credit_id_not_actor_or_return_position(self):
        snapshot = self.bilingual_snapshot()
        relations = {row["credit_id"]: row for row in snapshot["raw"]["relations"]}
        self.assertEqual((relations["first-role"]["value"], relations["first-role"]["value_cn"]), ("왕갑", "王甲"))
        self.assertEqual((relations["second-role"]["value"], relations["second-role"]["value_cn"]), ("왕을", "王乙"))
        self.assertEqual(snapshot["original_language"], "ko")
        self.assertEqual(snapshot["raw"]["credits"][0][2]["cast"][0]["roles"][0]["character"], "王甲")
        self.assertEqual(snapshot["raw"]["credits_original"][0][2]["cast"][0]["roles"][0]["character"], "왕을")
        updated, entities, _ = tmdb_people.build(snapshot, "fixture", self.record, self.repo)
        cast = people.project(updated, entities=entities)["cast"]
        self.assertEqual({row["character_name"]: row["character_name_cn"] for row in cast}, {"왕갑": "王甲", "왕을": "王乙"})

    def test_missing_duplicate_and_wrong_person_credit_ids_never_cross_match_roles(self):
        for localized in (
                {"id": 1001, "cast": [{"id": 1, "roles": [{"character": "不应匹配"}]}], "crew": []},
                {"id": 1001, "cast": [{"id": 1, "roles": [{"credit_id": "first-role", "character": "歧义一"}, {"credit_id": "first-role", "character": "歧义二"}]}], "crew": []},
                {"id": 1001, "cast": [{"id": 2, "roles": [{"credit_id": "first-role", "character": "另一演员"}]}], "crew": []}):
            with self.subTest(localized=localized):
                snapshot = self.bilingual_snapshot(localized=localized)
                self.assertTrue(all("value_cn" not in row for row in snapshot["raw"]["relations"]))

    def test_korean_show_does_not_koreanize_official_english_actor_name(self):
        original = {"id": 1001, "cast": [{"id": 1, "name": "배우", "original_name": "John Smith", "roles": [
            {"credit_id": "first-role", "character": "Mr. John", "episode_count": 2}]}], "crew": []}
        self.details[1]["also_known_as"] = ["존 스미스", "约翰·史密斯"]
        snapshot = self.bilingual_snapshot(original=original)
        updated, entities, _ = tmdb_people.build(snapshot, "fixture", self.record, self.repo)
        self.assertEqual(entities[0]["name"], "John Smith")
        self.assertEqual(people.project(updated, entities=entities)["cast"][0]["character_name"], "Mr. John")

    def test_wikipedia_supplement_is_deterministic_offline_and_preserves_manual_blanks(self):
        snapshot = self.bilingual_snapshot()
        snapshot["raw"]["details"]["1"]["name"] = "Kim Actor"
        snapshot["raw"]["details"]["1"]["also_known_as"] = []
        snapshot["raw"]["fallback"]["1"]["name"] = "김배우"
        snapshot["raw"]["fallback_localized"] = {}
        for relation in snapshot["raw"]["relations"]:
            relation.pop("value_cn", None)
        snapshot["raw"]["wikipedia_people"] = {"people": {"1": {"name_cn": "维基演员"}}, "relations": [
            {"person_id": 1, "credit_id": "first-role", "original_value": "왕갑", "character_name_cn": "维基角色"}]}
        with patch.object(tmdb_source, "_request_json", side_effect=AssertionError("offline")):
            updated, entities, _ = tmdb_people.build(snapshot, "fixture", self.record, self.repo)
        self.assertEqual(entities[0]["name_cn"], "维基演员")
        ref = next(row for row in updated["people_refs"] if row.get("credit_id") == "first-role")
        self.assertEqual(ref["character_name_cn"], "维基角色")
        ref["character_name_cn"] = ""
        entities[0]["name_cn"] = ""
        for entity in entities:
            self.save_entity(entity)
        refreshed, refreshed_entities, _ = tmdb_people.build(snapshot, "fixture2", updated, self.repo)
        self.assertEqual(next(row for row in refreshed["people_refs"] if row.get("credit_id") == "first-role")["character_name_cn"], "")
        self.assertEqual(refreshed_entities[0]["name_cn"], "维基演员")

    def test_verified_native_name_supplement_keeps_tmdb_name_as_alias_and_manual_name(self):
        snapshot = self.bilingual_snapshot()
        snapshot["raw"]["fallback"]["1"]["original_name"] = "Kim Actor"
        snapshot["raw"]["wikipedia_people"] = {"people": {"1": {"name": "김배우", "name_source": "native_name"}}, "relations": []}
        updated, entities, _ = tmdb_people.build(snapshot, "fixture", self.record, self.repo)
        self.assertEqual(entities[0]["name"], "김배우")
        self.assertIn("Kim Actor", entities[0]["aliases"])
        entities[0]["name"] = "Manual English Name"
        for entity in entities:
            self.save_entity(entity)
        _, refreshed, _ = tmdb_people.build(snapshot, "fixture2", updated, self.repo)
        self.assertEqual(refreshed[0]["name"], "Manual English Name")

    def test_original_language_failure_preserves_confirmed_names_and_chinese_translations(self):
        snapshot = self.bilingual_snapshot()
        previous, entities, _ = tmdb_people.build(snapshot, "fixture", self.record, self.repo)
        for entity in entities:
            self.save_entity(entity)
        with patch.object(tmdb_source, "_request_json", side_effect=self.request):
            fallback = tmdb_people.fetch_snapshot(100, season_number=1, season_data=self.season, original_language="ko")
        # Model an optional original-language failure with still-valid zh-CN
        # credit identities. Neither established language may be erased.
        fallback["raw"]["relations"] = [
            {"person_id": 1, "category": "cast", "value": "王甲", "value_cn": "王甲", "credit_id": "first-role", "eps": "2 集", "original_available": False},
            {"person_id": 1, "category": "cast", "value": "왕을", "credit_id": "second-role", "eps": "1 集", "original_available": True}]
        refreshed, _, _ = tmdb_people.build(fallback, "fixture2", previous, self.repo)
        names = {row["credit_id"]: (row["character_name"], row["character_name_cn"]) for row in refreshed["people_refs"]}
        self.assertEqual(names, {"first-role": ("왕갑", "王甲"), "second-role": ("왕을", "王乙")})

    def test_bilingual_ref_validation_is_optional_bounded_and_person_only(self):
        base = {"id": source_identity("tmdb", "person", 1), "type": "person", "source": tmdb_people.scope(100, 1), "character_name": "Original"}
        self.assertNotIn("character_name_cn", validate_people_refs([base])[0])
        self.assertEqual(validate_people_refs([{**base, "character_name_cn": "中文"}])[0]["character_name_cn"], "中文")
        for extra in ({"character_name_cn": None}, {"character_name_cn": "长" * 1001}, {"credit_id": ""},
                      {"source_values": {"unsafe": "value"}}, {"type": "character", "character_name_cn": "中文"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                validate_people_refs([{**base, **extra}])

    def test_legacy_refs_migrate_credit_ids_without_losing_manual_chinese_or_original(self):
        original = {"id": 1001, "cast": [{"id": 1, "name": "김배우", "original_name": "김배우", "roles": [
            {"credit_id": "first-role", "character": "왕갑", "episode_count": 2}]}], "crew": []}
        snapshot = self.bilingual_snapshot(original=original)
        for role_name, chinese in (("王甲", "手工中文"), ("王甲", ""), ("手工原名", "手工中文")):
            with self.subTest(role_name=role_name, chinese=chinese):
                record = deepcopy(self.record)
                record["people_refs"] = [{"id": source_identity("tmdb", "person", 1), "type": "person", "role": "演员",
                    "source": tmdb_people.scope(100, 1), "character_name": role_name, "character_name_cn": chinese}]
                updated, _, _ = tmdb_people.build(snapshot, "fixture", record, self.repo)
                self.assertEqual(len(updated["people_refs"]), 1)
                ref = updated["people_refs"][0]
                self.assertEqual(ref["credit_id"], "first-role")
                self.assertEqual(ref["character_name_cn"], chinese)
                self.assertEqual(ref["character_name"], "手工原名" if role_name == "手工原名" else "왕갑")

    def test_chinese_credit_actor_name_survives_original_language_fallback_and_failed_profile(self):
        snapshot = self.bilingual_snapshot()
        snapshot["raw"]["details"]["1"] = None
        _, entities, _ = tmdb_people.build(snapshot, "fixture", self.record, self.repo)
        self.assertEqual(entities[0]["name"], "김배우")
        self.assertEqual(entities[0]["name_cn"], "金演员")

    def test_wiki_person_and_role_variants_survive_tmdb_normalization(self):
        snapshot = self.bilingual_snapshot()
        snapshot["raw"]["details"]["1"] = {"id": 1, "name": "Actor"}
        snapshot["raw"]["fallback_localized"]["1"] = {}
        snapshot["raw"]["fallback"]["1"].update(name="Actor", original_name="김배우")
        next(row for row in snapshot["raw"]["relations"] if row["credit_id"] == "first-role").pop("value_cn", None)
        snapshot["raw"]["wikipedia_people"] = {"people": {"1": {
            "name_cn": "張演員", "localized": {"name": {"zh-Hant": "張演員"}}}}, "relations": [
            {"person_id": 1, "credit_id": "first-role", "original_value": "왕갑", "character_name_cn": "张部长",
             "localized": {"name": {"ko": "왕갑", "zh-Hans": "张部长", "zh-Hant": "張部長"}}}]}
        updated, entities, _ = tmdb_people.build(snapshot, "fixture", self.record, self.repo)
        actor = next(row for row in entities if row["id"] == source_identity("tmdb", "person", 1))
        self.assertEqual(actor["localized"]["name"]["zh-Hant"], "張演員")
        self.assertNotIn("zh-Hans", actor["localized"]["name"])
        relation = next(row for row in updated["people_refs"] if row.get("credit_id") == "first-role")
        character = next(row for row in entities if row["id"] == relation["character_id"])
        self.assertEqual(character["localized"]["name"]["zh-Hans"], "张部长")
        self.assertEqual(character["localized"]["name"]["zh-Hant"], "張部長")

    def test_optional_original_language_failure_keeps_chinese_credit_fallback(self):
        def request(path, **kwargs):
            if kwargs.get("query", {}).get("language") == "ko":
                raise ValueError("TMDB 连接失败或超时")
            return self.request(path, **kwargs)
        with patch.object(tmdb_source, "_request_json", side_effect=request):
            snapshot = tmdb_people.fetch_snapshot(100, season_number=1, season_data=self.season, original_language="ko")
        cast = next(row for row in snapshot["raw"]["relations"] if row["category"] == "cast")
        self.assertEqual((cast["value"], cast["value_cn"]), ("男主角", "男主角"))
        self.assertIs(cast["original_available"], False)
        self.assertTrue(any("原语言" in warning for warning in snapshot["warnings"]))

    def test_bilingual_preview_and_apply_share_hashed_snapshot_without_network(self):
        from catalog_library import wikipedia_people
        snapshot = self.bilingual_snapshot()
        next(row for row in snapshot["raw"]["relations"] if row["credit_id"] == "second-role").pop("value_cn")
        def enrich(value, record, **kwargs):
            value["raw"]["wikipedia_people"] = {"people": {}, "relations": [
                {"person_id": 1, "credit_id": "second-role", "original_value": "왕을", "character_name_cn": "维基王乙"}]}
        with patch.object(tmdb_people, "fetch_snapshot", return_value=snapshot), patch.object(wikipedia_people, "enrich", side_effect=enrich):
            preview = self.preview()
        with patch.object(tmdb_source, "_request_json", side_effect=AssertionError("offline apply")), \
             patch.object(wikipedia_people, "enrich", side_effect=AssertionError("offline wiki apply")):
            applied = self.apply(preview)
        self.assertEqual(applied["people"], people.project(self.current(), people_root=self.people_root))
        names = {row["character_name"]: row["character_name_cn"] for row in applied["people"]["cast"]}
        self.assertEqual(names, {"왕갑": "王甲", "왕을": "维基王乙"})

    def test_bound_wikipedia_lists_enrich_preview_and_apply_bilingual_refs_entirely_offline(self):
        from catalog_library import korean_people_source, wikipedia_source

        self.record["source_refs"].append({"provider": "wikipedia", "external_id": "777", "enabled": True})
        self.write_record()
        self.credits = {"id": 1001, "cast": [{"id": 1, "name": "Kim Actor", "original_name": "김배우", "roles": [
            {"credit_id": "wiki-role", "character": "Wang Gap", "episode_count": 2}]}], "crew": []}
        self.details[1] = {"id": 1, "name": "Kim Actor", "also_known_as": ["김배우"]}

        def article_payload(pageid, title, text):
            return {"query": {"pages": [{"pageid": pageid, "ns": 0, "title": title,
                "pageprops": {"wikibase_item": "Q777"},
                "revisions": [{"revid": pageid + 1000, "slots": {"main": {"content": text}}}]}]}}

        chinese_payload = article_payload(777, "测试作品", "== 演員陣容 ==\n* 金演员（김배우）飾演王甲")
        korean_article = {"pageid": 888, "title": "시험 작품", "revid": 1888, "language": "ko",
            "url": "https://ko.wikipedia.org/wiki/시험_작품", "revision_url": "https://ko.wikipedia.org/w/index.php?oldid=1888",
            "license": "CC BY-SA 4.0", "license_url": wikipedia_source.LICENSE_URL, "attribution": "한국어 위키백과 기여자",
            "work_identity": {"zh_pageid": 777, "wikidata_item": "Q777"},
            "raw": article_payload(888, "시험 작품", "== 등장인물 ==\n* [[김배우]] : 왕갑 역 - 작품의 주인공")}

        def tmdb_request(path, **kwargs):
            if path == "/tv/100":
                return {"id": 100, "name": "测试作品", "original_language": "ko"}
            return self.request(path, **kwargs)

        def wiki_request(query, **kwargs):
            if kwargs.get("action") == "parse":
                return {"parse": {"text": query["text"]}}
            self.assertEqual(query["pageids"], 777)
            self.assertTrue(kwargs["deadline"])
            return deepcopy(chinese_payload)

        before = self.source.read_bytes()
        with patch.object(tmdb_source, "_request_json", side_effect=tmdb_request), \
             patch.object(wikipedia_source, "_request_json", side_effect=wiki_request) as wiki_fetch, \
             patch.object(korean_people_source, "fetch_for_work", return_value=[korean_article]) as korean_fetch:
            # Exercise the real preview -> enrich -> parser -> build chain;
            # only remote reads are mocked, never the enrichment itself.
            preview = people.preview({"ref": self.ref(), "provider": "tmdb", "subject_id": 100, "season_number": 1},
                                     settings=self.settings, data_root=self.library, people_root=self.people_root)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse((self.people_root / "db").exists())
        self.assertEqual(wiki_fetch.call_count, 3)
        # Role display conversion is prioritized before optional actor work;
        # the final conversion covers additional actor names, not a re-fetch.
        self.assertEqual(sum(call.kwargs.get("action") == "parse" for call in wiki_fetch.call_args_list), 2)
        self.assertEqual(wiki_fetch.call_args_list[-1].kwargs["action"], "parse")
        korean_fetch.assert_called_once()
        self.assertEqual(korean_fetch.call_args.args[0]["pageid"], 777)
        self.assertEqual(len(list((self.people_root / "sources" / "tmdb").glob("*.json"))), 1)
        actor = cast(preview)[0]
        self.assertEqual((actor["name_cn"], actor["character_name"], actor["character_name_cn"]), ("金演员", "왕갑", "王甲"))

        with patch.object(tmdb_source, "_request_json", side_effect=AssertionError("offline TMDB apply")), \
             patch.object(wikipedia_source, "_request_json", side_effect=AssertionError("offline Wikipedia apply")), \
             patch.object(korean_people_source, "fetch_for_work", side_effect=AssertionError("offline Korean apply")), \
             patch.object(providers, "_request_json", side_effect=AssertionError("offline provider apply")), \
             patch("socket.socket.connect", side_effect=AssertionError("all network is disabled during apply/project")), \
             patch.object(service, "apply_edits", wraps=service.apply_edits) as writer:
            applied = self.apply(preview)
            projected = people.project(self.current(), people_root=self.people_root)
        writer.assert_called_once()
        self.assertEqual(applied["people"], people.project(self.current(), people_root=self.people_root))
        self.assertEqual(projected, applied["people"])
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 2)
        relation = self.current()["people_refs"][0]
        self.assertEqual(relation["credit_id"], "wiki-role")
        self.assertEqual((relation["character_name"], relation["character_name_cn"]), ("왕갑", "王甲"))
        self.assertEqual(relation["source_values"], {"character_name": "왕갑", "character_name_cn": "王甲"})
        self.assertEqual((projected["cast"][0]["character_name"], projected["cast"][0]["character_name_cn"]), ("왕갑", "王甲"))

    def test_old_snapshot_without_bilingual_metadata_builds_legacy_refs(self):
        snapshot = self.bilingual_snapshot()
        snapshot.pop("original_language")
        for key in ("credits_original", "fallback_localized", "subject"):
            snapshot["raw"].pop(key)
        for row in snapshot["raw"]["relations"]:
            for key in ("credit_id", "value_cn", "original_available"):
                row.pop(key, None)
        record, _, _ = tmdb_people.build(snapshot, "old-snapshot", self.record, self.repo)
        self.assertTrue(all(not set(ref) & {"credit_id", "character_name_cn", "source_values"} for ref in record["people_refs"]))

    def test_in_memory_wiki_binding_enriches_snapshot_without_replacing_original_proposal_record(self):
        from catalog_library import korean_people_source, wikipedia_people, wikipedia_source

        snapshot = self.bilingual_snapshot()
        snapshot["raw"]["relations"] = [next(row for row in snapshot["raw"]["relations"] if row["credit_id"] == "first-role")]
        snapshot["raw"]["relations"][0].pop("value_cn")
        item = providers._catalog_item(self.ref(), self.settings)
        original_item = deepcopy(item)
        supplement_record = deepcopy(item["record"])
        supplement_record["source_refs"].append({"provider": "wikipedia", "external_id": "777", "enabled": True})
        supplement_record["metadata"] = {"summary": "仅供来源选择的上下文，不应改变原作品提案"}
        original_context = deepcopy(supplement_record)
        article = {"pageid": 777, "title": "测试作品", "revid": 1777,
            "url": "https://zh.wikipedia.org/wiki/test", "revision_url": "https://zh.wikipedia.org/w/index.php?oldid=1777",
            "license": "CC BY-SA 4.0", "license_url": wikipedia_source.LICENSE_URL, "attribution": "中文维基百科贡献者",
            "raw": {"query": {"pages": [{"revisions": [{"slots": {"main": {"content":
                "== 演員陣容 ==\n* 金演员（김배우）飾演维基角色"}}}]}]}}}
        before = self.source.read_bytes()
        with patch.object(tmdb_people, "fetch_snapshot", side_effect=lambda *args, **kwargs: deepcopy(snapshot)), \
             patch.object(wikipedia_people, "_article", return_value=article) as wiki_fetch, \
             patch.object(korean_people_source, "fetch_for_work", return_value=[]):
            ordinary = people.preview_for_import(item, 100, settings=self.settings, people_root=self.people_root,
                                                 provider="tmdb", season_number=1)
            self.assertFalse(cast(ordinary["preview"])[0].get("character_name_cn"))
            wiki_fetch.assert_not_called()
            enriched = people.preview_for_import(item, 100, settings=self.settings, people_root=self.people_root,
                                                 provider="tmdb", season_number=1, supplement_record=supplement_record)
            wiki_fetch.assert_called_once()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse((self.people_root / "db").exists())
        self.assertEqual(item, original_item)
        self.assertEqual(supplement_record, original_context)
        self.assertEqual(enriched["proposal"]["provider"], "tmdb")
        self.assertEqual(enriched["preview"]["provider"], "tmdb")
        self.assertEqual(cast(enriched["preview"])[0]["character_name_cn"], "维基角色")
        snapshot_id = enriched["proposal"]["snapshot_id"]
        saved = providers._read_snapshot(snapshot_id, snapshot_id, self.people_root / "sources" / "tmdb", provider="tmdb")
        updated, entities, _ = people._build(saved, snapshot_id, item["record"], self.repo, report=False)
        from catalog_library import people_import
        display, _ = people_import.preview(saved, snapshot_id, item["record"], self.repo)
        self.assertEqual(people_import._hash(display), enriched["proposal"]["selection_plan_sha256"])
        self.assertEqual(updated["source_refs"], item["record"]["source_refs"])
        self.assertNotEqual(updated.get("metadata", {}), supplement_record["metadata"])
        final_record = deepcopy(item["record"])
        final_record["source_refs"] = deepcopy(supplement_record["source_refs"])
        enriched["proposal"] = people.with_supplement_selections([enriched["proposal"]], selections(enriched["preview"], self.repo))[0]
        with patch("socket.socket.connect", side_effect=AssertionError("confirmation is offline")), \
             patch.object(wikipedia_people, "_article", side_effect=AssertionError("no wiki during apply")):
            result = people.apply_with_work(enriched["proposal"], item, final_record, settings=self.settings,
                                            data_root=self.library, people_root=self.people_root)
        self.assertEqual(result["people"]["cast"][0]["character_name_cn"], "维基角色")
        self.assertEqual(self.current()["source_refs"], final_record["source_refs"])

    def test_empty_supplement_context_is_explicit_and_invalid_context_is_rejected(self):
        from catalog_library import wikipedia_people

        item = providers._catalog_item(self.ref(), self.settings)
        item["record"]["source_refs"].append({"provider": "wikipedia", "external_id": "777", "enabled": True})
        snapshot = self.bilingual_snapshot()
        with patch.object(tmdb_people, "fetch_snapshot", return_value=snapshot), \
             patch.object(wikipedia_people, "_article", side_effect=AssertionError("empty context must not use old bindings")):
            people.preview_for_import(item, 100, settings=self.settings, people_root=self.people_root,
                                      provider="tmdb", season_number=1, supplement_record={})
        with self.assertRaises(ValueError):
            people.preview_for_import(item, 100, settings=self.settings, people_root=self.people_root,
                                      provider="tmdb", season_number=1, supplement_record=[])

    def test_verified_wiki_original_can_fill_failed_tmdb_original_without_overwriting_manual_role(self):
        snapshot = self.bilingual_snapshot()
        relation = next(row for row in snapshot["raw"]["relations"] if row["credit_id"] == "first-role")
        relation["value"] = "Old Romanization"
        previous, entities, _ = tmdb_people.build(snapshot, "fixture", self.record, self.repo)
        for entity in entities:
            self.save_entity(entity)
        relation["original_available"] = False
        snapshot["raw"]["wikipedia_people"] = {"people": {}, "relations": [
            {"person_id": 1, "credit_id": "first-role", "original_value": "Old Romanization", "character_name": "왕갑"}]}
        updated, _, _ = tmdb_people.build(snapshot, "fixture2", previous, self.repo)
        self.assertEqual(next(row for row in updated["people_refs"] if row["credit_id"] == "first-role")["character_name"], "왕갑")
        next(row for row in previous["people_refs"] if row["credit_id"] == "first-role")["character_name"] = "Manual Role"
        updated, _, _ = tmdb_people.build(snapshot, "fixture3", previous, self.repo)
        self.assertEqual(next(row for row in updated["people_refs"] if row["credit_id"] == "first-role")["character_name"], "Manual Role")

    def test_tmdb_refresh_reuses_explicitly_merged_character_and_keeps_retired_file(self):
        self.apply(self.preview())
        record = self.current()
        actor_ref = next(row for row in record["people_refs"] if row.get("character_id"))
        donor = self.repo.read(actor_ref["character_id"])
        target = deepcopy(donor)
        target["id"] = source_identity("manual", "character", "chosen-target")
        target["name"] = "明确选择的现有角色"
        target["name_cn"] = "手工译名"
        self.save_entity({**target, "source_refs": []})
        retired = {**donor, "merged_into": target["id"], "source_refs": []}
        self.repo.apply_batch([target, retired], expected_revision=self.repo.revision([target["id"], donor["id"]]))
        self.record = record
        actor_ref["character_id"] = target["id"]
        self.write_record()
        self.apply(self.preview())
        current_ref = next(row for row in self.current()["people_refs"] if row.get("character_id"))
        self.assertEqual(current_ref["character_id"], target["id"])
        self.assertEqual(self.repo.read(donor["id"]), retired)
        self.assertEqual(self.repo.read(target["id"])["name_cn"], "手工译名")
        self.assertEqual(len(list((self.people_root / "db").glob("*.yaml"))), 4)

    def test_new_second_role_does_not_reuse_first_roles_existing_identity(self):
        self.apply(self.preview())
        existing = self.current()["people_refs"][0]["character_id"]
        self.credits["cast"][0]["roles"].append({"character": "第二角色", "episode_count": 1})
        preview = self.preview()
        roles = [row for row in preview["entries"] if row["entity_type"] == "character"]
        self.assertEqual(len(roles), 2)
        self.assertEqual(len({row["external_id"] for row in roles}), 2)
        self.assertEqual([row["identity_owner_id"] for row in roles].count(existing), 1)
        self.apply(preview)
        refs = [row for row in self.current()["people_refs"] if row.get("character_id")]
        self.assertEqual(len(refs), 2)
        self.assertEqual(len({row["character_id"] for row in refs}), 2)


if __name__ == "__main__":
    unittest.main()
