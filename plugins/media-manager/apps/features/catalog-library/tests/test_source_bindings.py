from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from catalog_library import providers, service, source_bindings
from catalog_library.model import validate_extensions
from collection_detail.catalog_edit_service import catalog_records
from work_catalog_yaml.jp_tv.browse_settings import JpTvBrowseSettings
from work_catalog_yaml.yaml_io import dump_yaml_string, load_yaml_string


def work(*, domain="television", country="korea"):
    return {"attributes": [{"type": "name", "data": "Local title"},
                           {"type": "country", "data": country},
                           {"type": "date", "data": {"start": "20200101", "end": ""}},
                           {"type": "collection-type", "data": {"domain": domain, "release_type": "tv",
                            "path": "Z:/Offline/Work", "collectioned": []}}],
            "metadata": {"summary": "User summary", "field_sources": {"summary": {"provider": "tmdb"}}},
            "custom": {"preserved": True}}


class SourceBindingPolicyTest(unittest.TestCase):
    def test_legacy_projection_is_stable_detached_and_does_not_migrate(self):
        raw = {"source_refs": [{"provider": "tmdb", "external_id": 12, "scope": "season:1", "custom": []},
                               {"provider": "wikipedia", "external_id": "条目"}]}
        original = deepcopy(raw)
        refs = source_bindings.normalized_refs(raw)
        self.assertEqual(raw, original)
        self.assertEqual(source_bindings.identity(refs[0]), ("tmdb", "12", "season:1"))
        self.assertRegex(refs[0]["binding_id"], r"^sb_[0-9a-f]{24}$")
        self.assertEqual(refs[0]["binding_id"], source_bindings.binding_id({**raw["source_refs"][0], "external_id": "12"}))
        self.assertNotEqual(refs[0]["binding_id"], source_bindings.binding_id({**raw["source_refs"][0], "scope": "season:2"}))
        self.assertEqual([ref["role"] for ref in refs], ["primary", "supplement"])
        self.assertTrue(all(ref["enabled"] for ref in refs))
        refs[0]["custom"].append("changed")
        self.assertEqual(raw, original)
        self.assertEqual(validate_extensions(raw), raw)

    def test_explicit_identity_enabled_and_role_survive_projection(self):
        ref = {"provider": "wikipedia", "external_id": "Title", "binding_id": "sb_" + "a" * 24,
               "enabled": False, "role": "primary"}
        self.assertEqual(source_bindings.normalized_refs({"source_refs": [ref]}), [ref])
        self.assertEqual(source_bindings.binding_id({**ref, "external_id": "Other"}), ref["binding_id"])

    def test_defaults_are_based_only_on_local_classification(self):
        for domain, country, expected, reason in (("animation", "korea", ["bangumi"], "animation"),
                                                  ("television", "korea", ["tmdb", "wikipedia"], "korean_television"),
                                                  ("television", "japan", ["bangumi"], "default")):
            with self.subTest(domain=domain, country=country):
                policy = source_bindings.source_policy(work(domain=domain, country=country))
                self.assertEqual(policy["missing_providers"], expected)
                self.assertEqual(policy["reason"], reason)
                self.assertFalse(policy["custom"])
        policy = source_bindings.source_policy(work())
        self.assertEqual(policy["primary_provider"], "tmdb")
        self.assertEqual(policy["supplement_providers"], ["wikipedia"])

    def test_legacy_binding_and_disabled_source_override_defaults(self):
        raw = work()
        raw["source_refs"] = [{"provider": "bangumi", "external_id": 12, "enabled": False}]
        policy = source_bindings.source_policy(raw)
        self.assertTrue(policy["custom"])
        self.assertEqual(policy["targets"], [{"provider": "bangumi", "role": "primary"}])
        self.assertEqual(policy["missing_providers"], [])

    def test_saved_default_keeps_missing_supplement_and_empty_custom_stays_empty(self):
        raw = work()
        targets = source_bindings.source_policy(raw)["targets"]
        raw["source_policy_config"] = {"custom": False, "targets": targets}
        raw["source_refs"] = [{"provider": "tmdb", "external_id": "tv:12", "enabled": False}]
        policy = source_bindings.source_policy(raw)
        self.assertFalse(policy["custom"])
        self.assertEqual(policy["missing_providers"], ["wikipedia"])
        raw["source_refs"] = []
        raw["source_policy_config"] = {"custom": True, "targets": []}
        policy = source_bindings.source_policy(raw)
        self.assertTrue(policy["custom"])
        self.assertIsNone(policy["primary_provider"])
        self.assertEqual(policy["targets"], [])
        self.assertEqual(policy["missing_providers"], [])

    def test_binding_validation_rejects_bad_ids_roles_flags_and_collisions(self):
        ref = {"provider": "tmdb", "external_id": "tv:12"}
        for field, values in (("binding_id", (None, True, "x", "sb_" + "A" * 24, "sb_" + "a" * 32)),
                              ("enabled", (None, 0, 1, "true", [])),
                              ("role", (None, 1, [], "main", "PRIMARY"))):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_extensions({"source_refs": [{**ref, field: value}]})
        for duplicate in (source_bindings.binding_id(ref), "sb_" + "a" * 24):
            first = ref if duplicate == source_bindings.binding_id(ref) else {**ref, "binding_id": duplicate}
            second = {"provider": "wikipedia", "external_id": "Title", "binding_id": duplicate}
            with self.assertRaisesRegex(ValueError, "binding_id 不能重复"):
                validate_extensions({"source_refs": [first, second]})

    def test_policy_config_validation_is_strict_and_backward_compatible(self):
        self.assertNotIn("source_policy_config", validate_extensions(work()))
        valid = {"custom": True, "targets": [{"provider": "wikipedia", "role": "supplement"}]}
        self.assertEqual(validate_extensions({"source_policy_config": valid})["source_policy_config"], valid)
        for config in (None, [], {}, {"custom": 1, "targets": []}, {"custom": True, "targets": None},
                       {"custom": True, "targets": [None]},
                       {"custom": True, "targets": [{"provider": "BAD", "role": "primary"}]},
                       {"custom": True, "targets": [{"provider": "tmdb", "role": []}]},
                       {"custom": False, "targets": valid["targets"] * 2}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_extensions({"source_policy_config": config})


class SourceBindingUpdateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "db"
        self.db.mkdir()
        self.file = self.db / "[KR][TVInfo][2020].yaml"
        self.record = work()
        self.record["source_refs"] = [{"provider": "tmdb", "external_id": "tv:12", "scope": "season:1"},
                                      {"provider": "wikipedia", "external_id": "Title", "scope": "zh"}]
        self.file.write_text(dump_yaml_string([self.record]), encoding="utf-8")
        self.settings = JpTvBrowseSettings(version=1, filesystem_root=self.db,
            resolved_default_readable=str(self.file), resolved_catalog_yaml_paths=(str(self.file),),
            enum_options={}, enum_labels={}, enum_section_labels={}, app_features=())
        self.library = self.root / "library"
        self.people = self.root / "people"
        self.addCleanup(service._CACHE.clear)

    def ref(self):
        return catalog_records(self.settings)[0]["ref"]

    def stored(self):
        return load_yaml_string(self.file.read_text(encoding="utf-8"))[0]

    def update(self, action, binding=None, **extra):
        return service.binding_update({"ref": self.ref(), "action": action,
            "binding_id": binding or source_bindings.binding_id(self.record["source_refs"][0]), **extra},
            settings=self.settings, data_root=self.library)

    def test_detail_projects_defaults_without_rewriting_raw_record_or_disk(self):
        before = self.file.read_bytes()
        paths = set(self.root.rglob("*"))
        with patch.object(providers, "_request_json", side_effect=AssertionError("must remain offline")):
            result = service.detail({"ref": self.ref()}, settings=self.settings,
                                    data_root=self.library, people_root=self.people)
        self.assertEqual(result["record"], self.record)
        self.assertEqual(result["sources"], source_bindings.normalized_refs(self.record))
        self.assertEqual(result["source_policy"]["missing_providers"], [])
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(set(self.root.rglob("*")), paths)

    def test_disable_enable_role_and_remove_use_shared_writer_and_keep_fields(self):
        before = deepcopy(self.record)
        with patch.object(providers, "_request_json", side_effect=AssertionError("must remain offline")), \
                patch.object(service, "apply_edits", wraps=service.apply_edits) as writer:
            result = self.update("disable")
            self.assertTrue(result["db_committed"])
            self.assertFalse(self.stored()["source_refs"][0]["enabled"])
            self.assertEqual(result["source_policy"]["missing_providers"], [])
            self.update("enable")
            self.assertTrue(self.stored()["source_refs"][0]["enabled"])
            self.update("set-role", role="supplement")
            self.assertEqual(self.stored()["source_refs"][0]["role"], "supplement")
            self.update("remove")
            self.assertEqual(len(self.stored()["source_refs"]), 1)
            self.assertEqual(writer.call_count, 4)
        after = self.stored()
        for field in ("attributes", "metadata", "custom"):
            self.assertEqual(after[field], before[field])
        self.assertTrue(after["source_policy_config"]["custom"])
        self.assertTrue(list((self.root / "History").glob("*.yaml")))

    def test_removing_last_binding_does_not_restore_default_recommendations(self):
        self.update("remove")
        self.update("remove", source_bindings.binding_id(self.record["source_refs"][1]))
        record = self.stored()
        self.assertEqual(record["source_refs"], [])
        self.assertEqual(record["source_policy_config"], {"custom": True, "targets": []})
        self.assertEqual(source_bindings.source_policy(record)["missing_providers"], [])

    def test_stale_version_and_invalid_action_never_write(self):
        stale = self.ref()
        self.update("disable")
        before = self.file.read_bytes()
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.update("remove", ref=stale)
        for extra in ({"action": "unknown"}, {"action": []}, {"binding_id": "missing"},
                      {"action": "set-role", "role": "unknown"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                body = {"ref": self.ref(), "action": "enable",
                        "binding_id": source_bindings.binding_id(self.record["source_refs"][0]), **extra}
                service.binding_update(body, settings=self.settings, data_root=self.library)
        self.assertEqual(self.file.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
