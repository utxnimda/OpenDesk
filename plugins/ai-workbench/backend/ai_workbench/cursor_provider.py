"""Cursor Agent CLI adapter using its public ACP protocol, never the IDE UI.

References: https://cursor.com/docs/cli/acp and /cli/reference/parameters.
Only an explicit prompt invokes inference. Model discovery does not create a chat.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from typing import Any, Callable

from .codex_provider import _WindowsJob, _safe_detail

RPC_TIMEOUT = 30.0
TURN_TIMEOUT = 1800.0
INTERRUPT_TIMEOUT = 10.0
CLOSE_TIMEOUT = 2.0
MAX_FRAME_BYTES = 1024 * 1024
MAX_OUTPUT_CHARS = 2 * 1024 * 1024


class CursorError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.cleanupPending = False
        self.retry_cleanup: Callable[[], None] | None = None


def _identifier(value: Any, name: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or not value.isprintable():
        raise CursorError("INVALID_PARAMS", name + "无效。")
    return value


def _workspace(value: str | None, required=False) -> str | None:
    if value is None or value == "":
        if required:
            raise CursorError("INVALID_PARAMS", "请为 Cursor 选择现有的绝对工作目录。")
        return None
    if not isinstance(value, str) or "\0" in value or not Path(value).is_absolute() or not Path(value).is_dir():
        raise CursorError("INVALID_PARAMS", "Cursor 工作目录必须是现有目录的绝对路径。")
    return str(Path(value).resolve())


def _resolve_command(executable: str | None) -> tuple[str, list[str]]:
    if executable:
        if not isinstance(executable, str) or "\0" in executable:
            raise CursorError("INVALID_PARAMS", "Cursor Agent 可执行文件路径无效。")
        path = Path(executable)
        if not path.is_absolute() or not path.is_file():
            raise CursorError("CURSOR_NOT_FOUND", "请填写 Cursor Agent CLI 文件的完整路径，不能填写整条命令。")
        candidates = [path]
    else:
        names = ("cursor-agent.exe", "agent.exe", "cursor-agent.cmd", "agent.cmd") if os.name == "nt" else ("cursor-agent", "agent")
        candidates = [Path(found) for name in names if (found := shutil.which(name))]
        if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
            directory = Path(os.environ["LOCALAPPDATA"]) / "cursor-agent"
            candidates += [directory / name for name in names]
        else:
            candidates += [Path.home() / ".local" / "bin" / name for name in names]
    for path in candidates:
        if not path.is_file():
            continue
        if os.name != "nt" or path.suffix.lower() == ".exe":
            if path.stem.lower() == "cursor":
                raise CursorError("CURSOR_WRONG_EXECUTABLE", "Cursor.exe 是编辑器，请选择独立的 Cursor Agent CLI。")
            return str(path.resolve()), [str(path.resolve())]
        # Official Windows scripts are not evaluated as shell commands. Resolve
        # their adjacent installed runtime instead; caller input never becomes code.
        if path.name.lower() in ("cursor-agent.cmd", "agent.cmd", "cursor-agent.ps1", "agent.ps1"):
            runtime, entry = path.parent / "node.exe", path.parent / "index.js"
            if runtime.is_file() and entry.is_file():
                return str(path.resolve()), [str(runtime.resolve()), str(entry.resolve())]
            versions = path.parent / "versions"
            if versions.is_dir():
                for folder in sorted(versions.iterdir(), reverse=True)[:64]:
                    if not folder.is_dir() or not re.fullmatch(r"\d{4}\.\d{1,2}\.\d{1,2}(?:-\d{2}-\d{2}-\d{2})?-[a-f0-9]+", folder.name):
                        continue
                    runtime, entry = folder / "node.exe", folder / "index.js"
                    if runtime.is_file() and entry.is_file():
                        return str(path.resolve()), [str(runtime.resolve()), str(entry.resolve())]
    raise CursorError("CURSOR_NOT_FOUND", "未找到 Cursor Agent CLI；Cursor 编辑器本身不能代替它。请按 cursor.com/docs/cli/installation 安装，并执行 agent login 后重试。")


def inspect_connection(executable: str | None = None) -> dict:
    """Check launcher/runtime files only; never starts CLI or reads credentials."""
    result = {"available": False, "executable": executable or "", "repaired": False}
    if executable is not None and (not isinstance(executable, str) or len(executable) > 2048 or "\0" in executable):
        return {**result, "executable": "", "code": "INVALID_PARAMS", "message": "Cursor Agent 程序路径无效。"}
    try:
        launcher, _ = _resolve_command(executable)
        return {**result, "available": True, "executable": launcher}
    except CursorError as error:
        return {**result, "code": error.code, "message": error.message}
    except OSError:
        return {**result, "code": "CURSOR_DISCOVERY_FAILED", "message": "无法检查 Cursor Agent 文件，请检查本机文件权限。"}


def _command(executable: str | None) -> list[str]:
    return _resolve_command(executable)[1]


def _provider_error(value: Any) -> CursorError:
    # Provider messages may contain private prompts or credentials; classify only.
    raw = json.dumps(value, ensure_ascii=False, default=str).lower()
    if any(word in raw for word in ("auth_required", "unauthorized", "not logged", "authentication", "login", "401")):
        return CursorError("CURSOR_AUTH_REQUIRED", "Cursor Agent 尚未登录或登录已过期，请先执行 agent login。")
    if any(word in raw for word in ("rate limit", "quota", "429")):
        return CursorError("CURSOR_RATE_LIMIT", "Cursor 当前额度或请求频率受限，请检查账号后重试。")
    if "invalid model value" in raw or "unknown model" in raw:
        return CursorError("CURSOR_MODEL_UNAVAILABLE", "此模型 ID 不在 Cursor ACP 目录中，请刷新模型列表并重新选择。")
    if isinstance(value, dict) and value.get("code") in (-32601, -32602):
        return CursorError("CURSOR_PROTOCOL_UNSUPPORTED", "当前 Cursor Agent 不支持所需 ACP 协议，请更新官方 CLI 后重试。")
    return CursorError("CURSOR_PROVIDER_ERROR", "Cursor Agent 操作失败，未收到可确认的成功结果。")


class _Process:
    def __init__(self, executable, workspace, arguments):
        self.queue: queue.Queue[bytes | None] = queue.Queue(maxsize=128)
        self.fault = None
        self.closing = threading.Event()
        self.job = None
        self.closed = False
        self.readers = []
        environment = dict(os.environ)
        for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CODEX_API_KEY", "CURSOR_API_KEY", "CURSOR_AUTH_TOKEN"):
            environment.pop(name, None)
        environment["NO_COLOR"] = "1"
        environment["CURSOR_AGENT_DISABLE_DEBUG_LOG"] = "1"
        kwargs = dict(cwd=workspace, env=environment, stdin=subprocess.PIPE,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | 0x4
        else:
            kwargs["start_new_session"] = True
        try:
            self.process = subprocess.Popen(_command(executable) + arguments, **kwargs)
        except OSError as error:
            raise CursorError("CURSOR_START_FAILED", "无法启动 Cursor Agent，请检查完整路径和文件权限。") from error
        try:
            if os.name == "nt":
                self.job = _WindowsJob(self.process)
            for target in (self._stdout, self._stderr):
                reader = threading.Thread(target=target, daemon=True)
                reader.start()
                self.readers.append(reader)
        except BaseException as error:
            self.process.kill()
            self.process.wait(timeout=5)
            self.close()
            raise CursorError("CURSOR_PROCESS_OWNERSHIP", "无法安全管理新建的 Cursor Agent 进程，已停止启动。") from error

    def _stdout(self):
        try:
            while not self.closing.is_set():
                line = self.process.stdout.readline(MAX_FRAME_BYTES + 2)
                if len(line) > MAX_FRAME_BYTES:
                    raise CursorError("CURSOR_OUTPUT_LIMIT", "Cursor 单条输出超过大小限制。")
                self.queue.put(line or None, timeout=0.25)
                if not line:
                    return
        except queue.Full:
            self.fault = CursorError("CURSOR_OUTPUT_LIMIT", "Cursor 输出积压，已停止任务。")
        except (OSError, ValueError):
            if not self.closing.is_set():
                self.fault = CursorError("CURSOR_DISCONNECTED", "Cursor 输出通道已断开，结果未确认。")
        except CursorError as error:
            self.fault = error

    def _stderr(self):
        try:
            while not self.closing.is_set() and self.process.stderr.read(4096):
                pass
        except (OSError, ValueError):
            if not self.closing.is_set():
                self.fault = CursorError("CURSOR_DISCONNECTED", "Cursor 错误通道已断开。")

    def read(self, deadline):
        if self.fault:
            raise self.fault
        if time.monotonic() >= deadline:
            raise CursorError("CURSOR_TIMEOUT", "等待 Cursor 响应超时，已停止本次进程，不会自动重试。")
        try:
            return self.queue.get(timeout=min(.1, max(.001, deadline - time.monotonic())))
        except queue.Empty:
            return b""

    def send(self, value):
        raw = json.dumps({"jsonrpc": "2.0", **value}, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        if len(raw) > MAX_FRAME_BYTES:
            raise CursorError("INVALID_PARAMS", "Cursor 请求超过大小限制。")
        try:
            self.process.stdin.write(raw)
            self.process.stdin.flush()
        except (OSError, ValueError) as error:
            raise CursorError("CURSOR_DISCONNECTED", "Cursor 输入通道已关闭。") from error

    def close(self):
        if self.closed:
            return
        try:
            self._close()
        except Exception as error:
            failure = CursorError("CURSOR_STOP_FAILED", "Cursor 进程尚未确认停止，结果未知。")
            failure.cleanupPending = True
            failure.retry_cleanup = self.close
            raise failure from error
        self.closed = True

    def _close(self):
        self.closing.set()
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            pass  # Broken input is already closed; process exit still must be confirmed.
        try:
            self.process.wait(timeout=CLOSE_TIMEOUT)
        except subprocess.TimeoutExpired:
            if self.job:
                self.job.close()
            elif os.name != "nt":
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                self.process.kill()
            self.process.wait(timeout=5)
        finally:
            if self.job:
                self.job.close()
            elif os.name != "nt":
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for reader in self.readers:
                reader.join(timeout=1)
            self.process.stdout.close()
            self.process.stderr.close()


def list_models(executable: str | None = None, workspace: str | None = None) -> list[dict]:
    client = _Client(executable, _workspace(workspace), None)
    try:
        cancelled = threading.Event()
        def reject_request(value):
            if "method" in value and "id" in value:
                client.process.send({"id": value["id"], "error": {"code": -32601, "message": "Catalog queries do not accept client requests."}})
                raise CursorError("CURSOR_UNSUPPORTED_REQUEST", "Cursor 查询模型时请求了其他交互，已拒绝。")
        initialized = client.request("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                                     "clientInfo": {"name": "opendesk_ai", "version": "0.2.0"}}, reject_request, cancelled)
        if initialized.get("protocolVersion") != 1:
            raise CursorError("CURSOR_PROTOCOL_UNSUPPORTED", "Cursor ACP 协议版本不受支持。")
        # CLI --list-models contains aliases that session/set_model rejects.
        # This Cursor ACP extension returns the actual model configuration IDs.
        result = client.request("cursor/list_available_models", {}, reject_request, cancelled)
        return _model_options(result.get("models"))
    finally:
        client.process.close()


def _model_options(entries) -> list[dict]:
    if not isinstance(entries, list) or not 1 <= len(entries) <= 500:
        raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 模型目录为空或格式无效。")
    models, seen = [], set()
    atom = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
    for entry in entries:
        if not isinstance(entry, dict):
            raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 模型条目格式无效。")
        identity, label = entry.get("value"), entry.get("name")
        configurations = entry.get("configOptions", [])
        if not isinstance(identity, str) or not atom.fullmatch(identity) or not isinstance(label, str) or not 1 <= len(label) <= 256 or not isinstance(configurations, list) or len(configurations) > 20:
            raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 模型标识或配置无效。")
        values, keys, parameters = [], set(), []
        for configuration in configurations:
            if not isinstance(configuration, dict):
                raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 模型参数无效。")
            key, value = configuration.get("id"), configuration.get("currentValue")
            if not isinstance(key, str) or not atom.fullmatch(key) or not isinstance(value, str) or not atom.fullmatch(value) or key in keys:
                raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 模型参数缺少有效的当前值。")
            keys.add(key)
            values.append(key + "=" + value)
            label_value, options = configuration.get("name"), configuration.get("options")
            if not isinstance(label_value, str) or not 1 <= len(label_value) <= 160 or not isinstance(options, list) or not 1 <= len(options) <= 100:
                raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 模型参数选项无效。")
            choices, choice_values = [], set()
            for option in options:
                if not isinstance(option, dict) or not isinstance(option.get("value"), str) or not atom.fullmatch(option["value"]) or not isinstance(option.get("name"), str) or not 1 <= len(option["name"]) <= 160:
                    raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 模型参数枚举无效。")
                if option["value"] in choice_values:
                    raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 模型参数枚举重复。")
                choice_values.add(option["value"])
                choices.append({"value": option["value"], "label": option["name"]})
            if value not in choice_values:
                raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 模型参数当前值不在支持的选项中。")
            parameters.append({"id": key, "label": label_value, "options": choices, "defaultValue": value})
        # ACP requires the brackets even for models with no parameters.
        identity += "[" + ",".join(values) + "]"
        _identifier(identity, "模型标识")
        if identity not in seen:
            models.append({"id": identity, "label": label, "providerModelId": entry["value"], "parameters": parameters})
            seen.add(identity)
    return models


def _option_shape(options) -> dict:
    if options is None:
        return {}
    atom = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
    if not isinstance(options, dict) or len(options) > 20 or any(not isinstance(key, str) or not atom.fullmatch(key) or not isinstance(value, str) or not atom.fullmatch(value) for key, value in options.items()):
        raise CursorError("INVALID_MODEL_OPTIONS", "Cursor 模型参数无效，请使用目录中的选项。")
    return dict(options)


def _catalog_entry(model: str, catalog: list[dict]) -> dict | None:
    base = model.split("[", 1)[0]
    return next((entry for entry in catalog if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"].split("[", 1)[0] == base), None)


def validate_model_options(model: str, catalog: list[dict], options) -> dict:
    selected = _option_shape(options)
    if not selected:
        return {}
    entry = _catalog_entry(model, catalog)
    if entry is None:
        raise CursorError("MODEL_OPTIONS_UNAVAILABLE", "当前模型不在最新 Cursor 目录中，不能设置模型参数。")
    allowed = {parameter["id"]: {option["value"] for option in parameter["options"]} for parameter in entry.get("parameters", [])}
    if any(key not in allowed or value not in allowed[key] for key, value in selected.items()):
        raise CursorError("MODEL_OPTION_UNSUPPORTED", "当前 Cursor 模型不支持所选参数，请刷新模型后重新选择。")
    return selected


def _model_with_options(model: str, catalog: list[dict], options) -> str:
    selected = validate_model_options(model, catalog, options)
    if not selected:
        return model
    entry = _catalog_entry(model, catalog)
    match = re.fullmatch(r"([^\[\]]+)\[([^\[\]]*)\]", model)
    if not match:
        raise CursorError("INVALID_MODEL_OPTIONS", "Cursor 模型 ID 缺少有效配置，请从目录重新选择。")
    embedded = {}
    for pair in match[2].split(",") if match[2] else []:
        parts = pair.split("=")
        if len(parts) != 2 or parts[0] in embedded:
            raise CursorError("INVALID_MODEL_OPTIONS", "Cursor 原有模型配置格式无效。")
        embedded[parts[0]] = parts[1]
    validate_model_options(model, catalog, embedded)
    chosen = {**embedded, **selected}
    parameters = entry.get("parameters", [])
    identity = match[1] + "[" + ",".join(parameter["id"] + "=" + chosen.get(parameter["id"], parameter["defaultValue"]) for parameter in parameters) + "]"
    return _identifier(identity, "模型标识")


def _apply_parameterized_model(client, session, canonical, handle, cancelled):
    """Cursor's negotiated parameter picker uses config options, not variant IDs."""
    base, values = canonical[:-1].split("[", 1)
    parameters = dict(pair.split("=", 1) for pair in values.split(",") if pair)
    result = client.request("session/set_config_option", {"sessionId": session, "configId": "model", "value": base}, handle, cancelled)
    for key, value in parameters.items():
        result = client.request("session/set_config_option", {"sessionId": session, "configId": key, "value": value}, handle, cancelled)
    options = result.get("configOptions")
    if not isinstance(options, list):
        raise CursorError("CURSOR_PROTOCOL_UNSUPPORTED", "Cursor 未确认模型参数，请更新 CLI 或清除自定义参数。")
    actual = {item.get("id"): item.get("currentValue") for item in options if isinstance(item, dict)}
    if actual.get("model") != base or any(actual.get(key) != value for key, value in parameters.items()):
        raise CursorError("CURSOR_MODEL_OPTIONS_MISMATCH", "Cursor 返回的模型参数与选择不一致，已停止发送。")


