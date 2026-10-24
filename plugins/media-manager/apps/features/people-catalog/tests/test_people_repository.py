from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from people_catalog.model import source_identity, validate_entity, validate_people_refs
from people_catalog.repository import PeopleRepository
from people_catalog import repository
from work_catalog_yaml import persistence
from work_catalog_yaml.yaml_io import dump_yaml_string


def person(source_id=1, kind="person", name="Same Name"):
    return {"id": source_identity("bangumi", kind, source_id), "schema_version": 1, "type": kind, "name": name,
            "source_refs": [{"provider": "bangumi", "entity_type": kind, "external_id": str(source_id)}]}


class PeopleRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "people"
        self.repo = PeopleRepository(self.root)

    def save(self, records, **kwargs):
        return self.repo.apply_batch(records, expected_revision=self.repo.revision([row["id"] for row in records]), **kwargs)

    def test_stable_identity_separates_provider_type_id_and_never_name_merges(self):
        identities = {source_identity(provider, kind, identity) for provider, kind, identity in (
            ("bangumi", "person", 1), ("bangumi", "character", 1), ("bangumi", "person", 2), ("other", "person", 1))}
        self.assertEqual(len(identities), 4)
        self.assertEqual(source_identity("bangumi", "person", 1), source_identity("bangumi", "person", "1"))

    def test_read_is_offline_and_does_not_create_directories(self):
        self.assertIsNone(self.repo.read(person()["id"]))
        self.assertFalse(self.root.exists())

    def test_independent_yaml_roundtrip_with_custom_fields(self):
        first = person()
        first["custom"] = {"favorite": True}
        self.save([first, person(2)])
        self.assertEqual(self.repo.read(first["id"]), first)
        self.assertEqual(len(list((self.root / "db").glob("*.yaml"))), 2)

    def test_revision_rejects_stale_writes(self):
        first = person()
        revision = self.repo.revision([first["id"]])
        self.save([first])
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.repo.apply_batch([first], expected_revision=revision)

    def test_watched_only_person_keeps_exact_yaml_comments_and_formatting(self):
        selected, watched = person(1), person(2)
        self.save([selected, watched])
        watched_path = self.repo.path(watched["id"])
        original = ("# Keep my local comment and flow-style fields\n"
                    f"id: {watched['id']}\nschema_version: 1\ntype: person\n"
                    "name: 'Same Name' # Never serialize this unselected candidate\n"
                    "source_refs: [{provider: bangumi, entity_type: person, external_id: '2'}]\n").encode("utf-8")
        watched_path.write_bytes(original)
        ids = [selected["id"], watched["id"]]
        result = self.repo.apply_batch([{**selected, "name": "Explicitly selected"}],
            expected_revision=self.repo.revision(ids), revision_ids=ids)
        self.assertEqual(watched_path.read_bytes(), original)
        self.assertEqual(self.repo.read(selected["id"])["name"], "Explicitly selected")
        self.assertEqual(len(result["records"]), 1)
        history = list((self.root / "history").glob("*.yaml"))
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0].name.startswith(selected["id"]))

    def test_stale_watched_person_blocks_every_selected_write_and_callback(self):
        selected, watched = person(1), person(2)
        self.save([selected, watched])
        ids = [selected["id"], watched["id"]]
        revision = self.repo.revision(ids)
        selected_before = self.repo.path(selected["id"]).read_bytes()
        watched_path = self.repo.path(watched["id"])
        watched_changed = watched_path.read_bytes() + b"\n# Concurrent comment-only change\n"
        watched_path.write_bytes(watched_changed)
        with patch("builtins.print") as callback, self.assertRaisesRegex(ValueError, "已变化"):
            self.repo.apply_batch([{**selected, "name": "Must not save"}],
                expected_revision=revision, revision_ids=ids, after_write=callback)
        callback.assert_not_called()
        self.assertEqual(self.repo.path(selected["id"]).read_bytes(), selected_before)
        self.assertEqual(watched_path.read_bytes(), watched_changed)
        self.assertFalse((self.root / "history").exists())

    def test_empty_write_batch_can_watch_candidates_without_rewriting_them(self):
        watched = person()
        self.save([watched])
        path = self.repo.path(watched["id"])
        original = b"# Keep unselected formatting\n" + path.read_bytes()
        path.write_bytes(original)
        result = self.repo.apply_batch([], expected_revision=self.repo.revision([watched["id"]]),
            revision_ids=[watched["id"]], after_write=lambda: {"ok": True, "records": []})
        self.assertTrue(result["ok"])
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.root / "history").exists())

    def test_revision_ids_require_bounded_unique_safe_ids_covering_writes(self):
        selected = person()
        invalid = ([], [person(2)["id"]], [selected["id"], selected["id"]],
                   ["../../unsafe"], (selected["id"],), [selected["id"]] * 10001)
        for ids in invalid:
            with self.subTest(ids=str(ids)[:100]), self.assertRaises(ValueError):
                self.repo.apply_batch([selected], expected_revision="irrelevant", revision_ids=ids)
        self.assertFalse(self.root.exists())

    def test_update_keeps_recovery_history(self):
        first = person()
        self.save([first])
        first["name"] = "Changed"
        self.save([first])
        self.assertEqual(len(list((self.root / "history").glob("*.yaml"))), 1)

    def test_callback_failure_rolls_back_old_and_new_records(self):
        first = person()
        self.save([first])
        before = self.repo.path(first["id"]).read_bytes()
        updated = {**first, "name": "Changed"}
        with self.assertRaisesRegex(ValueError, "work failed"):
            self.save([updated, person(2)], after_write=lambda: (_ for _ in ()).throw(ValueError("work failed")))
        self.assertEqual(self.repo.path(first["id"]).read_bytes(), before)
        self.assertIsNone(self.repo.read(person(2)["id"]))

    def test_unknown_or_committed_work_failure_keeps_people_dependencies(self):
        for failure_type, state, marked in ((persistence.PersistenceRollbackError, None, True),
                (persistence.PersistenceRollbackError, None, False), (OSError, True, True), (ValueError, None, True)):
            with self.subTest(failure_type=failure_type.__name__, state=state, marked=marked):
                record = person()
                record["name"] = str((failure_type.__name__, state, marked))
                failure = failure_type("work result uncertain")
                if marked:
                    failure.db_committed = state
                with patch.object(repository, "report_exception") as log, \
                     self.assertRaisesRegex(persistence.PersistenceRollbackError, "人物数据已保留") as raised:
                    self.save([record], after_write=lambda: (_ for _ in ()).throw(failure))
                self.assertEqual(self.repo.read(record["id"]), record)
                self.assertIs(raised.exception.db_committed, state)
                self.assertTrue(raised.exception.people_records_retained)
                self.assertEqual(log.call_count, 1)
                self.assertEqual(log.call_args.kwargs["context"]["path"], str(self.root))
                self.assertIs(log.call_args.kwargs["context"]["db_committed"], state)

    def test_explicit_not_committed_failure_still_rolls_back_people(self):
        record = person()
        failure = persistence.PersistenceRollbackError("rollback complete for the work database")
        failure.db_committed = False
        with self.assertRaises(persistence.PersistenceRollbackError):
            self.save([record], after_write=lambda: (_ for _ in ()).throw(failure))
        self.assertIsNone(self.repo.read(record["id"]))

    def test_noncommitted_result_cannot_be_reported_as_success(self):
        record = person()
        for state in (False, None):
            with self.subTest(state=state):
                with self.assertRaises((ValueError, persistence.PersistenceRollbackError)):
                    self.save([record], after_write=lambda: {"ok": False, "db_committed": state})
                self.assertEqual(self.repo.read(record["id"]) is not None, state is None)

    def test_mid_batch_failure_rolls_back_first_file(self):
        write = persistence.atomic_write_bytes
        calls = 0

        def fail_second(path, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("test disk failure")
            return write(path, data)

        with patch.object(persistence, "atomic_write_bytes", side_effect=fail_second), self.assertRaises(OSError):
            self.save([person(), person(2)])
        self.assertIsNone(self.repo.read(person()["id"]))
        self.assertIsNone(self.repo.read(person(2)["id"]))

    def test_voice_actor_must_exist_as_person_before_work_callback(self):
        character = person(1, "character")
        character["voice_actors"] = [{"person_id": person()["id"], "scope": {"provider": "bangumi", "subject_id": "100"}}]
        with self.assertRaisesRegex(ValueError, "不存在"):
            self.save([character])
        self.save([character, person()])
        self.assertEqual(self.repo.read(character["id"])["voice_actors"], character["voice_actors"])

    def test_rejects_linked_roots_and_paths(self):
        with patch.object(Path, "is_junction", lambda path: path == self.root, create=True):
            with self.assertRaisesRegex(ValueError, "联接"):
                PeopleRepository(self.root)
        for identity in ("../escaped", "person_" + "a" * 32 + "/x", "C:/out.yaml", "CON"):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                self.repo.read(identity)

    def test_rejects_linked_lock_file_before_acquiring_lock(self):
        with patch.object(Path, "is_junction", lambda path: path == self.root / ".nimda-catalog.lock", create=True):
            with self.assertRaisesRegex(ValueError, "联接"):
                self.save([person()])
        self.assertFalse((self.root / "db").exists())

    def test_rejects_malformed_oversized_yaml_and_id_mismatch(self):
        record = person()
        self.save([record])
        path = self.repo.path(record["id"])
        for raw in (b"- [bad", b"x" * (512 * 1024 + 1), dump_yaml_string(person(2)).encode("utf-8")):
            with self.subTest(length=len(raw)):
                path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    self.repo.read(record["id"])

    def test_ref_validator_rejects_embedded_profiles_and_invalid_types(self):
        ref = {"id": person()["id"], "type": "person", "role": "CV"}
        self.assertEqual(validate_people_refs([ref]), [ref])
        for refs in ([{**ref, "name": "embedded profile"}], [{**ref, "type": "anything"}], [ref, deepcopy(ref)], [{}]):
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                validate_people_refs(refs)

    def test_entity_validator_bounds_yaml_alias_graphs_before_serialization(self):
        record = person()
        record["cycle"] = record
        with self.assertRaisesRegex(ValueError, "安全限制"):
            validate_entity(record)
        record = person()
        nested = ["alias"]
        for _ in range(20):
            nested = [nested, nested]
        record["aliases_graph"] = nested
        with self.assertRaisesRegex(ValueError, "安全限制"):
            validate_entity(record)

    def test_empty_batch_can_clear_work_links_without_deleting_people(self):
        self.save([person()])
        result = self.save([], after_write=lambda: {"ok": True, "work": "saved"})
        self.assertEqual(result["work"], "saved")
        self.assertIsNotNone(self.repo.read(person()["id"]))


if __name__ == "__main__":
    unittest.main()
