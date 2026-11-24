"""Authoritative service layer for the DeepSeek Harness OpenDesk hostApi 2 plugin."""
from __future__ import annotations

import os
from pathlib import Path
import threading
import webbrowser

from .config import ConfigError, ConfigStore, DshConfig, redact_external_url, validate_config_data
from .mcp_bridge import McpToolBridge
from .probe import probe_environment
from .proxy import ReverseProxyServer
from .supervisor import DshSupervisor


class DataLock:
    """Single-instance file lock scoped to OPENDESK_MODULE_DATA_DIR."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.file = (directory / ".instance.lock").open("a+b")
        try:
            self.file.seek(0, 2)
            if not self.file.tell():
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            raise RuntimeError("DATA_BUSY: DeepSeek Harness 数据目录正被另一个实例使用。") from error

    def close(self) -> None:
        if self.file.closed:
            return
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()


class DshService:
    """Coordinates config, environment probing, local reverse proxy, and process supervision."""

    def __init__(self, data_dir: Path, *, version: str = "0.1.0"):
        self.data_dir = Path(data_dir)
        if not self.data_dir.is_absolute():
            raise ValueError("Host must provide an absolute module data directory.")
        self.version = version
        self._lock = threading.RLock()
        self._data_lock = DataLock(self.data_dir)
        self.config_store = ConfigStore(self.data_dir)
        self.proxy = ReverseProxyServer(log_callback=self._on_proxy_log)
        self.supervisor = DshSupervisor(self.data_dir, self.proxy)
        self.mcp = McpToolBridge(version=version)
        self._config: DshConfig = DshConfig()
        self._config_error: str | None = None
        self._last_probe = None
        self._auto_start_attempted = False
        self._load_initial_config()

    def _on_proxy_log(self, message: str) -> None:
        self.supervisor.append_log("proxy", message)

    def _load_initial_config(self) -> None:
        try:
            self._config = self.config_store.load()
            self._config_error = None
        except ConfigError as error:
            # Never overwrite a corrupted or future-version config file.
            self._config = DshConfig()
            self._config_error = error.message
            self.supervisor.append_log("system", f"配置加载失败：{error.message}")

    def start_webapp(self) -> str:
        """Called by OpenDesk host's module.webapp control RPC; binds proxy immediately (<1s)."""
        url = self.proxy.start()
        self.refresh_probe()
        self._maybe_auto_start()
        return url

    def _maybe_auto_start(self) -> None:
        with self._lock:
            if self._auto_start_attempted or self._config_error or not self._config.autoStart:
                return
            self._auto_start_attempted = True
            config = self._config
            probe = self._last_probe
        if config.launchMode == "managed":
            if probe and probe.dsh.available:
                try:
                    self.supervisor.start(config, probe=probe)
                except Exception as error:
                    self.supervisor.append_log("system", f"自动启动失败：{error}")
            else:
                self.proxy.set_status_message("未检测到 DeepSeek Harness (dsh)，请在控制面板安装或配置路径。")
        elif config.launchMode == "external" and config.externalUrl:
            try:
                self.supervisor.start(config, probe=probe)
            except Exception as error:
                self.supervisor.append_log("system", f"自动连接外部实例失败：{error}")

    def refresh_probe(self) -> dict:
        with self._lock:
            config = self._config
        result = probe_environment(
            self.data_dir,
            dsh_source=config.dshSource,
            custom_dsh_path=config.customDshPath,
            node_path=config.nodePath,
            source_launch_mode=config.sourceLaunchMode,
        )
        with self._lock:
            self._last_probe = result
        return result.to_dict()

    def status(self) -> dict:
        with self._lock:
            config = self._config
            config_error = self._config_error
            probe = self._last_probe
        if probe is None:
            probe_dict = self.refresh_probe()
        else:
            probe_dict = probe.to_dict()
        public_config = config.to_public_dict()
        # Never echo raw launch token in routine status polling.
        public_config["externalUrlRedacted"] = redact_external_url(config.externalUrl)
        public_config["hasExternalToken"] = "token=" in config.externalUrl.lower()
        public_config["externalUrl"] = redact_external_url(config.externalUrl)
        return {
            "version": self.version,
            "dataDir": str(self.data_dir),
            "config": public_config,
            "configError": config_error,
            "probe": probe_dict,
            "runtime": self.supervisor.snapshot(),
            "mcp": self.mcp.snapshot(include_token=False),
        }

    def configure(self, params: dict) -> dict:
        with self._lock:
            if self._config_error:
                raise ConfigError(
                    "CONFIG_LOCKED",
                    f"现有配置文件异常且受保护，请修复或备份移走后重试：{self._config_error}",
                )
            merged = self._config.to_public_dict()
            # If user didn't touch a redacted external URL, keep existing real URL intact.
            if (
                isinstance(params, dict)
                and "externalUrl" in params
                and params["externalUrl"] == redact_external_url(self._config.externalUrl)
            ):
                params = {**params, "externalUrl": self._config.externalUrl}
            if not isinstance(params, dict):
                raise ConfigError("INVALID_CONFIG", "配置参数必须是对象。")
            merged.update(params)
            merged["schemaVersion"] = 1
            validated = validate_config_data(merged)
            saved = self.config_store.save(validated)
            self._config = saved
        self.refresh_probe()
        return self.status()

    def start_dsh(self) -> dict:
        with self._lock:
            if self._config_error:
                raise ConfigError("CONFIG_CORRUPTED", self._config_error)
            config = self._config
        probe = probe_environment(
            self.data_dir,
            dsh_source=config.dshSource,
            custom_dsh_path=config.customDshPath,
            node_path=config.nodePath,
            source_launch_mode=config.sourceLaunchMode,
        )
        with self._lock:
            self._last_probe = probe
        self.supervisor.start(config, probe=probe)
        return self.status()

    def stop_dsh(self) -> dict:
        self.supervisor.stop(wait=True)
        return self.status()

    def restart_dsh(self) -> dict:
        self.supervisor.stop(wait=True)
        return self.start_dsh()

    def install_cli(self) -> dict:
        with self._lock:
            config = self._config
        self.supervisor.start_install(config)
        return self.status()

    def cancel_install(self) -> dict:
        self.supervisor.cancel_install()
        return self.status()

    def source_install(self) -> dict:
        with self._lock:
            config = self._config
        self.supervisor.start_source_task(config, "install", on_succeeded=self.refresh_probe)
        return self.status()

    def source_build(self, *, restart_after: bool = False) -> dict:
        with self._lock:
            config = self._config

        def after_build() -> None:
            self.refresh_probe()
            if restart_after:
                self.restart_dsh()

        self.supervisor.start_source_task(config, "build", on_succeeded=after_build)
        return self.status()

    def cancel_source_task(self) -> dict:
        self.supervisor.cancel_source_task()
        return self.status()

    def logs(self, limit: int = 200) -> dict:
        return {"entries": self.supervisor.get_logs(limit=limit)}

    def open_external_browser(self) -> dict:
        origin = self.proxy.origin
        if not origin or not origin.startswith("http://127.0.0.1:"):
            raise RuntimeError("PROXY_NOT_READY: 代理服务尚未启动。")
        snap = self.supervisor.snapshot()
        if snap["state"] != "running":
            raise RuntimeError("DSH_NOT_RUNNING: DeepSeek Harness 尚未就绪。")
        webbrowser.open(f"{origin}/")
        return {"opened": True, "url": f"{origin}/"}

    def mcp_sync(self, tools: list) -> dict:
        return self.mcp.sync_tools(tools)

    def mcp_poll(self) -> dict:
        return {"calls": self.mcp.poll_calls()}

    def mcp_respond(self, call_id: str, ok: bool, result: object = None, error: str = "") -> dict:
        return self.mcp.respond_call(call_id, ok, result=result, error=error)

    def prepare_shutdown(self) -> dict:
        self.mcp._fail_all_pending("OpenDesk 正在关闭。")
        return self.supervisor.prepare_shutdown()

    def close(self) -> None:
        try:
            self.mcp.close()
        finally:
            try:
                self.supervisor.close()
            finally:
                self._data_lock.close()
