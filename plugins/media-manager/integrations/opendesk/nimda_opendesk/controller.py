"""One module process owns one original 作品管理器 application and workspace mutex."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import unicodedata
from pathlib import Path

from .protocol import ProtocolError
from .security import PREFERENCE, SessionGate

if getattr(sys, "frozen", False):
    # Build tooling generates this Python constant from the package manifest.
    # No runtime parameter or mutable user configuration can change identity.
    from ._build_identity import IDENTITY
else:
    _manifest = json.loads(Path(__file__).resolve().parents[1].joinpath("manifest.json").read_text(encoding="utf-8"))
    IDENTITY = {key: _manifest[key] for key in ("id", "version", "hostApi")}
CAPABILITIES = sorted(["runtime.hello", "runtime.ping", "runtime.shutdown", "module.identity",
                       "module.webapp", "module.prepareShutdown", "nimda.status", "nimda.presentation", "nimda.configure"])
_COPIED_PATH_MARKS = "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff"


def normalize_workspace_path(value):
    if not isinstance(value, str) or len(value) > 4096 or any(unicodedata.category(char) == "Cc" for char in value):
        raise ProtocolError("INVALID_PARAMS", "workspace must be an absolute 作品管理器 workspace path without control characters.")
    # Match work_catalog_yaml.paths.normalize_copied_path: Explorer wrappers
    # are stripped only at the boundaries; actual filename contents stay intact.
    result = value
    while True:
        cleaned = result.strip().strip(_COPIED_PATH_MARKS).strip()
        if len(cleaned) >= 2 and cleaned.startswith('"') and cleaned.endswith('"'):
            cleaned = cleaned[1:-1]
        if cleaned == result:
            if not cleaned:
                raise ProtocolError("INVALID_PARAMS", "workspace must be an absolute 作品管理器 workspace path.")
            return cleaned
        result = cleaned


def application_root(entry: Path) -> Path:
    if getattr(sys, "frozen", False):
        candidates = [Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "application",
                      Path(sys.executable).parent / "application"]
    else:
        configured = os.environ.get("NIMDA_OPENDESK_APPLICATION_ROOT")
        candidates = [Path(configured)] if configured else [entry.resolve().parents[2]]
    for candidate in candidates:
        if (candidate / "apps" / "framework" / "frontend" / "index.html").is_file():
            return candidate.resolve()
    raise RuntimeError("作品管理器 application resources are missing from the module")


def setup_python(root: Path):
    candidates = [root / "apps" / "framework" / "backend", root / "apps" / "composition" / "backend",
                  *sorted((root / "apps" / "features").glob("*/backend"))]
    for candidate in candidates:
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))


class Controller:
    def __init__(self, root, data_dir, native, *, app_factory=None, mutex_factory=None, server_factory=None):
        self.root, self.data_dir, self.native = Path(root).resolve(), Path(data_dir), native
        if not self.data_dir.is_absolute():
            raise RuntimeError("OPENDESK_MODULE_DATA_DIR must be an absolute directory")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.configured, self.loading = False, False
        self.workspace, self.error, self.mode = "", "", "embedded"
        self.server, self.instance = None, None
        self._started = False
        self._shutdown = False
        self._loaded_workspace = None
        self._settings = self._read_json("settings.json", {})
        self._preferences = self._read_json("preferences.json", {})
        self.app_factory, self.mutex_factory, self.server_factory = app_factory, mutex_factory, server_factory
        self.gate = SessionGate(self)
        self._loader = None

    def _read_json(self, name, fallback):
        path = self.data_dir / name
        if not path.exists():
            return fallback
        if path.stat().st_size > 1024 * 1024:
            raise RuntimeError("Module configuration is too large")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("Module configuration must be an object")
        if "schemaVersion" in value:
            if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
                raise RuntimeError("Unsupported module configuration schemaVersion; original file preserved")
            value = {key: item for key, item in value.items() if key != "schemaVersion"}
        self._validate_json(name, value)
        return value

    @staticmethod
    def _validate_json(name, value):
        if name == "settings.json":
            if set(value) - {"port", "workspace"}:
                raise ValueError("Unknown module connection setting; original file preserved")
            if "port" in value and (type(value["port"]) is not int or not 0 <= value["port"] <= 65535):
                raise ValueError("Module port setting is invalid")
            if "workspace" in value and (not isinstance(value["workspace"], str) or len(value["workspace"]) > 4096 or
                                          any(unicodedata.category(char) == "Cc" for char in value["workspace"])):
                raise ValueError("Module workspace setting is invalid")
        elif name == "preferences.json":
            if len(value) > 256 or any(not PREFERENCE.fullmatch(key) or not (item is None or isinstance(item, str) and len(item) <= 16000)
                                       for key, item in value.items()):
                raise ValueError("Module preferences are invalid")
            if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 512 * 1024:
                raise ValueError("Module preferences are too large")
        else:
            raise ValueError("Unknown module configuration file")

    def _write_json(self, name, value):
        self._validate_json(name, value)
        path = self.data_dir / name
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"schemaVersion": 1, **value}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def preferences(self):
        with self.lock:
            return dict(self._preferences)

    def save_preference(self, key, value):
        with self.lock:
            updated = dict(self._preferences)
            updated[key] = value
            if len(updated) > 256 or len(json.dumps(updated, ensure_ascii=False).encode("utf-8")) > 512 * 1024:
                raise ValueError("Too many preferences")
            self._write_json("preferences.json", updated)
            self._preferences = updated

    def start(self):
        with self.lock:
            if self._shutdown or self.gate.draining:
                raise ProtocolError("MODULE_STOPPING", "作品管理器 is shutting down.")
            if self._started:
                return self.status()
            if self.server is None:
                if self.server_factory is None:
                    from work_catalog_yaml.desktop import DesktopServer
                    factory = DesktopServer
                else:
                    factory = self.server_factory
                port = self._settings.get("port", 0)
                if type(port) is not int or not 0 <= port <= 65535:
                    raise ProtocolError("CONFIG_INVALID", "Module port setting is invalid.")
                self.server = factory(self.gate, preferred_port=port)
                try:
                    url = self.server.start()
                    self.gate.bind(url)
                except Exception:
                    self.server = None
                    raise
            # Retry unfinished setup on this same owned service after a save or
            # native-navigation error; a listening socket alone is not readiness.
            # A stable origin preserves the module's WebView cache across restarts.
            self._settings["port"] = self.server.port
            self._write_json("settings.json", self._settings)
            self.native.navigate(self.url)
            workspace = self._settings.get("workspace")
            if workspace:
                try:
                    self.configure(workspace)
                except ProtocolError as error:
                    self.error = error.message
                    self.workspace = workspace if isinstance(workspace, str) else ""
            self._started = True
            return self.status()

    @property
    def url(self):
        return self.gate.origin + "/#nimda_session=" + self.gate.token if self.gate.origin else ""

    def status(self, *, include_url=True):
        with self.lock:
            queues = self.gate.queues()
            busy = self.loading or any(q.snapshot()["running"] or q.snapshot()["queued"] for q in queues)
            result = {"mode": self.mode, "configured": self.configured, "workspace": self.workspace, "busy": busy}
            if include_url:
                result["url"] = self.url
            if self.error:
                result["error"] = self.error
            if self.gate.draining:
                result["stopping"] = True
            return result

    def configure(self, workspace):
        path = Path(normalize_workspace_path(workspace))
        if not path.is_absolute() or not all((path / name).is_dir() for name in ("config", "data")):
            raise ProtocolError("INVALID_WORKSPACE", "Choose an existing 作品管理器 workspace with config and data directories.")
        path = path.resolve()
        with self.lock:
            if self._shutdown or self.gate.draining:
                raise ProtocolError("MODULE_STOPPING", "作品管理器 is shutting down.")
            if self.configured:
                if str(path) == self.workspace:
                    return self.status()
                raise ProtocolError("RESTART_REQUIRED", "Disable and re-enable this module before changing its workspace.")
            if self.loading:
                raise ProtocolError("MODULE_BUSY", "作品管理器 workspace is still loading.")
            if self._loaded_workspace is not None and self._loaded_workspace != str(path):
                raise ProtocolError("RESTART_REQUIRED", "Restart this module before loading a different workspace.")
            if self.server is None:
                raise ProtocolError("MODULE_NOT_READY", "Open the module page before configuring a workspace.")
            self.loading, self.error, self.workspace = True, "", str(path)
            self._loader = threading.Thread(target=self._load, args=(path,), name="nimda-module-loader", daemon=True)
            self._loader.start()
            return self.status()

    def _load(self, path):
        instance = None
        try:
            if self.mutex_factory is None:
                from work_catalog_yaml.desktop import SingleInstance
                instance = SingleInstance()
            else:
                instance = self.mutex_factory()
            if not instance.acquire():
                raise RuntimeError("原 作品管理器 或另一个 作品管理器 模块正在运行；请先正常关闭它。")
            self._loaded_workspace = str(path)
            if self.app_factory is None:
                from work_catalog_yaml.desktop import configure_desktop_workspace
                configure_desktop_workspace(self.root, path)
                from media_manager_app.application import build_jp_tv_browse_app
                app = build_jp_tv_browse_app(operation_log_root=path / "data" / "framework" / "logs" / "operations")
            else:
                app = self.app_factory(self.root, path)
            self.native.configure_services()
            with self.lock:
                self._settings["workspace"] = str(path)
                self._write_json("settings.json", self._settings)
            future = asyncio.run_coroutine_threadsafe(self.gate.install(app), self.gate.loop)
            # This is the loader thread, not stdio. Never abandon installation
            # and release the single-instance mutex while it may still complete.
            future.result()
            with self.lock:
                self.instance = instance
                instance = None
                self.configured = True
        except Exception as error:
            with self.lock:
                self.error = str(error)[:1500] or "作品管理器 工作区加载失败。"
        finally:
            if instance is not None:
                instance.release()
            with self.lock:
                self.loading = False

    def presentation(self, mode):
        if not isinstance(mode, str) or mode not in {"embedded", "popup"}:
            raise ProtocolError("INVALID_PARAMS", "mode must be embedded or popup.")
        with self.lock:
            if self.gate.draining:
                raise ProtocolError("MODULE_STOPPING", "作品管理器 is shutting down.")
            if not self.configured:
                raise ProtocolError("WORKSPACE_REQUIRED", "Configure the 作品管理器 workspace first.")
        self.native.present(mode)
        with self.lock:
            self.mode = mode
        return self.status()

    def popup_closed(self):
        with self.lock:
            self.mode = "embedded"

    def prepare_shutdown(self):
        return self.gate.begin_shutdown()

    def close(self):
        if self._shutdown:
            return
        result = self.prepare_shutdown()
        if not result["ready"]:
            raise ProtocolError("MODULE_BUSY", "作品管理器 is finishing active operations; poll module.prepareShutdown.")
        self.native.cleanup_services()
        if self.server is not None and not self.server.stop():
            raise RuntimeError("作品管理器 HTTP service did not stop")
        self.native.destroy()
        if self.instance is not None:
            self.instance.release()
            self.instance = None
        self._shutdown = True

    def dispatch(self, method, params):
        if method not in CAPABILITIES:
            raise ProtocolError("METHOD_NOT_FOUND", "Unknown module method.")
        if method == "nimda.configure":
            if set(params) != {"workspace"}:
                raise ProtocolError("INVALID_PARAMS", "nimda.configure accepts only workspace.")
            return self.configure(params["workspace"])
        if method == "nimda.presentation":
            if set(params) != {"mode"}:
                raise ProtocolError("INVALID_PARAMS", "nimda.presentation accepts only mode.")
            return self.presentation(params["mode"])
        if params:
            raise ProtocolError("INVALID_PARAMS", "This method accepts an empty params object.")
        if method == "runtime.hello":
            return {"protocolVersion": 1, "worker": "opendesk-python", "capabilities": CAPABILITIES, "maxFrameBytes": 65536}
        if method == "runtime.ping":
            return {"message": "pong", "protocolVersion": 1, "pid": os.getpid()}
        if method == "module.identity":
            return dict(IDENTITY)
        if method == "module.webapp":
            self.start()
            return {"url": self.url}
        if method == "nimda.status":
            return self.status()
        if method == "module.prepareShutdown":
            return self.prepare_shutdown()
        if method == "runtime.shutdown":
            # A failed close must leave stdio available for the host's retry.
            # Do not acknowledge shutdown until every owned service has stopped.
            self.close()
            return {"status": "stopping"}
        raise ProtocolError("METHOD_NOT_FOUND", "Unknown module method.")
