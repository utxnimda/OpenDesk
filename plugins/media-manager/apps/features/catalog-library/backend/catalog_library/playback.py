"""Local chapter bindings: catalog authority, bounded media reads, explicit playback.

Provider episodes and local file assignments have separate owners. Confirmed
assignments live in the work record and use the same catalog writer as editing.
No operation here renames, moves, or changes a media file.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import ntpath
import re
import time

from collection_detail.catalog_edit_service import resolve_record_directory
from work_catalog_yaml.common import media_files
from work_catalog_yaml.operation_progress import report_progress
from work_catalog_yaml.storage.filesystem import ordinary_directory
from . import service
from .chapter_numbers import NUMBERING_MODES, chapter_numbers, normalize_number
from .model import validate_extensions


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _number(value) -> str:
    return normalize_number(value)


def _chapter_rows(chapters: list) -> list[dict]:
    result = []
    for index, chapter in enumerate(chapters):
        numbers = chapter_numbers(chapter, index)
        number = numbers["local_match_number"]
        kind = chapter.get("type", 0)
        valid_kind = type(kind) is int and 0 <= kind <= 99
        identity = chapter.get("id")
        identified = isinstance(identity, (str, int)) and not isinstance(identity, bool) and str(identity).strip() and len(str(identity)) <= 160 and not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in str(identity))
        if identified:
            key = "id:" + str(identity)
        elif number and valid_kind:
            key = "number:" + str(kind) + ":" + number
        else:
            key = "row:" + str(index) + ":" + _hash(chapter)[:24]
        source_seconds = chapter.get("duration_seconds")
        if type(source_seconds) not in {int, float} or (type(source_seconds) is float and not math.isfinite(source_seconds)) or source_seconds <= 0:
            source_seconds = None
        result.append({"chapter_key": key, "number": numbers["local_number"],
                       "continuous_number": numbers["continuous_number"],
                       "has_continuous_number": numbers["has_continuous_number"],
                       "binding_allowed": bool(identified or number and valid_kind),
                       "title": str(chapter.get("name") or chapter.get("title") or chapter.get("name_cn") or "未命名章节"),
                       "name_cn": str(chapter.get("name_cn") or ""),
                       "source_duration_seconds": source_seconds,
                       "source_duration": str(chapter.get("duration") or ""),
                       "air_date": str(chapter.get("air_date") or chapter.get("date") or chapter.get("airdate") or ""),
                       "episode": number if valid_kind and kind == 0 else "",
                       "continuous_episode": numbers["continuous_match_number"]})
    counts = Counter(row["chapter_key"] for row in result)
    episode_counts = {field: Counter(row[field] for row in result if row[field])
                      for field in ("episode", "continuous_episode")}
    for index, row in enumerate(result):
        if counts[row["chapter_key"]] > 1:
            row["chapter_key"] = "row:" + str(index) + ":" + _hash(chapters[index])[:24]
            row["episode"] = ""
            row["continuous_episode"] = ""
            row["binding_allowed"] = False
        for field in episode_counts:
            if row[field] and episode_counts[field][row[field]] > 1:
                row[field] = ""
    return result


def resource_identity(resource):
    return {key: resource.get(key) for key in ("press_path", "press_format", "press_group", "segment", "continuation_index")}


def _path_key(path):
    return ntpath.normpath(str(path)).replace("\\", "/").casefold()


def _revision(scan):
    # Recognition rules may change without changing any media bytes. An old
    # cached episode assignment must still require an explicit refresh.
    return _hash({"root": _path_key(scan["root_path"]), "root_identity": scan["root_identity"],
                  "files": sorted(([row["relative_path"], row["signature"], row["episode"]]
                                   for row in scan["files"]), key=lambda row: row[0])})


def _entry(record, key):
    return record.get("local_playback", {}).get("resources", {}).get(key, {})


def _continuous_available(chapters):
    return any(row["has_continuous_number"] and row["continuous_episode"] and row["binding_allowed"]
               for row in chapters)


def _mapping_revision(stored, mode, chapters):
    # File inventory alone does not identify a match: the same files can be
    # mapped to episode 1 or 14. Include only authoritative chapter identities
    # and matching numbers, not display text or duration probes.
    return _hash({"inventory": _revision(stored), "numbering_mode": mode,
                  "chapters": [{field: row[field] for field in
                                ("chapter_key", "episode", "continuous_episode", "binding_allowed")}
                               for row in chapters]})


def _projection(detail, index, key, stored, warnings=()):
    files = deepcopy(stored["files"])
    by_path = {row["relative_path"]: row for row in files}
    entry = _entry(detail["record"], key)
    assignments = entry.get("assignments", {})
    mode = entry.get("numbering_mode", "local")
    chapters = _chapter_rows(detail["chapters"])
    revision = _mapping_revision(stored, mode, chapters)
    continuous_available = _continuous_available(chapters)
    warnings = list(warnings)
    if mode == "continuous" and not continuous_available:
        warnings.append("当前播放资源使用续接集数，但章节资料缺少可用的不同续接编号；不会改用本作集数，请检查章节资料或手动设置")
    elif mode == "continuous" and any(row["episode"] and not row["continuous_episode"] for row in chapters):
        warnings.append("部分章节缺少唯一有效的续接集数，未自动对应这些章节；请检查章节资料或手动设置")
    seasons = {match[1] for row in files if (match := re.fullmatch(r"S(\d+)E(\d+)", row.get("episode", "")))}
    paths_by_episode = {}
    for row in files:
        match = re.fullmatch(r"S(\d+)E(\d+)", row["episode"])
        number = (_number(match[2]) if len(seasons) == 1 else "") if match else _number(row["episode"])
        if number:
            paths_by_episode.setdefault(number, []).append(row["relative_path"])
    for chapter in chapters:
        assigned = assignments.get(chapter["chapter_key"])
        local_episode = chapter.pop("episode")
        continuous_episode = chapter.pop("continuous_episode")
        # A previously selected continuation mode must never silently become
        # local numbering when a later source refresh loses that distinction.
        match_number = (continuous_episode if continuous_available else "") if mode == "continuous" else local_episode
        candidates = paths_by_episode.get(match_number, [])
        chapter["matching_number"] = match_number
        chapter.update(candidates=list(candidates), relative_path="", duration_seconds=None)
        if not chapter["binding_allowed"]:
            chapter["status"] = "unmatched"
        elif assigned:
            chapter.update(relative_path=assigned, status="confirmed" if assigned in by_path else "missing")
        elif len(candidates) == 1:
            chapter.update(relative_path=candidates[0], status="automatic")
        else:
            chapter["status"] = "ambiguous" if candidates else "unmatched"
        if chapter["relative_path"] in by_path:
            chapter["duration_seconds"] = by_path[chapter["relative_path"]].get("duration_seconds")
    return {"ok": True, "ref": deepcopy(detail["ref"]), "resource_index": index,
            "resource_key": key, "revision": revision, "files": files, "chapters": chapters,
            "numbering_mode": mode, "has_continuous_number": any(row["has_continuous_number"] for row in chapters),
            "continuous_number_available": continuous_available,
            "warnings": warnings, "scanned_at": stored["scanned_at"],
            "duration_available": any(row.get("duration_seconds") is not None for row in files)}


def cached_state(detail, *, work_root=None):
    """Project the selected DB cache only; never inspect directories or probe media."""
    record = detail["record"]
    state = record.get("local_playback")
    if state is None:
        return None
    try:
        validate_extensions({"local_playback": state})
        key = state.get("selected_resource_key")
        matches = [(index, resource) for index, resource in enumerate(detail["resources"])
                   if _hash(resource_identity(resource)) == key]
        if len(matches) != 1:
            return None
        index, resource = matches[0]
        stored = state["resources"][key].get("scan")
        if not stored or not work_root or _path_key(stored["root_path"]) != _path_key(ntpath.join(work_root, resource["press_path"])):
            return None
        return _projection(detail, index, key, stored)
    except (KeyError, TypeError, ValueError):
        return None


def _context(body, *, settings, data_root=None):
    if not isinstance(body, dict) or not isinstance(body.get("ref"), dict):
        raise ValueError("播放需要当前作品完整版本 ref，请重新加载作品")
    # Never resolve by stable ID alone: an old resource index may point to a
    # different release after a concurrent edit or provider refresh.
    detail = service.detail({"ref": body["ref"]}, settings=settings, data_root=data_root)
    index = body.get("resource_index")
    resources = detail["resources"]
    if type(index) is not int or not 0 <= index < len(resources):
        raise ValueError("请选择当前作品的一个压制资源")
    resource = resources[index]
    if not resource.get("press_path"):
        raise ValueError("该压制资源尚未绑定目录，请先补齐 DB 目录关联")
    record = detail["record"]
    if "local_playback" in record:
        validate_extensions({"local_playback": record["local_playback"]})
    root = resolve_record_directory(record, resource["press_path"])
    if not ordinary_directory(str(root)):
        raise ValueError(f"压制目录不存在、不可用或包含链接：{root}")
    identity = resource_identity(resource)
    resource_key = _hash(identity)
    if sum(all(other.get(field) == value for field, value in identity.items()) for other in resources) != 1:
        raise ValueError("当前作品存在重复压制绑定，请先在作品数据中合并或区分记录")
    return detail, root, identity, resource_key


def _inventory(root):
    def root_identity():
        if not ordinary_directory(str(root)):
            raise ValueError("压制目录已变化，请重新识别")
        metadata = root.lstat()
        return [metadata.st_dev, metadata.st_ino, metadata.st_ctime_ns]
    before = root_identity()
    report_progress("识别本地章节文件", context={"stage": "章节匹配", "source_path": str(root), "action": "优先扫描 Disc 及单集子目录；没有 Disc 时扫描当前资源目录"})
    scan = media_files.scan_media_files(root)
    if before != root_identity():
        raise ValueError("压制目录在识别期间已变化，请重新识别")
    if scan.get("truncated") or scan.get("warnings") or len(scan["files"]) > 2000:
        reasons = [str(warning) for warning in scan.get("warnings", [])[:5]]
        if scan.get("truncated") or len(scan["files"]) > 2000:
            reasons.append("扫描达到文件数量、目录深度或遍历上限")
        reason = "；".join(reasons)
        report_progress("视频目录未完整识别", detail=reason,
                        context={"stage": "章节识别", "source_path": str(root), "reason": reason, "level": "warning"})
        raise ValueError(f"视频目录未完整识别（{root}）：{reason}；原播放资源与缓存保持不变")
    if not scan["files"]:
        raise ValueError(f"该资源没有可识别的视频文件（{root}），原播放资源保持不变")
    return {"root_path": str(root), "root_identity": before,
            "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files": [{**deepcopy(row), "duration_seconds": None} for row in scan["files"]]}


def _durations(root, inventory):
    warnings = []
    deadline = time.monotonic() + 12.0
    unavailable_reason = "本机未安装或未找到 ffprobe"
    try:
        available = media_files.duration_available()
    except Exception as exc:
        available = False
        unavailable_reason = str(exc) or type(exc).__name__
    failures = 0
    exhausted = False
    last_progress = float("-inf")
    processed = 0
    total = len(inventory["files"])
    for index, row in enumerate(inventory["files"]):
        if not available:
            break
        now = time.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            exhausted = True
            report_progress("时长读取达到总时间上限", completed=processed, total=total, unit="视频",
                            context={"stage": "时长读取警告", "source_path": str(root / row["relative_path"]),
                                     "reason": "12 秒整体预算已耗尽，未处理文件的时长保留为空", "level": "warning"})
            break
        if index == 0 or index % 20 == 0 or now - last_progress >= 1:
            report_progress("读取视频时长", completed=index, total=total, unit="视频", detail=row["relative_path"],
                            context={"stage": "读取视频时长", "source_path": str(root / row["relative_path"])})
            last_progress = now
        reason = ""
        try:
            target = media_files.safe_media_path(root, row["relative_path"])
            seconds = media_files.probe_media_duration(target, timeout=min(1.0, remaining))
            if type(seconds) in {int, float} and math.isfinite(seconds) and seconds > 0:
                row["duration_seconds"] = seconds
            else:
                failures += 1
                reason = "ffprobe 未返回有效时长（可能超时或媒体信息不可用）"
        except Exception as exc:
            failures += 1
            reason = str(exc) or type(exc).__name__
        processed += 1
        if reason:
            report_progress("视频时长不可用", detail=reason,
                            context={"stage": "时长读取警告", "source_path": str(root / row["relative_path"]),
                                     "reason": reason, "level": "warning"})
    if not available:
        warnings.append("本机未提供可用时长工具；视频识别与资源选择不受影响")
        report_progress(warnings[-1], detail=unavailable_reason,
                        context={"stage": "时长读取警告", "source_path": str(root), "reason": unavailable_reason, "level": "warning"})
    if failures:
        warnings.append(f"{failures} 个视频未取得时长，已保留为空")
    if exhausted:
        warnings.append("时长读取达到总时间上限，其余时长保留为空")
    for warning in warnings:
        report_progress(warning, context={"stage": "时长读取警告", "level": "warning", "source_path": str(root)})
    if available:
        report_progress("时长读取结束", completed=processed, total=total, unit="视频",
                        context={"stage": "时长读取", "source_path": str(root)})
    return warnings


def _commit(record, detail, index, key, *, settings, data_root, warnings=()):
    report_progress("保存识别缓存与播放配置", context={"stage": "播放配置保存", "action": "通过统一作品 DB 写入接口保存"})
    applied = service.apply_edits({"edits": [{"ref": detail["ref"], "record": record}]}, settings=settings, data_root=data_root)
    updated = applied.get("records", [])
    new_ref = None
    fresh = None
    try:
        if len(updated) != 1 or not isinstance(updated[0].get("ref"), dict):
            raise ValueError("未取得最新作品引用")
        new_ref = updated[0]["ref"]
        fresh = service.detail({"ref": new_ref}, settings=settings, data_root=data_root)
        result = _projection(fresh, index, key, _entry(fresh["record"], key)["scan"], warnings)
        return {**result, "detail": fresh, "db_committed": True}
    except (KeyError, ValueError, OSError, RuntimeError) as exc:
        warning = "播放配置已保存，但刷新失败；请重新加载作品，不要重复提交：" + str(exc)
        return {"ok": True, "db_committed": True, "requires_rescan": True, "ref": new_ref, "detail": fresh,
                "resource_index": index, "resource_key": key, "revision": "", "files": [], "chapters": [],
                "scanned_at": None, "duration_available": False, "warnings": [*warnings, warning], "message": warning}


def _recognize(body, *, settings, data_root=None, select_resource=False):
    detail, root, identity, key = _context(body, settings=settings, data_root=data_root)
    mode = body.get("numbering_mode", _entry(detail["record"], key).get("numbering_mode", "local"))
    if type(mode) is not str or mode not in NUMBERING_MODES:
        raise ValueError("播放资源编号方式 numbering_mode 必须为 local（本作集数）或 continuous（续接集数）")
    if body.get("numbering_mode") == "continuous" and not _continuous_available(_chapter_rows(detail["chapters"])):
        raise ValueError("当前章节没有可用的不同续接集数，请使用本作集数或补齐章节资料后再选择")
    report_progress("按续接集数识别播放资源" if mode == "continuous" else "按本作集数识别播放资源",
                    context={"stage": "章节编号方式", "source_path": str(root), "numbering_mode": mode})
    inventory = _inventory(root)
    warnings = _durations(root, inventory)
    if _revision(_inventory(root)) != _revision(inventory):
        raise ValueError("本地文件在识别期间已变化，请重新识别；未保存播放配置")
    record = deepcopy(detail["record"])
    state = record.setdefault("local_playback", {"version": 1, "resources": {}})
    entry = state["resources"].setdefault(key, {"identity": identity, "assignments": {}})
    entry["scan"] = inventory
    entry["numbering_mode"] = mode
    if select_resource:
        state["selected_resource_key"] = key
    return _commit(record, detail, body["resource_index"], key, settings=settings, data_root=data_root, warnings=warnings)


def scan(body, *, settings, data_root=None):
    return _recognize(body, settings=settings, data_root=data_root)


def select(body, *, settings, data_root=None):
    return _recognize(body, settings=settings, data_root=data_root, select_resource=True)


def _verified(body, *, settings, data_root=None, selected_only=False):
    detail, root, identity, key = _context(body, settings=settings, data_root=data_root)
    state = detail["record"].get("local_playback", {})
    if selected_only and (state.get("selected_resource_key") != key or not detail.get("playback")):
        raise ValueError("该资源不是当前有效播放资源，请先选择并识别")
    stored = _entry(detail["record"], key).get("scan")
    if not stored or _path_key(stored["root_path"]) != _path_key(root):
        raise ValueError("该资源尚无有效识别缓存，请先识别")
    result = _projection(detail, body["resource_index"], key, stored)
    if body.get("revision") != result["revision"] or _revision(_inventory(root)) != _revision(stored):
        raise ValueError("作品数据、本地文件或集数识别结果已变化，请重新识别后再操作")
    return result, detail, root, identity


def save(body, *, settings, data_root=None):
    result, detail, root, identity = _verified(body, settings=settings, data_root=data_root)
    changes = body.get("assignments")
    if not isinstance(changes, list) or not 1 <= len(changes) <= 5000:
        raise ValueError("请提供 1～5000 项待确认的章节对应关系")
    record = deepcopy(detail["record"])
    state = record.setdefault("local_playback", {"version": 1, "resources": {}})
    if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("resources"), dict):
        raise ValueError("作品本地播放配置格式无效，请先修复作品数据")
    resource_key = result["resource_key"]
    entry = state["resources"][resource_key]
    if not isinstance(entry, dict) or not isinstance(entry.get("assignments"), dict):
        raise ValueError("作品章节映射格式无效，请先修复作品数据")
    chapter_keys = {chapter["chapter_key"] for chapter in result["chapters"] if chapter["binding_allowed"]}
    paths = {row["relative_path"] for row in result["files"]}
    seen = set()
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("chapter_key"), str):
            raise ValueError("章节对应关系必须包含 chapter_key")
        key, path = change["chapter_key"], change.get("relative_path")
        if key not in chapter_keys or key in seen:
            raise ValueError("章节不存在或在本次确认中重复，请重新识别")
        seen.add(key)
        if not isinstance(path, str):
            raise ValueError("章节文件路径必须为文字")
        if not path:
            entry["assignments"].pop(key, None)
            continue
        if path not in paths:
            raise ValueError("只能选择本次识别的 Disc（没有 Disc 时为当前资源目录）及单集子目录中的视频文件")
        media_files.safe_media_path(root, path)
        entry["assignments"][key] = path
    report_progress("保存已确认的章节对应关系", context={"stage": "章节绑定", "action": "通过作品 DB 统一保存接口写入", "source_path": str(root)})
    if _revision(_inventory(root)) != _revision(entry["scan"]):
        raise ValueError("本地文件在保存前已变化，请重新识别")
    return _commit(record, detail, body["resource_index"], resource_key, settings=settings, data_root=data_root)


def open_chapter(body, *, settings, data_root=None):
    result, _detail, root, _identity = _verified(body, settings=settings, data_root=data_root, selected_only=True)
    chapter = next((row for row in result["chapters"] if row["chapter_key"] == body.get("chapter_key")), None)
    if not chapter or chapter["status"] not in {"automatic", "confirmed"}:
        raise ValueError("该章节尚未唯一匹配可播放文件，请先手动设置并保存")
    path = body.get("relative_path")
    if not path or path != chapter["relative_path"]:
        raise ValueError("播放文件与当前章节对应关系不一致，请重新识别")
    report_progress("使用系统默认播放器打开章节", context={"stage": "播放章节", "action": "打开已核对的视频文件", "source_path": str(root / path)})
    opened = media_files.open_media_file(root, path)
    return {"ok": True, "relative_path": path, "chapter_key": chapter["chapter_key"], "path": str(opened),
            "message": "已请求系统默认播放器打开文件"}
