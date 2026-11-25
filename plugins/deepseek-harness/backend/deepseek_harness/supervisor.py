"""Process ownership, dsh web supervision, optional explicit local CLI install, and safe drain."""
from __future__ import annotations

from collections import deque
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
from typing import Callable

from .config import DshConfig, validate_external_url
from .probe import (
    DshEntry,
    ProbeResult,
    build_pnpm_command,
    ensure_pnpm_shim,
    is_dsh_source_repo,
    probe_environment,
)
from .proxy import ReverseProxyServer

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
URL_RE = re.compile(r"https?://(?:127\.0\.0\.1|localhost):(\d+)(/[^\s\"'<>]*)?", re.IGNORECASE)
TOKEN_PARAM_RE = re.compile(r"([?&]token=)([^\s&\"'<>]+)", re.IGNORECASE)
SECRET_REDACT_RE = re.compile(
    r"(?i)(token=|authorization:\s*bearer\s+|sk-[a-z0-9_-]{8,}|dsh-auth-[a-z0-9_-]+=)([^\s;&\"'<>]+)"
)
MAX_LOGS = 400


def redact_log_line(line: str) -> str:
    """Strip ANSI escape codes and redact launch tokens, bearer headers, cookies, and API keys."""
    cleaned = ANSI_RE.sub("", line).rstrip("\r\n")
    cleaned = TOKEN_PARAM_RE.sub(r"\1***", cleaned)
    cleaned = SECRET_REDACT_RE.sub(lambda m: f"{m.group(1)}***", cleaned)
    if len(cleaned) > 1000:
        cleaned = cleaned[:1000] + "…"
    return cleaned


def extract_dsh_url_and_token(line: str) -> tuple[int, str] | None:
    """Parse a dsh web startup URL from stdout/stderr, returning (port, launch_token)."""
    clean_line = ANSI_RE.sub("", line)
    match = URL_RE.search(clean_line)
    if not match:
        return None
    port = int(match.group(1))
    if not (1 <= port <= 65535):
        return None
    suffix = match.group(2) or ""
    token = ""
    token_match = TOKEN_PARAM_RE.search(suffix)
    if token_match:
        token = token_match.group(2)
    return (port, token)


