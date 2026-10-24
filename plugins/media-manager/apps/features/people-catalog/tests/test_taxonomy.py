"""Taxonomy adapts legacy records without changing their stable source IDs."""
from copy import deepcopy
import unittest

from people_catalog import taxonomy
from people_catalog.model import source_identity, validate_entity


class EntityTaxonomyTest(unittest.TestCase):
    def test_documented_legacy_kinds_map_without_writes(self):
        cases = [
            ("person", "individual", "person", "real", "individual"),
            ("person", "corporation", "organization", "real", "corporation"),
            ("person", "association", "organization", "real", "association"),
            ("character", "character", "person", "fictional", "character"),
            ("character", "organization", "organization", "fictional", "organization"),
            ("character", "mechanic", "object", "fictional", "mechanic"),
            ("character", "ship", "object", "fictional", "ship"),
        ]
        for entity_type, kind, category, reality, subtype in cases:
            with self.subTest(kind=kind):
                entity = {"type": entity_type, "kind": kind}
                before = deepcopy(entity)
                self.assertEqual(taxonomy.project(entity), {"category": category, "reality": reality, "subtype": subtype})
                self.assertEqual(entity, before)
                self.assertEqual(taxonomy.is_real_person(entity), category == "person" and reality == "real")

    def test_unknown_person_or_character_is_not_guessed(self):
        for value in ({"type": "person"}, {"type": "character"}, {"type": "person", "kind": "unrecognized"}):
            self.assertEqual(taxonomy.project(value), {"category": "unknown", "reality": "unknown", "subtype": "unknown"})
            self.assertFalse(taxonomy.is_real_person(value))

    def test_explicit_classification_wins_and_return_value_is_independent(self):
        entity = {"type": "person", "kind": "individual", "taxonomy": {"category": "organization", "reality": "real", "subtype": "studio"}}
        result = taxonomy.project(entity)
        self.assertEqual(result["category"], "organization")
        result["category"] = "person"
        self.assertEqual(entity["taxonomy"]["category"], "organization")
        self.assertIn("工作室", taxonomy.label(entity))

    def test_invalid_taxonomy_never_falls_back_to_legacy(self):
        for value in (None, [], {}, {"category": "person", "reality": "real", "subtype": "corporation"},
                      {"category": [], "reality": "real", "subtype": "individual"},
                      {"category": "organization", "reality": "real", "subtype": "company"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                taxonomy.project({"type": "person", "kind": "individual", "taxonomy": value})

    def test_model_accepts_manual_taxonomy_without_rewriting_type_or_sources(self):
        identity = source_identity("bangumi", "person", "17")
        entity = {"id": identity, "schema_version": 1, "type": "person", "kind": "corporation", "name": "Studio",
                  "source_refs": [{"provider": "bangumi", "entity_type": "person", "external_id": "17"}],
                  "taxonomy": {"category": "organization", "reality": "real", "subtype": "studio"}}
        self.assertEqual(validate_entity(entity), entity)
        entity["taxonomy"]["subtype"] = "individual"
        with self.assertRaises(ValueError):
            validate_entity(entity)


if __name__ == "__main__":
    unittest.main()
