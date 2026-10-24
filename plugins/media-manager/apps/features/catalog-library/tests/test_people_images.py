"""Manual image selection only modifies temp people records after confirmation."""
import base64
from copy import deepcopy
import unittest
from unittest.mock import patch

from catalog_library import assets, people, people_assets, people_images, people_image_sources, people_library, tmdb_source
from people_catalog.model import source_identity, validate_avatar_reference
import test_people_assets as asset_fixtures
import test_people_library as fixtures


class PeopleImagesTest(unittest.TestCase):
    setUp = fixtures.PeopleLibraryTest.setUp
    save_entity = fixtures.PeopleLibraryTest.save_entity
    detail = fixtures.PeopleLibraryTest.detail
    preview = fixtures.PeopleLibraryTest.preview
    apply = fixtures.PeopleLibraryTest.apply
    bytes = fixtures.PeopleLibraryTest.bytes

    def upload_body(self, **overrides):
        return {"provider": "local", "file_name": "角色.png", "mime_type": "image/png",
                "image_base64": base64.b64encode(asset_fixtures.image_bytes()).decode("ascii"), **overrides}

    def image_preview(self, **overrides):
        return self.preview("image", **self.upload_body(**overrides))

    def test_upload_preview_is_readonly_and_confirm_only_updates_avatar_and_evidence(self):
        before = self.bytes()
        preview = self.image_preview()
        self.assertEqual(self.bytes(), before)
        self.assertEqual(preview["mode"], "people-library-image")
        self.assertEqual(preview["selected_fields"], ["avatar"])
        self.assertTrue(preview["candidate_avatar_url"].startswith("/api/catalog-library/people/assets/"))
        self.assertEqual(preview["image_source"]["provider"], "local")
        self.assertEqual(len(list((self.people_root / "assets").glob("*.png"))), 1)
        result = self.apply(preview)
        saved = self.repo.read(self.identity)
        self.assertTrue(result["people_updated"])
        self.assertEqual({key: value for key, value in saved.items() if key not in {"avatar", "field_sources"}},
                         {key: value for key, value in self.person.items() if key not in {"avatar", "field_sources"}})
        self.assertEqual(saved["avatar"]["source_url"], "local:" + saved["avatar"]["asset_id"])
        self.assertEqual(saved["field_sources"]["avatar"]["provider"], "manual")
        self.assertEqual(result["person"]["avatar_image_source"]["file_name"], "角色.png")
        self.assertEqual(self.source.read_bytes(), before[0])
        self.assertEqual(self.apply(self.image_preview(), [])["people_updated"], False)

    def test_cancel_and_deselect_leave_all_db_bytes_unchanged(self):
        before = self.bytes()
        preview = self.image_preview()
        self.assertEqual(self.bytes(), before)
        self.assertFalse(self.apply(preview, [])["people_updated"])
        self.assertEqual(self.bytes(), before)

    def test_invalid_uploads_reject_without_db_or_asset_writes(self):
        before = self.bytes()
        cases = [{"file_name": "../portrait.png"}, {"file_name": "C:\\portrait.png"}, {"file_name": "http://evil/image"},
                 {"mime_type": []}, {"mime_type": "image/svg+xml"}, {"mime_type": "image/jpeg"},
                 {"image_base64": "data:image/png;base64,AA=="}, {"image_base64": "%%%"},
                 {"image_base64": base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"/>').decode()},
                 {"image_base64": "A" * (((people_images.MAX_UPLOAD_BYTES + 2) // 3) * 4 + 4)},
                 {"image_base64": base64.b64encode(b'x' * (people_images.MAX_UPLOAD_BYTES + 1)).decode()}]
        for body in cases:
            with self.subTest(body={key: str(value)[:100] for key, value in body.items()}), self.assertRaises(ValueError):
                self.image_preview(**body)
        self.assertEqual(self.bytes(), before)
        self.assertFalse((self.people_root / "assets").exists())

    def test_image_token_is_bound_to_person_and_revision_and_cannot_use_edit_apply(self):
        preview = self.image_preview()
        with self.assertRaises(ValueError):
            people_library.edit_apply({"person_id": self.identity, "preview_token": preview["preview_token"], "selected_fields": ["avatar"]}, **self.kw)
        self.apply(self.preview(changes={"summary": "Edited during preview"}))
        before = self.bytes()
        with self.assertRaisesRegex(ValueError, "预览后已变化"):
            self.apply(preview)
        self.assertEqual(self.bytes(), before)

    def test_missing_or_corrupted_cached_asset_rejects_apply_without_db_changes(self):
        preview = self.image_preview()
        before = self.bytes()
        path = next((self.people_root / "assets").glob("*.png"))
        path.write_bytes(b"corrupt temp fixture")
        with self.assertRaisesRegex(ValueError, "校验失败"):
            self.apply(preview)
        self.assertEqual(self.bytes(), before)

    def test_remote_image_does_not_bind_new_entity_source_identity(self):
        info = assets.store_image(asset_fixtures.image_bytes(), "image/png", root=people_assets.asset_root(self.people_root))
        avatar = {"asset_id": info["sha256"] + ".png", "source_url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Portrait.png"}
        for provider in ("bangumi", "wikipedia", "commons"):
            with self.subTest(provider=provider):
                evidence = {"provider": provider, "external_id": "777", "entity_type": "person", "source_url": avatar["source_url"],
                            "title": "Portrait", "page_url": "https://commons.wikimedia.org/wiki/File:Portrait.png"}
                with patch.object(people_image_sources, "fetch_image", return_value={"avatar": avatar, "evidence": evidence, "warnings": []}) as fetch:
                    preview = self.preview("image", provider=provider, external_id="777")
                self.assertEqual(fetch.call_args.kwargs["entity_type"], "person")
                self.apply(preview)
                self.assertEqual(self.repo.read(self.identity)["source_refs"], self.person["source_refs"])

    def test_virtual_character_upload_does_not_copy_actor_picture_or_change_links(self):
        actor_id = self.identity
        self.identity = source_identity("manual", "character", "portrait-target")
        self.person.update(id=self.identity, type="character", kind="character", source_refs=[],
                           voice_actors=[{"person_id": actor_id,
                                          "scope": {"provider": "bangumi", "subject_id": "1"}, "role": "CV"}])
        self.save_entity(self.person)
        self.apply(self.image_preview())
        self.assertEqual(self.repo.read(self.identity)["voice_actors"], self.person["voice_actors"])

    def test_clear_avatar_removes_current_image_evidence_but_retains_cached_bytes(self):
        self.apply(self.image_preview())
        asset = self.repo.read(self.identity)["avatar"]["asset_id"]
        self.apply(self.preview("clear"), ["avatar"])
        saved = self.repo.read(self.identity)
        self.assertEqual(saved["avatar"], {})
        self.assertNotIn("image_source", saved["field_sources"]["avatar"])
        self.assertEqual(self.detail()["person"]["avatar_image_source"], {})
        self.assertTrue((self.people_root / "assets" / asset).is_file())

    def test_chosen_avatar_stays_manual_even_when_it_matches_old_import_baseline(self):
        self.apply(self.image_preview())
        saved = self.repo.read(self.identity)
        saved["source_values"]["bangumi"]["avatar"] = deepcopy(saved["avatar"])
        incoming = {key: deepcopy(saved.get(key, "")) for key in people._PROFILE_FIELDS}
        incoming.update(id=self.identity, type="person", avatar={"asset_id": "f" * 64 + ".png", "source_url": "https://lain.bgm.tv/pic/crt/m/other.png"},
                        field_sources={"avatar": {"provider": "bangumi", "external_id": "11"}})
        merged = people._merge_entity(saved, incoming, source={"provider": "bangumi", "entity_type": "person", "external_id": "11"})
        merged = people._merge_entity(merged, incoming, source={"provider": "bangumi", "entity_type": "person", "external_id": "11"})
        self.assertEqual(merged["avatar"], saved["avatar"])
        self.assertEqual(merged["field_sources"]["avatar"], saved["field_sources"]["avatar"])

    def test_local_reference_model_is_exact_not_a_filesystem_or_url_fetch_instruction(self):
        reference = {"asset_id": "a" * 64 + ".png", "source_url": "local:" + "a" * 64 + ".png"}
        self.assertEqual(validate_avatar_reference(reference), reference)
        for value in ("local:../x.png", "local:" + "b" * 64 + ".png", "file:///C:/x.png", "data:image/png;base64,AA=="):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_avatar_reference({**reference, "source_url": value})

    def candidate_list(self, **overrides):
        body = {"person_id": self.identity, "version": self.detail()["version"], "provider": "tmdb",
                "external_id": "259837", **overrides}
        raw = {"id": 259837, "backdrops": [{"file_path": "/Still_1.jpg", "width": 1920, "height": 1080}]}
        with patch.object(tmdb_source, "_request_json", return_value=raw), \
             patch.object(assets, "download_image", return_value=(asset_fixtures.image_bytes(), "image/png")):
            return people_library.image_candidates(body, **self.kw)

    def test_tmdb_candidates_are_person_bound_and_apply_uses_only_signed_cached_choice(self):
        before = self.bytes()
        rows = self.candidate_list()
        self.assertEqual(self.bytes(), before)
        candidate = rows["candidates"][0]
        self.assertTrue(candidate["candidate_token"])
        with patch.object(assets, "download_image", side_effect=AssertionError("already cached")):
            preview = self.preview("image", provider="tmdb-still", candidate_token=candidate["candidate_token"])
        self.assertEqual(preview["image_source"]["provider"], "tmdb-still")
        self.assertEqual(preview["image_source"]["external_id"], "tv:259837")
        self.assertTrue(preview["warnings"])
        self.apply(preview)
        self.assertEqual(self.repo.read(self.identity)["source_refs"], self.person["source_refs"])
        self.assertEqual(self.source.read_bytes(), before[0])

    def test_tmdb_candidate_token_rejects_person_revision_and_scope_changes(self):
        candidate = self.candidate_list()["candidates"][0]
        other = {**self.person, "id": source_identity("manual", "person", "another-image-target"), "source_refs": []}
        self.save_entity(other)
        with patch.object(assets, "download_image", side_effect=AssertionError("must reject before download")):
            with self.assertRaisesRegex(ValueError, "当前人物"):
                people_library.image_preview({"person_id": other["id"], "version": self.detail(other["id"])["version"],
                    "provider": "tmdb-still", "candidate_token": candidate["candidate_token"]}, **self.kw)
            self.apply(self.preview(changes={"summary": "changed since image candidates"}))
            with self.assertRaisesRegex(ValueError, "资料已变化"):
                self.preview("image", provider="tmdb-still", candidate_token=candidate["candidate_token"])

    def test_unsigned_tmdb_paths_and_failed_candidates_cannot_be_chosen(self):
        with self.assertRaises(ValueError):
            self.preview("image", provider="tmdb-still", file_path="/Still_1.jpg")
        with self.assertRaises(ValueError):
            self.preview("image", provider="tmdb-still", candidate_token="forged")
        from catalog_library import people_image_candidates
        failed = {"candidates": [{"file_path": "/bad.jpg", "selectable": False, "thumbnail_url": ""}],
                  "warnings": ["cache failed"], "page": 1, "page_size": 8, "total": 1, "has_more": False}
        with patch.object(people_image_candidates, "fetch_candidates", return_value=failed):
            result = people_library.image_candidates({"person_id": self.identity, "version": self.detail()["version"],
                "provider": "tmdb", "external_id": "259837"}, **self.kw)
        self.assertEqual(result["candidates"][0]["candidate_token"], "")


if __name__ == "__main__":
    unittest.main()
