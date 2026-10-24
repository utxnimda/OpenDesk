"""Receipts for shortcut ownership and safe retirement after a replacement exists.

This is persistent operational state, not the rebuildable index cache. Only
shortcuts created by this application and their confirmed replacement chain are
owned; unrelated historical/user links are never adopted by target similarity.
"""
from __future__ import annotations

import hashlib
import errno
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable

from work_catalog_yaml.operation_progress import report_exception, report_progress
from work_catalog_yaml.persistence import atomic_write_bytes
from work_catalog_yaml.storage.filesystem import ordinary_directory
from work_catalog_yaml.yaml_io import dump_yaml_string, load_yaml_string


TargetReader = Callable[[list[Path]], dict[str, Any]]
_RECEIPT_FIELDS = ("work_key", "stable_work_id", "press_key", "name", "yaml_source_rel", "index_in_file",
                   "shortcut_root", "shortcut_path", "target_path")
MAX_RECEIPT_BYTES = 32 * 1024 * 1024
MAX_SHORTCUT_BYTES = 1024 * 1024


def _regular_file(metadata: Any) -> bool:
    return bool(stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode) and not (
        getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)))


def _receipt_path_exists(path: Path) -> bool:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"生成凭据必须使用安全绝对路径：{path}")
    for parent in reversed(path.parents):
        try:
            metadata = parent.lstat()
        except FileNotFoundError:
            return False
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or (
            getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise ValueError(f"生成凭据父目录包含非普通目录、联接或符号链接：{parent}")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if not _regular_file(metadata):
        raise ValueError(f"生成凭据不是普通文件或包含联接/符号链接：{path}")
    return True


def _bounded_bytes(path: Path, limit: int) -> bytes:
    metadata = path.lstat()
    if not _regular_file(metadata):
        raise ValueError(f"读取目标不是普通文件或包含联接/符号链接：{path}")
    if metadata.st_size > limit:
        raise ValueError(f"文件超过安全读取上限 {limit} 字节：{path}")
    with path.open("rb") as stream:
        current = os.fstat(stream.fileno())
        if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError(f"文件在读取期间已被替换：{path}")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"文件超过安全读取上限 {limit} 字节：{path}")
    return raw


def shortcut_sha256(path: Path) -> str:
    if not ordinary_directory(str(path.parent)):
        raise ValueError(f"快捷方式父目录包含联接/符号链接或不是普通目录：{path.parent}")
    return hashlib.sha256(_bounded_bytes(path, MAX_SHORTCUT_BYTES)).hexdigest()


def path_key(raw: Any) -> str:
    return os.path.normcase(os.path.abspath(str(raw))) if raw else ""


_key = path_key


def _issue(item: dict, code: str, reason: str) -> dict:
    return {**{key: item.get(key, "") for key in (
        "work_key", "stable_work_id", "press_key", "name", "yaml_source_rel", "index_in_file",
        "shortcut_path", "target_path", "replacement_path")},
        "code": code, "level": "warning", "blocking": False,
        "stage": "退役旧快捷方式", "action": "核对生成凭据与实际快捷方式",
        "message": reason, "reason": reason}


