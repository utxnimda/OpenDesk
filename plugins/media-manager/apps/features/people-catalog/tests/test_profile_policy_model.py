"""Persisted policy metadata is bounded and cannot rewrite identity links."""
import unittest

from people_catalog.model import validate_entity, validate_people_refs


class ProfilePolicyModelTests(unittest.TestCase):
    def entity(self, **changes):
        return {"schema_version": 1, "id": "person_" + "1" * 32,
                "type": "person", "name": "Name", **changes}

    def test_variants_keep_compatible_display_schema(self):
        entity = self.entity(localized={"name": {"zh-Hans": "显示名"}},
            profile_variants={"name": {"zh-Hans": {
                "tmdb": {"value": "别名", "external_id": "1"},
                "wikipedia": {"value": "显示名", "external_id": "2"}}}},
            profile_preferences={"name": {"zh-Hans": {"source": "wikipedia"}}})
        self.assertEqual(validate_entity(entity), entity)

    def test_preferences_cannot_select_missing_sources_or_unknown_fields(self):
        for preferences in ({"name": {"zh-Hans": {"source": "wikipedia"}}},
                            {"birth_date": {"zh-Hans": {"locked": True}}},
                            {"name": {"zh-Hans": {"locked": True, "source": "tmdb"}}}):
            with self.subTest(preferences=preferences), self.assertRaises(ValueError):
                validate_entity(self.entity(profile_preferences=preferences))

    def test_stage_evidence_is_preserved_on_actor_links(self):
        relation = {"id": "person_" + "1" * 32, "type": "person", "role": "演员",
                    "character_name": "角色", "source": {"provider": "wikipedia", "subject_id": "1"},
                    "qualifier": {"kind": "age", "value": "child", "label": "童年", "language": "zh", "explicit": True},
                    "role_group": "wiki-cast:123"}
        self.assertEqual(validate_people_refs([relation]), [relation])
        for patch in ({"character_name": None}, {"type": "character"},
                      {"role_group": "../unsafe"}, {"qualifier": {"kind": "age", "value": "child", "explicit": False}}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_people_refs([{**relation, **patch}])

    def test_role_origin_retains_each_actor_and_stage(self):
        origin = {"work_key": "work:1", "actor_id": "person_" + "2" * 32,
                  "scope": {"provider": "tmdb", "subject_id": "10", "season_number": 1},
                  "external_id": "role:123", "original_name": "Role (young)",
                  "qualifier": {"kind": "age", "value": "young", "label": "young", "language": "en", "explicit": True},
                  "role_group": "wiki-cast:123"}
        entity = self.entity(type="character", role_origins=[origin])
        self.assertEqual(validate_entity(entity)["role_origins"], [origin])


if __name__ == "__main__":
    unittest.main()