def _pick_free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _tcp_port_open(port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


class _WindowsJob:
    """Owns a spawned child process tree via a Windows Job Object (KILL_ON_JOB_CLOSE)."""

    def __init__(self) -> None:
        self._handle: int | None = None
        if os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return
        self._handle = int(handle)
        self._configure_kill_on_close(kernel32)

    def _configure_kill_on_close(self, kernel32) -> None:
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject(
            wintypes.HANDLE(self._handle),
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(info),
            ctypes.sizeof(info),
        )

    def assign(self, pid: int) -> None:
        if os.name != "nt" or not self._handle:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        proc = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        if not proc:
            return
        try:
            kernel32.AssignProcessToJobObject(wintypes.HANDLE(self._handle), proc)
        finally:
            kernel32.CloseHandle(proc)

    def terminate_and_close(self) -> None:
        if os.name != "nt" or not self._handle:
            return
        handle = self._handle
        self._handle = None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        try:
            kernel32.TerminateJobObject(wintypes.HANDLE(handle), 1)
        finally:
            kernel32.CloseHandle(wintypes.HANDLE(handle))

    def close(self) -> None:
        if os.name != "nt" or not self._handle:
            return
        handle = self._handle
        self._handle = None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle(wintypes.HANDLE(handle))


class DshSupervisor:
    """Supervises either a managed dsh child process or a non-owned external connection."""

    def __init__(
        self,
        data_dir: Path,
        proxy: ReverseProxyServer,
        mcp_info_provider: Callable[[], tuple[str, str]] | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.proxy = proxy
        self._mcp_info_provider = mcp_info_provider
        self._lock = threading.RLock()
        self._logs: deque[dict] = deque(maxlen=MAX_LOGS)
        self._state = "idle"  # idle | starting | running | stopping | error | exited
        self._error: str | None = None
        self._mode = "managed"  # managed | external
        self._process: subprocess.Popen | None = None
        self._job: _WindowsJob | None = None
        self._upstream_port: int | None = None
        self._effective_dsh_home: str = ""
        self._effective_workspace: str = ""
        self._started_at: float | None = None
        self._run_generation = 0
        self._shutting_down = False
        self._stop_thread: threading.Thread | None = None

        # Explicit, user-initiated CLI installation state
        self._install_state = "idle"  # idle | running | succeeded | failed | cancelled
        self._install_error: str | None = None
        self._install_proc: subprocess.Popen | None = None
        self._install_job: _WindowsJob | None = None
        self._install_cancelled = False

        # Explicit, user-initiated source-repo workspace task state (pnpm install / build:artifacts)
        self._source_task_kind = ""  # "" | "install" | "build"
        self._source_task_state = "idle"  # idle | running | succeeded | failed | cancelled
        self._source_task_error: str | None = None
        self._source_task_proc: subprocess.Popen | None = None
        self._source_task_job: _WindowsJob | None = None
        self._source_task_cancelled = False
        self._launch_kind = ""  # "" | "node-script" | "source-dev" | "executable" | "external"

    def append_log(self, stream: str, line: str) -> None:
        safe = redact_log_line(line)
        if not safe:
            return
        with self._lock:
            self._logs.append({
                "ts": int(time.time() * 1000),
                "stream": stream,
                "line": safe,
            })

    def get_logs(self, limit: int = 200) -> list[dict]:
        bounded = max(1, min(limit, MAX_LOGS))
        with self._lock:
            items = list(self._logs)
        return items[-bounded:]

    def snapshot(self) -> dict:
        with self._lock:
            pid = self._process.pid if self._process and self._process.poll() is None else None
            return {
                "state": self._state,
                "mode": self._mode,
                "error": self._error,
                "pid": pid,
                "upstreamPort": self._upstream_port,
                "proxyUrl": f"{self.proxy.origin}/" if self.proxy.origin else "",
                "effectiveDshHome": self._effective_dsh_home,
                "effectiveWorkspace": self._effective_workspace,
                "startedAt": int(self._started_at * 1000) if self._started_at else None,
                "launchKind": self._launch_kind,
                "install": {
                    "state": self._install_state,
                    "error": self._install_error,
                },
                "sourceTask": {
                    "kind": self._source_task_kind,
                    "state": self._source_task_state,
                    "error": self._source_task_error,
                },
            }

    def start(self, config: DshConfig, probe: ProbeResult | None = None) -> dict:
        with self._lock:
            if self._shutting_down:
                raise RuntimeError("SHUTTING_DOWN: 模块正在关闭，不能启动新实例。")
            if self._state in ("starting", "running"):
                return self.snapshot()
            if self._state == "stopping":
                raise RuntimeError("STOPPING: 上一实例仍在退出，请稍后再试。")

            self._run_generation += 1
            generation = self._run_generation
            self._error = None
            self._mode = config.launchMode

            if config.launchMode == "external":
                self._launch_kind = "external"
                return self._connect_external_locked(config)

            checked = probe or probe_environment(
                self.data_dir,
                dsh_source=config.dshSource,
                custom_dsh_path=config.customDshPath,
                node_path=config.nodePath,
                source_launch_mode=config.sourceLaunchMode,
            )
            if not checked.dsh.available:
                self._state = "error"
                self._error = checked.dsh.error or "未找到可用的 DeepSeek Harness (dsh) 入口。"
                self.proxy.clear_upstream(self._error)
                raise RuntimeError(f"DSH_UNAVAILABLE: {self._error}")

            if (
                checked.dsh.kind == "source-dev"
                and self._source_task_proc is not None
                and self._source_task_proc.poll() is None
            ):
                raise RuntimeError("SOURCE_TASK_BUSY: 源码仓正在安装依赖或构建产物，请等待完成后再启动热开发模式。")

            return self._spawn_managed_locked(config, checked, generation)

    def _connect_external_locked(self, config: DshConfig) -> dict:
        _origin, port, token = validate_external_url(config.externalUrl)
        self._state = "starting"
        self._upstream_port = port
        self._effective_dsh_home = ""
        self._effective_workspace = ""
        self.proxy.set_status_message(f"正在连接外部 DeepSeek Harness (127.0.0.1:{port})…")
        self.append_log("system", f"正在连接外部 dsh web 实例 127.0.0.1:{port}（外部进程生命周期不受 OpenDesk 控制）")

        if not _tcp_port_open(port, timeout=1.5):
            self._state = "error"
            self._error = f"无法连接到本机 127.0.0.1:{port}，请确认外部 dsh web 已启动。"
            self.proxy.clear_upstream(self._error)
            raise RuntimeError(f"EXTERNAL_UNREACHABLE: {self._error}")

        cookies: dict[str, str] = {}
        if token:
            try:
                cookies = self.proxy.exchange_token(port, token, timeout=8.0)
                if cookies:
                    self.append_log("system", "已在服务端完成外部 dsh 启动令牌换票。")
                else:
                    self.append_log("system", "外部 dsh 未返回新会话 Cookie（若已登录或未启用令牌可继续使用）。")
            except Exception as error:
                self._state = "error"
                self._error = f"连接外部 dsh 换取会话失败：{error}"
                self.proxy.clear_upstream(self._error)
                raise RuntimeError(f"EXTERNAL_AUTH_FAILED: {self._error}") from error

        self.proxy.set_upstream(port, launch_token=token, cookies=cookies)
        self._state = "running"
        self._started_at = time.time()
        return self.snapshot()

    def _write_isolated_opendesk_integration(self, dsh_home: Path, mcp_url: str) -> Path:
        """Stage zero-config MCP patch overlay (no token on disk) and OpenDesk Router Skill in isolated DSH_HOME."""
        patch_path = (self.data_dir / "opendesk-bridge.patch.yml").resolve()
        patch_yaml = (
            "# Auto-generated by OpenDesk deepseek-harness plugin (isolated mode).\n"
            "# Bearer token is passed strictly via OPENDESK_MCP_TOKEN env var and never written to disk.\n"
            "- insert:\n"
            "    - id: mcp-opendesk\n"
            "      name: '@deepseek-ai/dsh-mcp-client'\n"
            "      config:\n"
            "        serverName: opendesk\n"
            "        transport: streamable-http\n"
            f"        url: {mcp_url}\n"
            "        toolCallTimeoutMs: 120000\n"
            "        headers:\n"
            "          Authorization: !!js \"'Bearer ' + (process.env.OPENDESK_MCP_TOKEN || '')\"\n"
        )
        patch_path.write_text(patch_yaml, encoding="utf-8", newline="\n")

        # Stage 4: built-in OpenDesk workbench router skill inside isolated DSH_HOME/skills/
        skill_dir = (dsh_home / "skills" / "opendesk-workbench").resolve()
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / "SKILL.md"
        skill_content = (
            "---\n"
            "name: opendesk-workbench\n"
            "description: Discover, open, and operate OpenDesk desktop workbench tabs safely through the local OpenDesk MCP bridge.\n"
            "whenToUse: Use whenever the user asks to inspect, open, or perform an action with another OpenDesk tab or plugin (for example Tower Assistant, Media Manager, or any installed OpenDesk module).\n"
            "---\n\n"
            "# OpenDesk Workbench Tab Routing Protocol\n\n"
            "You are running inside OpenDesk with an auto-connected local MCP server named `opendesk`.\n"
            "Follow this strict, safe decision sequence whenever the user asks to use an OpenDesk tab/feature:\n\n"
            "1. **Discover tabs**: Call `mcp__opendesk__opendesk_list_modules` first to see which modules are installed, enabled, already open, and support tools.\n"
            "2. **Wake unopened tabs on demand**: If the target tab is installed and enabled but `opened: false`, briefly inform the user and call `mcp__opendesk__opendesk_open_module` with `{ \"moduleId\": \"<id>\", \"focus\": false }` (or `focus: true` if the user explicitly wants to view it). The OpenDesk host will ask the user for native confirmation and return newly discovered tools once ready.\n"
            "3. **Respect authorization**: If the target tab's business tools are not yet listed under `mcp__opendesk__*`, tell the user to open the top bar 「设置与日志」 panel in the DeepSeek Harness tab and click 「请求宿主授权并同步到 MCP」 to approve the target tab's tools.\n"
            "4. **Read before write**: Prefer read-only tools first to fetch authoritative state from the target tab.\n"
            "5. **Explicit confirmation for writes**: Before calling any tool marked `write · requires user confirmation`, clearly summarize the target module and exact parameters for the user. OpenDesk will show a native per-call confirmation dialog. If the user declines, stop immediately and never fabricate success.\n"
            "6. **Verify outcome**: Report the structured result returned by the target tab accurately, including module version and any errors.\n"
        )
        skill_md.write_text(skill_content, encoding="utf-8", newline="\n")
        return patch_path

    def _build_launch_command(
        self,
        entry: DshEntry,
        config: DshConfig,
        port: int,
        probe: ProbeResult,
        *,
        patch_file: Path | None = None,
    ) -> tuple[list[str], Path | None, float]:
        """Return (argv, override_cwd, startup_timeout_seconds)."""
        if entry.kind == "source-dev":
            repo_root = Path(entry.script).resolve()
            args = ["run", "dev:web", "--"]
            skip_build = bool(probe.sourceRepo and probe.sourceRepo.artifactsBuilt)
            if skip_build:
                args.append("--skip-build")
            if patch_file is not None:
                args.extend(["--patch", str(patch_file)])
            args.extend(["--host", "127.0.0.1", "--port", str(port), "--no-open"])
            cmd = build_pnpm_command(probe.pnpm, args)
            timeout = 180.0 if skip_build else 600.0
            return (cmd, repo_root, timeout)
        profile_args = ["--profile", config.profile]
        if patch_file is not None:
            profile_args.extend(["--patch", str(patch_file)])
        profile_args.extend(["--host", "127.0.0.1", "--port", str(port), "--no-open"])
        if entry.kind == "node-script":
            return ([entry.executable, entry.script, *profile_args], None, 90.0)
        return ([entry.executable, *profile_args], None, 90.0)

    def _prepare_pnpm_shim_dir(self, probe: ProbeResult) -> Path | None:
        if not probe.pnpm.available or probe.pnpm.kind not in ("repo-pnpm", "corepack"):
            return None
        try:
            shim_dir = ensure_pnpm_shim(self.data_dir, probe.pnpm)
        except (OSError, ValueError, RuntimeError) as error:
            self.append_log("system", f"生成隔离 pnpm 兜底垫片失败（{probe.pnpm.display}）：{error}")
            return None
        if shim_dir is not None:
            self.append_log("system", f"未检测到全局 pnpm，已自动注入隔离 pnpm 垫片（{probe.pnpm.display}）供插件市场与源码脚本使用。")
        return shim_dir

    def _build_child_env(
        self,
        node_path: str = "",
        dsh_home: Path | None = None,
        agents_home: Path | None = None,
        pnpm_shim_dir: Path | None = None,
    ) -> dict[str, str]:
        env = os.environ.copy()
        path_parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
        norm = (lambda s: os.path.normcase(os.path.normpath(s)))
        if node_path:
            node_dir = str(Path(node_path).parent.resolve())
            node_key = norm(node_dir)
            path_parts = [node_dir, *(p for p in path_parts if norm(p) != node_key)]
        if pnpm_shim_dir is not None:
            # Append (never prepend) so any native global pnpm on PATH always takes precedence.
            shim_str = str(Path(pnpm_shim_dir).resolve())
            shim_key = norm(shim_str)
            if not any(norm(p) == shim_key for p in path_parts):
                path_parts.append(shim_str)
        if path_parts:
            env["PATH"] = os.pathsep.join(path_parts)
        if dsh_home is not None:
            env["DSH_HOME"] = str(dsh_home)
        if agents_home is not None:
            env["DSH_AGENTS_HOME"] = str(agents_home)
        # Keep dsh from trying to open an external browser window on its own.
        env.setdefault("BROWSER", "none")
        return env

    def _spawn_managed_locked(self, config: DshConfig, probe: ProbeResult, generation: int) -> dict:
        entry = probe.dsh
        port = _pick_free_loopback_port()
        if config.homeMode == "isolated":
            dsh_home = (self.data_dir / "dsh-home").resolve()
            dsh_home.mkdir(parents=True, exist_ok=True)
            agents_home = (self.data_dir / "dsh-agents").resolve()
            agents_home.mkdir(parents=True, exist_ok=True)
            effective_home = str(dsh_home)
        elif config.homeMode == "custom":
            dsh_home = Path(config.customHomeDir).resolve()
            dsh_home.mkdir(parents=True, exist_ok=True)
            agents_home = None
            effective_home = str(dsh_home)
        else:
            dsh_home = None
            agents_home = None
            effective_home = str((Path.home() / ".dsh").resolve())

        if config.workspaceDir:
            workspace = Path(config.workspaceDir).resolve()
            if not workspace.is_dir():
                self._state = "error"
                self._error = f"指定的工作目录不存在：{workspace}"
                self.proxy.clear_upstream(self._error)
                raise RuntimeError(f"INVALID_WORKSPACE: {self._error}")
        else:
            workspace = (self.data_dir / "workspace").resolve()
            workspace.mkdir(parents=True, exist_ok=True)

        pnpm_shim_dir = self._prepare_pnpm_shim_dir(probe)
        env = self._build_child_env(
            node_path=probe.node.path if probe.node.available else "",
            dsh_home=dsh_home,
            agents_home=agents_home,
            pnpm_shim_dir=pnpm_shim_dir,
        )

        patch_file: Path | None = None
        if config.homeMode == "isolated" and dsh_home is not None and self._mcp_info_provider is not None:
            mcp_url, mcp_token = self._mcp_info_provider()
            if mcp_url and mcp_token:
                patch_file = self._write_isolated_opendesk_integration(dsh_home, mcp_url)
                env["OPENDESK_MCP_TOKEN"] = mcp_token

        cmd, override_cwd, startup_timeout = self._build_launch_command(
            entry,
            config,
            port,
            probe,
            patch_file=patch_file,
        )
        spawn_cwd = override_cwd or workspace
        self._launch_kind = entry.kind
        mode_note = "源码热开发 dev:web" if entry.kind == "source-dev" else f"profile={config.profile}"
        self.append_log("system", f"正在启动托管 DeepSeek Harness（{mode_note}, port={port}, DSH_HOME={effective_home}）")

        job = _WindowsJob()
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(spawn_cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            )
            job.assign(proc.pid)
        except OSError as error:
            job.close()
            self._state = "error"
            self._error = f"无法拉起 DeepSeek Harness 进程：{error}"
            self.proxy.clear_upstream(self._error)
            raise RuntimeError(f"SPAWN_FAILED: {self._error}") from error

        self._process = proc
        self._job = job
        self._upstream_port = port
        self._effective_dsh_home = effective_home
        self._effective_workspace = str(spawn_cwd)
        self._state = "starting"
        self._started_at = time.time()
        if entry.kind == "source-dev":
            self.proxy.set_status_message("正在启动源码热开发模式（tsc/tsdown/vite 监听 + dsh web），首次启动可能需要 30–120 秒…")
        else:
            self.proxy.set_status_message("正在启动 DeepSeek Harness 后台服务，首次冷启动可能需要 10–30 秒…")

        threading.Thread(
            target=self._watch_stream,
            args=(proc, proc.stdout, "stdout", generation, port),
            name=f"dsh-stdout-{generation}",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._watch_stream,
            args=(proc, proc.stderr, "stderr", generation, port),
            name=f"dsh-stderr-{generation}",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._watch_lifecycle,
            args=(proc, generation, port, startup_timeout),
            name=f"dsh-watch-{generation}",
            daemon=True,
        ).start()
        return self.snapshot()

    def _watch_stream(self, proc: subprocess.Popen, pipe, stream_name: str, generation: int, expected_port: int) -> None:
        if pipe is None:
            return
        try:
            for raw_line in pipe:
                self.append_log(stream_name, raw_line)
                parsed = extract_dsh_url_and_token(raw_line)
                if parsed:
                    actual_port, launch_token = parsed
                    self._on_upstream_announced(generation, actual_port, launch_token)
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    def _on_upstream_announced(self, generation: int, port: int, launch_token: str) -> None:
        with self._lock:
            if generation != self._run_generation or self._state != "starting":
                return
        # Exchange launch token outside lock so status queries never block.
        cookies: dict[str, str] = {}
        if launch_token:
            for attempt in range(5):
                try:
                    cookies = self.proxy.exchange_token(port, launch_token, timeout=5.0)
                    if cookies:
                        break
                except Exception:
                    time.sleep(0.3)
        with self._lock:
            if generation != self._run_generation or self._state != "starting":
                return
            self._upstream_port = port
            self.proxy.set_upstream(port, launch_token=launch_token, cookies=cookies)
            self._state = "running"
            self._error = None
            if launch_token and cookies:
                self.append_log("system", f"DeepSeek Harness 已就绪（port={port}，已在服务端完成会话鉴权）。")
            else:
                self.append_log("system", f"DeepSeek Harness 已就绪（port={port}）。")

    def _watch_lifecycle(
        self,
        proc: subprocess.Popen,
        generation: int,
        expected_port: int,
        startup_timeout: float = 90.0,
    ) -> None:
        deadline = time.monotonic() + startup_timeout
        fallback_ready_after = time.monotonic() + (8.0 if startup_timeout > 90.0 else 4.0)
        while True:
            rc = proc.poll()
            if rc is not None:
                with self._lock:
                    if generation == self._run_generation:
                        was_stopping = self._state == "stopping" or self._shutting_down
                        self._cleanup_job_locked()
                        self._process = None
                        self.proxy.clear_upstream("DeepSeek Harness 进程已退出。")
                        if was_stopping:
                            self._state = "idle"
                            self._error = None
                            self.append_log("system", f"DeepSeek Harness 已停止（退出码 {rc}）。")
                        else:
                            self._state = "exited" if rc == 0 else "error"
                            self._error = None if rc == 0 else f"DeepSeek Harness 异常退出（退出码 {rc}），请查看下方日志。"
                            self.append_log("system", f"DeepSeek Harness 进程退出（退出码 {rc}）。")
                return

            with self._lock:
                still_starting = generation == self._run_generation and self._state == "starting"
            if still_starting:
                now = time.monotonic()
                # Fallback readiness probe if a custom dsh build doesn't emit a URL line.
                if now >= fallback_ready_after and _tcp_port_open(expected_port, timeout=0.2):
                    # Give stdout reader a brief window to deliver the tokenized URL first.
                    time.sleep(0.5)
                    with self._lock:
                        if generation == self._run_generation and self._state == "starting":
                            self._upstream_port = expected_port
                            self.proxy.set_upstream(expected_port, launch_token="", cookies={})
                            self._state = "running"
                            self._error = None
                            self.append_log("system", f"检测到 127.0.0.1:{expected_port} 已监听，代理已连接。")
                elif now >= deadline:
                    secs = int(startup_timeout)
                    self.append_log("system", f"等待 DeepSeek Harness 启动超时（{secs} 秒），正在回收进程。")
                    self.stop(wait=False)
                    with self._lock:
                        if generation == self._run_generation:
                            self._state = "error"
                            self._error = f"DeepSeek Harness 启动超过 {secs} 秒未就绪，请检查日志或配置。"
                    return
            time.sleep(0.25)

    def _cleanup_job_locked(self) -> None:
        if self._job is not None:
            self._job.close()
            self._job = None

    def stop(self, *, wait: bool = True) -> dict:
        with self._lock:
            if self._mode == "external":
                # Never terminate a user-owned external dsh process.
                self.proxy.clear_upstream("已断开外部 DeepSeek Harness 连接（外部进程保持运行）。")
                self._state = "idle"
                self._error = None
                self._upstream_port = None
                self.append_log("system", "已断开与外部 DeepSeek Harness 实例的代理连接。")
                return self.snapshot()

            proc = self._process
            if proc is None or proc.poll() is not None:
                self._cleanup_job_locked()
                self._process = None
                self.proxy.clear_upstream("DeepSeek Harness 已停止。")
                self._state = "idle"
                return self.snapshot()

            self._state = "stopping"
            self.proxy.clear_upstream("正在停止 DeepSeek Harness…")
            if self._stop_thread is None or not self._stop_thread.is_alive():
                job = self._job
                self._stop_thread = threading.Thread(
                    target=self._stop_process_tree,
                    args=(proc, job),
                    name="dsh-stop",
                    daemon=True,
                )
                self._stop_thread.start()
            stopper = self._stop_thread

        if wait and stopper is not None:
            stopper.join(timeout=10.0)
        return self.snapshot()

    def _stop_process_tree(self, proc: subprocess.Popen, job: _WindowsJob | None) -> None:
        try:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
                deadline = time.monotonic() + 4.0
                while proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.1)
            if proc.poll() is None:
                if job is not None:
                    job.terminate_and_close()
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=3.0)
                except subprocess.SubprocessError:
                    pass
            elif job is not None:
                # Terminate any lingering descendant processes (MCP servers / shell jobs) in this Job.
                job.terminate_and_close()
        finally:
            with self._lock:
                if self._process is proc:
                    self._process = None
                if self._job is job:
                    self._job = None
                if self._state == "stopping":
                    self._state = "idle"

    def start_install(self, config: DshConfig) -> dict:
        """Explicitly install @deepseek-ai/dsh into <data_dir>/runtime-cli using local npm."""
        with self._lock:
            if self._shutting_down:
                raise RuntimeError("SHUTTING_DOWN: 模块正在关闭。")
            if self._install_state == "running":
                return self.snapshot()
            probe = probe_environment(self.data_dir, node_path=config.nodePath)
            if not probe.node.available or not probe.node.meetsMinimum:
                raise RuntimeError(f"NODE_INVALID: {probe.node.error or '需要 Node.js >= 24.9.0'}")
            if not probe.npmPath:
                raise RuntimeError("NPM_MISSING: 未找到 npm，请先安装完整的 Node.js >= 24.9.0。")

            cli_dir = (self.data_dir / "runtime-cli").resolve()
            cli_dir.mkdir(parents=True, exist_ok=True)
            pkg_json = cli_dir / "package.json"
            if not pkg_json.exists():
                pkg_json.write_text('{"name":"opendesk-dsh-runtime","private":true}\n', encoding="utf-8")

            self._install_state = "running"
            self._install_error = None
            self._install_cancelled = False
            self.append_log("system", f"开始在模块隔离目录安装 @deepseek-ai/dsh：{cli_dir}")

            threading.Thread(
                target=self._run_install,
                args=(probe.npmPath, cli_dir, probe.node.path),
                name="dsh-npm-install",
                daemon=True,
            ).start()
            return self.snapshot()

    def _run_install(self, npm_path: str, cli_dir: Path, node_path: str = "") -> None:
        job = _WindowsJob()
        npm_cli_js = (
            Path(node_path).parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
            if node_path
            else None
        )
        if npm_cli_js is not None and npm_cli_js.is_file():
            cmd = [node_path, str(npm_cli_js), "install", "--no-audit", "--no-fund", "@deepseek-ai/dsh@latest"]
        else:
            cmd = [npm_path, "install", "--no-audit", "--no-fund", "@deepseek-ai/dsh@latest"]
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cli_dir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            )
            job.assign(proc.pid)
            with self._lock:
                self._install_proc = proc
                self._install_job = job

            if proc.stdout is not None:
                for line in proc.stdout:
                    self.append_log("install", line)
                proc.stdout.close()
            rc = proc.wait()
            with self._lock:
                cancelled = self._install_cancelled
                self._install_proc = None
                self._install_job = None
                if cancelled:
                    self._install_state = "cancelled"
                    self._install_error = "安装已取消。"
                    self.append_log("system", "用户已取消 @deepseek-ai/dsh 安装。")
                elif rc == 0:
                    self._install_state = "succeeded"
                    self._install_error = None
                    self.append_log("system", "@deepseek-ai/dsh 安装完成。")
                else:
                    self._install_state = "failed"
                    self._install_error = f"npm 安装失败（退出码 {rc}），请查看日志。"
                    self.append_log("system", self._install_error)
        except Exception as error:
            with self._lock:
                self._install_proc = None
                self._install_job = None
                self._install_state = "failed"
                self._install_error = f"安装过程发生错误：{error}"
                self.append_log("system", self._install_error)
        finally:
            job.close()

    def cancel_install(self) -> dict:
        with self._lock:
            proc = self._install_proc
            job = self._install_job
            if proc is None or proc.poll() is not None:
                return self.snapshot()
            self._install_cancelled = True
        if job is not None:
            job.terminate_and_close()
        try:
            proc.terminate()
        except OSError:
            pass
        return self.snapshot()

    def start_source_task(
        self,
        config: DshConfig,
        kind: str,
        *,
        on_succeeded: Callable[[], None] | None = None,
    ) -> dict:
        """Run a strictly allowlisted source-repo task (`install` or `build`) inside a validated DSH checkout."""
        if kind not in ("install", "build"):
            raise RuntimeError("INVALID_SOURCE_TASK: 仅支持 install 或 build 任务。")
        with self._lock:
            if self._shutting_down:
                raise RuntimeError("SHUTTING_DOWN: 模块正在关闭。")
            if self._source_task_state == "running":
                return self.snapshot()
            if config.dshSource != "custom" or not config.customDshPath:
                raise RuntimeError("SOURCE_REPO_UNSET: 请先在配置中选择「本地源码仓」并填写 DeepSeek Harness 仓库根目录。")
            repo_dir = Path(config.customDshPath).resolve()
            if not is_dsh_source_repo(repo_dir):
                raise RuntimeError("INVALID_SOURCE_REPO: 指定目录不是有效的 DeepSeek Harness 源码仓（缺少 pnpm-workspace.yaml、apps/cli/package.json 或 scripts/build.ts）。")
            if (
                kind == "build"
                and self._launch_kind == "source-dev"
                and self._state in ("starting", "running")
            ):
                raise RuntimeError("DEV_WATCHER_ACTIVE: 源码热开发模式正在运行并监听重建，请勿并发执行全量构建；如需全量构建请先停止。")

            probe = probe_environment(
                self.data_dir,
                dsh_source="custom",
                custom_dsh_path=str(repo_dir),
                node_path=config.nodePath,
                source_launch_mode=config.sourceLaunchMode,
            )
            if not probe.node.available or not probe.node.meetsMinimum:
                raise RuntimeError(f"NODE_INVALID: {probe.node.error or '需要 Node.js >= 24.9.0'}")
            if not probe.pnpm.available:
                raise RuntimeError(f"PNPM_MISSING: {probe.pnpm.error or '未找到 pnpm 或 corepack。'}")

            pnpm_args = ["install"] if kind == "install" else ["run", "build:artifacts"]
            cmd = build_pnpm_command(probe.pnpm, pnpm_args)
            pnpm_shim_dir = self._prepare_pnpm_shim_dir(probe)
            env = self._build_child_env(node_path=probe.node.path, pnpm_shim_dir=pnpm_shim_dir)

            self._source_task_kind = kind
            self._source_task_state = "running"
            self._source_task_error = None
            self._source_task_cancelled = False
            label = "安装源码仓依赖（pnpm install）" if kind == "install" else "构建源码仓产物（pnpm run build:artifacts）"
            self.append_log("system", f"开始{label}：{repo_dir}")

            threading.Thread(
                target=self._run_source_task,
                args=(kind, cmd, repo_dir, env, on_succeeded),
                name=f"dsh-source-{kind}",
                daemon=True,
            ).start()
            return self.snapshot()

    def _run_source_task(
        self,
        kind: str,
        cmd: list[str],
        repo_dir: Path,
        env: dict[str, str],
        on_succeeded: Callable[[], None] | None,
    ) -> None:
        job = _WindowsJob()
        stream_name = "source"
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(repo_dir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            )
            job.assign(proc.pid)
            with self._lock:
                self._source_task_proc = proc
                self._source_task_job = job

            if proc.stdout is not None:
                for line in proc.stdout:
                    self.append_log(stream_name, line)
                proc.stdout.close()
            rc = proc.wait()
            should_callback = False
            with self._lock:
                cancelled = self._source_task_cancelled
                self._source_task_proc = None
                self._source_task_job = None
                if cancelled:
                    self._source_task_state = "cancelled"
                    self._source_task_error = "源码任务已取消。"
                    self.append_log("system", f"用户已取消源码仓 {kind} 任务。")
                elif rc == 0:
                    self._source_task_state = "succeeded"
                    self._source_task_error = None
                    self.append_log("system", f"源码仓 {kind} 任务已完成。")
                    should_callback = True
                else:
                    self._source_task_state = "failed"
                    self._source_task_error = f"源码仓 {kind} 失败（退出码 {rc}），请查看日志。"
                    self.append_log("system", self._source_task_error)
            if should_callback and on_succeeded is not None:
                try:
                    on_succeeded()
                except Exception as cb_err:
                    self.append_log("system", f"构建完成后自动重启失败：{cb_err}")
        except Exception as error:
            with self._lock:
                self._source_task_proc = None
                self._source_task_job = None
                self._source_task_state = "failed"
                self._source_task_error = f"源码仓任务发生错误：{error}"
                self.append_log("system", self._source_task_error)
        finally:
            job.close()

    def cancel_source_task(self) -> dict:
        with self._lock:
            proc = self._source_task_proc
            job = self._source_task_job
            if proc is None or proc.poll() is not None:
                return self.snapshot()
            self._source_task_cancelled = True
        if job is not None:
            job.terminate_and_close()
        try:
            proc.terminate()
        except OSError:
            pass
        return self.snapshot()

    def prepare_shutdown(self) -> dict:
        """hostApi 2 safe drain contract: reject new starts, stop owned children, report exact active count."""
        with self._lock:
            self._shutting_down = True
            has_install = self._install_proc is not None and self._install_proc.poll() is None
            has_source_task = self._source_task_proc is not None and self._source_task_proc.poll() is None
            has_managed = (
                self._mode == "managed"
                and self._process is not None
                and self._process.poll() is None
            )

        if has_install:
            self.cancel_install()
        if has_source_task:
            self.cancel_source_task()
        if has_managed:
            self.stop(wait=False)
        elif self._mode == "external":
            self.proxy.clear_upstream("OpenDesk 正在关闭。")

        with self._lock:
            active = 0
            if self._install_proc is not None and self._install_proc.poll() is None:
                active += 1
            if self._source_task_proc is not None and self._source_task_proc.poll() is None:
                active += 1
            if (
                self._mode == "managed"
                and self._process is not None
                and self._process.poll() is None
            ):
                active += 1
            return {"ready": active == 0, "active": active}

    def close(self) -> None:
        self.prepare_shutdown()
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if self.prepare_shutdown()["ready"]:
                break
            time.sleep(0.1)
        self.proxy.stop()