def load_receipts(path: Path, catalog_root: str) -> tuple[dict, str]:
    if not _receipt_path_exists(path):
        return {"version": 1, "catalog_root": catalog_root, "items": []}, ""
    raw = _bounded_bytes(path, MAX_RECEIPT_BYTES)
    data = load_yaml_string(raw.decode("utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("items"), list):
        raise ValueError(f"快捷方式生成凭据格式错误，未删除任何快捷方式：{path}")
    if data.get("catalog_root") != catalog_root:
        raise ValueError(f"快捷方式生成凭据属于其他作品数据库，未删除任何快捷方式：{path}")
    if len(data["items"]) > 100000 or any(not isinstance(item, dict) for item in data["items"]):
        raise ValueError(f"快捷方式生成凭据条目错误，未删除任何快捷方式：{path}")
    for item in data["items"]:
        required = ("shortcut_root", "shortcut_path", "target_path", "work_key", "press_key", "file_sha256")
        if any(not isinstance(item.get(key), str) or not item[key] for key in required):
            raise ValueError(f"快捷方式生成凭据缺少有效身份或路径，未删除任何快捷方式：{path}")
        if re.fullmatch(r"[0-9a-f]{64}", item["file_sha256"]) is None:
            raise ValueError(f"快捷方式生成凭据指纹无效，未删除任何快捷方式：{path}")
        stable_id = item.get("stable_work_id", "")
        if not isinstance(stable_id, str) or (stable_id and re.fullmatch(r"work_[0-9a-f]{32}", stable_id) is None):
            raise ValueError(f"快捷方式生成凭据稳定 ID 无效，未删除任何快捷方式：{path}")
    return data, hashlib.sha256(raw).hexdigest()


def _safe_file(item: dict, roots: list[Path]) -> Path:
    root = Path(str(item.get("shortcut_root") or ""))
    path = Path(str(item.get("shortcut_path") or ""))
    if not root.is_absolute() or not path.is_absolute() or ".." in path.parts:
        raise ValueError("旧快捷方式路径不是有效的绝对路径")
    if _key(root) not in {_key(value) for value in roots}:
        raise ValueError("旧快捷方式输出根目录已不在当前配置中")
    relative = path.relative_to(root)
    if not relative.parts or path.suffix.casefold() != ".lnk":
        raise ValueError("受管路径不是输出根目录内的 .lnk 文件")
    if not ordinary_directory(str(path.parent)):
        raise ValueError("旧快捷方式父目录不存在或包含联接/符号链接")
    metadata = path.lstat()
    if not _regular_file(metadata):
        raise ValueError("旧快捷方式不是普通文件或包含联接/符号链接")
    if metadata.st_size > MAX_SHORTCUT_BYTES:
        raise ValueError("旧快捷方式超过安全大小限制，已保留")
    return path


def _verify(item: dict, roots: list[Path], targets: dict) -> Path:
    path = _safe_file(item, roots)
    if shortcut_sha256(path) != item.get("file_sha256"):
        raise ValueError("旧快捷方式内容已被修改，与原生成凭据不同，已保留")
    info = targets.get(str(path.resolve())) or {}
    if _key(info.get("target_path")) != _key(item.get("target_path")):
        raise ValueError("旧快捷方式实际目标与生成凭据不同或无法读取，已保留")
    return path


def preview_retirements(receipts: dict, items: list[dict], *, protected_paths: list[str],
                        roots: list[Path], read_targets: TargetReader) -> tuple[list[dict], list[dict]]:
    protected = {_key(path) for path in protected_paths}
    by_identity: dict[tuple, list[dict]] = {}
    for item in items:
        if item.get("stable_work_id"):
            by_identity.setdefault((item["stable_work_id"], item.get("press_key")), []).append(item)
    candidates, issues = [], []
    for old in receipts["items"]:
        if _key(old.get("shortcut_path")) in protected:
            continue  # Even an unselected/shared DB record still needs this link.
        if not old.get("stable_work_id"):
            issues.append(_issue(old, "shortcut-retirement-identity-missing", "旧生成凭据没有作品稳定 ID，无法安全确认关联，旧快捷方式已保留"))
            continue
        matches = [item for item in by_identity.get((old.get("stable_work_id"), old.get("press_key")), [])
                   if item.get("target_exists") and _key(item.get("target_path")) == _key(old.get("target_path"))]
        if len(matches) != 1:
            continue  # Removed/reordered/rebound records are not safe retirement evidence.
        candidate = {**old, "replacement_path": matches[0]["shortcut_path"],
                     "replacement": {key: matches[0].get(key, "") for key in _RECEIPT_FIELDS}}
        try:
            path = _safe_file(candidate, roots)
            candidate["observed_sha256"] = shortcut_sha256(path)
            candidates.append(candidate)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            issues.append(_issue(candidate, "shortcut-retirement-unsafe", str(exc)))
    targets = read_targets([Path(item["shortcut_path"]) for item in candidates])
    ready = []
    for item in candidates:
        try:
            _verify(item, roots, targets)
            ready.append(item)
        except (OSError, ValueError) as exc:
            issues.append(_issue(item, "shortcut-retirement-conflict", str(exc)))
    return ready, issues


def finish_generation(receipt_path: Path, receipts: dict, *, created_items: list[dict],
                      retirements: list[dict], available_paths: set[str], roots: list[Path],
                      read_targets: TargetReader, force_receipt_save: bool = False) -> dict:
    """Persist newly created ownership first; retire only verified replacements."""
    result = {"removed_count": 0, "removed_directory_count": 0, "retired": [], "issues": []}
    entries = {_key(item.get("shortcut_path")): item for item in receipts["items"]}
    targets = read_targets([Path(item["shortcut_path"]) for item in created_items])
    for item in created_items:
        try:
            path = _safe_file(item, roots)
            row = {key: item.get(key, "") for key in _RECEIPT_FIELDS}
            row["file_sha256"] = shortcut_sha256(path)
            _verify(row, roots, targets)
            entries[_key(path)] = row
        except (OSError, ValueError) as exc:
            result["issues"].append(_issue(item, "shortcut-receipt-failed", str(exc)))
            report_exception(exc, context=result["issues"][-1])

    def persist() -> None:
        payload = {**receipts, "items": sorted(entries.values(), key=lambda item: _key(item.get("shortcut_path")))}
        raw = dump_yaml_string(payload).encode("utf-8")
        if len(raw) > MAX_RECEIPT_BYTES:
            raise ValueError("快捷方式生成凭据超过安全大小限制，旧快捷方式已保留")
        _receipt_path_exists(receipt_path)
        atomic_write_bytes(receipt_path, raw)

    try:
        if created_items or retirements or force_receipt_save:
            persist()
    except (OSError, ValueError) as exc:
        issue = _issue({"shortcut_path": str(receipt_path)}, "shortcut-receipt-save-failed",
                       f"新快捷方式已生成，但生成凭据保存失败；未清理任何旧快捷方式：{exc}")
        result["issues"].append(issue)
        report_exception(exc, context=issue)
        return result

    # ShellLink parsing starts a helper process, so batch the final target read.
    # Fingerprints are still rechecked per file immediately before retirement.
    retirement_paths: list[Path] = []
    replacement_hashes: dict[str, str] = {}
    for item in retirements:
        replacement_path = Path(item["replacement_path"])
        if _key(replacement_path) not in available_paths:
            continue
        try:
            replacement_hashes[_key(replacement_path)] = shortcut_sha256(replacement_path)
            _safe_file(item, roots)
        except (OSError, ValueError):
            continue  # The per-item pass below records the detailed failure.
        retirement_paths.extend((Path(item["shortcut_path"]), replacement_path))
    retirement_targets = read_targets(list(dict.fromkeys(retirement_paths)))
    for item in retirements:
        if _key(item.get("replacement_path")) not in available_paths:
            issue = _issue(item, "shortcut-retirement-replacement-missing", "新快捷方式未成功生成，旧快捷方式已保留")
            result["issues"].append(issue)
            report_progress(issue["message"], context=issue)
            continue
        try:
            # Recheck both files immediately before deleting: preflight alone
            # cannot protect against modifications made during creation.
            replacement = {**item, "shortcut_path": item["replacement_path"]}
            replacement["shortcut_root"] = next(str(root) for root in roots
                if Path(item["replacement_path"]).is_relative_to(root))
            replacement_path = _safe_file(replacement, roots)
            replacement_hash = shortcut_sha256(replacement_path)
            if replacement_hash != replacement_hashes.get(_key(replacement_path)):
                raise ValueError("新快捷方式在复核期间发生变化；旧快捷方式已保留")
            info = retirement_targets.get(str(replacement_path.resolve())) or {}
            if _key(info.get("target_path")) != _key(item.get("target_path")):
                raise ValueError("新快捷方式目标复核失败；旧快捷方式已保留")
            path = _verify(item, roots, retirement_targets)
            # A retry may find the replacement from an earlier successful
            # creation whose receipt save failed. Retain this exact, confirmed
            # ownership chain before removing its predecessor; never infer
            # ownership from unrelated links which only share a target.
            successor = {**item["replacement"], "file_sha256": replacement_hash}
            if entries.get(_key(replacement_path)) != successor:
                entries[_key(replacement_path)] = successor
                persist()
            _verify(successor, roots, retirement_targets)
            path = _verify(item, roots, retirement_targets)
            path.unlink()
            entries.pop(_key(path), None)
            result["removed_count"] += 1
            result["retired"].append(str(path))
            report_progress("已移除由新快捷方式替代的旧快捷方式", context=_issue(item, "shortcut-retired", "新路径已确认，旧受管快捷方式已移除"))
            root, parent = Path(item["shortcut_root"]), path.parent
            while parent != root and parent.is_relative_to(root):
                if not ordinary_directory(str(parent)):
                    break
                try:
                    parent.rmdir()  # Empty only: never recurse or remove user content.
                except FileNotFoundError:
                    break
                except OSError as exc:
                    if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST} and getattr(exc, "winerror", None) != 145:
                        issue = _issue({**item, "shortcut_path": str(parent)}, "shortcut-empty-directory-cleanup-failed",
                                       f"旧快捷方式已移除，但清理空目录失败，目录已保留：{exc}")
                        result["issues"].append(issue)
                        report_exception(exc, context=issue)
                    break
                result["removed_directory_count"] += 1
                parent = parent.parent
        except (OSError, ValueError, StopIteration) as exc:
            issue = _issue(item, "shortcut-retirement-failed", str(exc))
            result["issues"].append(issue)
            report_exception(exc, context=issue)
    if result["removed_count"]:
        try:
            persist()
        except (OSError, ValueError) as exc:
            issue = _issue({"shortcut_path": str(receipt_path)}, "shortcut-receipt-save-failed", str(exc))
            result["issues"].append(issue)
            report_exception(exc, context=issue)
    return result
