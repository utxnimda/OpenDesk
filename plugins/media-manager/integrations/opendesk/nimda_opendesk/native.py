"""pywebview stays on the process main thread; stdio runs separately."""
from pathlib import Path
import threading

from .protocol import ProtocolError


class NativeWindows:
    def __init__(self, storage):
        self.storage = Path(storage)
        self.window = None
        self.controller = None
        self.webview = None
        self.ready = threading.Event()
        self.pending_url = None
        self.exiting = False

    def navigate(self, url):
        self.pending_url = url
        if self.ready.is_set() and self.window is not None:
            self.window.load_url(url)

    def present(self, mode):
        if not self.ready.is_set() or self.window is None:
            raise ProtocolError("MODULE_NOT_READY", "The 作品管理器 desktop window is still starting.")
        if mode == "popup":
            self.window.show()
            self.window.restore()
        else:
            self.window.hide()

    def configure_services(self):
        from work_catalog_yaml.common.native_dialogs import set_directory_picker
        from work_catalog_yaml.common import source_browser

        def choose(initial):
            if not self.ready.is_set():
                raise ValueError("作品管理器 桌面窗口仍在初始化，请稍后重试。")
            from work_catalog_yaml.paths import normalize_copied_path
            directory = normalize_copied_path(initial)
            if directory and not Path(directory).is_dir():
                directory = ""
            selected = self.window.create_file_dialog(self.webview.FOLDER_DIALOG, directory=directory, allow_multiple=False)
            return selected[0] if selected else ""

        def create_window(*args, **kwargs):
            if not self.ready.is_set() or self.exiting:
                raise ValueError("作品管理器 桌面窗口尚未就绪或正在退出。")
            return self.webview.create_window(*args, **kwargs)

        set_directory_picker(choose)
        source_browser.configure(window_factory=create_window)

    def cleanup_services(self):
        from work_catalog_yaml.common.native_dialogs import set_directory_picker
        from work_catalog_yaml.common import source_browser
        set_directory_picker(None)
        source_browser.shutdown()

    def destroy(self):
        self.exiting = True
        if self.ready.is_set() and self.window is not None:
            self.window.destroy()

    def run(self, controller):
        import webview
        self.webview, self.controller = webview, controller
        self.storage.mkdir(parents=True, exist_ok=True)
        self.window = webview.create_window("作品管理器", "about:blank", width=1480, height=920,
                                           min_size=(1050, 680), background_color="#101827",
                                           text_select=True, hidden=True)

        def closing(*_):
            if self.exiting:
                return True
            self.window.hide()
            controller.popup_closed()
            return False

        self.window.events.closing += closing

        def started():
            self.ready.set()
            if self.exiting:
                self.window.destroy()
            elif self.pending_url:
                self.window.load_url(self.pending_url)

        webview.start(started, gui="edgechromium", debug=False, private_mode=False, storage_path=str(self.storage))
