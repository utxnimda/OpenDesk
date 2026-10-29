"""Bounded local Codex installation checks; never starts a process or reads auth."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil

_VERSION = re.compile(r"[a-f0-9]{16}")


def _app_bin() -> Path | None:
    value = os.environ.get("LOCALAPPDATA", "")
    if os.name != "nt" or not value or not Path(value).is_absolute():
        return None
    return (Path(value) / "OpenAI" / "Codex" / "bin").resolve()


def _official_version_path(path: Path, directory: Path | None) -> bool:
    if directory is None:
        return False
    try:
        relative = path.resolve().relative_to(directory)
    except (OSError, ValueError):
        return False
    return (len(relative.parts) == 2 and bool(_VERSION.fullmatch(relative.parts[0]))
            and relative.parts[1].lower() == "codex.exe")


def _app_candidates(directory: Path | None) -> list[Path]:
    if directory is None or not directory.exists():
        return []
    candidates = []
    for entry in directory.iterdir():
        if len(candidates) >= 128:
            break
        candidate = entry / "codex.exe"
        if _VERSION.fullmatch(entry.name) and candidate.is_file() and _official_version_path(candidate, directory):
            candidates.append(candidate.resolve())
    return sorted(candidates, key=lambda path: path.stat().st_mtime_ns, reverse=True)


def inspect_connection(executable: str | None = None) -> dict:
    """Resolve only known local installations; the caller owns any persisted repair.

    An absent explicit path is repairable only inside this user's official App
    bin/<version>/codex.exe layout. Arbitrary missing paths never fall back.
    """
    result = {"available": False, "executable": executable or "", "repaired": False,
              "source": "explicit" if executable else "auto"}
    if executable is not None and (not isinstance(executable, str) or len(executable) > 2048 or "\0" in executable):
        return {**result, "executable": "", "code": "INVALID_PARAMS", "message": "Codex 可执行文件路径无效。"}
    try:
        directory = _app_bin()
        if executable:
            path = Path(executable)
            if not path.is_absolute() or (os.name == "nt" and path.suffix.lower() != ".exe"):
                return {**result, "code": "INVALID_PARAMS", "message": "请填写 Codex 可执行文件的完整路径，不能填写整条命令。"}
            official = _official_version_path(path, directory)
            if path.is_file():
                return {**result, "available": True, "executable": str(path.resolve()),
                        "source": "codex-app" if official else "explicit"}
            if official:
                candidates = _app_candidates(directory)
                if candidates:
                    return {**result, "available": True, "executable": str(candidates[0]), "repaired": True,
                            "source": "codex-app", "message": "Codex App 已更新，已找到当前安装的代理程序。"}
            return {**result, "code": "CODEX_NOT_FOUND", "message": "Codex 程序路径已失效。请填写当前路径，或清空路径使用自动发现。"}
        found = shutil.which("codex.exe" if os.name == "nt" else "codex")
        if found and Path(found).is_file() and (os.name != "nt" or Path(found).suffix.lower() == ".exe"):
            path = Path(found).resolve()
            return {**result, "available": True, "executable": str(path),
                    "source": "codex-app" if _official_version_path(path, directory) else "path"}
        candidates = _app_candidates(directory)
        if candidates:
            return {**result, "available": True, "executable": str(candidates[0]), "source": "codex-app"}
        return {**result, "code": "CODEX_NOT_FOUND", "message": "未找到 Codex CLI，请先安装并登录，或填写可执行文件路径。"}
    except OSError:
        return {**result, "code": "CODEX_DISCOVERY_FAILED", "message": "无法检查 Codex 安装目录，请检查本机文件权限。"}
