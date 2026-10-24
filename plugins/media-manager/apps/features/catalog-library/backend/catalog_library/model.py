"""Pure validation for optional, backward-compatible work-library extensions."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Any
from uuid import uuid4

from work_catalog_yaml.common.media_files import VIDEO_EXTENSIONS
from .chapter_numbers import NUMBERING_MODES
from .source_bindings import ROLES, binding_id

SCHEMA_VERSION = 1
WORK_ID = re.compile(r"work_[0-9a-f]{32}\Z")
BINDING_ID = re.compile(r"sb_[0-9a-f]{24}\Z")
CLASSIFICATION_ID = re.compile(r"classification_[0-9a-f]{32}\Z")
CLASSIFICATION_TYPES = frozenset({"series", "topic", "universe"})
COVER_MIMES = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}
_PLAYBACK_IDENTITY_FIELDS = {"press_path", "press_format", "press_group", "segment", "continuation_index"}
_WINDOWS_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", re.IGNORECASE)
_SCAN_FIELDS = {"root_path", "root_identity", "scanned_at", "files"}
_SCAN_FILE_FIELDS = {"relative_path", "episode", "size", "mtime_ns", "signature", "duration_seconds"}


def _playback_relative_path(value: Any, label: str) -> None:
    """Validate path syntax only; runtime checks bound roots and playable files."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} 必须为非空相对路径")
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or PureWindowsPath(normalized).drive or any(
        part in {"", ".", ".."} or part.endswith((" ", ".")) or
        re.search(r'[<>:"|?*\x00-\x1f\x7f-\x9f]', part) or _WINDOWS_RESERVED.fullmatch(part)
        for part in normalized.split("/")
    ):
        raise ValueError(f"{label} 必须为安全相对路径，不允许绝对路径、路径穿越或保留名称")


def _nonnegative_signature(value: Any, length: int, label: str) -> None:
    if not isinstance(value, list) or len(value) != length or any(type(item) is not int or item < 0 for item in value):
        raise ValueError(f"{label} 必须为 {length} 个非负整数")


