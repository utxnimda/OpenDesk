"""Local agent identity and directory metadata; never opens a chat or a shell."""
from __future__ import annotations

import os
from pathlib import Path


def inspect_connection(connection, has_key=False):
    kind = connection["kind"]
    if kind == "codex":
        from .codex_discovery import inspect_connection as inspect
    elif kind == "cursor":
        from .cursor_provider import inspect_connection as inspect
    else:
        return {"status": "ready" if has_key else "unconfigured",
                "message": "已配置 API；请求时验证服务与账号。" if has_key else "尚未配置 API Key。"}
    result = inspect(connection.get("executable") or None)
    return {"status": "ready" if result.get("available") else "missing",
            "message": result.get("message") or ("本机代理程序可用。" if result.get("available") else "找不到代理程序。"),
            "resolvedExecutable": result.get("executable") or "", "autoRepaired": result.get("repaired") is True}


def path_key(value):
    # Avoid resolving paths, which may touch unavailable network shares.
    return os.path.normcase(os.path.normpath(value)) if value else ""


def workspace_entry(connection_id, path, *, label=None, source="configured"):
    try:
        available = bool(path and Path(path).is_absolute()
                         and not path.startswith(("\\\\", "//")) and Path(path).is_dir())
    except OSError:
        available = False
    return {"connectionId": connection_id, "path": path,
            "label": label or Path(path).name or path, "source": source, "available": available}


def mark_duplicates(connections):
    """Keep every identity/history, but expose one new-task choice per local agent."""
    groups = {}
    for connection in connections:
        if connection["kind"] not in {"codex", "cursor"} or connection["health"]["status"] != "ready":
            continue
        key = (connection["kind"], path_key(connection["health"]["resolvedExecutable"]), path_key(connection.get("workspace", "")))
        groups.setdefault(key, []).append(connection)
    for group in groups.values():
        # Automatic discovery survives normal vendor upgrades; prefer it when
        # both it and a stale version-pinned connection describe the same agent.
        primary = min(group, key=lambda c: (bool(c.get("executable")), c.get("origin") == "detected", c["id"]))
        for connection in group:
            if connection["id"] != primary["id"]:
                connection["duplicateOf"] = primary["id"]
    return connections
