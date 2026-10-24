"""Removed remote adapters never erase existing local source evidence."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from catalog_library import douban_search, douban_source, model, people, people_library, providers, service, source_bindings, source_sync, web
from work_catalog_yaml.jp_tv.browse_app import build_jp_tv_browse_app
from work_catalog_yaml.yaml_io import dump_yaml_string
import test_people as people_fixtures
import test_people_library as library_fixtures
import test_source_sync as sync_fixtures
from test_ratings import rating


class RetiredSourceRequestsTest(unittest.TestCase):
    setUp = people_fixtures.CatalogPeopleTest.setUp
    ref = people_fixtures.CatalogPeopleTest.ref

    def test_douban_people_search_and_import_are_rejected_offline(self):
        before = self.source.read_bytes()
        requests = [
            (providers.search_provider, {"provider": "douban", "query": "https://www.douban.com/search?cat=1065&q=演员"}, {}),
            (people.preview, {"provider": "douban", "ref": self.ref(), "subject_id": 123}, {"settings": self.settings}),
            (people_library.source_preview, {"provider": "douban", "person_id": "unused", "external_id": "personage:7"}, {}),
        ]
        with patch("socket.socket.connect", side_effect=AssertionError("retired source network")), \
             patch.object(service, "apply_edits", side_effect=AssertionError("retired source write")):
            for handler, body, options in requests:
                with self.subTest(handler=handler.__name__, body=body), self.assertRaises(ValueError):
                    handler(body, **options)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse((self.people_root / "db").exists())

    def test_douban_person_routes_removed_while_rating_browser_remains(self):
        app = build_jp_tv_browse_app(operation_log_root=self.root / "logs")
        paths = []
        def collect(routes):
            for route in routes:
                paths.append(getattr(route, "path", ""))
                collect(getattr(route, "routes", []))
        collect(app.routes)
        self.assertFalse(any("douban-repair" in path or "/people/supplement/" in path for path in paths))
        for name in ("people_supplement_search", "people_supplement_preview", "people_supplement_apply",
                     "people_douban_repair_preview", "people_douban_repair_apply"):
            self.assertFalse(hasattr(web, name), name)
        for action in ("start", "step", "cancel"):
            self.assertTrue(hasattr(web, "douban_browser_" + action))
            self.assertTrue(any(path.endswith("/douban/browser/" + action) for path in paths))

    def test_read_only_policy_keeps_original_retired_binding_and_rating(self):
        record = {"source_refs": [{"provider": "douban", "external_id": "123", "enabled": True}],
                  "source_policy_config": {"custom": True, "targets": [{"provider": "douban", "role": "supplement"}]},
                  "metadata": {"ratings": {"douban": rating()}, "summary": "本地内容"}}
        before = deepcopy(record)
        self.assertEqual(model.validate_extensions(record), before)
        self.assertEqual(source_bindings.normalized_refs(record)[0]["provider"], "douban")
        self.assertEqual(source_bindings.source_policy(record)["targets"], [{"provider": "douban", "role": "supplement"}])
        self.assertEqual(record, before)


class RetiredSourceSyncTest(unittest.TestCase):
    setUp = sync_fixtures.SourceSyncTest.setUp
    ref = sync_fixtures.SourceSyncTest.ref
    current = sync_fixtures.SourceSyncTest.current
    save_sources = sync_fixtures.SourceSyncTest.save_sources
    preview = sync_fixtures.SourceSyncTest.preview
    apply = sync_fixtures.SourceSyncTest.apply

    def test_legacy_douban_preview_names_cannot_leak_into_aggregate_aliases(self):
        record = self.current()
        before = deepcopy(record)
        entry = {"binding": {"provider": "douban", "role": "primary", "binding_id": "legacy-douban"},
                 "preview": {"diff": [
                     {"field": "name", "after": "旧缓存作品名", "selectable": True},
                     {"field": "metadata.aliases", "after": ["旧缓存别名"], "selectable": True}]}}
        for chosen in (None, "all", "legacy-douban"):
            projected = source_sync._alias_projection(record, [entry], alias_binding=chosen)
            self.assertEqual(projected["after"], record["metadata"]["aliases"])
            self.assertEqual(projected["automatic"], [])
        self.assertEqual(source_sync._diff(record, [entry]), [])
        self.assertEqual(record, before)

    def rating_snapshot(self):
        from test_douban_source import html, movie
        value = movie(url=douban_source.source_url(123), name="来源不同作品名", description="不应导入的简介",
                      alternateName=["不应导入的别名"], aggregateRating={"ratingValue": 8.5, "ratingCount": 12})
        return douban_source.parse_html(html(value, canonical=douban_source.source_url(123)), 123)

    def test_rating_refresh_alongside_other_sources_preserves_existing_people(self):
        old = {"provider": "douban", "external_id": "123", "source_url": "https://movie.douban.com/subject/123/"}
        self.save_sources([*self.bindings, old])
        record = self.current()
        record["metadata"]["ratings"] = {"douban": rating()}
        snapshot = self.rating_snapshot()
        self.file.write_text(dump_yaml_string([record]), encoding="utf-8")
        before = self.file.read_bytes()
        with patch("socket.socket.connect", side_effect=AssertionError("only mocked active adapters allowed")), \
             patch.object(douban_source, "fetch_snapshot", return_value=snapshot):
            preview = self.preview()
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual({row["provider"] for row in preview["sources"]}, {"tmdb", "wikipedia", "douban"})
        self.assertTrue(all(row["status"] == "ok" for row in preview["sources"]))
        self.apply(preview, ["metadata.summary", "metadata.ratings.douban"])
        saved = self.current()
        retained = next(row for row in saved["source_refs"] if row["provider"] == "douban")
        self.assertTrue(all(retained[key] == value for key, value in old.items()))
        self.assertEqual(saved["metadata"]["ratings"]["douban"], snapshot["candidates"]["metadata.ratings.douban"])
        self.assertNotIn("来源不同作品名", saved["metadata"]["aliases"])
        self.assertNotIn("不应导入的别名", saved["metadata"]["aliases"])

    def test_rating_preview_and_save_cannot_import_any_other_fields(self):
        original = deepcopy(self.current())
        snapshot = self.rating_snapshot()
        before = self.file.read_bytes()
        with patch.object(douban_source, "fetch_snapshot", return_value=snapshot):
            preview = providers.preview_provider_import({"provider": "douban", "ref": self.ref(), "subject_id": 123},
                settings=self.settings, source_root=self.sources)
        self.assertEqual([row["field"] for row in preview["diff"]], ["metadata.ratings.douban"])
        self.assertEqual(preview["automatic_aliases"], [])
        self.assertIsNone(preview["people_preview"])
        self.assertEqual(self.file.read_bytes(), before)
        body = {"provider": "douban", "ref": preview["ref"], "snapshot_id": preview["snapshot_id"],
                "preview_token": preview["preview_token"]}
        for field in ("name", "metadata.summary", "metadata.aliases", "metadata.episodes", "metadata.cover", "air_date_start"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "仅允许更新评分"):
                providers.apply_provider_import({**body, "selected_fields": [field]}, settings=self.settings, source_root=self.sources)
        with patch("socket.socket.connect", side_effect=AssertionError("confirmation must be offline")):
            providers.apply_provider_import({**body, "selected_fields": ["metadata.ratings.douban"]},
                settings=self.settings, source_root=self.sources)
        saved = self.current()
        self.assertEqual(saved["attributes"], original["attributes"])
        for field in ("aliases", "summary", "episodes", "cover"):
            self.assertEqual(saved["metadata"].get(field), original["metadata"].get(field))
        self.assertEqual(saved.get("people_refs"), original.get("people_refs"))
        self.assertEqual(saved["metadata"]["ratings"]["douban"]["score"], 8.5)

    def test_zero_source_rating_is_unavailable_and_preserves_existing_rating(self):
        from test_douban_source import html, movie
        record = self.current()
        record["metadata"]["ratings"] = {"douban": rating()}
        self.file.write_text(dump_yaml_string([record]), encoding="utf-8")
        zero = movie(url=douban_source.source_url(123), aggregateRating={"ratingValue": 0, "ratingCount": 0})
        snapshot = douban_source.parse_html(html(zero, canonical=douban_source.source_url(123)), 123)
        self.assertNotIn("metadata.ratings.douban", snapshot["candidates"])
        with patch.object(douban_source, "fetch_snapshot", return_value=snapshot):
            preview = providers.preview_provider_import({"provider": "douban", "ref": self.ref(), "subject_id": 123},
                settings=self.settings, source_root=self.sources)
        self.assertFalse(preview["diff"][0]["selectable"])
        body = {"provider": "douban", "ref": preview["ref"], "snapshot_id": preview["snapshot_id"],
                "preview_token": preview["preview_token"]}
        with self.assertRaisesRegex(ValueError, "为空或无效"):
            providers.apply_provider_import({**body, "selected_fields": ["metadata.ratings.douban"]},
                settings=self.settings, source_root=self.sources)
        providers.apply_provider_import({**body, "selected_fields": []}, settings=self.settings, source_root=self.sources)
        self.assertEqual(self.current()["metadata"]["ratings"]["douban"], rating())

    def test_rating_only_refresh_never_adds_empty_aliases_or_follows_old_primary_role(self):
        self.save_sources([{"provider": "douban", "external_id": "123", "role": "primary"}])
        record = self.current()
        record["metadata"].pop("aliases", None)
        record["source_policy_config"] = {"custom": True, "targets": [{"provider": "douban", "role": "primary"}]}
        self.file.write_text(dump_yaml_string([record]), encoding="utf-8")
        self.assertEqual(source_bindings.source_policy(record)["targets"], [{"provider": "douban", "role": "supplement"}])
        with patch.object(douban_source, "fetch_snapshot", return_value=self.rating_snapshot()), \
             patch("socket.socket.connect", side_effect=AssertionError("mocked source only")):
            preview = source_sync.preview({"ref": self.ref()}, settings=self.settings, source_root=self.sources)
        self.apply(preview, ["metadata.ratings.douban"])
        self.assertNotIn("aliases", self.current()["metadata"])
        self.assertEqual(self.current()["source_refs"][0]["role"], "supplement")

    def test_internal_browser_snapshot_is_work_scoped_and_still_rating_only(self):
        snapshot = self.rating_snapshot()
        snapshot["source_metadata"]["evidence_kind"] = "browser_page"
        snapshot["candidates"].update({"name": "不可采用的名字", "metadata.summary": "不可采用的详情", "metadata.aliases": ["不可采用的别名"]})
        body = {"provider": "douban", "ref": self.ref(), "subject_id": 123}
        with patch("socket.socket.connect", side_effect=AssertionError("browser snapshot is already local")):
            result = providers.preview_provider_import(body, settings=self.settings, source_root=self.sources, _source_snapshot=snapshot)
            self.assertEqual([row["field"] for row in result["diff"]], ["metadata.ratings.douban"])
            self.assertEqual(result["automatic_aliases"], [])
            for changed in ({"kind": "person"}, {"subject_id": 999}, {"source_url": "https://movie.douban.com/subject/999/"}, {"provider": "tmdb"}):
                with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "评分快照"):
                    providers.preview_provider_import(body, settings=self.settings, source_root=self.sources, _source_snapshot={**snapshot, **changed})


class RetiredPersonDataTest(unittest.TestCase):
    setUp = library_fixtures.PeopleLibraryTest.setUp
    save_entity = library_fixtures.PeopleLibraryTest.save_entity
    detail = library_fixtures.PeopleLibraryTest.detail
    preview = library_fixtures.PeopleLibraryTest.preview
    apply = library_fixtures.PeopleLibraryTest.apply

    def test_existing_person_and_avatar_reference_remain_editable_offline(self):
        legacy = {"provider": "douban", "entity_type": "person", "external_id": "personage:7",
                  "source_url": "https://www.douban.com/personage/7/"}
        self.person["source_refs"].append(legacy)
        self.person["avatar"] = {"asset_id": "a" * 64 + ".jpg", "source_url": "https://img1.doubanio.com/view/personage/raw/public/p1.jpg"}
        self.save_entity(self.person)
        detail = self.detail()
        self.assertIn(legacy, detail["person"]["source_refs"])
        self.assertEqual(people_library.browse({"provider": "douban"}, **self.kw)["total"], 1)
        preview = self.preview(changes={"summary": "用户编辑后的简介"})
        self.apply(preview, ["summary"])
        saved = self.repo.read(self.identity)
        self.assertEqual(saved["summary"], "用户编辑后的简介")
        self.assertIn(legacy, saved["source_refs"])
        self.assertEqual(saved["avatar"], self.person["avatar"])


if __name__ == "__main__":
    unittest.main()
