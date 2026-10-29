"""Read registered Cursor workspace metadata; never load outside conversations.

ACP session/list supplies Agent directories. The desktop App's known workspace
index contributes folders without reading chat records or authentication data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sqlite3
import threading
from urllib.parse import unquote, urlsplit, urlunsplit

from .cursor_provider import CursorError, _Client, _identifier, _workspace

_MAX_METADATA_BYTES = 512 * 1024
_MAX_ENTRIES = 1000
_MAX_WORKSPACES = 256


def _parse_json(raw) -> dict:
    if not isinstance(raw, (str, bytes)) or len(raw.encode("utf-8") if isinstance(raw, str) else raw) > _MAX_METADATA_BYTES:
        raise CursorError("CURSOR_METADATA_LIMIT", "Cursor 工作区索引超过读取上限。")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise CursorError("CURSOR_METADATA_INVALID", "Cursor 工作区索引格式无效，未修改原文件。") from error
    if not isinstance(value, dict):
        raise CursorError("CURSOR_METADATA_INVALID", "Cursor 工作区索引格式无效。")
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > 16 or count > 20_000:
            raise CursorError("CURSOR_METADATA_LIMIT", "Cursor 工作区索引层级或条目超过上限。")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return value


def _entries(value, name) -> list:
    entries = value.get(name, [])
    if not isinstance(entries, list) or len(entries) > _MAX_ENTRIES:
        raise CursorError("CURSOR_METADATA_INVALID", "Cursor 工作区索引条目无效或超过上限。")
    return entries


def _workspace_option(raw, source: str, *, uri=False) -> dict:
    raw = _identifier(raw, "Cursor 工作区路径", 2048)
    if uri:
        parsed = urlsplit(raw)
        if parsed.scheme != "file":
            safe_uri = urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
            return {"path": safe_uri, "label": unquote(parsed.path).rstrip("/").rsplit("/", 1)[-1] or parsed.hostname or "远程工作区",
                    "source": source, "available": False}
        if parsed.query or parsed.fragment:
            raise CursorError("CURSOR_METADATA_INVALID", "Cursor 文件目录 URI 包含无效参数。")
        raw = unquote(parsed.path)
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            raw = "//" + parsed.netloc + raw
        elif len(raw) > 3 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
    windows, posix = PureWindowsPath(raw), PurePosixPath(raw)
    if not windows.is_absolute() and not posix.is_absolute():
        raise CursorError("CURSOR_METADATA_INVALID", "Cursor 工作区路径必须为绝对目录。")
    directory = Path(raw)
    available = False
    if directory.is_absolute() and not raw.startswith(("\\\\", "//")):
        try:
            available = directory.is_dir()
        except OSError:
            available = False
    return {"path": str(directory) if directory.is_absolute() else raw,
            "label": (windows.name if windows.is_absolute() else posix.name)[:160] or raw[:160],
            "source": source, "available": available}


def _app_directories(storage_root: Path | None = None) -> tuple[list[dict], list[str]]:
    if storage_root is None:
        roaming = os.environ.get("APPDATA", "")
        if os.name != "nt" or not roaming or not Path(roaming).is_absolute():
            return [], []
        storage_root = Path(roaming) / "Cursor" / "User" / "globalStorage"
    result, warnings = [], []
    storage = storage_root / "storage.json"
    try:
        if storage.is_file():
            with storage.open("rb") as stream:
                data = _parse_json(stream.read(_MAX_METADATA_BYTES + 1))
            backups, windows = data.get("backupWorkspaces", {}), data.get("windowsState", {})
            if not isinstance(backups, dict) or not isinstance(windows, dict):
                raise CursorError("CURSOR_METADATA_INVALID", "Cursor 工作区索引字段无效。")
            for folder in _entries(backups, "folders"):
                if not isinstance(folder, dict) or "folderUri" not in folder:
                    raise CursorError("CURSOR_METADATA_INVALID", "Cursor 文件夹索引无效。")
                result.append(_workspace_option(folder["folderUri"], "cursor-app", uri=True))
            opened = _entries(windows, "openedWindows")
            if windows.get("lastActiveWindow"):
                opened = [*opened, windows["lastActiveWindow"]]
            for window in opened:
                if not isinstance(window, dict):
                    raise CursorError("CURSOR_METADATA_INVALID", "Cursor 窗口目录索引无效。")
                if window.get("folder"):
                    result.append(_workspace_option(window["folder"], "cursor-app", uri=True))
    except (OSError, CursorError) as error:
        warnings.append(getattr(error, "message", "无法读取 Cursor App 工作区索引，请检查文件权限。"))
    database_path = storage_root / "state.vscdb"
    try:
        if database_path.is_file():
            # One known metadata key only. No table scan, database copy, or writes.
            database = sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
            try:
                database.execute("PRAGMA query_only=ON")
                row = database.execute("SELECT CASE WHEN length(CAST(value AS BLOB))<=? THEN value ELSE NULL END FROM ItemTable WHERE key=?",
                                       (_MAX_METADATA_BYTES, "history.recentlyOpenedPathsList")).fetchone()
                if row is not None:
                    if row[0] is None:
                        raise CursorError("CURSOR_METADATA_LIMIT", "Cursor 最近工作区记录超过读取上限。")
                    data = _parse_json(row[0])
                    for entry in _entries(data, "entries"):
                        if not isinstance(entry, dict):
                            raise CursorError("CURSOR_METADATA_INVALID", "Cursor 最近工作区记录无效。")
                        if "folderUri" in entry:
                            result.append(_workspace_option(entry["folderUri"], "cursor-app", uri=True))
            finally:
                database.close()
    except (OSError, sqlite3.Error, CursorError) as error:
        warnings.append(getattr(error, "message", "无法读取 Cursor App 最近工作区记录，请检查文件权限。"))
    return result, warnings


def _agent_directories(executable, workspace) -> list[dict]:
    client = _Client(executable, _workspace(workspace), None)
    cancelled = threading.Event()
    try:
        def reject(value):
            if "id" in value and "method" in value:
                client.process.send({"id": value["id"], "error": {"code": -32601, "message": "Workspace discovery does not accept client requests."}})
                raise CursorError("CURSOR_UNSUPPORTED_REQUEST", "Cursor 查询工作区时请求了其他交互，已拒绝。")
        initialized = client.request("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                                     "clientInfo": {"name": "opendesk_ai", "version": "0.3.0"}}, reject, cancelled)
        capabilities = initialized.get("agentCapabilities", {})
        sessions = capabilities.get("sessionCapabilities", {}) if isinstance(capabilities, dict) else {}
        if initialized.get("protocolVersion") != 1 or not isinstance(sessions, dict) or "list" not in sessions:
            raise CursorError("CURSOR_WORKSPACES_UNSUPPORTED", "当前 Cursor Agent 不支持会话目录，请更新 CLI 或手动填写工作目录。")
        result, seen, cursor, count = [], set(), None, 0
        for _ in range(20):
            page = client.request("session/list", {"cursor": cursor} if cursor else {}, reject, cancelled)
            entries = page.get("sessions")
            if not isinstance(entries, list):
                raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 会话目录格式无效。")
            count += len(entries)
            if count > _MAX_ENTRIES:
                raise CursorError("CURSOR_OUTPUT_LIMIT", "Cursor 会话目录超过显示上限。")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 会话目录条目无效。")
                result.append(_workspace_option(entry.get("cwd"), "cursor-agent"))
                # Session titles/IDs and message data are neither returned nor saved.
                if "additionalDirectories" in sessions:
                    for directory in _entries(entry, "additionalDirectories"):
                        result.append(_workspace_option(directory, "cursor-agent"))
            cursor = page.get("nextCursor")
            if not cursor:
                return result
            cursor = _identifier(cursor, "Cursor 工作区游标", 2048)
            if cursor in seen:
                raise CursorError("CURSOR_PROTOCOL_ERROR", "Cursor 工作区目录游标重复。")
            seen.add(cursor)
        raise CursorError("CURSOR_OUTPUT_LIMIT", "Cursor 工作区目录超过分页上限。")
    finally:
        client.process.close()


def list_workspaces(executable: str | None = None, workspace: str | None = None) -> dict:
    """Merge App-registered folders before CLI directories; no external chat load."""
    app, warnings = _app_directories()
    try:
        agent = _agent_directories(executable, workspace)
    except CursorError as error:
        if error.cleanupPending:
            raise
        agent = []
        warnings.append(error.message)
    result, seen = [], set()
    for item in [*app, *agent]:
        key = os.path.normcase(os.path.normpath(item["path"]))
        if key not in seen:
            if len(result) >= _MAX_WORKSPACES:
                warnings.append("Cursor 工作区超过显示上限，请手动填写其他目录。")
                break
            seen.add(key)
            result.append(item)
    if not result:
        warnings.append("Cursor 尚未返回已登记工作区；仍可手动填写目录。")
    if any(not item["available"] for item in result):
        warnings.append("部分工作区为远程目录或本机已不可用，不能直接选用。")
    output = {"workspaces": result}
    if warnings:
        output["warning"] = " ".join(dict.fromkeys(warnings))
    return output
