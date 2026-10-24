"""The desktop rating flow reads one work only and uses the normal writer."""
import json
import unittest
from unittest.mock import patch

from catalog_library import douban_browser as browser, douban_source, providers
from collection_detail.catalog_edit_service import catalog_records
from work_catalog_yaml.common import source_browser
from work_catalog_yaml.yaml_io import dump_yaml_string
import test_catalog_library_providers as fixtures


class RatingBrowserTest(unittest.TestCase):
    ref = fixtures.ProviderImportTest.ref

    def setUp(self):
        fixtures.ProviderImportTest.setUp(self)
        browser.shutdown()
        self.addCleanup(browser.shutdown)
        self.url = "https://movie.douban.com/subject/123/"
        self.movie = {"@type": "Movie", "url": self.url, "name": "Not the local title",
                      "description": "Never import this", "actor": [{"name": "Never create this", "url": "https://www.douban.com/personage/1/"}],
                      "aggregateRating": {"ratingValue": 8.7, "ratingCount": 234, "bestRating": 10}}
        self.opened, self.closed, self.close_reasons = [], [], []
        self.capture = {"status": "ready", "url": self.url, "html": self.html()}
        for name, implementation in (
            ("available", lambda: True), ("open_page", lambda url: self.opened.append(url) or "test-window"),
            ("close_page", lambda handle, **kwargs: (self.closed.append(handle), self.close_reasons.append(kwargs.get("reason_code")))),
            ("capture", lambda _handle: self.capture),
        ):
            mock = patch.object(source_browser, name, side_effect=implementation)
            mock.start()
            self.addCleanup(mock.stop)
        network = patch("socket.socket.connect", side_effect=AssertionError("No network in browser workflow"))
        network.start()
        self.addCleanup(network.stop)

    def html(self):
        return '<html><head><link rel="canonical" href="' + self.url + '"><script type="application/ld+json">' + json.dumps(self.movie) + '</script></head></html>'

    def start(self, **updates):
        return browser.start({"ref": self.ref(), "subject_id": 123, **updates}, settings=self.settings, source_root=self.sources)

    def step(self, session, **updates):
        return browser.step({"ref": session["ref"], "session_id": session["session_id"], **updates}, settings=self.settings, source_root=self.sources)

    def test_one_page_generates_rating_only_preview_and_confirms_via_shared_writer(self):
        before = self.file.read_bytes()
        session = self.start()
        result = self.step(session)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(result["status"], "complete")
        self.assertEqual((result["completed"], result["total"]), (1, 1))
        self.assertEqual(self.opened, [self.url])
        self.assertEqual(self.closed, ["test-window"])
        preview = result["preview"]
        self.assertEqual({row["field"] for row in preview["diff"]}, {"metadata.ratings.douban"})
        self.assertFalse(preview.get("people_preview"))
        self.assertFalse(preview.get("automatic_aliases"))
        result = providers.apply_provider_import({"ref": preview["ref"], "provider": "douban", "snapshot_id": preview["snapshot_id"],
            "preview_token": preview["preview_token"], "selected_fields": ["metadata.ratings.douban"]}, settings=self.settings, source_root=self.sources)
        self.assertTrue(result["db_committed"])
        saved = catalog_records(self.settings)[0]["record"]
        self.assertEqual(saved["attributes"], self.record["attributes"])
        self.assertEqual(saved["metadata"]["summary"], self.record["metadata"]["summary"])
        self.assertNotIn("aliases", saved["metadata"])
        self.assertNotIn("people_refs", saved)
        self.assertEqual(saved["metadata"]["ratings"]["douban"]["score"], 8.7)

    def test_completed_continue_is_idempotent_without_capturing_again(self):
        result = self.step(self.start())
        with patch.object(source_browser, "capture", side_effect=AssertionError("already captured")):
            repeated = self.step(result)
        self.assertEqual(repeated["preview"], result["preview"])
        self.assertEqual((repeated["completed"], repeated["total"]), (1, 1))

    def test_async_capture_states_are_explicit_and_log_only_safe_context(self):
        before = self.file.read_bytes()
        with patch.object(browser, "report_progress") as reporter:
            session = self.start()
            self.capture = {"status": "loading", "reason_code": "capture_loading"}
            pending = self.step(session)
            self.capture = {"status": "failed", "code": "handle-unavailable", "reason_code": "handle_unconfirmed"}
            failed = self.step(session)
            self.assertEqual((pending["result_type"], pending["code"], pending["auto_continue"]),
                             ("douban-rating-read", "capture_loading", True))
            self.assertEqual((failed["status"], failed["warnings"][0]["code"], failed["auto_continue"]),
                             ("failed", "handle_unconfirmed", False))
            self.capture = {"status": "ready", "url": self.url, "html": self.html()}
            ready = self.step(session)
        self.assertEqual((ready["completed"], ready["code"]), (1, "preview_ready"))
        self.assertEqual(self.close_reasons, ["session_cleanup"])
        contexts = [call.kwargs["context"] for call in reporter.call_args_list]
        self.assertTrue(any(row["action"] == "start" and row["object"] == session["session_id"] for row in contexts))
        self.assertTrue(any(row["actual"] == "test-window" and row["code"] == "handle_unconfirmed" for row in contexts))
        self.assertNotIn("Never", str(contexts))
        self.assertNotIn("preview_token", str(contexts))
        self.assertEqual(self.file.read_bytes(), before)

    def test_failed_preview_retry_uses_captured_rating_without_reading_again(self):
        session = self.start()
        with patch.object(providers, "preview_provider_import", side_effect=ValueError("temporary preview issue")), self.assertRaisesRegex(ValueError, "temporary"):
            self.step(session)
        with patch.object(source_browser, "capture", side_effect=AssertionError("already captured")):
            self.assertEqual(self.step(session)["status"], "complete")

    def test_capture_diagnostics_log_fallback_counts_without_warning_or_database_write(self):
        before = self.file.read_bytes()
        self.capture["diagnostics"] = {"jsonld_blocks": 3, "invalid_jsonld_blocks": 2, "rating_source": "dom",
                                       "remote_error": "PRIVATE_SCRIPT_OR_CREDENTIAL", "html": "PRIVATE_PAGE"}
        self.capture["unknown"] = "PRIVATE_BROWSER_FIELD"
        with patch.object(browser, "report_progress") as reporter:
            session = self.start()
            result = self.step(session)
        self.assertEqual(result["status"], "complete")
        self.assertNotIn("warnings", result)
        self.assertEqual({row["field"] for row in result["preview"]["diff"]}, {"metadata.ratings.douban"})
        self.assertEqual(self.file.read_bytes(), before)
        calls = [call for call in reporter.call_args_list if call.kwargs["context"]["code"] == "rating_ready_invalid_jsonld_skipped"]
        self.assertEqual(len(calls), 1)
        message, context = calls[0].args[0], calls[0].kwargs["context"]
        self.assertIn("页面评分区域", message)
        self.assertIn("JSON-LD 共 3 块", message)
        self.assertIn("跳过语法无效的附加 JSON-LD 2 块", message)
        self.assertEqual((context["source_path"], context["object"], context["stage"], context["status"]),
                         (self.url, session["session_id"], "豆瓣评分读取", "ready"))
        self.assertNotIn("PRIVATE", str(reporter.call_args_list))
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertNotIn("diagnostics", browser._sessions[session["session_id"]].snapshot)

    def test_capture_diagnostics_describe_all_supported_rating_sources(self):
        before = self.file.read_bytes()
        for source, label in (("dom", "页面评分区域"), ("jsonld", "结构化评分数据"),
                              ("dom+jsonld", "页面评分与结构化评分一致")):
            with self.subTest(source=source):
                self.capture["diagnostics"] = {"jsonld_blocks": 1, "invalid_jsonld_blocks": 0, "rating_source": source}
                with patch.object(browser, "report_progress") as reporter:
                    result = self.step(self.start())
                self.assertEqual(result["status"], "complete")
                self.assertNotIn("warnings", result)
                calls = [call for call in reporter.call_args_list if call.kwargs["context"]["code"] == "rating_ready_diagnostics"]
                self.assertEqual(len(calls), 1)
                self.assertIn(label, calls[0].args[0])
                self.assertIn("JSON-LD 0 块", calls[0].args[0])
        self.assertEqual(self.file.read_bytes(), before)

    def test_absent_or_invalid_diagnostics_do_not_leak_or_change_legacy_preview(self):
        before = self.file.read_bytes()
        cases = (None, "PRIVATE_DIAGNOSTIC", {},
                 {"jsonld_blocks": 1, "invalid_jsonld_blocks": 2, "rating_source": "dom"},
                 {"jsonld_blocks": True, "invalid_jsonld_blocks": 0, "rating_source": "dom"},
                 {"jsonld_blocks": 33, "invalid_jsonld_blocks": 0, "rating_source": "dom"},
                 {"jsonld_blocks": 1, "invalid_jsonld_blocks": 0, "rating_source": "PRIVATE_SOURCE"})
        for diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                self.capture["diagnostics"] = diagnostic
                with patch.object(browser, "report_progress") as reporter:
                    result = self.step(self.start())
                self.assertEqual(result["status"], "complete")
                self.assertFalse(any(call.kwargs["context"]["code"].startswith("rating_ready_") for call in reporter.call_args_list))
                self.assertNotIn("PRIVATE", str(reporter.call_args_list))
        self.assertEqual(self.file.read_bytes(), before)

    def test_login_verification_pauses_without_snapshot_or_db_change(self):
        before = self.file.read_bytes()
        session = self.start()
        self.capture = {"status": "verification"}
        result = self.step(session)
        self.assertEqual(result["status"], "verification")
        self.assertFalse(result["auto_continue"])
        self.assertTrue(result["warnings"])
        self.assertFalse(self.sources.exists())
        self.assertEqual(self.file.read_bytes(), before)
        self.capture = {"status": "ready", "url": self.url, "html": self.html()}
        self.assertEqual(self.step(session)["status"], "complete")

    def test_reopen_after_preview_failure_discards_old_capture_and_reads_again(self):
        before = self.file.read_bytes()
        session = self.start()
        with patch.object(providers, "preview_provider_import", side_effect=ValueError("temporary preview issue")), self.assertRaises(ValueError):
            self.step(session)
        self.assertIsNotNone(browser._sessions[session["session_id"]].snapshot)
        self.assertEqual(self.step(session, action="reopen")["status"], "loading")
        self.assertIsNone(browser._sessions[session["session_id"]].snapshot)
        self.assertEqual(self.opened, [self.url, self.url])
        self.movie["aggregateRating"]["ratingValue"] = 9.1
        self.capture["html"] = self.html()
        result = self.step(session)
        rating = next(row for row in result["preview"]["diff"] if row["field"] == "metadata.ratings.douban")
        self.assertEqual(rating["after"]["score"], 9.1)
        self.assertEqual(self.file.read_bytes(), before)

    def test_missing_rating_and_other_work_do_not_create_empty_rating(self):
        before = self.file.read_bytes()
        session = self.start()
        self.movie.pop("aggregateRating")
        self.capture["html"] = self.html()
        self.assertEqual(self.step(session)["status"], "failed")
        self.capture["url"] = "https://movie.douban.com/subject/999/"
        self.assertEqual(self.step(session)["status"], "failed")
        self.assertFalse(self.sources.exists())
        self.assertEqual(self.file.read_bytes(), before)

    def test_person_or_client_html_script_fields_are_rejected_before_open(self):
        for update in ({"kind": "person"}, {"html": "private"}, {"script": "private"}, {"person_id": "p1"}, {"binding_role": "primary"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.start(**update)
        self.assertEqual(self.opened, [])

    def test_skip_and_finish_are_not_supported(self):
        session = self.start()
        for action in ("skip", "finish", "navigate"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.step(session, action=action)

    def test_wrong_ref_and_changed_database_cannot_complete(self):
        session = self.start()
        with self.assertRaises(ValueError):
            self.step(session, ref={**session["ref"], "index_in_file": 2})
        self.record["metadata"]["summary"] = "new local value"
        self.file.write_text(dump_yaml_string([self.record]), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.step(session)
        self.assertEqual(self.closed, ["test-window"])
        self.assertFalse(self.sources.exists())

    def test_cancel_and_reopen_never_follow_a_person_page(self):
        session = self.start()
        reopened = self.step(session, action="reopen")
        self.assertEqual(reopened["status"], "loading")
        self.assertEqual(self.opened, [self.url, self.url])
        result = browser.cancel({"ref": session["ref"], "session_id": session["session_id"]}, settings=self.settings)
        self.assertEqual(result["status"], "closed")
        self.assertEqual(result["code"], "session_cancelled")
        self.assertEqual(self.close_reasons, ["session_replaced", "explicit_close"])
        with self.assertRaises(ValueError):
            self.step(session)

    def test_single_active_session_and_expiry_are_bounded(self):
        session = self.start()
        with self.assertRaisesRegex(ValueError, "已有"):
            self.start()
        browser._sessions[session["session_id"]].created -= browser.SESSION_LIFETIME + 1
        with self.assertRaisesRegex(ValueError, "过期"):
            self.step(session)
        self.assertEqual(self.closed, ["test-window"])

    def test_unknown_host_handle_is_failed_not_falsely_closed(self):
        session = self.start()
        self.capture = {"status": "failed", "code": "handle-unavailable", "reason_code": "handle_missing",
                        "message": "评分窗口登记暂时无法核实"}
        result = self.step(session)
        self.assertEqual((result["status"], result["code"]), ("failed", "handle_missing"))
        self.assertFalse(result["auto_continue"])
        self.assertEqual(self.closed, [])

    def test_cancel_during_reopen_closes_new_window_without_orphan(self):
        session = self.start()
        def open_after_cancel(_url):
            browser.cancel({"ref": session["ref"], "session_id": session["session_id"]}, settings=self.settings)
            return "new-window-after-cancel"
        with patch.object(source_browser, "open_page", side_effect=open_after_cancel):
            result = self.step(session, action="reopen")
        self.assertEqual(result["status"], "closed")
        self.assertIn("new-window-after-cancel", self.closed)
        self.assertNotIn(session["session_id"], browser._sessions)

    def test_cancel_while_checking_catalog_never_opens_new_window(self):
        session = self.start()
        def cancel_while_checking(_row, _settings):
            browser.cancel({"ref": session["ref"], "session_id": session["session_id"]}, settings=self.settings)
        with patch.object(browser, "_current", side_effect=cancel_while_checking), \
             patch.object(source_browser, "open_page", side_effect=AssertionError("cancelled request cannot open")):
            self.assertEqual(self.step(session, action="reopen")["status"], "closed")


if __name__ == "__main__":
    unittest.main()
