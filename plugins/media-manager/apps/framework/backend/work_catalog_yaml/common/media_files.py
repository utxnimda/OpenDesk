"""Read-only, bounded media discovery and guarded local-player integration.

The caller authorizes the release root. This module never resolves away links,
writes media or installs tools. Episode directories are considered only inside
the caller-selected playback resource and never outside that boundary.
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
import math
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import subprocess

from work_catalog_yaml.storage.disk_inventory import lexical_path
from work_catalog_yaml.storage.filesystem import ordinary_directory


VIDEO_EXTENSIONS = frozenset({
    ".mkv", ".mp4", ".m4v", ".avi", ".m2ts", ".mts", ".ts", ".wmv",
    ".mov", ".mpg", ".mpeg", ".vob", ".webm", ".ogv",
})
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_INVALID = re.compile(r'[<>:"|?*\x00-\x1f\x7f-\x9f]')
_RESERVED = re.compile(r"(?i)^(?:con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)")
_DISC = re.compile(r"(?i)(?:disc|_disc|.+_disc)\Z")
_BONUS = re.compile(r"(?i)(?:^|[^a-z0-9])(?:ncop|nced|op|ed|cm|pv|sp|trailer|preview|teaser|logo|interview|bonus)\d*(?![a-z])")
_NON_FEATURE = re.compile(r"(?i)(?:^|[^a-z0-9])(?:others?|images?|scans?|covers?|fonts?|subtitles?|subs?|audio|music|booklets?|soundtracks?|cd|extras?|samples?|menus?)(?:$|[^a-z0-9])|字幕|音轨|音軌|特典")
_SEASON = re.compile(r"(?i)(?<![a-z0-9])s(\d{1,2})[ ._-]*e(\d{1,5})(?:v\d+)?(?![a-z0-9])")
_EPISODE = re.compile(r"(?i)(?<![a-z0-9])(?:episode|ep|e|live)[ ._-]*(\d{1,5})(?:v\d+)?(?![a-z0-9])")
_CHINESE_EPISODE = re.compile(r"第\s*(\d{1,5})\s*[集話话]")
_NUMBER_TAG = re.compile(r"[\[【](\d{1,5})(?:[vV]\d+)?(?:\s*\([^()\d]*\))?[\]】]")
_DASH_EPISODE = re.compile(r"(?i)(?:^|\s)-\s*(\d{1,5})(?:v\d+)?(?=\s*(?:\[|【|\(|$))")
_BARE_EPISODE = re.compile(r"(?i)^(\d{1,5})(?:v\d+)?$")
_SUFFIX_EPISODE = re.compile(r"(?i)[ _-](\d{1,5})(?:v\d+)?(?=[ _.-]*(?:\[|【|\(|$))")
_PREFIX_EPISODE = re.compile(r"(?i)^(0\d{1,4}|\d{3,5})(?:v\d+)?(?:[ \t]+(?=\S)|(?=[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]))")
_NUMBER_RANGE = re.compile(r"(?i)\d+(?:v\d+)?\s*(?:[-~–+&]|to)\s*(?:episode|ep|e)?\s*\d+")
_METADATA_NUMBERS = {480, 576, 720, 1080, 1440, 2160, 4320}


def _excluded_name(name: str) -> bool:
    return bool(_BONUS.search(name) or _NON_FEATURE.search(name))


def _episode_details(stem: str) -> tuple[str, bool]:
    """Parse a complete stem; the boolean forbids ambiguous-name fallback."""
    if _excluded_name(stem) or _NUMBER_RANGE.search(stem):
        return "", True
    seasons = {(int(match[1]), int(match[2])) for match in _SEASON.finditer(stem)}
    explicit = {int(match[1]) for pattern in (_EPISODE, _CHINESE_EPISODE)
                for match in pattern.finditer(stem)}
    numeric = {int(match[1]) for pattern in (_NUMBER_TAG, _DASH_EPISODE, _BARE_EPISODE, _SUFFIX_EPISODE, _PREFIX_EPISODE)
               for match in pattern.finditer(stem)}
    numeric = {number for number in numeric if not 1900 <= number <= 2099 and number not in _METADATA_NUMBERS}
    # Check every marker family before selecting a result. A season marker or
    # EP prefix must not hide a conflicting explicit numeric episode tag.
    candidates = explicit | numeric | {episode for _, episode in seasons}
    if len(seasons) > 1 or len(candidates) > 1:
        return "", True
    if not candidates:
        return "", False
    if seasons:
        season, episode = next(iter(seasons))
        return f"S{season:02d}E{episode:02d}", False
    return f"{next(iter(candidates)):02d}", False


def media_episode_key(relative_path: str) -> str:
    """Return e.g. ``01`` or ``S02E03`` only for unambiguous filename markers."""
    if not isinstance(relative_path, str):
        return ""
    name = relative_path.replace("\\", "/").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return _episode_details(stem)[0]


def _episodes_agree(first: str, second: str) -> bool:
    if first.startswith("S") and second.startswith("S"):
        return first == second
    return int(first.rsplit("E", 1)[-1]) == int(second.rsplit("E", 1)[-1])


def _episode_group(relative: str) -> str:
    """Identify only the direct episode folder under the logical Disc root."""
    directories = relative.split("/")[:-1]
    offset = int(bool(directories and _DISC.fullmatch(directories[0])))
    return "/".join(directories[:offset + 1]) if len(directories) > offset else ""


def _file_episode(relative: str, *, unique: bool) -> str:
    parts = relative.split("/")
    episode, ambiguous = _episode_details(Path(parts[-1]).stem)
    # Directory names may contain dots; they have no extension to strip.
    group = _episode_group(relative)
    directory_episode = _episode_details(group.rsplit("/", 1)[-1])[0] if group else ""
    if ambiguous or (episode and directory_episode and not _episodes_agree(episode, directory_episode)):
        return ""
    return episode or (directory_episode if unique else "")


def _relative_media_path(raw: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValueError("视频相对路径不能为空")
    value = raw.replace("\\", "/")
    parts = value.split("/")
    if PureWindowsPath(value).drive or value.startswith("/") or any(
        part in {"", ".", ".."} or part[-1:] in {" ", "."}
        or _INVALID.search(part) or _RESERVED.match(part) for part in parts
    ):
        raise ValueError("视频路径包含不安全的 Windows 路径组件")
    if Path(parts[-1]).suffix.casefold() not in VIDEO_EXTENSIONS:
        raise ValueError("仅允许打开普通视频文件")
    directories = parts[:-1]
    if directories and _DISC.fullmatch(directories[0]):
        directories = directories[1:]
    if any(_excluded_name(name) or _DISC.fullmatch(name) for name in directories) or _excluded_name(Path(parts[-1]).stem):
        raise ValueError("视频必须位于播放目录或其单集子目录，不能位于附加资源分类")
    return value


def _ordinary_file(metadata: os.stat_result) -> bool:
    return bool(stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)
                and not getattr(metadata, "st_file_attributes", 0) & _REPARSE)


def _verified_root(root: str | Path) -> Path:
    path = lexical_path(root)
    if not ordinary_directory(str(path)):
        raise ValueError("资源目录不存在或包含符号链接 / 联接")
    return path


def safe_media_path(root: str | Path, relative_path: str) -> Path:
    """Validate scope and the original ancestor chain before any file access."""
    relative = _relative_media_path(relative_path)
    base = _verified_root(root)
    # A complete bounded root listing is required before falling back to root
    # videos. This prevents stale/manual paths from bypassing Disc precedence.
    disc_names = set()
    with os.scandir(base) as iterator:
        for index, entry in enumerate(iterator):
            if index >= 20000:
                raise ValueError("资源目录条目过多，无法安全确认 Disc 范围")
            if _DISC.fullmatch(entry.name):
                metadata = (base / entry.name).lstat()
                if stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                    disc_names.add(entry.name)
    return _validated_media_path(base, relative, disc_names)


def _validated_media_path(base: Path, relative: str, disc_names: set[str]) -> Path:
    relative = _relative_media_path(relative)
    if disc_names and os.path.normcase(relative.split("/", 1)[0]) not in {os.path.normcase(name) for name in disc_names}:
        raise ValueError("存在直属 Disc 分类时只能打开该分类内的视频")
    candidate = base.joinpath(*relative.split("/"))
    candidate.relative_to(base)
    if not ordinary_directory(str(candidate.parent)):
        raise ValueError("视频目录不存在或包含符号链接 / 联接")
    if not _ordinary_file(candidate.lstat()):
        raise ValueError("视频不是普通文件（不支持符号链接 / 联接）")
    return candidate


def scan_media_files(root: str | Path, *, max_files: int = 2000,
                     max_entries: int = 20000, max_depth: int = 8) -> dict:
    """List direct Disc trees, or the resource itself when no Disc exists.

    Limits include non-media entries. Unreadable/changed subtrees are reported,
    never silently treated as a complete inventory. Durations are lazy probes.
    """
    for name, value, maximum in (("max_files", max_files, 20000),
                                  ("max_entries", max_entries, 100000),
                                  ("max_depth", max_depth, 32)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"{name} 超出允许范围")
    base = _verified_root(root)
    files: list[dict] = []
    warnings: list[str] = []
    pending = [(base, 0)]
    examined = 0
    truncated = False
    disc_names: set[str] = set()
    while pending:
        directory, depth = pending.pop()
        if not ordinary_directory(str(directory)):
            warnings.append(f"跳过不可读或链接目录：{directory.relative_to(base).as_posix()}")
            continue
        entries = []
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    if examined >= max_entries:
                        truncated = True
                        break
                    examined += 1
                    entries.append(entry)
        except OSError:
            warnings.append(f"目录读取失败：{directory.relative_to(base).as_posix()}")
            continue
        if depth == 0:
            # A partial listing cannot establish that there is no later Disc.
            if truncated:
                break
            for entry in entries:
                if _DISC.fullmatch(entry.name):
                    try:
                        metadata = (base / entry.name).lstat()
                        if stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                            disc_names.add(entry.name)
                    except OSError:
                        warnings.append(f"Disc 范围读取失败：{entry.name}")
                        return {"files": [], "truncated": truncated, "warnings": warnings}
        for entry in sorted(entries, key=lambda item: item.name.casefold()):
            path = directory / entry.name
            relative = path.relative_to(base).as_posix()
            try:
                metadata = path.lstat()
                if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & _REPARSE:
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    if ((depth == 0 and disc_names and entry.name not in disc_names)
                            or (_excluded_name(entry.name) and not (depth == 0 and entry.name in disc_names))
                            or (depth > 0 and _DISC.fullmatch(entry.name))):
                        continue
                    if depth >= max_depth:
                        truncated = True
                    else:
                        pending.append((path, depth + 1))
                    continue
                if not _ordinary_file(metadata) or path.suffix.casefold() not in VIDEO_EXTENSIONS:
                    continue
                if (depth == 0 and disc_names) or _excluded_name(path.stem):
                    continue
                _validated_media_path(base, relative, disc_names)
                if len(files) >= max_files:
                    truncated = True
                    pending.clear()
                    break
                record = {"relative_path": relative, "size": metadata.st_size,
                              "mtime_ns": metadata.st_mtime_ns, "signature": list(_signature(metadata)),
                              "episode": _file_episode(relative, unique=False)}
                files.append(record)
            except (OSError, ValueError):
                warnings.append(f"跳过已变化或不可读的视频路径：{relative}")
        if examined >= max_entries or len(files) >= max_files:
            truncated = truncated or bool(pending)
            break
    # Count the entire direct episode subtree, including nested version folders.
    # Incomplete inventories must never make multiple videos look unique.
    if not truncated and not warnings:
        groups = Counter(_episode_group(record["relative_path"]) for record in files)
        for record in files:
            group = _episode_group(record["relative_path"])
            record["episode"] = _file_episode(record["relative_path"], unique=bool(group) and groups[group] == 1)
    files.sort(key=lambda item: item["relative_path"].casefold())
    return {"files": files, "truncated": truncated, "warnings": warnings}


def open_media_file(root: str | Path, relative_path: str) -> Path:
    """Revalidate then ask Windows to open a video with its default player."""
    path = safe_media_path(root, relative_path)
    launcher = getattr(os, "startfile", None)
    if launcher is None:
        raise OSError("当前平台不支持系统默认播放器")
    path = safe_media_path(root, relative_path)
    launcher(str(path), "open")
    return path


def _signature(metadata: os.stat_result) -> tuple:
    return (metadata.st_dev, metadata.st_ino, metadata.st_ctime_ns, metadata.st_size, metadata.st_mtime_ns)


def duration_available() -> bool:
    """Report the local ffprobe executable without reading any media."""
    return bool(shutil.which("ffprobe"))


class _DurationUnavailable(Exception):
    """Failed probes must bypass the successful-duration LRU cache."""


@lru_cache(maxsize=512)
def _cached_duration(path: str, signature: tuple, executable: str, timeout: float) -> float:
    try:
        result = subprocess.run(
            [executable, "-v", "error", "-protocol_whitelist", "file,pipe", "-show_entries",
             "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", "-i", path],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise _DurationUnavailable
        duration = float(result.stdout.strip())
        current = Path(path).lstat()
        if not _ordinary_file(current) or _signature(current) != signature:
            raise _DurationUnavailable
        if not math.isfinite(duration) or duration <= 0:
            raise _DurationUnavailable
        return duration
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise _DurationUnavailable from error


def probe_media_duration(path: str | Path, *, timeout: float = 3.0) -> float | None:
    """Best-effort local ffprobe duration; missing tools/metadata return None."""
    try:
        timeout = float(timeout)
        if not math.isfinite(timeout) or not 0 < timeout <= 10:
            return None
        candidate = lexical_path(path)
        if candidate.suffix.casefold() not in VIDEO_EXTENSIONS or not ordinary_directory(str(candidate.parent)):
            return None
        metadata = candidate.lstat()
        if not _ordinary_file(metadata):
            return None
        executable = shutil.which("ffprobe")
        if not executable:
            return None
        return _cached_duration(str(candidate), _signature(metadata), executable, timeout)
    except (OSError, ValueError, TypeError, _DurationUnavailable):
        return None
