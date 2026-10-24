"""Work detail clearing never edits independent people or actual media files."""
from copy import deepcopy
import time
import unittest
from unittest.mock import patch

from catalog_library import work_details, providers, service
from catalog_library.source_bindings import source_policy
from people_catalog.model import source_identity
from work_catalog_yaml.yaml_io import dump_yaml_string, load_yaml_string
import test_people as fixtures


class WorkDetailsTest(unittest.TestCase):
    ref = fixtures.CatalogPeopleTest.ref
    current = fixtures.CatalogPeopleTest.current
    save_entity = fixtures.CatalogPeopleTest.save_entity

    def setUp(self):
        fixtures.CatalogPeopleTest.setUp(self)
        self.identity = source_identity("bangumi", "person", "11")
        self.person = {"schema_version": 1, "id": self.identity, "type": "person", "name": "Shared Person", "summary": "Keep global profile"}
        self.save_entity(self.person)
        self.record["metadata"] = {"summary": "Clearable summary", "aliases": ["Alias"], "tags": ["Tag"],
            "episodes": [{"id": 1, "name": "Episode", "sort": 1}],
            "field_sources": {"metadata.summary": {"provider": "bangumi"}, "metadata.aliases": {"provider": "bangumi"}}}
        self.record["people_refs"] = [{"id": self.identity, "type": "person", "role": "演员", "character_name": "Hero"}]
        self.record["local_playback"] = {"version": 1, "resources": {}}
        self.media = self.root / "media"
        self.media.mkdir()
        self.video = self.media / "episode.mkv"
        self.video.write_bytes(b"synthetic media bytes never delete")
        self.record["attributes"][-1]["data"]["path"] = self.media.as_posix()
        self.write()

    def write(self, records=None):
        self.source.write_text(dump_yaml_string(records or [self.record]), encoding="utf-8")
        with service._CACHE_LOCK:
            service._CACHE.clear()

    def preview(self):
        with patch("socket.socket.connect", side_effect=AssertionError("offline")):
            return work_details.preview({"ref": self.ref()}, settings=self.settings, data_root=self.library)

    def apply(self, preview, groups):
        with patch("socket.socket.connect", side_effect=AssertionError("offline")):
            return work_details.apply({"ref": preview["ref"], "preview_token": preview["preview_token"], "groups": groups},
                settings=self.settings, data_root=self.library)

    def bytes(self):
        return self.source.read_bytes(), self.repo.path(self.identity).read_bytes(), self.video.read_bytes()

    def test_preview_is_read_only_and_no_groups_selected(self):
        before = self.bytes()
        preview = self.preview()
        self.assertTrue(all(not group["selected"] for group in preview["groups"]))
        self.assertEqual(self.bytes(), before)

    def test_description_only_preserves_work_attributes_people_and_media(self):
        attributes = deepcopy(self.record["attributes"])
        before = self.bytes()
        self.apply(self.preview(), ["description"])
        record = self.current()
        self.assertEqual(record["attributes"], attributes)
        self.assertEqual(record["people_refs"], self.record["people_refs"])
        self.assertEqual(record["metadata"]["episodes"], self.record["metadata"]["episodes"])
        self.assertNotIn("summary", record["metadata"])
        self.assertNotIn("metadata.summary", record["metadata"]["field_sources"])
        self.assertEqual(self.bytes()[1:], before[1:])
        self.assertTrue(list((self.db.parent / "History").glob("*.yaml")))

    def test_explicit_people_clear_unlinks_one_work_not_global_person_or_other_work(self):
        other = deepcopy(self.record)
        other["attributes"][0]["data"] = "Other Work"
        self.write([self.record, other])
        before = self.bytes()
        self.apply(self.preview(), ["people"])
        rows = load_yaml_string(self.source.read_text(encoding="utf-8"))
        self.assertEqual(rows[0]["people_refs"], [])
        self.assertEqual(rows[1], other)
        self.assertEqual(self.bytes()[1:], before[1:])

    def test_source_clear_keeps_explicit_empty_policy_and_other_metadata(self):
        self.apply(self.preview(), ["sources"])
        record = self.current()
        self.assertEqual(record["source_refs"], [])
        self.assertEqual(record["source_policy_config"], {"custom": True, "targets": []})
        self.assertEqual(source_policy(record)["targets"], [])
        self.assertEqual(record["metadata"]["summary"], self.record["metadata"]["summary"])
        self.assertNotIn("field_sources", record["metadata"])

    def test_chapters_clear_removes_playback_cache_not_resources_or_media(self):
        before = self.bytes()
        attributes = deepcopy(self.record["attributes"])
        self.apply(self.preview(), ["chapters"])
        self.assertNotIn("episodes", self.current()["metadata"])
        self.assertEqual(self.current()["local_playback"], {"version": 1, "resources": {}})
        self.assertEqual(self.current()["attributes"], attributes)
        self.assertEqual(self.bytes()[1:], before[1:])

    def test_no_unknown_duplicate_or_absent_group_is_implicitly_applied(self):
        preview = self.preview()
        before = self.bytes()
        for groups in ([], ["attributes"], ["description", "description"], ["cover"], ["ratings"], "description", [None]):
            with self.subTest(groups=groups), self.assertRaises(ValueError):
                self.apply(preview, groups)
            self.assertEqual(self.bytes(), before)

    def test_cas_detects_content_changes_even_when_preview_counts_are_identical(self):
        preview = self.preview()
        self.record["metadata"]["summary"] = "Changed summary of same presence"
        self.write()
        before = self.bytes()
        with self.assertRaises(ValueError):
            self.apply(preview, ["description"])
        self.assertEqual(self.bytes(), before)

    def test_tampered_plan_hash_and_expired_token_fail_closed(self):
        preview = self.preview()
        payload = providers._verify_token(preview["preview_token"])
        before = self.bytes()
        for change in ({"plan_sha256": "0" * 64}, {"expires": int(time.time()) - 1}, {"scope": "other-workspace"}):
            altered = {**preview, "preview_token": providers._sign({**payload, **change})}
            with self.assertRaises(ValueError):
                self.apply(altered, ["description"])
            self.assertEqual(self.bytes(), before)

    def test_concurrent_edit_before_shared_writer_is_preserved(self):
        preview = self.preview()
        original = service.apply_edits

        def race(body, **kwargs):
            self.record["metadata"]["summary"] = "Concurrent owner edit"
            self.write()
            return original(body, **kwargs)

        before_people, before_media = self.bytes()[1:]
        with patch.object(service, "apply_edits", side_effect=race), self.assertRaises(ValueError):
            self.apply(preview, ["description"])
        self.assertEqual(self.current()["metadata"]["summary"], "Concurrent owner edit")
        self.assertEqual(self.bytes()[1:], (before_people, before_media))


if __name__ == "__main__":
    unittest.main()
