from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from people_catalog.model import source_identity
from people_catalog.repository import PeopleRepository
from people_catalog.source_links import build_source_index


class SourceLinksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "people"
        self.repo = PeopleRepository(self.root)

    def row(self, identity, *, kind="person", sources=()):
        return {"schema_version": 1, "id": identity, "type": kind, "name": "Identical display name",
                "source_refs": [{"provider": provider, "entity_type": kind, "external_id": external}
                                for provider, external in sources]}

    def save(self, *rows):
        self.repo.apply_batch(list(rows), expected_revision=self.repo.revision([row["id"] for row in rows]))

    def test_empty_offline_index_does_not_create_database(self):
        with patch("socket.socket.connect", side_effect=AssertionError("offline")):
            index = build_source_index(self.repo)
        self.assertFalse(self.root.exists())
        self.assertIsNone(index.find("douban", "person", "personage:1"))
        self.assertEqual(index.resolve("douban", "person", "personage:1"), source_identity("douban", "person", "personage:1"))

    def test_provider_namespace_and_kind_are_exact_and_display_names_never_merge(self):
        scopes = [("douban", "person", "celebrity:12"), ("douban", "person", "personage:12"),
                  ("bangumi", "person", "12"), ("bangumi", "character", "12")]
        rows = [self.row(source_identity("manual", kind, str(i)), kind=kind, sources=[(provider, external)])
                for i, (provider, kind, external) in enumerate(scopes)]
        self.save(*rows)
        index = build_source_index(self.repo)
        for scope, row in zip(scopes, rows):
            self.assertEqual(index.resolve(*scope), row["id"])
        self.assertEqual(len({index.resolve(*scope) for scope in scopes}), 4)
        self.assertIsNone(index.find("douban", "person", "personage:13"))

    def test_existing_canonical_identity_without_source_row_is_preserved(self):
        identity = source_identity("douban", "person", "personage:1")
        self.save(self.row(identity))
        self.assertEqual(build_source_index(self.repo).resolve("douban", "person", "personage:1"), identity)

    def test_cached_overlay_includes_unwritten_planned_people_and_enforces_limit(self):
        identity = source_identity("bangumi", "person", "1")
        row = self.row(identity, sources=[("bangumi", "1")])
        class Overlay:
            root = self.root
            import_rows = {identity: row}
            def read(self, key):
                return self.import_rows.get(key)
        self.assertEqual(build_source_index(Overlay()).find("bangumi", "person", "1"), identity)
        self.assertFalse(self.root.exists())
        other = source_identity("bangumi", "person", "2")
        Overlay.import_rows[other] = self.row(other, sources=[("bangumi", "2")])
        with self.assertRaises(ValueError):
            build_source_index(Overlay(), maximum=1)

    def test_duplicate_explicit_bindings_are_rejected_but_same_entity_duplicates_are_safe(self):
        source = ("douban", "personage:1")
        first = self.row(source_identity("tmdb", "person", "1"), sources=[source, source])
        self.save(first)
        self.assertEqual(build_source_index(self.repo).resolve("douban", "person", "personage:1"), first["id"])
        self.save(self.row(source_identity("tmdb", "person", "2"), sources=[source]))
        with self.assertRaises(ValueError):
            build_source_index(self.repo)

    def test_explicit_owner_and_independent_canonical_entity_cannot_be_silently_merged(self):
        self.save(self.row(source_identity("tmdb", "person", "1"), sources=[("douban", "personage:1")]),
                  self.row(source_identity("douban", "person", "personage:1")))
        with self.assertRaises(ValueError):
            build_source_index(self.repo).resolve("douban", "person", "personage:1")

    def test_wrong_type_at_canonical_uuid_is_not_resolved_as_real_person(self):
        self.save(self.row(source_identity("douban", "person", "personage:1"), kind="character"))
        with self.assertRaises(ValueError):
            build_source_index(self.repo).find("douban", "person", "personage:1")

    def test_index_reads_each_record_once_and_checks_count_before_loading(self):
        self.save(*(self.row(source_identity("manual", "person", str(i))) for i in range(3)))
        with patch.object(self.repo, "read", wraps=self.repo.read) as reader:
            index = build_source_index(self.repo, maximum=3)
            for number in range(20):
                index.resolve("douban", "person", f"personage:{number + 1}")
        self.assertEqual(reader.call_count, 3)
        with patch.object(self.repo, "read") as reader, self.assertRaises(ValueError):
            build_source_index(self.repo, maximum=2)
        reader.assert_not_called()
        for maximum in (0, -1, True, 100001):
            with self.subTest(maximum=maximum), self.assertRaises(ValueError):
                build_source_index(self.repo, maximum=maximum)


if __name__ == "__main__":
    unittest.main()