class _Client:
    def __init__(self, executable, workspace, model):
        arguments = ["--model", model] if model else []
        self.process = _Process(executable, workspace, ["--disable-auto-update"] + arguments + ["--mode", "ask", "acp"])
        self.identity = 0

    def begin(self, method, params):
        self.identity += 1
        self.process.send({"id": self.identity, "method": method, "params": params})
        return self.identity

    def read(self, deadline):
        line = self.process.read(deadline)
        if line is None:
            raise CursorError("CURSOR_EXITED", "Cursor 在完成协议交互前退出，不能确认任务完成。")
        if not line:
            return None
        try:
            value = json.loads(line)
        except (ValueError, UnicodeDecodeError, RecursionError) as error:
            raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 返回了无效的 JSON 协议消息。") from error
        if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
            raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 返回的 ACP 消息格式无效。")
        if "id" in value and (type(value["id"]) not in (str, int) or len(str(value["id"])) > 256):
            raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 请求标识无效。")
        if "method" in value:
            if not isinstance(value["method"], str) or "result" in value or "error" in value:
                raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 请求与响应格式混合。")
        elif "id" not in value or (("result" in value) == ("error" in value)):
            raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 响应缺少明确结果。")
        return value

    def request(self, method, params, handle, cancelled):
        request = self.begin(method, params)
        deadline = time.monotonic() + RPC_TIMEOUT
        while True:
            if cancelled.is_set():
                raise CursorError("CANCELLED", "Cursor 请求已停止。")
            value = self.read(deadline)
            if value is None:
                continue
            if "method" not in value and value.get("id") == request:
                if "error" in value:
                    raise _provider_error(value["error"])
                if not isinstance(value.get("result"), dict):
                    raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 协议结果无效。")
                return value["result"]
            handle(value)


