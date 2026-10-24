from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from catalog_library import assets, people, people_assets, people_portraits, providers
from people_catalog.model import source_identity
from people_catalog.repository import PeopleRepository
from work_catalog_yaml.common.operation_logs import OperationLogStore
from work_catalog_yaml.operation_progress import OperationRegistry, execute_operation
import test_people as fixtures
from test_people_assets import SOURCE, image_bytes


class PortraitPreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "people"
        self.repo = PeopleRepository(self.root)
        self.data = image_bytes()
        self.snapshot = {"raw": {"characters": [], "persons": [], "details": {}}}
        self.warnings = []

    def prepare(self):
        people_portraits.prepare(self.snapshot, self.repo, lambda *args: self.warnings.append(args))

    def test_empty_images_do_not_start_network_workers_or_create_directories(self):
        self.snapshot["raw"]["characters"] = [{"id": 1, "actors": [{"id": 2}]}]
        with patch.object(people_portraits, "ThreadPoolExecutor", side_effect=AssertionError("no jobs")):
            self.prepare()
        self.assertEqual(self.snapshot["avatars"], {"character:1": None, "person:2": None})
        self.assertFalse(self.root.exists())

    def test_duplicate_urls_download_once_across_character_actor_and_staff(self):
        raw = self.snapshot["raw"]
        raw["characters"] = [{"id": 1, "images": {"medium": SOURCE}, "actors": [{"id": 2, "images": {"medium": SOURCE}}]}]
        raw["persons"] = [{"id": 2, "images": {"medium": SOURCE}}]
        raw["details"] = {"character:1": {"id": 1}, "person:2": {"id": 2}}
        with patch.object(assets, "download_image", return_value=(self.data, "image/png")) as download:
            self.prepare()
        download.assert_called_once()
        self.assertEqual(self.snapshot["avatars"]["character:1"], self.snapshot["avatars"]["person:2"])
        self.assertEqual(len(list((self.root / "assets").iterdir())), 1)
        self.assertEqual(self.warnings, [])
        self.assertFalse((self.root / "db").exists())

    def test_actor_picture_fallback_survives_staff_row_without_picture(self):
        raw = self.snapshot["raw"]
        raw["characters"] = [{"id": 1, "actors": [{"id": 2, "images": {"medium": SOURCE}}]}]
        raw["persons"] = [{"id": 2, "relation": "导演"}]
        raw["details"] = {"character:1": {"id": 1}, "person:2": {"id": 2, "images": {}}}
        with patch.object(assets, "download_image", return_value=(self.data, "image/png")) as download:
            self.prepare()
        download.assert_called_once()
        self.assertIsNotNone(self.snapshot["avatars"]["person:2"])

    def test_any_existing_entity_in_same_url_group_can_supply_offline_cache(self):
        with patch.object(assets, "download_image", return_value=(self.data, "image/png")):
            avatar = people_assets.acquire_avatar({"images": {"medium": SOURCE}}, user_agent="test", root=self.root)
        identity = source_identity("bangumi", "person", 2)
        existing = {"id": identity, "schema_version": 1, "type": "person", "name": "Already cached", "avatar": avatar}
        self.repo.apply_batch([existing], expected_revision=self.repo.revision([identity]))
        raw = self.snapshot["raw"]
        raw["characters"] = [{"id": 1, "images": {"medium": SOURCE}, "actors": []}]
        raw["persons"] = [{"id": 2, "images": {"medium": SOURCE}}]
        with patch.object(assets, "download_image", side_effect=AssertionError("reuse another entity's local cache")):
            self.prepare()
        self.assertEqual(self.snapshot["avatars"]["character:1"], avatar)
        self.assertEqual(self.snapshot["avatars"]["person:2"], avatar)

    def test_empty_image_fields_cannot_mask_actor_picture_from_another_role(self):
        empty = {"large": "", "medium": "", "small": "", "grid": ""}
        raw = self.snapshot["raw"]
        raw["characters"] = [
            {"id": 1, "actors": [{"id": 2, "images": empty}]},
            {"id": 3, "actors": [{"id": 2, "images": {"medium": SOURCE}}]},
            {"id": 4, "actors": [{"id": 2, "images": empty}]},
        ]
        raw["persons"] = [{"id": 2, "images": empty}]
        raw["details"] = {"person:2": {"id": 2, "images": empty}}
        with patch.object(assets, "download_image", return_value=(self.data, "image/png")) as download:
            self.prepare()
        download.assert_called_once()
        self.assertIsNotNone(self.snapshot["avatars"]["person:2"])

    def test_malformed_image_field_is_diagnosed_instead_of_hidden_by_fallback(self):
        raw = self.snapshot["raw"]
        raw["persons"] = [{"id": 2, "images": {"medium": SOURCE}}]
        raw["details"] = {"person:2": {"id": 2, "images": ["invalid"]}}
        with patch.object(assets, "download_image", side_effect=AssertionError("no network")):
            self.prepare()
        self.assertEqual(self.snapshot["avatars"]["person:2"], None)
        self.assertTrue(self.warnings)

    def test_workers_have_common_deadline_bounded_pool_and_operation_context(self):
        self.snapshot["raw"]["persons"] = [{"id": identity, "images": {"medium": SOURCE.replace("12_Abc", str(identity))}}
                                           for identity in range(1, 21)]
        context = ContextVar("portrait-context", default="missing")
        token = context.set("active-operation")
        self.addCleanup(context.reset, token)
        deadlines = []

        def acquire(raw, **kwargs):
            self.assertEqual(context.get(), "active-operation")
            self.assertEqual(kwargs["root"], self.root)
            self.assertEqual(kwargs["user_agent"], providers.USER_AGENT)
            deadlines.append(kwargs["deadline"])
            return {"asset_id": "a" * 64 + ".png", "source_url": raw["images"]["medium"]}

        with patch.object(people_portraits, "ThreadPoolExecutor", wraps=ThreadPoolExecutor) as executor, \
             patch.object(people_portraits.time, "monotonic", return_value=1000), \
             patch.object(people_assets, "acquire_avatar", side_effect=acquire) as download:
            self.prepare()
        self.assertEqual(executor.call_args.kwargs["max_workers"], 4)
        self.assertEqual(download.call_count, 20)
        self.assertEqual(set(deadlines), {1000 + people_portraits.PORTRAIT_BUDGET})
        self.assertEqual(len(self.snapshot["avatars"]), 20)

    def test_invalid_url_and_one_failed_download_are_nonblocking_per_entity(self):
        self.snapshot["raw"]["persons"] = [
            {"id": 1, "images": {"medium": "https://example.invalid/private.jpg"}},
            {"id": 2, "images": {"medium": SOURCE}},
            {"id": 3, "images": {"medium": SOURCE.replace("12_Abc", "13_Abc")}},
        ]

        def acquire(raw, **kwargs):
            if raw["id"] == 2:
                raise ValueError("人物图片连接失败或超时")
            return {"asset_id": "a" * 64 + ".png", "source_url": raw["images"]["medium"]}

        with patch.object(people_assets, "acquire_avatar", side_effect=acquire) as download:
            self.prepare()
        self.assertEqual(download.call_count, 2)
        self.assertIsNone(self.snapshot["avatars"]["person:1"])
        self.assertIsNone(self.snapshot["avatars"]["person:2"])
        self.assertIsNotNone(self.snapshot["avatars"]["person:3"])
        self.assertEqual({row[0] for row in self.warnings}, {"person:1", "person:2"})
        self.assertTrue(all(row[1] == "avatar" for row in self.warnings))


