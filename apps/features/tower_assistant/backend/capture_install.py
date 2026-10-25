"""Explicit, reversible installation of the audited local capture adapter."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import stat
import tempfile
from zipfile import BadZipFile, ZipFile

from . import reader

PROFILE = "audited-2026-10-25"
# Capture support is explicit, independent of read-only save format support.
# Add a revision only after its runtime action and expression audit is complete.
FORMULA_EXPRESSIONS = {PROFILE: 5579, "audited-2026-10-25-r2": 5584}
WORK_FORMULA_EXPRESSIONS = {PROFILE: frozenset((5615, 5616, 5617, 5618)),
                            "audited-2026-10-25-r2": frozenset((5620, 5621, 5622, 5623))}
PROFILES = frozenset(FORMULA_EXPRESSIONS)
STATE_DIR = ".opendesk-capture"
ENTRY = "opendesk-capture/inject.js"
WORKER_ENTRY = "opendesk-capture-worker.js"
ASSETS = {"inject.js": ENTRY, "observer.js": "opendesk-capture/observer.js",
          "runtime-worker.js": WORKER_ENTRY}
ASSET_DIR = Path(__file__).resolve().parents[1] / "capture"
MAX_PACKAGE = 1024 * 1024 * 1024


class CaptureError(ValueError):
    pass


def safe_path(path: Path, *, missing: bool = False) -> Path:
    """Reject every symlink/junction component, including redirects within root."""
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")) or ".." in path.parts:
        raise CaptureError("采集仅支持本机游戏目录的绝对路径。")
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise CaptureError("采集所需文件不存在。") from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise CaptureError("采集路径不能包含符号链接或目录重定向。")
    return path


def root_path(value: str | Path) -> Path:
    root = safe_path(Path(value))
    if not root.is_dir():
        raise CaptureError("游戏目录无效。")
    for name in ("game.exe", "package.nw"):
        if not safe_path(root / name).is_file():
            raise CaptureError("请选择包含 game.exe 和 package.nw 的游戏目录。")
    return root.resolve()


def digest(path: Path) -> str:
    safe_path(path)
    before = path.stat()
    if before.st_size > MAX_PACKAGE:
        raise CaptureError("游戏包超出支持大小。")
    result = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise CaptureError("读取期间游戏文件改变，请退出游戏后重试。")
    return result.hexdigest()


def read_json(path: Path, limit: int = 65536) -> dict:
    safe_path(path)
    if path.stat().st_size > limit:
        raise CaptureError("采集数据超出大小限制。")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("too large")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite")))
        if not isinstance(value, dict):
            raise ValueError("not object")
        return value
    except (UnicodeError, ValueError, RecursionError) as error:
        raise CaptureError("采集数据不是完整的有效 JSON。") from error


def atomic_bytes(path: Path, raw: bytes) -> None:
    safe_path(path, missing=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".opendesk-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        safe_path(path, missing=True)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path: Path, value: dict) -> None:
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def atomic_copy(source: Path, target: Path, *, expected_current: str | None = None,
                expected_source: str | None = None) -> None:
    safe_path(source)
    safe_path(target, missing=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".opendesk-", suffix=".tmp", dir=target.parent)
    try:
        with source.open("rb") as source_stream, os.fdopen(descriptor, "wb") as target_stream:
            shutil.copyfileobj(source_stream, target_stream, 1024 * 1024)
            target_stream.flush()
            os.fsync(target_stream.fileno())
        if expected_source is not None and digest(Path(temporary)) != expected_source:
            raise CaptureError("复制期间原始备份改变，已拒绝恢复。")
        safe_path(target, missing=True)
        if expected_current is not None and digest(target) != expected_current:
            raise CaptureError("复制期间游戏包改变，已拒绝覆盖新文件。")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def game_running(root: Path) -> bool:
    """Inspect executable identity only; never start or stop any process."""
    if os.name != "nt":
        raise CaptureError("此采集安装器仅支持 Windows。")
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise CaptureError("无法核实游戏进程，已拒绝修改游戏包。")
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel.Process32FirstW(handle, ctypes.byref(entry))
        if not found and ctypes.get_last_error() != 18:
            raise CaptureError("无法读取进程列表，已拒绝修改游戏包。")
        while found:
            if entry.szExeFile.lower() == "game.exe":
                process = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
                if not process:
                    raise CaptureError("无法核实同名游戏进程的路径，请退出游戏后重试。")
                try:
                    buffer, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
                    if not kernel.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(length)):
                        raise CaptureError("无法核实游戏进程的路径，请退出游戏后重试。")
                    if os.path.normcase(buffer.value) == os.path.normcase(str(root / "game.exe")):
                        return True
                finally:
                    kernel.CloseHandle(process)
            found = kernel.Process32NextW(handle, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:
            raise CaptureError("进程列表读取未完成，已拒绝修改游戏包。")
        return False
    finally:
        kernel.CloseHandle(handle)


def _closed(root: Path) -> None:
    if game_running(root):
        raise CaptureError("游戏正在运行，请手动保存并退出游戏后再安装或移除采集器。")


def audit_package(path: Path) -> str:
    try:
        with ZipFile(path) as package:
            seen = set()
            total = 0
            for member in package.infolist():
                name = member.orig_filename
                normalized = name.rstrip("/").casefold()
                parts = PurePosixPath(name).parts
                if (not name or name != member.filename or "\\" in name or ":" in name or name.startswith("/")
                        or "//" in name or any(ord(c) < 32 for c in name)
                        or any(p in ("..", ".") for p in name.split("/"))
                        or normalized in seen or stat.S_ISLNK(member.external_attr >> 16)):
                    raise CaptureError("游戏 ZIP 含重复、越界或链接路径，已拒绝安装。")
                if parts and parts[0].casefold() in ("opendesk-capture", "capture", WORKER_ENTRY):
                    raise CaptureError("游戏包已有采集文件，不能覆盖现有注入。")
                seen.add(normalized)
                total += member.file_size
                if member.file_size > 64 * 1024 * 1024 or total > MAX_PACKAGE:
                    raise CaptureError("游戏 ZIP 解压大小超出支持范围。")
            identity = reader._json(package.read("package.json"), "游戏标识")
            if not isinstance(identity, dict) or any(k in identity for k in ("inject_js_start", "inject_js_end")):
                raise CaptureError("游戏包已有注入配置，不能覆盖。")
            profile, _ = reader._audited_package(package)
            if profile not in PROFILES:
                raise CaptureError("采集器只支持已核实的游戏运行规则版本（2026-10-25 起）。")
            return profile
    except (BadZipFile, KeyError, reader.ReaderError) as error:
        raise CaptureError(str(error)) from error


def installation(root: Path) -> dict | None:
    state = safe_path(root / STATE_DIR, missing=True)
    meta_path = safe_path(state / "meta.json", missing=True)
    if not meta_path.exists():
        if state.exists() and any(state.iterdir()):
            entries = list(state.iterdir())
            for entry in entries:
                safe_path(entry)
            # A failure before metadata was committed leaves only our verified
            # original and possibly the config. Retrying cannot overwrite a
            # changed package because both bytes must still match.
            backup = state / "original-package.nw"
            if ({entry.name for entry in entries} <= {"original-package.nw", "config.json"}
                    and backup.is_file() and digest(backup) == digest(root / "package.nw")):
                return {"schemaVersion": 1, "ruleProfile": audit_package(root / "package.nw"),
                        "originalSha256": digest(backup), "installedSha256": "0" * 64, "installed": False}
            raise CaptureError("采集目录存在未知或未完成的数据，请先核查备份；未修改游戏包。")
        return None
    meta = read_json(meta_path)
    if (set(meta) != {"schemaVersion", "ruleProfile", "originalSha256", "installedSha256"}
            or type(meta["schemaVersion"]) is not int or meta["schemaVersion"] != 1
            or not isinstance(meta["ruleProfile"], str) or meta["ruleProfile"] not in PROFILES
            or any(not isinstance(meta[k], str) or len(meta[k]) != 64
                   or any(c not in "0123456789abcdef" for c in meta[k]) for k in ("originalSha256", "installedSha256"))):
        raise CaptureError("采集备份元数据不受支持，不能修改游戏包。")
    for name in ("original-package.nw", "config.json", "stream.json"):
        safe_path(state / name, missing=True)
    current = digest(root / "package.nw")
    if current not in (meta["originalSha256"], meta["installedSha256"]):
        raise CaptureError("游戏包已更新或被修改；不会用旧备份覆盖，请先核查当前游戏版本。")
    return {**meta, "installed": current == meta["installedSha256"]}


def _assets() -> dict[str, bytes]:
    assets = {}
    try:
        for name, destination in ASSETS.items():
            with (ASSET_DIR / name).open("rb") as stream:
                assets[destination] = stream.read(1024 * 1024 + 1)
    except OSError as error:
        raise CaptureError("采集素材缺失或无法读取，请重新安装塔防页签。") from error
    if any(not raw or len(raw) > 1024 * 1024 for raw in assets.values()):
        raise CaptureError("采集素材缺失或大小异常，请重新安装塔防页签。")
    return assets


def needs_update(root: Path) -> bool:
    """Compare the installed adapter to this module without changing the game."""
    expected = _assets()
    try:
        with ZipFile(root / "package.nw") as package:
            for name, raw in expected.items():
                try:
                    info = package.getinfo(name)
                except KeyError:
                    return True
                if info.file_size != len(raw) or package.read(info) != raw:
                    return True
    except BadZipFile as error:
        raise CaptureError("无法核对游戏中的采集脚本，请检查游戏包。") from error
    return False


def install(root: Path) -> None:
    root = root_path(root)
    _closed(root)
    existing = installation(root)
    if existing and existing["installed"]:
        raise CaptureError("采集器已经安装；更新采集脚本前，请先移除采集器恢复原包，再重新安装。")
    profile = audit_package(root / "package.nw")
    assets = _assets()
    original = digest(root / "package.nw")
    state = safe_path(root / STATE_DIR, missing=True)
    state.mkdir(exist_ok=True)
    backup = state / "original-package.nw"
    if existing:
        if digest(backup) != original:
            raise CaptureError("原始游戏包备份摘要不符，已拒绝覆盖。")
    else:
        atomic_copy(root / "package.nw", backup, expected_source=original)
        if digest(backup) != original:
            raise CaptureError("备份校验失败，未修改游戏包。")
    descriptor, temp_name = tempfile.mkstemp(prefix=".opendesk-package-", suffix=".nw", dir=state)
    os.close(descriptor)
    temporary = Path(temp_name)
    installed_digest = None
    try:
        with ZipFile(backup) as source, ZipFile(temporary, "w") as target:
            for info in source.infolist():
                raw = source.read(info)
                if info.filename == "package.json":
                    manifest = reader._json(raw, "游戏标识")
                    manifest["inject_js_start"] = ENTRY
                    raw = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
                target.writestr(info, raw)
            for name, raw in assets.items():
                target.writestr(name, raw)
        installed_digest = digest(temporary)
        atomic_json(state / "config.json", {"schemaVersion": 1, "token": secrets.token_hex(32), "ruleProfile": profile})
        atomic_json(state / "meta.json", {"schemaVersion": 1, "ruleProfile": profile,
                    "originalSha256": original, "installedSha256": installed_digest})
        stream = safe_path(state / "stream.json", missing=True)
        if stream.exists():
            stream.unlink()
        _closed(root)
        if digest(backup) != original:
            raise CaptureError("安装期间原始备份改变，已停止安装。")
        if digest(root / "package.nw") != original:
            raise CaptureError("安装期间游戏包改变，已停止；不会覆盖新文件。")
        safe_path(root / "package.nw")
        os.replace(temporary, root / "package.nw")
    except Exception:
        # The original remains recoverable even when a filesystem step fails.
        if installed_digest and digest(root / "package.nw") == installed_digest:
            atomic_copy(backup, root / "package.nw", expected_current=installed_digest, expected_source=original)
        raise
    finally:
        if temporary.exists():
            temporary.unlink()


def remove(root: Path) -> None:
    root = root_path(root)
    _closed(root)
    meta = installation(root)
    if not meta or not meta["installed"]:
        raise CaptureError("采集器尚未安装。")
    backup = root / STATE_DIR / "original-package.nw"
    if digest(backup) != meta["originalSha256"]:
        raise CaptureError("原始游戏包备份已改变，拒绝恢复。")
    _closed(root)
    if digest(root / "package.nw") != meta["installedSha256"]:
        raise CaptureError("游戏包已改变，拒绝覆盖。")
    atomic_copy(backup, root / "package.nw", expected_current=meta["installedSha256"],
                expected_source=meta["originalSha256"])
    for name in ("config.json", "stream.json"):
        path = safe_path(root / STATE_DIR / name, missing=True)
        if path.exists():
            path.unlink()
