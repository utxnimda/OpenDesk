"""Workspace-wide presentation preferences, separate from authoritative works."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import re

from ruamel.yaml.error import YAMLError
from work_catalog_yaml.layout import feature_config_path, feature_data_root
from work_catalog_yaml.persistence import FileWrite, commit_file_writes, directory_write_transaction, history_snapshot_name
from work_catalog_yaml.yaml_io import dump_yaml_string, load_yaml_string

_KEY = re.compile(r"^[a-z0-9_-]{1,64}$")
_PRIORITIES = {"original", "chinese"}
_DEFAULTS = {("animation", "japan"): "original", ("television", "korea"): "chinese"}
_DOMAIN_LABELS = {"animation": "动画", "television": "电视剧", "music": "音乐", "movie": "电影"}
_COUNTRY_LABELS = {"japan": "日本", "korea": "韩国", "china": "中国"}


def _safe_directory(path):
    from .service import _safe_directory as inspect
    return inspect(path)


def _path(config_path=None):
    path = Path(config_path) if config_path is not None else feature_config_path("catalog-library").with_name("display.yaml")
    path = path.expanduser().absolute()
    _safe_directory(path.parent)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("作品库显示配置不是普通文件")
    return path


def _validate(state):
    if not isinstance(state, dict) or set(state) != {"schema_version", "default_priority", "chapter_titles"} or type(state.get("schema_version")) is not int or state["schema_version"] != 1:
        raise ValueError("作品库显示配置格式或版本无效")
    if not isinstance(state["default_priority"], str) or state["default_priority"] not in _PRIORITIES:
        raise ValueError("默认章节语言必须为 original 或 chinese")
    rows = state["chapter_titles"]
    if not isinstance(rows, list) or len(rows) > 256:
        raise ValueError("章节显示规则必须为最多 256 项的列表")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"domain", "country", "priority"}:
            raise ValueError("章节显示规则必须包含品类、国家和语言优先项")
        if any(not isinstance(row[key], str) or not _KEY.fullmatch(row[key]) for key in ("domain", "country")) or not isinstance(row["priority"], str) or row["priority"] not in _PRIORITIES:
            raise ValueError("章节显示规则的品类、国家或语言优先项无效")
        key = (row["domain"], row["country"])
        if key in seen:
            raise ValueError("同一收集类型不能重复配置章节语言")
        seen.add(key)
    return state


def _state(config_path=None):
    path = _path(config_path)
    content = path.read_bytes() if path.is_file() else None
    if content is not None and len(content) > 262144:
        raise ValueError("作品库显示配置过大")
    try:
        state = load_yaml_string(content.decode("utf-8")) if content is not None else {
            "schema_version": 1, "default_priority": "original",
            "chapter_titles": [{"domain": key[0], "country": key[1], "priority": value} for key, value in _DEFAULTS.items()]}
    except (YAMLError, UnicodeError) as exc:
        raise ValueError("作品库显示配置无法解析，请检查 YAML 格式") from exc
    return path, content, _validate(state)


def payload(body=None, *, settings=None, records=(), config_path=None):
    _filename, content, state = _state(config_path)
    configured = {(row["domain"], row["country"]): row["priority"] for row in state["chapter_titles"]}
    keys = set(_DEFAULTS) | set(configured)
    keys.update((row.get("domain", ""), row.get("country", "")) for row in records
                if all(isinstance(row.get(key), str) and _KEY.fullmatch(row[key]) for key in ("domain", "country")))
    labels = getattr(settings, "enum_labels", {})
    options = [{"domain": domain, "country": country,
                "label": str(labels.get("domain", {}).get(domain) or _DOMAIN_LABELS.get(domain, domain)) + " · " + str(labels.get("country", {}).get(country) or _COUNTRY_LABELS.get(country, country)),
                "priority": configured.get((domain, country), state["default_priority"])} for domain, country in sorted(keys)]
    return {"config": deepcopy(state), "revision": hashlib.sha256(content or b"").hexdigest(), "collection_types": options}


def priority(record, *, config_path=None):
    _filename, _content, state = _state(config_path)
    attributes = {item.get("type"): item.get("data") for item in record.get("attributes", []) if isinstance(item, dict)}
    collection = attributes.get("collection-type") or {}
    key = (collection.get("domain", ""), attributes.get("country", ""))
    return next((row["priority"] for row in state["chapter_titles"] if (row["domain"], row["country"]) == key), state["default_priority"])


def save(body, *, settings=None, config_path=None, history_root=None):
    if not isinstance(body, dict) or set(body) != {"revision", "config"}:
        raise ValueError("显示配置保存参数必须包含当前版本和配置")
    updated = deepcopy(_validate(body["config"]))
    path, _content, _current = _state(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    history = _safe_directory(Path(history_root).absolute() if history_root is not None else feature_data_root("catalog-library") / "history" / "display-settings")
    with directory_write_transaction(path.parent):
        path, content, _current = _state(config_path)
        if body["revision"] != hashlib.sha256(content or b"").hexdigest():
            raise ValueError("显示配置已变化，请重新打开设置后保存")
        data = dump_yaml_string(updated).encode("utf-8")
        if content != data:
            commit_file_writes([FileWrite(path, data, content, history / history_snapshot_name(path) if content is not None else None)])
    return payload(settings=settings, config_path=config_path)
