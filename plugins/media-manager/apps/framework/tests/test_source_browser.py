"""Rating-only desktop browser tests: inert windows, no real network or UI."""
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from work_catalog_yaml.common import source_browser as browser

URL = "https://movie.douban.com/subject/1292052/"


class Event:
    def __init__(self, ready=True):
        self.ready, self.handlers = ready, []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def is_set(self):
        return self.ready

    def fire(self):
        self.ready = True
        for handler in self.handlers:
            handler()


class Window:
    def __init__(self, url):
        self.url, self.calls, self.destroyed = url, [], False
        self.events = SimpleNamespace(shown=Event(), loaded=Event(False), before_load=Event(), closed=Event(False))
        self.uid = "rating-fixture"
        self.gui = SimpleNamespace(renderer="edgechromium", get_current_url=lambda uid: self.url,
                                   BrowserView=SimpleNamespace(instances={self.uid: SimpleNamespace(pywebview_window=self)}))
        self.payload = {"status": "ready", "url": url, "title": "作品 (豆瓣)", "name": "作品", "rating": "8.2", "votes": "1234"}
        self.block, self.after_script, self.error = None, None, None

    def get_current_url(self):
        raise AssertionError("Edge native getter must not wait for the application bridge")

    def run_js(self, script):
        self.calls.append(script)
        if self.block is not None:
            self.block.wait(3)
        if self.error:
            raise RuntimeError(self.error)
        if self.after_script:
            self.url = self.after_script
        return json.dumps(self.payload, ensure_ascii=False)

    def destroy(self):
        self.destroyed = True
        self.gui.BrowserView.instances.pop(self.uid, None)
        if hasattr(self.events.closed, "fire"):
            self.events.closed.fire()
        else:
            self.events.closed.set()


