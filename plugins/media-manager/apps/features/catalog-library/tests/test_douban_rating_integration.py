"""Offline host-to-service rating contract; only disposable fixture files exist.

The host capture worker, rating parser and signed provider preview are real.
Only the native Window is inert; network, people reads and confirmed writes
are explicitly forbidden. These tests never open a browser or the user's DB.
"""
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from catalog_library import douban_browser, douban_source, providers
from work_catalog_yaml.common import source_browser
import test_catalog_library_providers as fixtures


class _Event:
    def __init__(self, window, ready=False):
        self.window, self.ready, self.handlers = window, ready, []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def is_set(self):
        return self.ready

    def fire(self):
        self.ready = True
        for handler in self.handlers:
            handler(self.window)


class _Window:
    def __init__(self, url, ordinal):
        self.url, self.uid, self.destroyed = url, f"rating-integration-{ordinal}", False
        self.events = SimpleNamespace(shown=_Event(self, True), before_load=_Event(self, True), closed=_Event(self))
        self.native = SimpleNamespace(pywebview_window=self, IsDisposed=False, Disposing=False)
        self.registry = {self.uid: self.native}
        self.gui = SimpleNamespace(renderer="edgechromium", BrowserView=SimpleNamespace(instances=self.registry),
                                   get_current_url=lambda _uid: self.url)
        self.payload = {"status": "ready", "url": url, "title": "离线作品 (豆瓣)", "name": "离线作品", "rating": "8.7", "votes": "234"}
        self.entered, self.release = threading.Event(), threading.Event()
        self.scripts = []

    def run_js(self, script):
        self.scripts.append(script)
        self.entered.set()
        if not self.release.wait(2):
            raise AssertionError("the test must release its inert capture worker")
        return json.dumps(self.payload, ensure_ascii=False)

    def destroy(self):
        self.destroyed = True
        self.native.IsDisposed = True
        self.registry.pop(self.uid, None)
        self.events.closed.fire()


