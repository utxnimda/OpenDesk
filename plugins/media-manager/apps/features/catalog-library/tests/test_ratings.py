from copy import deepcopy
import unittest

from catalog_library import ratings, providers, model, service


def rating(**changes):
    return {"score": 8.1, "scale": 10, "votes": 12345,
            "source_url": "https://movie.douban.com/subject/123/", "external_id": "123",
            "fetched_at": "2026-10-06T12:00:00+00:00", **changes}


class RatingsTest(unittest.TestCase):
    def test_provider_separated_nested_fields_preserve_other_ratings(self):
        other = rating(source_url="https://bangumi.tv/subject/456", external_id="456")
        record = {"metadata": {"ratings": {"bangumi": other}, "summary": "手填简介"}}
        providers._set_field(record, "metadata.ratings.douban", rating())
        self.assertEqual(providers._field(record, "metadata.ratings.douban"), rating())
        self.assertEqual(providers._field(record, "metadata.ratings.bangumi"), other)
        self.assertEqual(record["metadata"]["summary"], "手填简介")
        self.assertIsNone(providers._field({}, "metadata.ratings.douban"))

    def test_valid_douban_rating_survives_extension_validation(self):
        value = {"metadata": {"ratings": {"douban": rating()}}}
        self.assertEqual(model.validate_extensions(value), value)

    def test_score_and_votes_reject_invalid_optional_values(self):
        for change in ({"score": float("nan")}, {"score": float("inf")}, {"score": True},
                       {"score": -1}, {"score": 11}, {"score": 10**1000}, {"scale": 0},
                       {"scale": 100}, {"votes": -1}, {"votes": True}, {"votes": "12"}, {"votes": 1.5}):
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                ratings.validate_rating("douban", rating(**change))

    def test_invalid_origin_subject_and_timestamp_rejected(self):
        for change in ({"source_url": "https://movie.douban.com.evil.test/subject/123/"},
                       {"source_url": "javascript:alert(1)"}, {"source_url": "https://movie.douban.com/subject/456/"},
                       {"source_url": "https://user:secret@movie.douban.com/subject/123/"},
                       {"external_id": "../123"}, {"fetched_at": "2026-10-06"}, {"fetched_at": None}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                ratings.validate_rating("douban", rating(**change))

    def test_invalid_old_rating_degrades_view_without_mutating_record(self):
        record = {"metadata": {"ratings": {"douban": rating(score="unknown")}, "summary": "仍可查看"}}
        before = deepcopy(record)
        metadata, _, warnings = service._extension_projection(record)
        self.assertEqual(metadata["ratings"], {})
        self.assertEqual(metadata["summary"], "仍可查看")
        self.assertTrue(warnings)
        self.assertEqual(record, before)

    def test_manual_score_change_is_detected_by_leaf_provenance(self):
        import hashlib
        record = {"metadata": {"ratings": {"douban": rating(score=9.0)}, "field_sources": {
            "metadata.ratings.douban": {"imported_value_sha256": hashlib.sha256(providers._json_bytes(rating())).hexdigest()}}}}
        candidates = {"name": "作品", "metadata.summary": "", "metadata.aliases": [], "metadata.episodes": [],
                      "metadata.cover": None, "metadata.ratings.douban": rating(score=8.3)}
        row = next(row for row in providers._diff(record, candidates) if row["field"] == "metadata.ratings.douban")
        self.assertTrue(row["locally_modified"])
        self.assertEqual(row["before"]["score"], 9.0)


if __name__ == "__main__":
    unittest.main()
