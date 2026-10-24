"""Durable, identity-bound directory completion flags outside the media tree."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
from typing import Iterator
import uuid

from work_catalog_yaml.common.log_files import ordinary_log_file
from work_catalog_yaml.persistence import atomic_write_bytes, directory_write_transaction

from .snapshots import absolute_path, existing_directory, path_key, safe_target


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"重复字段：{key}")
        result[key] = value
    return result


class CompletionStore:
    """One application-owned JSON file; reads never create directories or locks.

    False entries are intentional tombstones: each state transition gets a new
    revision so a preview cannot survive a mark/unmark (ABA) cycle unnoticed.
    """

    def __init__(self, path: Path):
        self.path = absolute_path(str(path))

    def _ordinary_file(self, path: Path):
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return None
        if not ordinary_log_file(metadata):
            raise ValueError(f"整理完成状态文件不是普通独占文件（不支持链接）：{path}")
        return metadata

    def _validate(self, document) -> dict:
        if (not isinstance(document, dict) or set(document) != {"version", "directories"}
                or type(document["version"]) is not int or document["version"] != 1
                or not isinstance(document["directories"], dict)):
            raise ValueError("需要 version=1 和 directories 对象")
        for key, entry in document["directories"].items():
            if (not isinstance(key, str) or path_key(absolute_path(key)) != key
                    or not isinstance(entry, dict)
                    or set(entry) != {"completed", "revision", "identity"}
                    or type(entry["completed"]) is not bool
                    or not isinstance(entry["revision"], str)
                    or not re.fullmatch(r"[0-9a-f]{32}", entry["revision"])
                    or not isinstance(entry["identity"], list)
                    or len(entry["identity"]) != 2
                    or any(type(value) is not int or value < 0 for value in entry["identity"])):
                raise ValueError(f"目录标记结构不合法：{key}")
        return document["directories"]

    def read(self) -> dict:
        """Return validated directory entries without touching the filesystem."""
        try:
            safe_target(self.path.parent)
            initial = self._ordinary_file(self.path)
            if initial is None:
                return {}
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                if (not ordinary_log_file(opened)
                        or (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino)):
                    raise ValueError("状态文件读取期间被链接或替换")
                document = json.loads(stream.read(), object_pairs_hook=_unique_object)
            return self._validate(document)
        except (OSError, ValueError, UnicodeError) as exc:
            raise ValueError(f"无法读取整理完成状态文件 {self.path}：{exc}") from exc

    def _source(self, source: Path) -> tuple[str, list[int]]:
        directory = existing_directory(str(source))
        identity = directory.lstat()
        return path_key(directory), [identity.st_dev, identity.st_ino]

    def get(self, source: Path, *, entries=None) -> dict:
        entries = self.read() if entries is None else entries
        key, identity = self._source(source)
        entry = entries.get(key)
        if entry is None or entry["identity"] != identity:
            return {"completion_marked": False, "completion_revision": ""}
        return {"completion_marked": entry["completed"], "completion_revision": entry["revision"]}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Reentrant thread/process lock, also held throughout media execution."""
        parent = safe_target(self.path.parent)
        parent.mkdir(parents=True, exist_ok=True)
        safe_target(parent)
        self._ordinary_file(parent / ".nimda-catalog.lock")
        with directory_write_transaction(parent):
            safe_target(parent)
            self._ordinary_file(self.path)
            yield

    def set(self, source: Path, completed: bool) -> dict:
        if type(completed) is not bool:
            raise ValueError("整理完成标记必须为布尔值")
        with self.transaction():
            entries = self.read()
            key, identity = self._source(source)
            current = entries.get(key)
            if current is None or current["identity"] != identity or current["completed"] != completed:
                entries[key] = {"completed": completed, "revision": uuid.uuid4().hex, "identity": identity}
                payload = {"version": 1, "directories": entries}
                safe_target(self.path.parent)
                self._ordinary_file(self.path)
                atomic_write_bytes(self.path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            return {"completion_marked": entries[key]["completed"], "completion_revision": entries[key]["revision"]}
