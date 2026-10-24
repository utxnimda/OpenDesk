from copy import deepcopy
import time
import unittest
from unittest.mock import patch

from catalog_library import people_assets, tmdb_people, tmdb_source
import test_tmdb_people as fixtures


class TmdbPeopleRetryTest(unittest.TestCase):
    setUp = fixtures.TmdbPeopleTest.setUp
    ref = fixtures.TmdbPeopleTest.ref
    current = fixtures.TmdbPeopleTest.current
    save_entity = fixtures.TmdbPeopleTest.save_entity
    write_record = fixtures.TmdbPeopleTest.write_record
    request = fixtures.TmdbPeopleTest.request

    def fetch(self, failed=(), **kwargs):
        def request(path, **options):
            if path in failed:
                raise ValueError("TLS 握手被中断（SSLEOFError）")
            return self.request(path, **options)
        with patch.object(tmdb_source, "_request_json", side_effect=request):
            return tmdb_people.fetch_snapshot(100, season_number=1, **kwargs)

    @staticmethod
    def failure(snapshot, kind, object_id=None):
        return next(row["id"] for row in snapshot["retry_failures"] if row["kind"] == kind
                    and (object_id is None or row["object_id"] == str(object_id)))

    def retry(self, snapshot, selected=None, **kwargs):
        self.calls = []
        with patch.object(tmdb_source, "_request_json", side_effect=self.request):
            result = tmdb_people.retry_snapshot(snapshot, selected, repository=self.repo, **kwargs)
        return result

    def test_selected_detail_only_then_remaining_detail_never_refetch_success(self):
        snapshot = self.fetch(("/person/1", "/person/2"))
        before = deepcopy(snapshot)
        snapshot["raw"]["wikipedia_people"] = {"people": {"1": {"name_cn": "额外中文名"}}}
        snapshot["import_entries"] = [{"obsolete": True}]
        first = self.retry(snapshot, [self.failure(snapshot, "person-detail", 1)])
        self.assertEqual([path for path, _ in self.calls], ["/person/1"])
        self.assertEqual(first["raw"]["details"]["1"], self.details[1])
        self.assertIsNone(first["raw"]["details"]["2"])
        self.assertEqual(len(first["retry_failures"]), 1)
        self.assertIn("李导演", first["retry_failures"][0]["label"])
        self.assertNotIn("import_entries", first)
        self.assertEqual(first["raw"]["wikipedia_people"], snapshot["raw"]["wikipedia_people"])
        self.assertEqual(snapshot["raw"]["details"], before["raw"]["details"])
        final = self.retry(first)
        self.assertEqual([path for path, _ in self.calls], ["/person/2"])
        self.assertEqual(final["raw"]["details"]["1"], self.details[1])
        self.assertEqual(final["retry_failures"], [])
        self.assertFalse(any("SSLEOFError" in warning for warning in final["warnings"]))
        self.assertFalse(list((self.people_root / "db").glob("*.yaml")))

    def test_failed_retry_retains_stable_identity_and_successful_details(self):
        snapshot = self.fetch(("/person/1",))
        selected = self.failure(snapshot, "person-detail", 1)
        with patch.object(tmdb_source, "_request_json", side_effect=ValueError("新一次连接超时")) as request:
            retried = tmdb_people.retry_snapshot(snapshot, [selected], repository=self.repo)
        request.assert_called_once()
        self.assertEqual(retried["retry_failures"][0]["id"], selected)
        self.assertEqual(retried["retry_failures"][0]["reason"], "新一次连接超时")
        self.assertEqual(retried["raw"]["details"]["2"], snapshot["raw"]["details"]["2"])
        self.assertFalse(any("SSLEOFError" in warning for warning in retried["warnings"]))

    def test_detail_retry_preserves_successful_portraits_and_unselected_portrait_failures(self):
        self.credits["cast"][0]["profile_path"] = "/Actor.jpg"
        self.credits["crew"][0]["profile_path"] = "/Director.jpg"
        snapshot = self.fetch(("/person/1",))
        actor_avatar = {"asset_id": "a" * 64 + ".jpg", "source_url": "https://image.tmdb.org/t/p/w500/Actor.jpg"}
        def avatar(raw, **kwargs):
            if raw["profile_path"] == "/Director.jpg":
                raise ValueError("图片连接失败")
            return deepcopy(actor_avatar)
        with patch.object(people_assets, "acquire_tmdb_avatar", side_effect=avatar):
            tmdb_people.prepare_portraits(snapshot, self.repo)
        self.details[1]["profile_path"] = "/NewPortrait.jpg"
        with patch.object(people_assets, "acquire_tmdb_avatar") as acquire:
            retried = self.retry(snapshot, [self.failure(snapshot, "person-detail", 1)])
        acquire.assert_not_called()
        self.assertEqual(retried["avatars"]["1"], actor_avatar)
        self.assertEqual([row["kind"] for row in retried["retry_failures"]], ["person-avatar"])
        director_avatar = {"asset_id": "b" * 64 + ".jpg", "source_url": "https://image.tmdb.org/t/p/w500/Director.jpg"}
        with patch.object(people_assets, "acquire_tmdb_avatar", return_value=director_avatar) as acquire:
            final = self.retry(retried)
        self.assertEqual(self.calls, [])
        acquire.assert_called_once()
        self.assertEqual(final["avatars"], {"1": actor_avatar, "2": director_avatar})
        self.assertEqual(final["retry_failures"], [])

    def test_recovered_detail_can_discover_portrait_without_refetching_others(self):
        snapshot = self.fetch(("/person/1",))
        tmdb_people.prepare_portraits(snapshot, self.repo)
        self.details[1]["profile_path"] = "/Recovered.jpg"
        with patch.object(people_assets, "acquire_tmdb_avatar", return_value={"asset_id": "a" * 64 + ".jpg"}) as acquire:
            result = self.retry(snapshot)
        acquire.assert_called_once()
        self.assertEqual(acquire.call_args.args[0], {"profile_path": "/Recovered.jpg"})
        self.assertEqual([path for path, _ in self.calls], ["/person/1"])
        self.assertTrue(result["avatars"]["1"])

    def test_localized_credit_retry_uses_successful_original_and_person_details(self):
        path = "/tv/100/season/1/aggregate_credits"
        self.credits["cast"][0]["roles"][0]["credit_id"] = "actor-credit"
        original = deepcopy(self.credits)
        original["cast"][0]["roles"][0]["character"] = "주인공"
        def request(target, **kwargs):
            if target == path:
                if kwargs["query"]["language"] == "zh-CN":
                    raise ValueError("中文列表获取失败")
                return deepcopy(original)
            return self.request(target, **kwargs)
        with patch.object(tmdb_source, "_request_json", side_effect=request):
            snapshot = tmdb_people.fetch_snapshot(100, season_number=1, season_data=self.season, original_language="ko")
        result = self.retry(snapshot)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], path)
        self.assertEqual(self.calls[0][1]["query"], {"language": "zh-CN"})
        self.assertEqual(result["raw"]["details"], snapshot["raw"]["details"])
        actor = next(row for row in result["raw"]["relations"] if row["category"] == "cast")
        self.assertEqual((actor["value"], actor["value_cn"]), ("주인공", "男主角"))

    def test_season_retry_discovers_previously_unreachable_dependent_requests(self):
        snapshot = self.fetch(("/tv/100/season/1",))
        self.assertEqual(len(snapshot["retry_failures"]), 1)
        result = self.retry(snapshot)
        self.assertEqual(result["original_language"], "zh")
        self.assertEqual(result["retry_failures"], [])
        self.assertEqual({path for path, _ in self.calls}, {"/tv/100/season/1", "/tv/100",
            "/tv/100/season/1/aggregate_credits", "/person/1", "/person/2"})

    def test_expired_retry_budget_keeps_success_cache_without_network(self):
        snapshot = self.fetch(("/person/1",))
        with patch.object(tmdb_source, "_request_json") as request:
            result = tmdb_people.retry_snapshot(snapshot, repository=self.repo, deadline=time.monotonic() - 1)
        request.assert_not_called()
        self.assertEqual(result["raw"]["details"]["2"], snapshot["raw"]["details"]["2"])
        self.assertEqual(len(result["retry_failures"]), 1)
        self.assertIn("时间上限", result["retry_failures"][0]["reason"])

    def test_invalid_selections_and_foreign_failure_scopes_reject_before_network(self):
        snapshot = self.fetch(("/person/1",))
        identity = self.failure(snapshot, "person-detail", 1)
        for selected in ([], ["unknown"], [identity, identity], "not-a-list"):
            with self.subTest(selected=selected), patch.object(tmdb_source, "_request_json") as request, self.assertRaises(ValueError):
                tmdb_people.retry_snapshot(snapshot, selected, repository=self.repo)
            request.assert_not_called()
        malformed = deepcopy(snapshot)
        malformed["retry_failures"][0].update(path="/person/999", object_id="999", id="tmdb:person-detail:/person/999:zh-CN")
        with patch.object(tmdb_source, "_request_json") as request, self.assertRaises(ValueError):
            tmdb_people.retry_snapshot(malformed, repository=self.repo)
        request.assert_not_called()

    def test_wrong_response_identity_and_network_safety_errors_are_not_optional(self):
        snapshot = self.fetch(("/person/1",))
        for answer in ({"id": 999}, ValueError("TMDB API 响应超出安全大小限制"), ValueError("TMDB API 返回重定向")):
            with self.subTest(answer=answer), patch.object(tmdb_source, "_request_json", side_effect=answer if isinstance(answer, Exception) else None,
                    return_value=answer), self.assertRaises(ValueError):
                tmdb_people.retry_snapshot(snapshot, repository=self.repo)

    def test_parallel_failures_capture_every_person_once(self):
        self.credits = {"id": 1001, "cast": [{"id": identity, "name": f"演员 {identity}",
            "roles": [{"character": f"角色 {identity}", "episode_count": 2}]} for identity in range(1, 81)], "crew": []}
        snapshot = self.fetch({f"/person/{identity}" for identity in range(1, 81)})
        failures = snapshot["retry_failures"]
        self.assertEqual(len(failures), 80)
        self.assertEqual(len({row["id"] for row in failures}), 80)
        self.assertEqual({row["object_id"] for row in failures}, {str(identity) for identity in range(1, 81)})


if __name__ == "__main__":
    unittest.main()
