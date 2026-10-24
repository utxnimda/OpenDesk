from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from catalog_library import browse_filters, service
from work_catalog_yaml.jp_tv.browse_settings import JpTvBrowseSettings
from work_catalog_yaml.yaml_io import dump_yaml_string


def work(name, country, release_type, resources):
    return {"attributes": [{"type": "name", "data": name}, {"type": "country", "data": country},
        {"type": "date", "data": {"start": "20260101", "end": ""}},
        {"type": "collection-type", "data": {"domain": "animation", "release_type": release_type,
         "path": "Z:/NeverAccessed/" + name, "collectioned": [
            {"press_group": group, "press_format": format_, "press_path": name + "_" + format_ + str(index)}
            for index, (group, format_) in enumerate(resources)]}}]}


class LibraryBrowseFiltersTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "2026.yaml"
        self.source.write_text(dump_yaml_string([
            work("Alpha", "japan", "tv", [("VCB", "BDRip"), ("ANK", "WEBRip"), ("vcb", "BDRip")]),
            work("Beta", "korea", "tv", [("ANK", "BDRip")]),
            work("Gamma", "japan", "movie", [("", "BDRip")]),
            work("Delta", "korea", "tv", [])]), encoding="utf-8")
        self.settings = JpTvBrowseSettings(version=1, filesystem_root=self.root,
            resolved_default_readable=str(self.source), resolved_catalog_yaml_paths=(str(self.source),),
            enum_options={"country": ("japan", "korea", "china"), "release_type": ("tv", "movie", "ova"),
                          "press_group": ("VCB", "ANK"), "press_format": ("BDRip", "WEBRip", "DVDRip")},
            enum_labels={"country": {"japan": "日本", "korea": "韩国", "china": "中国"},
                         "release_type": {"tv": "TV", "movie": "电影", "ova": "OVA"}},
            enum_section_labels={}, app_features=())

    def browse(self, **filters):
        return service.browse(filters, settings=self.settings, data_root=self.root / "library")

    def names(self, **filters):
        return [item["name"] for item in self.browse(sort="name", **filters)["items"]]

    def test_four_exact_filters_combine_with_existing_browse_parameters(self):
        self.assertEqual(self.names(country="korea"), ["Beta", "Delta"])
        self.assertEqual(self.names(release_type="movie"), ["Gamma"])
        self.assertEqual(self.names(press_group="VCB"), ["Alpha"])
        self.assertEqual(self.names(press_format="WEBRip"), ["Alpha"])
        self.assertEqual(self.names(country="korea", release_type="tv", press_group="ANK", press_format="BDRip"), ["Beta"])
        self.assertEqual(self.names(country="japan", release_type="tv", year="2026", query="Alp", domain="animation"), ["Alpha"])
        self.assertEqual(self.names(country="japan", release_type="tv", year="2025"), [])

    def test_group_and_format_must_match_the_same_resource_row(self):
        self.assertEqual(self.names(press_group="VCB", press_format="WEBRip"), [])
        self.assertEqual(self.names(press_group="ANK", press_format="WEBRip"), ["Alpha"])
        self.assertEqual(self.names(press_group="ANK", press_format="BDRip"), ["Beta"])

    def test_case_and_fullwidth_are_comparison_only_and_empty_group_is_explicit(self):
        before = self.source.read_bytes()
        self.assertEqual(self.names(press_group="ｖｃｂ", press_format="bdrip", country="JAPAN"), ["Alpha"])
        self.assertEqual(self.names(press_group=browse_filters.EMPTY_VALUE), ["Gamma"])
        self.assertEqual(self.names(press_group="----"), ["Gamma"])
        self.assertEqual(self.names(press_group="", press_format=""), ["Alpha", "Beta", "Delta", "Gamma"])
        self.assertEqual(self.names(press_format=browse_filters.EMPTY_VALUE), [])
        self.assertEqual(self.source.read_bytes(), before)

    def test_facets_share_canonical_enums_and_count_unique_works(self):
        facets = self.browse()["facets"]
        self.assertEqual(facets["countries"], [{"value": "japan", "label": "日本", "count": 2},
            {"value": "korea", "label": "韩国", "count": 2}, {"value": "china", "label": "中国", "count": 0}])
        self.assertEqual(facets["press_groups"], [{"value": "VCB", "label": "VCB", "count": 1},
            {"value": "ANK", "label": "ANK", "count": 2}, {"value": "__none__", "label": "----（未填写）", "count": 1}])
        self.assertEqual(facets["press_formats"][0]["count"], 3)
        self.assertEqual(facets["press_formats"][2], {"value": "DVDRip", "label": "DVDRip", "count": 0})
        self.assertEqual(self.browse(country="korea")["facets"], facets)

    def test_legacy_values_remain_filterable_without_becoming_new_enum_definitions(self):
        projected = [{"country": "historic", "release_type": "custom", "press": [
            {"press_group": "Old", "press_format": "Raw"}, {"press_group": "OLD", "press_format": "RAW"}]}]
        before = deepcopy(projected)
        facets = browse_filters.filter_facets(projected, self.settings)
        self.assertEqual(facets["press_groups"][-1], {"value": "Old", "label": "Old", "count": 1})
        self.assertTrue(browse_filters.matches_filters(projected[0], browse_filters.parse_filters({"country": "historic", "press_format": "raw"})))
        self.assertEqual(projected, before)
        self.assertNotIn("Old", self.settings.enum_options["press_group"])

    def test_invalid_filter_shapes_fail_instead_of_silently_ignoring_them(self):
        for field in browse_filters.FILTER_FIELDS:
            for value in (True, 1, [], {}, "x" * 257):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.browse(**{field: value})

    def test_filtering_never_accesses_media_and_does_not_reparse_cached_records(self):
        with patch.object(service, "catalog_records", wraps=service.catalog_records) as reader, \
             patch("collection_detail.catalog_edit_service.resolve_record_directory", side_effect=AssertionError("must not resolve media")):
            self.browse()
            self.browse(country="japan", press_group="VCB")
            self.assertEqual(reader.call_count, 1)
        self.assertFalse((self.root / "library").exists())


if __name__ == "__main__":
    unittest.main()