def run_cursor(*, executable: str | None, workspace: str, model: str, prompt: str,
               thread_id: str | None, emit: Callable[[dict], None], cancelled: threading.Event,
               request_approval: Callable[[dict], str], model_options: dict | None = None) -> dict:
    directory = _workspace(workspace, required=True)
    _identifier(model, "模型标识")
    selected_options = _option_shape(model_options)
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 200_000 or "\0" in prompt:
        raise CursorError("INVALID_PARAMS", "Cursor 输入为空或超过大小限制。")
    if thread_id is not None:
        _identifier(thread_id, "会话标识")
    if cancelled.is_set():
        raise CursorError("CANCELLED", "Cursor 请求已停止。")
    client = _Client(executable, directory, None if selected_options else model)
    session = thread_id
    text = ""
    prompting = False

    def handle(value):
        nonlocal text
        method, params = value.get("method"), value.get("params", {})
        if not isinstance(params, dict):
            raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 事件参数无效。")
        if "id" in value and method:
            if method != "session/request_permission" or params.get("sessionId") != session or not prompting:
                client.process.send({"id": value["id"], "error": {"code": -32601, "message": "Unsupported client request."}})
                raise CursorError("CURSOR_UNSUPPORTED_REQUEST", "Cursor 请求了当前不支持的交互，已拒绝，未自动授权。")
            options, detail = params.get("options"), params.get("toolCall")
            if not isinstance(options, list) or not isinstance(detail, dict) or len(json.dumps(detail, ensure_ascii=False)) > 4000:
                client.process.send({"id": value["id"], "result": {"outcome": {"outcome": "cancelled"}}})
                raise CursorError("CURSOR_APPROVAL_LIMIT", "Cursor 权限请求无法完整展示，已拒绝执行。")
            decisions = {}
            for option in options:
                if not isinstance(option, dict):
                    raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 权限选项无效。")
                kind = option.get("kind")
                if kind in ("allow_once", "reject_once"):
                    decisions[kind] = _identifier(option.get("optionId"), "权限选项")
            emit({"type": "status", "status": "waiting_approval"})
            choice = "decline" if cancelled.is_set() else request_approval({"title": "Cursor 请求操作授权", "detail": _safe_detail(detail)})
            chosen = decisions.get("allow_once" if choice == "accept" else "reject_once")
            outcome = {"outcome": "selected", "optionId": chosen} if chosen and not cancelled.is_set() else {"outcome": "cancelled"}
            client.process.send({"id": value["id"], "result": {"outcome": outcome}})
            emit({"type": "status", "status": "cancelling" if cancelled.is_set() else "running"})
        elif method == "session/update" and params.get("sessionId") == session:
            update = params.get("update")
            if not isinstance(update, dict):
                raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 会话更新无效。")
            if update.get("sessionUpdate") == "current_mode_update" and update.get("currentModeId") != "ask":
                raise CursorError("CURSOR_MODE_CHANGED", "Cursor 意外离开只读 Ask 模式，已停止本次任务。")
            if prompting and update.get("sessionUpdate") == "agent_message_chunk":
                content = update.get("content")
                if not isinstance(content, dict) or content.get("type") != "text" or not isinstance(content.get("text"), str):
                    raise CursorError("CURSOR_PROTOCOL_UNSUPPORTED", "当前工作台仅支持 Cursor 文本回答。")
                text += content["text"]
                if len(text) > MAX_OUTPUT_CHARS:
                    raise CursorError("CURSOR_OUTPUT_LIMIT", "Cursor 回答超过单次任务上限。")
                emit({"type": "delta", "text": content["text"]})

    try:
        emit({"type": "status", "status": "running"})
        client_capabilities = {"_meta": {"parameterizedModelPicker": True}} if selected_options else {}
        initialized = client.request("initialize", {"protocolVersion": 1, "clientCapabilities": client_capabilities,
                                     "clientInfo": {"name": "opendesk_ai", "version": "0.2.0"}}, handle, cancelled)
        if initialized.get("protocolVersion") != 1:
            raise CursorError("CURSOR_PROTOCOL_UNSUPPORTED", "Cursor ACP 协议版本不受支持。")
        capabilities = initialized.get("agentCapabilities", {})
        if not isinstance(capabilities, dict):
            raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor ACP 能力声明无效。")
        if session and capabilities.get("loadSession") is not True:
            raise CursorError("CURSOR_RESUME_UNSUPPORTED", "此 Cursor CLI 不支持继续已保存会话，请更新 CLI 或新建对话。")
        if selected_options:
            catalog = client.request("cursor/list_available_models", {}, handle, cancelled)
            model = _model_with_options(model, _model_options(catalog.get("models")), selected_options)
        params = {"cwd": directory, "mcpServers": []}
        if session:
            params["sessionId"] = session
        result = client.request("session/load" if session else "session/new", params, handle, cancelled)
        if not session:
            session = _identifier(result.get("sessionId"), "会话标识")
        elif result.get("sessionId", session) != session:
            raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 返回了不同的会话身份。")
        client.request("session/set_mode", {"sessionId": session, "modeId": "ask"}, handle, cancelled)
        if selected_options:
            _apply_parameterized_model(client, session, model, handle, cancelled)
        else:
            client.request("session/set_model", {"sessionId": session, "modelId": model}, handle, cancelled)
        prompting = True
        request = client.begin("session/prompt", {"sessionId": session, "prompt": [{"type": "text", "text": prompt}]})
        deadline, interrupted = time.monotonic() + TURN_TIMEOUT, None
        while True:
            if cancelled.is_set() and interrupted is None:
                emit({"type": "status", "status": "cancelling"})
                client.process.send({"method": "session/cancel", "params": {"sessionId": session}})
                interrupted = time.monotonic() + INTERRUPT_TIMEOUT
            if interrupted and time.monotonic() >= interrupted:
                raise CursorError("CANCELLED", "Cursor 未及时响应中断，已结束本次拥有的进程。")
            value = client.read(deadline)
            if value is None:
                continue
            if "method" not in value and value.get("id") == request:
                if "error" in value:
                    raise _provider_error(value["error"])
                result = value.get("result")
                reason = result.get("stopReason") if isinstance(result, dict) else None
                if reason == "cancelled" or cancelled.is_set():
                    raise CursorError("CANCELLED", "Cursor 已确认本次任务中断。")
                if reason != "end_turn":
                    raise CursorError("CURSOR_INCOMPLETE", "Cursor 未正常完成本轮回答，请检查额度或新建对话。")
                if not text:
                    raise CursorError("CURSOR_EMPTY_RESULT", "Cursor 已结束，但没有返回可展示的回答。")
                return {"text": text, "threadId": session}
            handle(value)
    finally:
        client.process.close()