class RatingBrowserTest(unittest.TestCase):
    def setUp(self):
        self.windows, self.creations = [], []

        def factory(title, url, **kwargs):
            self.creations.append((title, url, kwargs))
            window = Window(url)
            self.windows.append(window)
            return window

        browser.configure(window_factory=factory)

    def tearDown(self):
        for window in self.windows:
            if window.block:
                window.block.set()
        browser.shutdown()

    def wait_result(self, handle):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            value = browser.capture(handle)
            if value["status"] != "loading":
                return value
            time.sleep(0.005)
        self.fail("inert rating reader timed out")

    def test_subject_only_url_whitelist_rejects_people_and_other_sites(self):
        for url in ("https://movie.douban.com/celebrity/7/", "https://www.douban.com/personage/7/",
                    "https://www.douban.com/search?q=a", "http://movie.douban.com/subject/1/",
                    "https://movie.douban.com:443/subject/1/", "https://u@movie.douban.com/subject/1/",
                    "https://movie.douban.com/subject/1/?token=PRIVATE", "https://movie.douban.com/subject/1/#x",
                    "https://movie.douban.com/subject/1/?", "https://movie.douban.com/subject/0/",
                    "file:///c:/private", "https://evil.test/subject/1/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                browser.open_page(url)
        self.assertEqual(self.creations, [])
        self.assertEqual(browser._url(URL.rstrip("/")), URL)

    def test_visible_window_has_no_application_bridge_and_returns_only_rating(self):
        handle = browser.open_page(URL)
        self.assertIsNone(self.creations[0][2]["js_api"])
        self.assertIs(self.creations[0][2]["hidden"], False)
        result = self.wait_result(handle)
        self.assertEqual((result["status"], result["url"]), ("ready", URL))
        self.assertIn('"ratingValue": 8.2', result["html"])
        self.assertIn('"ratingCount": 1234', result["html"])
        self.assertNotIn("actor", result["html"])
        self.assertNotIn("description", result["html"])
        self.assertFalse(hasattr(browser, "navigate"), "rating reader never follows linked people")

    def test_external_navigation_and_identity_change_are_not_captured(self):
        for target in ("https://accounts.douban.com/login?token=PRIVATE", "https://movie.douban.com/subject/7/",
                       "https://www.douban.com/personage/7/"):
            with self.subTest(target=target):
                handle = browser.open_page(URL)
                window = self.windows[-1]
                window.url = target
                result = self.wait_result(handle)
                self.assertEqual(result["status"], "verification")
                self.assertEqual(window.calls, [])
                self.assertNotIn("PRIVATE", json.dumps(result))
                browser.close_page(handle)
        handle = browser.open_page(URL)
        self.windows[-1].after_script = "https://movie.douban.com/subject/7/"
        self.assertEqual(self.wait_result(handle)["status"], "verification")

    def test_payload_cannot_include_people_html_or_unbounded_invalid_ratings(self):
        for extra in ({"html": "PRIVATE"}, {"actor": []}, {"summary": "PRIVATE"}, {"url": "https://movie.douban.com/subject/7/"},
                      {"rating": False}, {"rating": "NaN"}, {"rating": 0}, {"rating": 11}, {"votes": -1}, {"votes": "1.2"},
                      {"title": "安全验证 PRIVATE"}):
            with self.subTest(extra=extra):
                handle = browser.open_page(URL)
                self.windows[-1].payload.update(extra)
                result = self.wait_result(handle)
                self.assertEqual(result["status"], "failed")
                self.assertNotIn("html", result)
                self.assertNotIn("PRIVATE", json.dumps(result))
                browser.close_page(handle)

    def test_fixed_diagnostics_do_not_log_remote_exception_or_page_text(self):
        handle = browser.open_page(URL)
        self.windows[-1].error = "PRIVATE_COOKIE"
        result = self.wait_result(handle)
        self.assertEqual(result["code"], "native-error")
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_rating_projection_diagnostics_are_bounded_and_never_become_imported_html(self):
        handle = browser.open_page(URL)
        diagnostic = {"jsonld_blocks": 2, "invalid_jsonld_blocks": 1, "rating_source": "dom"}
        self.windows[-1].payload["diagnostics"] = diagnostic
        result = self.wait_result(handle)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["diagnostics"], diagnostic)
        self.assertNotIn("diagnostics", result["html"])
        self.assertIn('"ratingValue": 8.2', result["html"])
        browser.close_page(handle)
        for invalid in ({**diagnostic, "html": "PRIVATE"}, {**diagnostic, "jsonld_blocks": 33},
                        {**diagnostic, "invalid_jsonld_blocks": 3}, {**diagnostic, "invalid_jsonld_blocks": True},
                        {**diagnostic, "rating_source": "PRIVATE"}, {"rating_source": "dom"}, "PRIVATE"):
            with self.subTest(invalid=invalid):
                handle = browser.open_page(URL)
                self.windows[-1].payload["diagnostics"] = invalid
                result = self.wait_result(handle)
                self.assertEqual(result["code"], "invalid-public-data")
                self.assertNotIn("html", result)
                self.assertNotIn("PRIVATE", json.dumps(result))
                browser.close_page(handle)

    def test_no_rating_and_verification_are_not_successful_empty_captures(self):
        for status, code in (("failed", "no-rating"), ("failed", "rating-conflict"),
                             ("failed", "invalid-jsonld"), ("failed", "projection-limit"),
                             ("verification", "page-verification")):
            handle = browser.open_page(URL)
            self.windows[-1].payload = {"status": status, "code": code}
            result = self.wait_result(handle)
            self.assertEqual((result["status"], result["code"]), (status, code))
            self.assertNotIn("html", result)
            browser.close_page(handle)

    def test_loading_and_javascript_timeouts_are_bounded(self):
        handle = browser.open_page(URL)
        window = self.windows[-1]
        window.events.shown.ready = False
        browser._pages[handle].load_started -= browser.LOAD_TIMEOUT + 1
        self.assertEqual(browser.capture(handle)["code"], "load-timeout")
        self.assertEqual(window.calls, [])
        browser.close_page(handle)
        handle = browser.open_page(URL)
        window = self.windows[-1]
        window.block = threading.Event()
        self.assertEqual(browser.capture(handle)["status"], "loading")
        browser._pages[handle].started -= browser.CAPTURE_TIMEOUT + 1
        self.assertEqual(browser.capture(handle)["code"], "capture-timeout")
        browser.close_page(handle)
        window.block.set()
        self.assertEqual(browser.capture(handle)["status"], "closed")

    def test_before_load_gate_never_waits_for_application_bridge(self):
        handle = browser.open_page(URL)
        window = self.windows[-1]
        window.events.before_load.ready = False
        result = browser._capture_once(browser._pages[handle], URL)
        self.assertEqual(result["status"], "loading")
        self.assertEqual(window.calls, [])
        window.events.before_load.ready = True
        self.assertEqual(self.wait_result(handle)["status"], "ready")

    def test_window_limit_closing_and_shutdown_disable_all_owned_windows(self):
        handles = [browser.open_page(URL) for _ in range(browser.MAX_WINDOWS)]
        with self.assertRaises(ValueError):
            browser.open_page(URL)
        browser.close_page(handles[0])
        self.assertEqual(browser.capture(handles[0])["status"], "closed")
        browser.shutdown()
        self.assertFalse(browser.available())
        self.assertTrue(all(window.destroyed for window in self.windows))
        with self.assertRaises(ValueError):
            browser.open_page(URL)

    def test_closed_reasons_distinguish_missing_native_explicit_and_shutdown(self):
        missing = browser.capture("0" * 32)
        self.assertEqual((missing["status"], missing["code"], missing["reason_code"]),
                         ("failed", "handle-unavailable", "handle_missing"))
        handle = browser.open_page(URL)
        self.windows[-1].destroy()
        self.assertEqual(browser.capture(handle)["reason_code"], "native_closed")
        handle = browser.open_page(URL)
        browser.close_page(handle)
        self.assertEqual(browser.capture(handle)["reason_code"], "explicit_close")
        handle = browser.open_page(URL)
        browser.shutdown()
        self.assertEqual(browser.capture(handle)["reason_code"], "application_shutdown")

    def test_unregistered_or_expired_history_never_claims_native_window_is_closed(self):
        handle = browser.open_page(URL)
        window = self.windows[-1]
        page = browser._owned.pop(handle)
        browser._pages.pop(handle)
        try:
            result = browser.capture(handle)
            self.assertEqual((result["status"], result["reason_code"]), ("failed", "handle_missing"))
            self.assertFalse(window.destroyed)
            self.assertEqual(window.calls, [])
            self.assertEqual(len(self.creations), 1)
        finally:
            browser._owned[handle] = browser._pages[handle] = page
        browser.close_page(handle)
        browser._closed.pop(handle)
        result = browser.capture(handle)
        self.assertEqual((result["status"], result["reason_code"]), ("failed", "handle_missing"))

    def test_lost_active_index_restores_only_exact_live_owned_window(self):
        handle = browser.open_page(URL)
        page = browser._pages.pop(handle)
        self.assertIs(browser._owned[handle], page)
        self.assertEqual(self.wait_result(handle)["status"], "ready")
        self.assertIs(browser._pages[handle], page)
        self.assertEqual(len(self.creations), 1)
        browser._pages.pop(handle)
        self.windows[-1].gui.BrowserView.instances.clear()
        self.assertEqual(browser.capture(handle)["reason_code"], "native_closed")
        self.assertNotIn(handle, browser._owned)
        self.assertEqual(len(self.creations), 1)

    def test_unproven_or_foreign_window_is_not_attached(self):
        handle = browser.open_page(URL)
        browser._pages.pop(handle)
        window = self.windows[-1]
        del window.gui.BrowserView
        self.assertEqual(browser.capture(handle)["reason_code"], "handle_unconfirmed")
        self.assertEqual(window.calls, [])
        self.assertEqual(len(self.creations), 1)
        window.gui.BrowserView = SimpleNamespace(instances={window.uid: SimpleNamespace(pywebview_window=object())})
        self.assertEqual(browser.capture(handle)["reason_code"], "native_closed")
        self.assertNotIn(handle, browser._pages)

    def test_stale_closed_callback_and_wrong_window_cannot_unregister_live_owner(self):
        handle = browser.open_page(URL)
        window, page = self.windows[-1], browser._pages[handle]
        window.events.closed.fire()
        self.assertIs(browser._pages[handle], page)
        self.assertFalse(page.closed)
        window.events.closed.handlers[0](object())
        self.assertIs(browser._pages[handle], page)
        self.assertEqual(self.wait_result(handle)["status"], "ready")
        window.destroy()
        self.assertEqual(browser.capture(handle)["reason_code"], "native_closed")

    def test_explicit_close_keeps_strong_owner_until_native_close_and_never_recovers(self):
        handle = browser.open_page(URL)
        window, page = self.windows[-1], browser._pages[handle]
        with patch.object(window, "destroy"):
            browser.close_page(handle, reason_code="session_expired")
        self.assertIs(browser._owned[handle].window, window)
        self.assertNotIn(handle, browser._pages)
        self.assertEqual(browser.capture(handle)["reason_code"], "session_expired")
        self.assertEqual(window.calls, [])
        window.destroy()
        self.assertNotIn(handle, browser._owned)
        self.assertEqual(browser.capture(handle)["reason_code"], "session_expired")

    def test_duplicate_factory_cannot_destroy_or_replace_existing_owner(self):
        handle = browser.open_page(URL)
        window, page = self.windows[-1], browser._pages[handle]
        with patch.object(browser, "_factory", return_value=window):
            with self.assertRaises(ValueError):
                browser.open_page(URL)
        self.assertIs(browser._pages[handle], page)
        self.assertFalse(window.destroyed)
        self.assertEqual(self.wait_result(handle)["status"], "ready")

    def test_old_main_window_callback_cannot_shutdown_new_configuration(self):
        first = SimpleNamespace(events=SimpleNamespace(closed=Event(False)))
        factory = browser._factory
        browser.configure(window_factory=factory, main_window=first)
        browser.configure(window_factory=factory)
        handle = browser.open_page(URL)
        first.events.closed.fire()
        self.assertTrue(browser.available())
        self.assertEqual(self.wait_result(handle)["status"], "ready")

    def test_owned_limit_does_not_depend_on_active_index_and_tombstones_are_bounded(self):
        handles = [browser.open_page(URL) for _ in range(browser.MAX_WINDOWS)]
        browser._pages.clear()
        with self.assertRaises(ValueError):
            browser.open_page(URL)
        for handle in handles:
            browser.close_page(handle)
        with patch.object(browser, "_CLOSED_LIMIT", 2):
            for _ in range(3):
                browser.close_page(browser.open_page(URL))
            self.assertLessEqual(len(browser._closed), 2)

    def test_structured_diagnostics_redact_arbitrary_handles_urls_and_native_errors(self):
        with self.assertLogs("nimda.source_browser", level="INFO") as logs:
            handle = browser.open_page(URL)
            self.windows[-1].error = "PRIVATE_COOKIE https://example.test/token=PRIVATE"
            self.wait_result(handle)
            browser.capture("https://accounts.douban.com/?token=PRIVATE")
            browser.close_page(handle)
        text = "\n".join(logs.output)
        self.assertIn("browser_handle=" + handle, text)
        self.assertIn("window_uid=rating-fixture", text)
        self.assertIn("reason_code=explicit_close", text)
        self.assertNotIn("PRIVATE", text)
        self.assertNotIn("https://", text)

    def test_real_pywebview_event_async_order_never_loses_live_window(self):
        try:
            from webview.event import Event as NativeEvent
        except ImportError:
            self.skipTest("pywebview native Event available in desktop build environment")
        window = Window(URL)
        self.windows.append(window)
        window.events = SimpleNamespace(**{name: NativeEvent(window) for name in ("shown", "loaded", "before_load", "closed")})
        window.events.shown.set()
        window.events.before_load.set()
        with patch.object(browser, "_factory", return_value=window):
            handle = browser.open_page(URL)
        observed = threading.Event()
        window.events.closed += lambda: observed.set()
        window.events.closed.set()
        self.assertTrue(observed.wait(1))
        self.assertFalse(browser._owned[handle].closed)
        self.assertEqual(self.wait_result(handle)["status"], "ready")
        observed.clear()
        window.events.closed.clear()
        # Force the actual pywebview callback to finish before Event.set marks
        # its internal flag, matching the ordering allowed by pywebview 6.1.
        native_flag_set = window.events.closed._event.set
        def set_after_callback():
            self.assertTrue(observed.wait(1))
            native_flag_set()
        with patch.object(window.events.closed._event, "set", side_effect=set_after_callback):
            window.destroy()
        self.assertTrue(observed.wait(1))
        self.assertEqual(browser.capture(handle)["reason_code"], "native_closed")

    def test_native_close_without_callback_is_detected_before_capture(self):
        handle = browser.open_page(URL)
        window = self.windows[-1]
        window.gui.BrowserView.instances.clear()
        self.assertEqual(browser.capture(handle)["reason_code"], "native_closed")
        self.assertEqual(window.calls, [])

    def test_native_disposed_object_is_not_recovered_even_if_registry_is_stale(self):
        handle = browser.open_page(URL)
        window = self.windows[-1]
        browser._pages.pop(handle)
        window.gui.BrowserView.instances[window.uid].IsDisposed = True
        self.assertEqual(browser.capture(handle)["reason_code"], "native_closed")
        self.assertNotIn(handle, browser._owned)
        self.assertEqual(window.calls, [])

    def test_close_before_callback_registration_is_not_reported_as_live(self):
        window = Window(URL)
        self.windows.append(window)
        window.events.shown.ready = False
        window.destroy()
        with patch.object(browser, "_factory", return_value=window):
            handle = browser.open_page(URL)
        self.assertEqual(browser.capture(handle)["reason_code"], "native_closed")
        self.assertNotIn(handle, browser._owned)

    def test_failed_destroy_retains_owner_and_blocks_capture_without_disclosing_exception(self):
        handle = browser.open_page(URL)
        window = self.windows[-1]
        with patch.object(window, "destroy", side_effect=RuntimeError("PRIVATE")):
            with self.assertLogs("nimda.source_browser", level="INFO") as logs:
                browser.shutdown()
        self.assertIs(browser._owned[handle].window, window)
        self.assertEqual(browser.capture(handle)["reason_code"], "application_shutdown")
        self.assertNotIn("PRIVATE", "\n".join(logs.output))
        window.destroy()
        self.assertNotIn(handle, browser._owned)

    def test_old_registration_callback_cannot_unregister_reused_window_object(self):
        first = browser.open_page(URL)
        window = self.windows[-1]
        old_callback = window.events.closed.handlers[0]
        browser.close_page(first)
        window.destroyed = False
        window.events.closed.ready = False
        window.gui.BrowserView.instances[window.uid] = SimpleNamespace(pywebview_window=window)
        with patch.object(browser, "_factory", return_value=window):
            current = browser.open_page(URL)
        old_callback(window)
        self.assertFalse(browser._owned[current].closed)
        self.assertEqual(self.wait_result(current)["status"], "ready")
        self.assertEqual(browser.capture(first)["reason_code"], "explicit_close")


if __name__ == "__main__":
    unittest.main()
