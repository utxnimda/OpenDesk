"""Read local Codex App projects through the installed CLI's project/list RPC.

The experimental project protocol is feature-detected by its response. This
adapter never reads chat contents or private Codex state/database files.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

from .codex_provider import CodexError, _Client, _identifier, _workspace

_MAX_WORKSPACES = 256


def list_workspaces(executable: str | None = None, workspace: str | None = None) -> dict:
    """Return a fresh project snapshot only after an explicit UI request."""
    client = _Client(executable, _workspace(workspace))
    try:
        client.initialize(experimental=True)
        workspaces: list[dict] = []
        seen: set[str] = set()
        seen_cursors: set[str] = set()
        cursor = None
        for _ in range(20):
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            try:
                page = client.request("project/list", params)
            except CodexError as error:
                if error.code == "CODEX_PROTOCOL_UNSUPPORTED":
                    raise CodexError("CODEX_WORKSPACES_UNSUPPORTED", "当前 Codex CLI 尚不支持工作区目录，请更新 Codex App 或手动填写工作目录。") from error
                raise
            if not isinstance(page.get("data"), list):
                raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 工作区目录格式无效。")
            for project in page["data"]:
                if not isinstance(project, dict) or not isinstance(project.get("roots"), list):
                    raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 工作区条目格式无效。")
                name = _identifier(project.get("name"), "Codex 项目名称", 512)
                for root in project["roots"]:
                    if not isinstance(root, dict):
                        raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 工作区路径格式无效。")
                    raw_path = _identifier(root.get("path"), "Codex 工作区路径", 2048)
                    directory = Path(raw_path)
                    local_absolute = directory.is_absolute()
                    if not local_absolute and not (PurePosixPath(raw_path).is_absolute() or PureWindowsPath(raw_path).is_absolute()):
                        raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 工作区路径必须为绝对目录。")
                    normalized = os.path.normcase(os.path.normpath(str(directory))) if local_absolute else raw_path
                    if normalized in seen:
                        continue
                    if len(workspaces) >= _MAX_WORKSPACES:
                        raise CodexError("CODEX_OUTPUT_LIMIT", "Codex 工作区超过显示上限，请手动填写工作目录。")
                    seen.add(normalized)
                    # Do not contact a network share while displaying local choices.
                    try:
                        available = local_absolute and not raw_path.startswith(("\\\\", "//")) and directory.is_dir()
                    except OSError:
                        available = False
                    workspaces.append({"path": str(directory) if local_absolute else raw_path, "label": name[:160],
                                       "source": "codex-app", "available": available})
            next_cursor = page.get("nextCursor")
            if not next_cursor:
                result = {"workspaces": workspaces}
                if not workspaces:
                    result["warning"] = "Codex App 尚未返回本地工作区；仍可手动填写工作目录。"
                elif any(not item["available"] for item in workspaces):
                    result["warning"] = "部分工作区目录在本机不可用；远程目录或已移动的目录不能直接选用。"
                return result
            cursor = _identifier(next_cursor, "Codex 工作区游标", 2048)
            if cursor in seen_cursors:
                raise CodexError("CODEX_PROTOCOL_ERROR", "Codex 工作区目录游标重复。")
            seen_cursors.add(cursor)
        raise CodexError("CODEX_OUTPUT_LIMIT", "Codex 工作区目录超过分页上限。")
    finally:
        client.close()