class RatingIntegrationTest(unittest.TestCase):
    ref = fixtures.ProviderImportTest.ref

    def setUp(self):
        fixtures.ProviderImportTest.setUp(self)
        douban_browser.shutdown()
        source_browser.shutdown()
        self.windows, self.pages = [], []

        def factory(_title, url, **options):
            self.assertIsNone(options["js_api"])
            self.assertFalse(options["hidden"])
            self.assertEqual(url, "https://movie.douban.com/subject/123/")
            window = _Window(url, len(self.windows))
            self.windows.append(window)
            return window

        source_browser.configure(window_factory=factory)
        self.addCleanup(self.close_fixtures)
        self.network = patch("socket.socket.connect", side_effect=AssertionError("No network in offline integration"))
        self.network.start()
        self.addCleanup(self.network.stop)
        for target in ("catalog_library.people.preview_for_import", "catalog_library.douban_source._request_html",
                       "catalog_library.providers._request_json", "catalog_library.providers.apply_provider_import"):
            guard = patch(target, side_effect=AssertionError("No people, network fallback or automatic apply"))
            guard.start()
            self.addCleanup(guard.stop)
        self.before = {path.relative_to(self.db): path.read_bytes() for path in self.db.rglob("*") if path.is_file()}

    def close_fixtures(self):
        for window in self.windows:
            window.release.set()
        for page in self.pages:
            self.wait_until(lambda page=page: not page.pending)
        douban_browser.shutdown()
        source_browser.shutdown()
        for window in self.windows:
            if not window.destroyed:
                window.destroy()

    def wait_until(self, predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.002)
        self.fail("offline capture did not settle")

    def unchanged(self):
        after = {path.relative_to(self.db): path.read_bytes() for path in self.db.rglob("*") if path.is_file()}
        self.assertEqual(after, self.before)

    def start(self):
        session = douban_browser.start({"ref": self.ref(), "subject_id": 123}, settings=self.settings, source_root=self.sources)
        handle = douban_browser._sessions[session["session_id"]].handle
        page = source_browser._owned[handle]
        self.pages.append(page)
        return session, handle, page

    def step(self, session, action="continue"):
        return douban_browser.step({"ref": session["ref"], "session_id": session["session_id"], "action": action},
                                   settings=self.settings, source_root=self.sources)

    def complete(self, session):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            result = self.step(session)
            if result["status"] != "loading":
                self.assertEqual(result["status"], "complete", result)
                return result
            time.sleep(0.002)
        self.fail("offline host result was not consumed by the rating service")

    def test_real_async_capture_flows_to_signed_rating_only_preview_without_database_write(self):
        session, handle, page = self.start()
        self.assertEqual((session["status"], session["auto_continue"], session["completed"]), ("loading", True, 0))
        self.assertEqual(self.windows[0].scripts, [])
        loading = self.step(session)
        self.assertEqual((loading["status"], loading["auto_continue"], loading["code"]), ("loading", True, "capture_loading"))
        self.assertTrue(self.windows[0].entered.wait(1))
        self.assertNotIn("preview", loading)
        self.assertFalse(self.sources.exists())
        self.unchanged()
        self.windows[0].release.set()
        result = self.complete(session)
        preview = result["preview"]
        self.assertEqual((result["status"], result["completed"], result["total"], result["auto_continue"]), ("complete", 1, 1, False))
        self.assertEqual(result["result_type"], "douban-rating-read")
        self.assertTrue(preview["preview_token"])
        self.assertEqual([row["field"] for row in preview["diff"]], ["metadata.ratings.douban"])
        self.assertEqual(preview["diff"][0]["after"]["score"], 8.7)
        self.assertEqual(preview["diff"][0]["after"]["votes"], 234)
        self.assertFalse(preview.get("people_preview"))
        self.assertFalse(preview.get("automatic_aliases"))
        self.assertNotIn("db_committed", result)
        self.assertEqual(len(list(self.sources.glob("*.json"))), 1)
        self.assertTrue(self.windows[0].destroyed)
        self.assertIsNone(page.result)
        self.assertEqual(source_browser.capture(handle)["status"], "closed")
        self.assertEqual(self.step(session)["preview"], preview)
        self.unchanged()

    def test_owned_native_window_recovers_missing_active_index_without_reopening(self):
        session, handle, page = self.start()
        source_browser._pages.pop(handle)
        self.assertEqual(self.step(session)["status"], "loading")
        self.assertIs(source_browser._pages[handle], page)
        self.assertEqual(len(self.windows), 1)
        self.assertFalse(self.windows[0].destroyed)
        self.windows[0].release.set()
        self.complete(session)
        self.unchanged()

    def test_unknown_host_registration_is_failed_not_closed_or_silently_reopened(self):
        session, handle, page = self.start()
        source_browser._pages.pop(handle)
        source_browser._owned.pop(handle)
        try:
            result = self.step(session)
            self.assertEqual((result["status"], result["code"], result["auto_continue"]), ("failed", "handle_missing", False))
            self.assertEqual(len(self.windows), 1)
            self.assertFalse(self.windows[0].destroyed)
            self.assertNotIn("preview", result)
            self.assertFalse(self.sources.exists())
            self.unchanged()
        finally:
            source_browser._owned[handle] = page

    def test_cancel_discards_the_in_flight_host_capture_and_never_creates_preview(self):
        session, handle, page = self.start()
        self.assertEqual(self.step(session)["status"], "loading")
        self.assertTrue(self.windows[0].entered.wait(1))
        cancelled = douban_browser.cancel({"ref": session["ref"], "session_id": session["session_id"]}, settings=self.settings)
        self.assertEqual((cancelled["status"], cancelled["completed"]), ("closed", 0))
        self.windows[0].release.set()
        self.wait_until(lambda: not page.pending)
        self.assertIsNone(page.result)
        self.assertNotIn(session["session_id"], douban_browser._sessions)
        self.assertEqual(source_browser.capture(handle)["reason_code"], "explicit_close")
        self.assertFalse(self.sources.exists())
        self.unchanged()

    def test_reopen_retires_old_capture_and_only_new_window_rating_reaches_preview(self):
        session, old_handle, old_page = self.start()
        self.windows[0].payload["rating"] = "1.2"
        self.assertEqual(self.step(session)["status"], "loading")
        self.assertTrue(self.windows[0].entered.wait(1))
        reopened = self.step(session, "reopen")
        self.assertEqual((reopened["status"], reopened["session_id"]), ("loading", session["session_id"]))
        self.assertEqual(len(self.windows), 2)
        self.assertTrue(self.windows[0].destroyed)
        new_handle = douban_browser._sessions[session["session_id"]].handle
        self.assertNotEqual(new_handle, old_handle)
        self.pages.append(source_browser._owned[new_handle])
        self.windows[0].release.set()
        self.wait_until(lambda: not old_page.pending)
        self.assertIsNone(old_page.result)
        self.windows[1].release.set()
        result = self.complete(session)
        self.assertEqual(result["preview"]["diff"][0]["after"]["score"], 8.7)
        self.assertEqual(source_browser.capture(old_handle)["reason_code"], "session_replaced")
        self.unchanged()


if __name__ == "__main__":
    unittest.main()
