"""Signed parent preview retries never refetch successes or write databases."""
import time
import unittest
from unittest.mock import patch

from catalog_library import people, people_import, people_retry, providers, service, source_sync, tmdb_source
from people_catalog.model import source_identity
from people_catalog.repository import PeopleRepository
from people_import_test_helpers import selections
import test_tmdb_people as fixture


class PeopleRetryTest(unittest.TestCase):
    setUp = fixture.TmdbPeopleTest.setUp
    ref = fixture.TmdbPeopleTest.ref
    current = fixture.TmdbPeopleTest.current
    write_record = fixture.TmdbPeopleTest.write_record
    request = fixture.TmdbPeopleTest.request
    save_entity = fixture.TmdbPeopleTest.save_entity

    def preview(self, mode="people"):
        def failing(path, **kwargs):
            if path == "/person/1":
                raise ValueError("TLS handshake failed")
            return self.request(path, **kwargs)
        body = {"ref": self.ref(), "provider": "tmdb", "subject_id": 100, "season_number": 1}
        with patch.object(tmdb_source, "_request_json", side_effect=failing), patch.object(tmdb_source, "acquire_cover", return_value={}):
            if mode == "people":
                return people.preview(body, settings=self.settings, people_root=self.people_root)
            if mode == "work":
                return providers.preview_provider_import(body, settings=self.settings, people_root=self.people_root,
                    source_root=self.root / "sources", assets_root=self.root / "assets")
            return source_sync.preview({"ref": body["ref"]}, settings=self.settings, people_root=self.people_root,
                source_root=self.root / "sources", assets_root=self.root / "assets")

    def child(self, preview):
        display = preview.get("people_preview", preview)
        return display["sources"][0] if "sources" in display else display

    def retry(self, preview, **changes):
        child = self.child(preview)
        return people_retry.retry({"ref": preview["ref"], "preview_token": preview["preview_token"],
            "snapshot_id": child["snapshot_id"], "failure_ids": [child["retry_failures"][0]["id"]], **changes},
            settings=self.settings, people_root=self.people_root, source_root=self.root / "sources")

    def test_all_three_parents_reissue_coherent_previews_without_database_writes(self):
        for mode in ("people", "work", "sync"):
            with self.subTest(mode=mode):
                preview = self.preview(mode)
                child = self.child(preview)
                before, old_id = self.source.read_bytes(), child["snapshot_id"]
                old_path = self.people_root / "sources" / "tmdb" / (old_id + ".json")
                old_bytes = old_path.read_bytes()
                calls = []
                def requested(path, **kwargs):
                    calls.append(path)
                    return self.request(path, **kwargs)
                with patch.object(tmdb_source, "_request_json", side_effect=requested), \
                        patch.object(service, "apply_edits", side_effect=AssertionError("no work write")), \
                        patch.object(PeopleRepository, "apply_batch", side_effect=AssertionError("no entity write")):
                    result = self.retry(preview)
                self.assertEqual(calls, ["/person/1"])
                self.assertEqual(self.source.read_bytes(), before)
                self.assertEqual(old_path.read_bytes(), old_bytes)
                self.assertFalse((self.people_root / "db").exists())
                updated = {**preview, **result["preview_patch"]}
                new_child = self.child(updated)
                self.assertFalse(new_child["retry_failures"])
                self.assertEqual(result["snapshot_replacements"], {old_id: new_child["snapshot_id"]})
                self.assertFalse(any("TLS handshake" in warning for warning in new_child["warnings"]))
                actor = next(row for row in new_child["entries"] if row["external_id"] == "person:1")
                self.assertEqual(actor["candidate"]["summary"], "演员介绍")
                # Confirmation validates the whole newly signed parent, not just
                # its display. Skip every person here to avoid fixture DB writes.
                choices = {new_child["snapshot_id"]: []}
                with patch.object(tmdb_source, "_request_json", side_effect=AssertionError("confirmation offline")):
                    if mode == "people":
                        prepared = providers._verify_token(updated["preview_token"])
                        people_retry._display(prepared, service._records(self.settings)[0],
                            people_import._cached_repository(self.repo), self.settings)
                    elif mode == "work":
                        providers.apply_provider_import({"provider": "tmdb", "ref": updated["ref"],
                            "preview_token": updated["preview_token"], "snapshot_id": updated["snapshot_id"],
                            "selected_fields": [], "people_selections": choices}, settings=self.settings,
                            people_root=self.people_root, source_root=self.root / "sources", _prepare_only=True)
                    else:
                        payload = providers._verify_token(updated["preview_token"])
                        cached = providers._read_snapshot(payload["snapshot_id"], payload["snapshot_sha256"],
                            source_sync._root(self.root / "sources"))
                        proposal = providers._verify_token(cached["entries"][0]["preview"]["preview_token"])["people_proposal"]
                        people_retry._display(proposal, service._records(self.settings)[0],
                            people_import._cached_repository(self.repo), self.settings)

    def test_invalid_scope_snapshot_token_and_failure_ids_never_request_network(self):
        preview = self.preview()
        invalid_token = providers._verify_token(preview["preview_token"])
        invalid_token["expires"] = int(time.time()) - 1
        for changes in ({"snapshot_id": "0" * 64}, {"failure_ids": []}, {"failure_ids": ["person-detail:999"]},
                        {"preview_token": providers._sign(invalid_token)}, {"ref": {}}):
            with self.subTest(changes=changes), patch.object(tmdb_source, "_request_json") as request, self.assertRaises(ValueError):
                self.retry(preview, **changes)
            request.assert_not_called()

    def test_two_sources_rebase_later_preview_without_refetching_its_failed_items(self):
        self.record["source_refs"].append({"provider": "tmdb", "external_id": "101", "season_number": 1,
            "scope": "season:1", "role": "supplement"})
        self.write_record()
        original_request = self.request
        def second_source(path, **kwargs):
            result = original_request(path.replace("/tv/101", "/tv/100"), **kwargs)
            if path == "/tv/101":
                result["id"] = 101
            if path == "/tv/101/season/1":
                for row in result.get("episodes", []):
                    row["show_id"] = 101
            return result
        self.request = second_source
        preview = self.preview("sync")
        before_children = preview["people_preview"]["sources"]
        self.assertEqual(len(before_children), 2)
        with patch.object(tmdb_source, "_request_json", side_effect=self.request) as request:
            response = self.retry(preview)
        self.assertEqual(request.call_count, 1)
        updated = response["preview_patch"]
        children = updated["people_preview"]["sources"]
        self.assertEqual(children[1]["snapshot_id"], before_children[1]["snapshot_id"])
        self.assertTrue(children[1]["retry_failures"])
        self.assertNotEqual(children[1]["selection_token"], before_children[1]["selection_token"])
        self.assertFalse(children[0]["retry_failures"])
        payload = providers._verify_token(updated["preview_token"])
        cached = providers._read_snapshot(payload["snapshot_id"], payload["snapshot_sha256"], source_sync._root(self.root / "sources"))
        for entry in cached["entries"]:
            proposal = providers._verify_token(entry["preview"]["preview_token"])["people_proposal"]
            people_retry._display(proposal, service._records(self.settings)[0], people_import._cached_repository(self.repo), self.settings)
        self.assertFalse((self.people_root / "db").exists())

    def test_changed_work_or_people_reject_before_network(self):
        preview = self.preview()
        self.record["metadata"] = {"summary": "concurrent edit"}
        self.write_record()
        with patch.object(tmdb_source, "_request_json") as request, self.assertRaises(ValueError):
            self.retry(preview)
        request.assert_not_called()
        preview = self.preview()
        self.save_entity({"id": source_identity("tmdb", "person", 999), "schema_version": 1, "type": "person", "name": "Local"})
        with patch.object(tmdb_source, "_request_json") as request, self.assertRaises(ValueError):
            self.retry(preview)
        request.assert_not_called()

    def test_concurrent_work_change_during_retry_rejects_new_plan(self):
        preview = self.preview()
        def changed(path, **kwargs):
            self.record["metadata"] = {"summary": "changed during request"}
            self.write_record()
            return self.request(path, **kwargs)
        with patch.object(tmdb_source, "_request_json", side_effect=changed), self.assertRaises(ValueError):
            self.retry(preview)
        self.assertFalse((self.people_root / "db").exists())

    def test_concurrent_people_change_during_retry_rejects_new_plan(self):
        preview = self.preview()
        def changed(path, **kwargs):
            self.save_entity({"id": source_identity("tmdb", "person", 999), "schema_version": 1,
                "type": "person", "name": "Concurrent local entry"})
            return self.request(path, **kwargs)
        with patch.object(tmdb_source, "_request_json", side_effect=changed), self.assertRaisesRegex(ValueError, "人物库在重试期间"):
            self.retry(preview)
        self.assertIsNone(self.repo.read(source_identity("tmdb", "person", 1)))

    def test_repeated_partial_failure_remains_retryable_without_refetching_success(self):
        preview = self.preview()
        with patch.object(tmdb_source, "_request_json", side_effect=ValueError("second failure")) as request:
            result = self.retry(preview)
        self.assertEqual(request.call_count, 1)
        updated = {**preview, **result["preview_patch"]}
        self.assertEqual(len(updated["retry_failures"]), 1)
        with patch.object(tmdb_source, "_request_json", side_effect=self.request) as request:
            finished = self.retry(updated)
        self.assertEqual(request.call_count, 1)
        self.assertFalse(finished["preview_patch"]["retry_failures"])

    def confirm_retried(self, mode):
        preview = self.preview(mode)
        with patch.object(tmdb_source, "_request_json", side_effect=self.request):
            response = self.retry(preview)
        updated = {**preview, **response["preview_patch"]}
        body = {"ref": updated["ref"], "preview_token": updated["preview_token"],
            "people_selections": selections(updated, self.repo)}
        with patch.object(tmdb_source, "_request_json", side_effect=AssertionError("confirmation must be offline")):
            if mode == "people":
                result = people.apply(body, settings=self.settings, people_root=self.people_root, data_root=self.library)
            elif mode == "work":
                result = providers.apply_provider_import({**body, "provider": "tmdb", "snapshot_id": updated["snapshot_id"],
                    "selected_fields": []}, settings=self.settings, people_root=self.people_root,
                    source_root=self.root / "sources", data_root=self.library)
            else:
                result = source_sync.apply({**body, "selected_fields": []}, settings=self.settings,
                    people_root=self.people_root, source_root=self.root / "sources", data_root=self.library)
        self.assertTrue(result["db_committed"])
        self.assertEqual(self.repo.read(source_identity("tmdb", "person", 1))["summary"], "演员介绍")

    def test_retried_independent_people_confirmation_is_offline(self):
        self.confirm_retried("people")

    def test_retried_work_confirmation_is_offline(self):
        self.confirm_retried("work")

    def test_retried_batch_confirmation_is_offline(self):
        self.confirm_retried("sync")