def _validate_playback_scan(scan: Any) -> None:
    if not isinstance(scan, dict) or set(scan) != _SCAN_FIELDS:
        raise ValueError("local_playback.scan 必须包含 root_path、root_identity、scanned_at 和 files")
    root = scan["root_path"]
    if not isinstance(root, str) or not root.strip() or re.search(r"[\x00-\x1f\x7f-\x9f]", root):
        raise ValueError("local_playback.scan.root_path 必须为无控制字符的非空绝对路径")
    windows = PureWindowsPath(root)
    if not (windows.is_absolute() or PurePosixPath(root).is_absolute()) or ".." in root.replace("\\", "/").split("/"):
        raise ValueError("local_playback.scan.root_path 必须为无上级跳转的绝对路径")
    _nonnegative_signature(scan["root_identity"], 3, "local_playback.scan.root_identity")
    scanned_at = scan["scanned_at"]
    if not isinstance(scanned_at, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", scanned_at) is None:
        raise ValueError("local_playback.scan.scanned_at 必须为 UTC ISO 时间字符串")
    try:
        timestamp = datetime.fromisoformat(scanned_at.replace("Z", "+00:00"))
        if timestamp.utcoffset() != timedelta(0):
            raise ValueError
    except ValueError as error:
        raise ValueError("local_playback.scan.scanned_at 必须为有效 UTC ISO 时间字符串") from error
    files = scan["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= 2000:
        raise ValueError("local_playback.scan.files 必须为非空数组，最多 2000 项")
    seen = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != _SCAN_FILE_FIELDS:
            raise ValueError("local_playback.scan.files 文件字段无效")
        relative = item["relative_path"]
        _playback_relative_path(relative, "local_playback.scan.files.relative_path")
        normalized = relative.replace("\\", "/")
        if PurePosixPath(normalized).suffix.casefold() not in VIDEO_EXTENSIONS:
            raise ValueError("local_playback.scan.files 仅允许视频文件")
        if normalized.casefold() in seen:
            raise ValueError("local_playback.scan.files 文件相对路径不能重复")
        seen.add(normalized.casefold())
        if not isinstance(item["episode"], str):
            raise ValueError("local_playback.scan.files.episode 必须为字符串")
        if any(type(item[key]) is not int or item[key] < 0 for key in ("size", "mtime_ns")):
            raise ValueError("local_playback.scan.files 大小和修改时间必须为非负整数")
        _nonnegative_signature(item["signature"], 5, "local_playback.scan.files.signature")
        if item["signature"][3:] != [item["size"], item["mtime_ns"]]:
            raise ValueError("local_playback.scan.files.signature 与文件大小或修改时间不一致")
        duration = item["duration_seconds"]
        if duration is not None and (type(duration) not in {int, float} or duration <= 0 or
                                     (type(duration) is float and not math.isfinite(duration))):
            raise ValueError("local_playback.scan.files.duration_seconds 必须为正有限数值或 null")


def _validate_local_playback(playback: Any) -> None:
    if not isinstance(playback, dict) or not {"version", "resources"}.issubset(playback) or set(playback) - {"version", "resources", "selected_resource_key"}:
        raise ValueError("local_playback 必须包含 version 和 resources")
    if type(playback["version"]) is not int or playback["version"] != 1:
        raise ValueError("不支持的 local_playback.version")
    resources = playback["resources"]
    if not isinstance(resources, dict) or len(resources) > 200:
        raise ValueError("local_playback.resources 必须为对象，最多 200 项")
    for resource_key, resource in resources.items():
        if not isinstance(resource_key, str) or re.fullmatch(r"[0-9a-f]{64}", resource_key) is None:
            raise ValueError("local_playback 资源标识必须为 64 位小写 SHA-256")
        if not isinstance(resource, dict) or not {"identity", "assignments"}.issubset(resource) or set(resource) - {"identity", "assignments", "scan", "numbering_mode"}:
            raise ValueError("local_playback 资源必须包含 identity 和 assignments")
        if "numbering_mode" in resource and (not isinstance(resource["numbering_mode"], str) or resource["numbering_mode"] not in NUMBERING_MODES):
            raise ValueError("local_playback.numbering_mode 必须为 local 或 continuous")
        identity = resource["identity"]
        if not isinstance(identity, dict) or set(identity) != _PLAYBACK_IDENTITY_FIELDS:
            raise ValueError("local_playback.identity 缺少有效压制记录身份")
        if any(not isinstance(identity[key], str) for key in _PLAYBACK_IDENTITY_FIELDS - {"continuation_index"}):
            raise ValueError("local_playback.identity 压制记录字段必须为字符串")
        _playback_relative_path(identity["press_path"], "local_playback.identity.press_path")
        position = identity["continuation_index"]
        if identity["segment"] not in {"main", "continuation"} or (
            identity["segment"] == "main" and position is not None
        ) or (identity["segment"] == "continuation" and (type(position) is not int or position < 0)):
            raise ValueError("local_playback.identity 分段或续行位置无效")
        expected = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if resource_key != expected:
            raise ValueError("local_playback 资源标识与 identity 不一致")
        assignments = resource["assignments"]
        if not isinstance(assignments, dict) or len(assignments) > 5000:
            raise ValueError("local_playback.assignments 必须为对象，最多 5000 项")
        for chapter_key, relative_path in assignments.items():
            if not isinstance(chapter_key, str) or not chapter_key.strip() or len(chapter_key) > 200 or re.search(r"[\x00-\x1f\x7f-\x9f]", chapter_key):
                raise ValueError("local_playback 章节标识必须为 1～200 个非控制字符")
            _playback_relative_path(relative_path, "local_playback.assignments 路径")
        if "scan" in resource:
            _validate_playback_scan(resource["scan"])
    if "selected_resource_key" in playback:
        selected = playback["selected_resource_key"]
        if not isinstance(selected, str) or re.fullmatch(r"[0-9a-f]{64}", selected) is None:
            raise ValueError("local_playback.selected_resource_key 必须为 64 位小写 SHA-256")
        if selected not in resources or "scan" not in resources[selected]:
            raise ValueError("local_playback.selected_resource_key 必须指向有扫描缓存的资源")


def validate_cover_reference(cover):
    """Only local content identities can become a browser image source."""
    if not isinstance(cover, dict) or not isinstance(cover.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", cover["sha256"]):
        raise ValueError("metadata.cover 缺少有效本地内容 SHA-256")
    extension = cover.get("extension")
    if not isinstance(extension, str) or extension not in COVER_MIMES or cover.get("mime_type") != COVER_MIMES[extension]:
        raise ValueError("metadata.cover 图片类型无效")
    if any(type(cover.get(key)) is not int or not 0 < cover[key] <= 12000 for key in ("width", "height")) or cover["width"] * cover["height"] > 40_000_000:
        raise ValueError("metadata.cover 图片尺寸无效")
    if "source_url" in cover and (not isinstance(cover["source_url"], str) or len(cover["source_url"]) > 2048):
        raise ValueError("metadata.cover 来源地址格式无效")
    return cover


def new_work_id() -> str:
    return "work_" + uuid4().hex


def attribute(record: dict[str, Any], kind: str, default: Any = None) -> Any:
    return next((item.get("data", default) for item in record.get("attributes", [])
                 if isinstance(item, dict) and item.get("type") == kind), default)


def validate_extensions(record: dict[str, Any]) -> dict[str, Any]:
    """Do not invent missing metadata or interpret continuations as sequels."""
    result = deepcopy(record)
    if "id" in result and (not isinstance(result["id"], str) or not WORK_ID.fullmatch(result["id"])):
        raise ValueError("作品 id 必须为 work_ 加 32 位小写十六进制稳定标识")
    if "schema_version" in result and (type(result["schema_version"]) is not int or result["schema_version"] != SCHEMA_VERSION):
        raise ValueError("不支持的作品 schema_version")
    if "local_playback" in result:
        _validate_local_playback(result["local_playback"])
    if "people_refs" in result:
        from people_catalog.model import validate_people_refs
        result["people_refs"] = validate_people_refs(result["people_refs"])
    if "classifications" in result:
        values = result["classifications"]
        if not isinstance(values, list) or len(values) > 200:
            raise ValueError("classifications 必须是数组，最多 200 项")
        seen = set()
        for item in values:
            if not isinstance(item, dict) or item.get("type") not in CLASSIFICATION_TYPES:
                raise ValueError("分类 type 必须为 series、topic 或 universe")
            identity = item.get("value_id")
            if not isinstance(identity, str) or not CLASSIFICATION_ID.fullmatch(identity):
                raise ValueError("分类 value_id 无效")
            if identity in seen:
                raise ValueError("同一作品不能重复关联相同分类")
            seen.add(identity)
            if "order" in item and (type(item["order"]) not in {int, float} or not math.isfinite(item["order"])):
                raise ValueError("分类 order 必须为有限数值")
    if "metadata" in result:
        metadata = result["metadata"]
        if not isinstance(metadata, dict):
            raise ValueError("metadata 必须为对象")
        if "ratings" in metadata:
            from .ratings import validate_ratings
            validate_ratings(metadata["ratings"])
        if "summary" in metadata and not isinstance(metadata["summary"], str):
            raise ValueError("metadata.summary 必须为字符串")
        if "cover" in metadata:
            validate_cover_reference(metadata["cover"])
        if "aliases" in metadata and (not isinstance(metadata["aliases"], list) or
                                      any(not isinstance(alias, str) for alias in metadata["aliases"])):
            raise ValueError("metadata.aliases 必须为字符串数组")
        if "episodes" in metadata and (not isinstance(metadata["episodes"], list) or
                                       any(not isinstance(episode, dict) for episode in metadata["episodes"])):
            raise ValueError("metadata.episodes 必须为对象数组")
    if "source_refs" in result:
        refs = result["source_refs"]
        if not isinstance(refs, list) or any(not isinstance(ref, dict) for ref in refs):
            raise ValueError("source_refs 必须为对象数组")
        identities = set()
        binding_ids = set()
        for ref in refs:
            provider, external_id = ref.get("provider"), ref.get("external_id")
            if not isinstance(provider, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider):
                raise ValueError("来源 provider 无效")
            if type(external_id) not in {str, int} or not str(external_id).strip():
                raise ValueError("来源 external_id 不能为空")
            identity = (provider, str(external_id), str(ref.get("scope", "")))
            if identity in identities:
                raise ValueError("不能重复关联相同来源和范围")
            identities.add(identity)
            if "binding_id" in ref and (not isinstance(ref["binding_id"], str) or not BINDING_ID.fullmatch(ref["binding_id"])):
                raise ValueError("来源 binding_id 必须为 sb_ 加 24 位小写十六进制稳定标识")
            source_binding_id = binding_id(ref)
            if source_binding_id in binding_ids:
                raise ValueError("来源 binding_id 不能重复")
            binding_ids.add(source_binding_id)
            if "enabled" in ref and type(ref["enabled"]) is not bool:
                raise ValueError("来源 enabled 必须为布尔值")
            if "role" in ref and (not isinstance(ref["role"], str) or ref["role"] not in ROLES):
                raise ValueError("来源 role 必须为 primary 或 supplement")
            if "title" in ref and (not isinstance(ref["title"], str) or len(ref["title"]) > 1000):
                raise ValueError("来源 title 必须为最多 1000 字符的字符串")
    if "source_policy_config" in result:
        config = result["source_policy_config"]
        if not isinstance(config, dict) or type(config.get("custom")) is not bool or not isinstance(config.get("targets"), list):
            raise ValueError("source_policy_config 必须包含布尔 custom 和 targets 数组")
        if len(config["targets"]) > 200:
            raise ValueError("source_policy_config.targets 最多 200 项")
        providers = set()
        for target in config["targets"]:
            if not isinstance(target, dict):
                raise ValueError("source_policy_config.targets 必须为对象数组")
            provider, role = target.get("provider"), target.get("role")
            if not isinstance(provider, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider):
                raise ValueError("source_policy_config 来源 provider 无效")
            if not isinstance(role, str) or role not in ROLES:
                raise ValueError("source_policy_config role 必须为 primary 或 supplement")
            if provider in providers:
                raise ValueError("source_policy_config 来源 provider 不能重复")
            providers.add(provider)
    return result


def classification_definition(raw: Any, *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("分类定义必须为对象")
    kind = raw.get("type", existing.get("type") if existing else "series")
    if kind not in CLASSIFICATION_TYPES:
        raise ValueError("分类 type 必须为 series、topic 或 universe")
    if existing and kind != existing["type"]:
        raise ValueError("已有分类不能修改 type，请新增分类")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
        raise ValueError("分类名称必须为 1～200 字符")
    aliases = raw.get("aliases", existing.get("aliases", []) if existing else [])
    if not isinstance(aliases, list) or len(aliases) > 100 or any(not isinstance(alias, str) or len(alias) > 200 for alias in aliases):
        raise ValueError("分类 aliases 必须为字符串数组，最多 100 项")
    return {"id": existing["id"] if existing else "classification_" + uuid4().hex,
            "type": kind, "name": name.strip(), "aliases": list(dict.fromkeys(alias.strip() for alias in aliases if alias.strip()))}