class PortraitImportTest(unittest.TestCase):
    setUp = fixtures.CatalogPeopleTest.setUp
    ref = fixtures.CatalogPeopleTest.ref
    current = fixtures.CatalogPeopleTest.current
    request = fixtures.CatalogPeopleTest.request
    preview = fixtures.CatalogPeopleTest.preview
    apply = fixtures.CatalogPeopleTest.apply
    save_entity = fixtures.CatalogPeopleTest.save_entity

    def with_portrait(self):
        self.details["character:1"]["images"] = {"medium": SOURCE}
        with patch.object(assets, "download_image", return_value=(image_bytes(), "image/png")):
            return self.preview()

    def test_local_refs_enter_hashed_snapshot_and_apply_and_browse_stay_offline(self):
        before = self.source.read_bytes()
        preview = self.with_portrait()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse((self.people_root / "db").exists())
        path = self.people_root / "sources" / "bangumi" / (preview["snapshot_id"] + ".json")
        raw = path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), preview["snapshot_id"])
        snapshot = json.loads(raw)
        avatar = snapshot["avatars"]["character:1"]
        self.assertEqual(avatar["source_url"], SOURCE)
        self.assertTrue((self.people_root / "assets" / avatar["asset_id"]).is_file())
        with patch.object(providers, "_request_json", side_effect=AssertionError("apply is offline")), \
             patch.object(people_assets, "acquire_avatar", side_effect=AssertionError("apply cannot reacquire images")), \
             patch.object(assets, "build_opener", side_effect=AssertionError("views are offline")):
            result = self.apply(preview)
            projected = people.project(self.current(), people_root=self.people_root)
        self.assertEqual(result["people"], projected)
        self.assertEqual(projected["characters"][0]["avatar_url"], people_assets.avatar_url(avatar))
        serialized = json.dumps(projected)
        self.assertNotIn(SOURCE, serialized)
        self.assertNotIn('"images"', serialized)
        self.assertNotIn('"source_values"', serialized)
        stored = self.repo.read(source_identity("bangumi", "character", 1))
        self.assertEqual(stored["avatar"], avatar)
        self.assertEqual(stored["source_values"]["bangumi"]["avatar"], avatar)

    def test_missing_and_failed_portrait_refresh_keep_existing_and_update_other_fields(self):
        self.apply(self.with_portrait())
        identity = source_identity("bangumi", "character", 1)
        old = self.repo.read(identity)["avatar"]
        for pictures in (None, {"medium": SOURCE.replace("1720910081", "1720910082")}):
            with self.subTest(pictures=pictures):
                self.details["character:1"]["images"] = pictures
                self.details["character:1"]["summary"] = "Updated profile " + str(pictures)
                with patch.object(assets, "download_image", side_effect=ValueError("人物图片连接失败或超时")):
                    preview = self.preview()
                self.apply(preview)
                stored = self.repo.read(identity)
                self.assertEqual(stored["avatar"], old)
                self.assertEqual(stored["source_values"]["bangumi"]["avatar"], old)
                self.assertEqual(stored["localized"]["summary"]["und-Latn"], self.details["character:1"]["summary"])

    def test_manually_changed_portrait_is_not_replaced_by_source_refresh(self):
        self.apply(self.with_portrait())
        identity = source_identity("bangumi", "character", 1)
        row = self.repo.read(identity)
        row["avatar"]["source_url"] = SOURCE.replace("1720910081", "12345")
        manual = deepcopy(row["avatar"])
        self.save_entity(row)
        self.apply(self.with_portrait())
        stored = self.repo.read(identity)
        self.assertEqual(stored["avatar"], manual)
        self.assertEqual(stored["source_values"]["bangumi"]["avatar"]["source_url"], SOURCE)

    def test_malformed_image_url_logs_specific_warning_and_does_not_block_import(self):
        self.details["character:1"]["images"] = {"medium": "https://example.invalid/private.jpg"}
        logs = OperationLogStore(self.root / "logs")
        registry = OperationRegistry(log_store=logs)
        operation = registry.register(str(uuid4()), "人物图片预览")
        with patch.object(assets, "build_opener", side_effect=AssertionError("invalid URL must never fetch")):
            preview = execute_operation(registry, operation, self.preview)
        self.assertEqual(registry.snapshot(operation.id)["status"], "warning")
        log = logs.find(operation.id).read_text(encoding="utf-8")
        self.assertIn("character:1 / avatar", log)
        self.assertIn("CDN", log)
        self.assertNotIn("example.invalid", log)
        self.assertTrue(self.apply(preview)["ok"])
        self.assertEqual(self.repo.read(source_identity("bangumi", "character", 1))["avatar"], {})

    def test_tampered_avatar_reference_invalidates_snapshot_before_writes(self):
        preview = self.with_portrait()
        path = self.people_root / "sources" / "bangumi" / (preview["snapshot_id"] + ".json")
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        snapshot["avatars"]["character:1"]["asset_id"] = "a" * 64 + ".png"
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        before = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, "校验失败"):
            self.apply(preview)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse((self.people_root / "db").exists())


if __name__ == "__main__":
    unittest.main()
