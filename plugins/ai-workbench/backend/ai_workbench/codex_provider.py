"""Bounded Codex app-server adapter; only processes created here are owned.

Protocol reference: https://learn.chatgpt.com/docs/app-server
No inference happens until ``run_codex`` sends the explicit user's turn.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
import time
from typing import Any, Callable

from .codex_discovery import inspect_connection


RPC_TIMEOUT = 30.0
TURN_TIMEOUT = 1800.0
INTERRUPT_TIMEOUT = 10.0
CLOSE_TIMEOUT = 2.0
MAX_FRAME_BYTES = 1024 * 1024
MAX_OUTPUT_CHARS = 2 * 1024 * 1024
_POLL_SECONDS = 0.1


class CodexError(Exception):
    """A stable, safe error for the application UI, never raw provider stderr."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.cleanupPending = False
        self.retry_cleanup: Callable[[], None] | None = None


def _identifier(value: Any, name: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or not value.isprintable():
        raise CodexError("INVALID_PARAMS", f"{name}无效。")
    return value


def _workspace(value: str | None, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise CodexError("INVALID_PARAMS", "请为 Codex 选择一个现有的绝对工作目录。")
        return None
    if not isinstance(value, str) or "\x00" in value:
        raise CodexError("INVALID_PARAMS", "Codex 工作目录无效。")
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise CodexError("INVALID_PARAMS", "Codex 工作目录必须是现有目录的绝对路径。")
    return str(path.resolve())


def _command(executable: str | None) -> list[str]:
    detected = inspect_connection(executable)
    if not detected["available"]:
        raise CodexError(detected["code"], detected["message"])
    return [detected["executable"], "-c", "analytics.enabled=false", "app-server", "--listen", "stdio://"]


def _safe_detail(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = re.sub(r"(?i)\b(Bearer\s+)\S+", r"\1[已隐藏]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[已隐藏]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*)[^\s,;]+", r"\1[已隐藏]", text)
    return text[:4000]


def _provider_error(error: Any, operation: str) -> CodexError:
    # Inspect only for classification. Arbitrary remote message text is never returned.
    raw = json.dumps(error, ensure_ascii=False, default=str).lower()
    if any(word in raw for word in ("unauthorized", "authentication", "not logged", "sign in", "login", '"401"')):
        return CodexError("CODEX_AUTH_REQUIRED", "Codex 尚未登录或登录已过期，请在官方 Codex CLI 完成登录后重试。")
    if any(word in raw for word in ("usagelimit", "rate limit", "quota", '"429"')):
        return CodexError("CODEX_RATE_LIMIT", "Codex 当前额度或请求频率受限，请稍后重试或检查对应账号。")
    if isinstance(error, dict) and error.get("code") in (-32601, -32602):
        return CodexError("CODEX_PROTOCOL_UNSUPPORTED", f"当前 Codex 版本不接受 {operation} 协议，请检查 CLI 版本。")
    return CodexError("CODEX_PROVIDER_ERROR", f"Codex 的 {operation} 操作失败；未收到可确认的成功结果。")


class _WindowsJob:
    """Assign a suspended child before it can create descendants, like the host."""

    def __init__(self, process: subprocess.Popen[bytes]):
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
                        ("Flags", w.DWORD), ("Minimum", ctypes.c_size_t), ("Maximum", ctypes.c_size_t),
                        ("ProcessLimit", w.DWORD), ("Affinity", ctypes.c_size_t),
                        ("Priority", w.DWORD), ("Scheduling", w.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

        class Limits(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", Io), ("ProcessMemory", ctypes.c_size_t),
                        ("JobMemory", ctypes.c_size_t), ("PeakProcess", ctypes.c_size_t), ("PeakJob", ctypes.c_size_t)]

        class ThreadEntry(ctypes.Structure):
            _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("thread", w.DWORD),
                        ("owner", w.DWORD), ("priority", w.LONG), ("delta", w.LONG), ("flags", w.DWORD)]

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        api.CreateJobObjectW.restype = w.HANDLE
        api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        api.SetInformationJobObject.restype = w.BOOL
        api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        api.AssignProcessToJobObject.restype = w.BOOL
        api.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
        api.CreateToolhelp32Snapshot.restype = w.HANDLE
        api.Thread32First.argtypes = [w.HANDLE, ctypes.POINTER(ThreadEntry)]
        api.Thread32Next.argtypes = [w.HANDLE, ctypes.POINTER(ThreadEntry)]
        api.OpenThread.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.OpenThread.restype = w.HANDLE
        api.ResumeThread.argtypes = [w.HANDLE]
        api.ResumeThread.restype = w.DWORD
        api.CloseHandle.argtypes = [w.HANDLE]
        api.CloseHandle.restype = w.BOOL
        self.api = api
        self.handle = api.CreateJobObjectW(None, None)
        if not self.handle:
            raise CodexError("CODEX_PROCESS_OWNERSHIP", "无法建立 Codex 子进程生命周期，请检查本机进程权限。")
        try:
            limits = Limits()
            limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise OSError("SetInformationJobObject")
            if not api.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise OSError("AssignProcessToJobObject")
            snapshot = api.CreateToolhelp32Snapshot(0x4, 0)
            if snapshot == ctypes.c_void_p(-1).value:
                raise OSError("CreateToolhelp32Snapshot")
            thread_id = None
            try:
                entry = ThreadEntry()
                entry.size = ctypes.sizeof(entry)
                present = api.Thread32First(snapshot, ctypes.byref(entry))
                while present:
                    if entry.owner == process.pid:
                        thread_id = entry.thread
                        break
                    present = api.Thread32Next(snapshot, ctypes.byref(entry))
            finally:
                api.CloseHandle(snapshot)
            if thread_id is None:
                raise OSError("Owned initial thread missing")
            thread = api.OpenThread(0x2, False, thread_id)
            if not thread:
                raise OSError("OpenThread")
            try:
                if api.ResumeThread(thread) != 1:
                    raise OSError("Unexpected suspension state")
            finally:
                api.CloseHandle(thread)
        except (OSError, ValueError) as error:
            self.close()
            raise CodexError("CODEX_PROCESS_OWNERSHIP", "无法安全接管新建的 Codex 子进程，已停止启动。") from error

    def close(self) -> None:
        if self.handle:
            if not self.api.CloseHandle(self.handle):
                raise CodexError("CODEX_STOP_FAILED", "无法回收本次 Codex 进程资源，请检查运行状态。")
            self.handle = None


class _Client:
    def __init__(self, executable: str | None, workspace: str | None):
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=128)
        self._fault: CodexError | None = None
        self._closing = threading.Event()
        self._id = 0
        self._job: _WindowsJob | None = None
        self._readers: list[threading.Thread] = []
        self._closed = False
        environment = dict(os.environ)
        # API connection credentials must not leak into a separate Codex provider.
        for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CURSOR_API_KEY", "CODEX_API_KEY"):
            environment.pop(key, None)
        kwargs: dict[str, Any] = {"cwd": workspace, "env": environment,
                                  "stdin": subprocess.PIPE, "stdout": subprocess.PIPE,
                                  "stderr": subprocess.PIPE, "shell": False}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | 0x4  # CREATE_SUSPENDED
        else:
            kwargs["start_new_session"] = True
        try:
            self.process = subprocess.Popen(_command(executable), **kwargs)
        except OSError as error:
            raise CodexError("CODEX_START_FAILED", "无法启动指定的 Codex 可执行文件，请检查路径和权限。") from error
        try:
            if os.name == "nt":
                self._job = _WindowsJob(self.process)
            for target in (self._read_stdout, self._drain_stderr):
                thread = threading.Thread(target=target, daemon=True)
                thread.start()
                self._readers.append(thread)
        except BaseException:
            self.process.kill()
            self.process.wait(timeout=5)
            self.close()
            raise

    def _enqueue(self, value: dict[str, Any] | None) -> None:
        try:
            self._queue.put(value, timeout=0.25)
        except queue.Full:
            self._fault = CodexError("CODEX_OUTPUT_LIMIT", "Codex 输出过快或交互积压，已停止本次任务。")

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        try:
            while not self._closing.is_set():
                raw = self.process.stdout.readline(MAX_FRAME_BYTES + 2)
                if not raw:
                    self._enqueue(None)
                    return
                if len(raw) > MAX_FRAME_BYTES or not raw.endswith(b"\n"):
                    raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 返回了过大或不完整的协议消息。")
                try:
                    message = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError, RecursionError) as error:
                    raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 返回了无效的 UTF-8 JSON 协议消息。") from error
                if not isinstance(message, dict):
                    raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 返回的协议消息不是对象。")
                if "id" in message and (type(message["id"]) not in (str, int)
                                        or isinstance(message["id"], str) and not 1 <= len(message["id"]) <= 256):
                    raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 协议消息标识无效。")
                if "method" in message:
                    if not isinstance(message["method"], str) or "result" in message or "error" in message:
                        raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 请求与响应格式混合。")
                elif "id" not in message or (("result" in message) == ("error" in message)):
                    raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 响应缺少明确的结果或错误。")
                self._enqueue(message)
                if self._fault:
                    return
        except CodexError as error:
            self._fault = error
        except (OSError, ValueError):
            if not self._closing.is_set():
                self._fault = CodexError("CODEX_DISCONNECTED", "Codex 协议连接已中断，任务结果未确认。")

    def _drain_stderr(self) -> None:
        # Drain in bounded chunks, but never retain or expose authentication/log data.
        assert self.process.stderr is not None
        try:
            while not self._closing.is_set() and self.process.stderr.read(4096):
                pass
        except (OSError, ValueError):
            if not self._closing.is_set():
                self._fault = CodexError("CODEX_DISCONNECTED", "Codex 错误输出通道已中断。")

    def send(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        if len(encoded) > MAX_FRAME_BYTES:
            raise CodexError("INVALID_PARAMS", "Codex 请求超过允许大小，请减少上下文。")
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(encoded)
            self.process.stdin.flush()
        except (OSError, ValueError) as error:
            raise CodexError("CODEX_DISCONNECTED", "Codex 输入连接已关闭，任务结果未确认。") from error

    def begin(self, method: str, params: dict[str, Any]) -> int:
        self._id += 1
        self.send({"id": self._id, "method": method, "params": params})
        return self._id

    def read(self, deadline: float) -> dict[str, Any] | None:
        if self._fault:
            raise self._fault
        if time.monotonic() >= deadline:
            raise CodexError("CODEX_TIMEOUT", "等待 Codex 协议响应超时；本次进程已停止，不会自动重试任务。")
        try:
            message = self._queue.get(timeout=min(_POLL_SECONDS, max(0.001, deadline - time.monotonic())))
        except queue.Empty:
            return None
        if message is None:
            raise CodexError("CODEX_EXITED", "Codex 在返回最终结果前退出，不能确认任务完成。")
        return message

    def request(self, method: str, params: dict[str, Any], handler: Callable[[dict[str, Any]], None] | None = None,
                cancelled: threading.Event | None = None) -> dict[str, Any]:
        request_id = self.begin(method, params)
        deadline = time.monotonic() + RPC_TIMEOUT
        while True:
            if cancelled is not None and cancelled.is_set():
                raise CodexError("CANCELLED", "本次 Codex 请求已停止。")
            message = self.read(deadline)
            if message is None:
                continue
            if "method" not in message and message.get("id") == request_id:
                if "error" in message:
                    raise _provider_error(message["error"], method)
                result = message.get("result")
                if not isinstance(result, dict):
                    raise CodexError("CODEX_PROTOCOL_ERROR", f"Codex 的 {method} 响应无效。")
                return result
            if handler:
                handler(message)
            elif "method" in message and "id" in message:
                self.reject_request(message)

    def reject_request(self, message: dict[str, Any]) -> None:
        self.send({"id": message["id"], "error": {"code": -32601, "message": "OpenDesk does not support this server request."}})
        raise CodexError("CODEX_UNSUPPORTED_REQUEST", "Codex 请求了当前插件尚未支持的交互；已拒绝，未自动授权。")

    def initialize(self, cancelled: threading.Event | None = None, *, experimental: bool = False) -> None:
        self.request("initialize", {"clientInfo": {"name": "opendesk_ai", "title": "OpenDesk AI", "version": "0.1.0"},
                                    "capabilities": {"experimentalApi": experimental}}, cancelled=cancelled)
        self.send({"method": "initialized", "params": {}})

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._finish_close()
        except (OSError, ValueError, CodexError) as error:
            failure = CodexError("CODEX_STOP_FAILED", "本次 Codex 进程尚未确认回收，停止操作仍在等待。")
            failure.cleanupPending = True
            failure.retry_cleanup = self.close
            raise failure from error
        self._closed = True

    def _finish_close(self) -> None:
        self._closing.set()
        try:
            if self.process.stdin:
                self.process.stdin.close()
        except (OSError, ValueError):
            # Broken stdin means it is already closed; process exit is checked below.
            pass
        try:
            self.process.wait(timeout=CLOSE_TIMEOUT)
        except subprocess.TimeoutExpired:
            if os.name == "nt" and self._job:
                self._job.close()
            elif os.name != "nt":
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                self.process.kill()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired as error:
                raise CodexError("CODEX_STOP_FAILED", "本次 Codex 进程尚未确认退出，不能显示为已取消。") from error
        finally:
            if self._job:
                self._job.close()
            elif os.name != "nt":
                # The root may have exited while an owned descendant remains.
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for thread in self._readers:
                thread.join(timeout=1)
            for pipe in (self.process.stdout, self.process.stderr):
                if pipe:
                    pipe.close()


def _model_option(entry: dict) -> dict:
    model = _identifier(entry.get("model") or entry.get("id"), "模型标识")
    label = entry.get("displayName") if isinstance(entry.get("displayName"), str) else model
    result = {"id": model, "label": label[:256], "isDefault": entry.get("isDefault") is True, "parameters": []}
    efforts = entry.get("supportedReasoningEfforts", [])
    if not isinstance(efforts, list) or len(efforts) > 32:
        raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 推理强度目录无效。")
    effort_options, seen = [], set()
    effort_labels = {"none": "None", "minimal": "Minimal", "low": "Low", "medium": "Medium", "high": "High",
                     "xhigh": "Extra high", "max": "Max", "ultra": "Ultra"}
    normalized_efforts = []
    for option in efforts:
        if not isinstance(option, dict):
            raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 推理强度选项无效。")
        value = _identifier(option.get("reasoningEffort"), "推理强度", 80)
        description = option.get("description")
        if not isinstance(description, str):
            raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 推理强度说明无效。")
        if value not in seen:
            seen.add(value)
            effort_options.append({"value": value, "label": effort_labels.get(value, value), "description": description[:500]})
            normalized_efforts.append({"reasoningEffort": value, "description": description[:500]})
    if effort_options:
        default = _identifier(entry.get("defaultReasoningEffort"), "默认推理强度", 80)
        if default not in seen:
            raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 默认推理强度不在可用目录中。")
        result["supportedReasoningEfforts"] = normalized_efforts
        result["defaultReasoningEffort"] = default
        result["parameters"].append({"id": "reasoningEffort", "label": "推理强度", "options": effort_options, "defaultValue": default})
    tiers = entry.get("serviceTiers", [])
    if not isinstance(tiers, list) or len(tiers) > 32:
        raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 速度档目录无效。")
    tier_options = [{"value": "default", "label": "Standard", "description": "标准速度"}]
    seen = {"default"}
    for tier in tiers:
        if not isinstance(tier, dict):
            raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 速度档选项无效。")
        value = _identifier(tier.get("id"), "速度档", 80)
        label = _identifier(tier.get("name"), "速度档名称", 160)
        description = tier.get("description")
        if not isinstance(description, str):
            raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 速度档说明无效。")
        if value not in seen:
            seen.add(value)
            tier_options.append({"value": value, "label": label, "description": description[:500]})
    if tiers:
        default = entry.get("defaultServiceTier") or "default"
        if not isinstance(default, str) or default not in seen:
            raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 默认速度档不在可用目录中。")
        result["parameters"].append({"id": "serviceTier", "label": "速度", "options": tier_options, "defaultValue": default})
    return result


def _read_model_catalog(client, cancelled=None) -> list[dict]:
    models, seen, seen_cursors = [], set(), set()
    cursor = None
    for _ in range(20):
        params: dict[str, Any] = {"limit": 100, "includeHidden": False}
        if cursor:
            params["cursor"] = cursor
        result = client.request("model/list", params, cancelled=cancelled)
        if not isinstance(result.get("data"), list):
            raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 模型目录格式无效。")
        for entry in result["data"]:
            if not isinstance(entry, dict):
                raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 模型目录条目无效。")
            if entry.get("hidden") is True:
                continue
            option = _model_option(entry)
            if option["id"] not in seen:
                seen.add(option["id"])
                models.append(option)
        next_cursor = result.get("nextCursor")
        if not next_cursor:
            if not models:
                raise CodexError("CODEX_NO_MODELS", "Codex 没有返回可选模型，请检查官方客户端登录状态后重新读取模型。")
            return models
        cursor = _identifier(next_cursor, "模型目录游标", 2048)
        if cursor in seen_cursors:
            raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 模型目录游标重复。")
        seen_cursors.add(cursor)
    raise CodexError("CODEX_OUTPUT_LIMIT", "Codex 模型目录超过分页上限。")


def list_models(executable: str | None = None, workspace: str | None = None) -> list[dict[str, Any]]:
    """Discover this CLI account's catalog without reading or creating threads."""
    client = _Client(executable, _workspace(workspace))
    try:
        client.initialize()
        return _read_model_catalog(client)
    finally:
        client.close()


def _option_shape(options) -> dict:
    if options is None:
        return {}
    if (not isinstance(options, dict) or set(options) - {"reasoningEffort", "serviceTier"}
            or any(not isinstance(value, str) or not value.strip() or len(value) > 80 or not value.isprintable() for value in options.values())):
        raise CodexError("INVALID_MODEL_OPTIONS", "Codex 模型参数无效，请使用当前模型提供的选项。")
    return dict(options)


def validate_model_options(model: str, catalog: list[dict], options) -> dict:
    """Validate against backend-owned discovery, never client-provided capability data."""
    selected = _option_shape(options)
    if not selected:
        return {}
    entry = next((item for item in catalog if isinstance(item, dict) and item.get("id") == model), None)
    if entry is None:
        raise CodexError("MODEL_OPTIONS_UNAVAILABLE", "当前模型不在最新 Codex 目录中，不能设置模型参数。")
    allowed = {parameter["id"]: {item["value"] for item in parameter["options"]}
               for parameter in entry.get("parameters", [])}
    if any(key not in allowed or value not in allowed[key] for key, value in selected.items()):
        raise CodexError("MODEL_OPTION_UNSUPPORTED", "当前模型不支持所选参数，请刷新模型后重新选择。")
    return selected


def run_codex(*, executable: str | None, workspace: str, model: str, prompt: str,
              thread_id: str | None, emit: Callable[[dict[str, Any]], None],
              cancelled: threading.Event, request_approval: Callable[[dict[str, Any]], str],
              model_options: dict | None = None) -> dict[str, Any]:
    """Run one explicit turn; the caller owns conversation persistence and UI events."""
    directory = _workspace(workspace, required=True)
    _identifier(model, "模型标识")
    selected_options = _option_shape(model_options)
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 200_000 or "\x00" in prompt:
        raise CodexError("INVALID_PARAMS", "Codex 输入为空或超过大小限制。")
    if thread_id is not None:
        _identifier(thread_id, "会话标识")
    if cancelled.is_set():
        raise CodexError("CANCELLED", "本次 Codex 请求已停止。")
    client = _Client(executable, directory)
    active_thread = thread_id
    active_turn: str | None = None
    terminal: str | None = None
    terminal_error: Any = None
    items: dict[str, dict[str, str]] = {}
    usage: dict[str, Any] | None = None
    emitted_chars = 0

    def belongs(params: dict[str, Any]) -> bool:
        return (active_thread is not None and params.get("threadId") == active_thread
                and (active_turn is None or params.get("turnId") in (None, active_turn)))

    def handle(message: dict[str, Any]) -> None:
        nonlocal active_turn, terminal, terminal_error, usage, emitted_chars
        method = message.get("method")
        params = message.get("params", {})
        if not isinstance(method, str):
            return
        if not isinstance(params, dict):
            raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 事件参数格式无效。")
        if "id" in message:
            if not belongs(params) or active_turn is None or params.get("turnId") != active_turn:
                client.send({"id": message["id"], "error": {"code": -32602, "message": "Request does not belong to the active turn."}})
                raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 权限请求不属于当前任务，已拒绝。")
            if method not in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
                client.reject_request(message)
            detail = {key: params[key] for key in ("command", "cwd", "reason", "networkApprovalContext") if key in params}
            if len(json.dumps(detail, ensure_ascii=False)) > 4000:
                client.send({"id": message["id"], "result": {"decision": "decline"}})
                raise CodexError("CODEX_APPROVAL_LIMIT", "Codex 权限请求过长，无法完整展示，已拒绝执行。")
            emit({"type": "status", "status": "waiting_approval"})
            decision = "decline" if cancelled.is_set() else request_approval({
                "title": "Codex 请求执行命令" if "commandExecution" in method else "Codex 请求修改文件",
                "detail": _safe_detail(detail), "method": method,
            })
            if decision not in ("accept", "decline") or cancelled.is_set():
                decision = "decline"
            client.send({"id": message["id"], "result": {"decision": decision}})
            emit({"type": "status", "status": "cancelling" if cancelled.is_set() else "running"})
            return
        if not belongs(params):
            return
        if method == "turn/started":
            turn = params.get("turn")
            if isinstance(turn, dict) and isinstance(turn.get("id"), str):
                active_turn = turn["id"]
        elif method == "item/agentMessage/delta":
            item_id = _identifier(params.get("itemId"), "消息标识")
            delta = params.get("delta")
            if not isinstance(delta, str):
                raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 文本增量格式无效。")
            emitted_chars += len(delta)
            if emitted_chars > MAX_OUTPUT_CHARS:
                raise CodexError("CODEX_OUTPUT_LIMIT", "Codex 输出超过单次任务上限。")
            entry = items.setdefault(item_id, {"text": "", "phase": ""})
            entry["text"] += delta
            emit({"type": "delta", "text": delta})
        elif method == "item/completed":
            item = params.get("item", {})
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                item_id = _identifier(item.get("id"), "消息标识")
                text = item.get("text")
                if not isinstance(text, str) or len(text) > MAX_OUTPUT_CHARS:
                    raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 最终消息格式无效。")
                entry = items.setdefault(item_id, {"text": "", "phase": ""})
                if not entry["text"] and text:
                    emit({"type": "delta", "text": text})
                entry["text"] = text
                entry["phase"] = item.get("phase", "")
        elif method == "thread/tokenUsage/updated":
            source = params.get("tokenUsage", {})
            if isinstance(source, dict):
                usage = {}
                for bucket in ("last", "total"):
                    values = source.get(bucket)
                    if isinstance(values, dict):
                        usage[bucket] = {key: value for key, value in values.items()
                                         if key in ("totalTokens", "inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens")
                                         and type(value) is int and value >= 0}
        elif method == "turn/completed":
            turn = params.get("turn")
            if not isinstance(turn, dict) or (active_turn is not None and turn.get("id") != active_turn):
                return
            if active_turn is None:
                active_turn = _identifier(turn.get("id"), "任务标识")
            terminal = turn.get("status")
            terminal_error = turn.get("error")
            if terminal not in ("completed", "interrupted", "failed"):
                raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 返回了未知的终止状态。")

    try:
        emit({"type": "status", "status": "running"})
        client.initialize(cancelled)
        if selected_options:
            selected_options = validate_model_options(model, _read_model_catalog(client, cancelled), selected_options)
        params: dict[str, Any] = {"model": model, "cwd": directory,
                                  "approvalPolicy": "on-request", "sandbox": "read-only"}
        if active_thread:
            params.update({"threadId": active_thread, "excludeTurns": True})
        result = client.request("thread/resume" if active_thread else "thread/start", params, handle, cancelled)
        thread = result.get("thread")
        if not isinstance(thread, dict):
            raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 未返回会话身份。")
        returned_thread = _identifier(thread.get("id"), "会话标识")
        if active_thread and returned_thread != active_thread:
            raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 返回了不同的会话身份。")
        active_thread = returned_thread
        turn_params = {"threadId": active_thread, "input": [{"type": "text", "text": prompt}],
                       "model": model, "cwd": directory, "approvalPolicy": "on-request", "sandboxPolicy": {"type": "readOnly"}}
        if "reasoningEffort" in selected_options:
            turn_params["effort"] = selected_options["reasoningEffort"]
        if "serviceTier" in selected_options:
            # The protocol explicitly distinguishes Standard from inheriting a Fast thread.
            turn_params["serviceTierForTurn"] = selected_options["serviceTier"]
        started = client.request("turn/start", turn_params, handle, cancelled)
        turn = started.get("turn")
        if not isinstance(turn, dict):
            raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 未返回任务身份。")
        returned_turn = _identifier(turn.get("id"), "任务标识")
        if active_turn and active_turn != returned_turn:
            raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 返回了不同的任务身份。")
        active_turn = returned_turn
        deadline = time.monotonic() + TURN_TIMEOUT
        interrupt_deadline: float | None = None
        while terminal is None:
            if cancelled.is_set() and interrupt_deadline is None:
                emit({"type": "status", "status": "cancelling"})
                client.begin("turn/interrupt", {"threadId": active_thread, "turnId": active_turn})
                interrupt_deadline = time.monotonic() + INTERRUPT_TIMEOUT
            if interrupt_deadline and time.monotonic() >= interrupt_deadline:
                # finally closes the owned Job and waits before cancellation is reported.
                raise CodexError("CANCELLED", "Codex 未及时响应中断，已结束本次拥有的代理进程。")
            message = client.read(deadline)
            if message:
                handle(message)
        if terminal == "interrupted":
            raise CodexError("CANCELLED", "Codex 已确认本次任务中断。")
        if terminal == "failed":
            raise _provider_error(terminal_error, "turn/start")
        selected = [entry for entry in items.values() if entry["phase"] == "final_answer"]
        text = "\n\n".join(entry["text"] for entry in (selected or list(items.values())) if entry["text"])
        if not text:
            raise CodexError("CODEX_EMPTY_RESULT", "Codex 已结束，但没有返回可展示的回答。")
        answer: dict[str, Any] = {"text": text, "threadId": active_thread}
        if usage:
            answer["usage"] = usage
        return answer
    finally:
        client.close()
