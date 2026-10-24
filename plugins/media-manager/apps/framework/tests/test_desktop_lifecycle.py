"""Desktop lifecycle regressions without opening windows or touching app data."""
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from work_catalog_yaml import desktop
from work_catalog_yaml.common import native_dialogs, source_browser


class ClosedEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        for handler in self.handlers:
            handler()


class DesktopLifecycleTest(unittest.TestCase):
    def run_shell(self, *, fail=False):
        root = Path("isolated-desktop-fixture")
        configuration = desktop.DesktopConfiguration(root, root, None)
        server = Mock(url="http://127.0.0.1:8765")
        instance = Mock()
        instance.acquire.return_value = True
        closed = ClosedEvent()
        window = SimpleNamespace(events=SimpleNamespace(closed=closed))

        def start(**_kwargs):
            self.assertEqual(len(closed.handlers), 1)
            self.assertEqual(picker.call_count, 1)
            rating_configure.assert_called_once_with(window_factory=webview.create_window)
            if fail:
                raise RuntimeError("fixture window initialization failed")
            closed.fire()
            rating_shutdown.assert_called_once()
            server.stop.assert_called_once()

        webview = SimpleNamespace(create_window=Mock(return_value=window), start=start)
        with ExitStack() as stack:
            stack.enter_context(patch.dict("sys.modules", {"webview": webview}))
            for name, result in {
                "resolve_desktop_workspace": root,
                "load_desktop_configuration": configuration,
                "configure_desktop_workspace": None,
                "_configure_desktop_logging": root / "app.log",
                "_load_application": object(),
                "_desktop_storage_path": root / "webview",
                "_write_runtime_state": root / "runtime.json",
                "SingleInstance": instance,
                "DesktopServer": server,
                "_show_error": None,
            }.items():
                stack.enter_context(patch.object(desktop, name, return_value=result))
            removed = stack.enter_context(patch.object(desktop, "_remove_runtime_state"))
            picker = stack.enter_context(patch.object(native_dialogs, "set_directory_picker"))
            rating_configure = stack.enter_context(patch.object(source_browser, "configure"))
            rating_shutdown = stack.enter_context(patch.object(source_browser, "shutdown"))
            registered = stack.enter_context(patch.object(desktop.atexit, "register"))
            unregistered = stack.enter_context(patch.object(desktop.atexit, "unregister"))
            stack.enter_context(patch.object(desktop, "_LOG"))
            result = desktop.run_desktop()

        self.assertEqual(result, 1 if fail else 0)
        self.assertEqual(webview.create_window.call_count, 1)
        server.start.assert_called_once()
        self.assertEqual(server.stop.call_count, 1 if fail else 2)
        self.assertEqual(rating_shutdown.call_count, 1 if fail else 2)
        registered.assert_called_once_with(server.stop)
        unregistered.assert_called_once_with(server.stop)
        removed.assert_called_once_with(root / "runtime.json")
        picker.assert_called_with(None)
        instance.release.assert_called_once()

    def test_closing_the_only_window_stops_server_and_cleans_runtime(self):
        self.run_shell()

    def test_window_initialization_failure_still_stops_server(self):
        self.run_shell(fail=True)


if __name__ == "__main__":
    unittest.main()
