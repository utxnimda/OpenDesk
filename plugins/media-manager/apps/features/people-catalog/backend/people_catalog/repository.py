"""Bounded local YAML repository with shared CAS, locks, history and rollback."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from ruamel.yaml.error import YAMLError

from work_catalog_yaml.layout import feature_data_root
from work_catalog_yaml.operation_progress import report_exception
from work_catalog_yaml.persistence import (FileWrite, PersistenceRollbackError, atomic_write_bytes,
    commit_file_writes, directory_write_transaction, history_snapshot_name)
from work_catalog_yaml.yaml_io import dump_yaml_string, load_yaml_string

from .model import MAX_ENTITY_BYTES, entity_id, validate_entity


def safe_path(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError(f"人物数据库不允许符号链接或联接：{part}")
    return path


class PeopleRepository:
    def __init__(self, root=None):
        self.root = safe_path(root if root is not None else feature_data_root("people-catalog"))

    def path(self, identity):
        return safe_path(self.root / "db" / (entity_id(identity) + ".yaml"))

    def _bytes(self, identity):
        path = self.path(identity)
        if not path.exists():
            return None
        if not path.is_file() or path.stat().st_size > MAX_ENTITY_BYTES:
            raise ValueError(f"人物记录不是普通文件或超过安全大小限制：{identity}")
        with path.open("rb") as stream:
            data = stream.read(MAX_ENTITY_BYTES + 1)
        if len(data) > MAX_ENTITY_BYTES:
            raise ValueError("人物记录超过安全大小限制")
        return data

    def read(self, identity):
        data = self._bytes(identity)
        if data is None:
            return None
        try:
            result = validate_entity(load_yaml_string(data.decode("utf-8")))
        except (ValueError, UnicodeError, RecursionError, YAMLError) as exc:
            raise ValueError(f"人物记录无法读取（{identity}）：{exc}") from exc
        if result["id"] != identity:
            raise ValueError("人物文件名与记录 ID 不一致")
        return result

    def revision(self, identities):
        digest = hashlib.sha256()
        for identity in sorted(set(identities)):
            data = self._bytes(identity)
            digest.update(identity.encode("ascii"))
            digest.update(hashlib.sha256(data).digest() if data is not None else b"missing")
        return digest.hexdigest()

    def apply_batch(self, records, *, expected_revision, after_write=None, revision_ids=None):
        """Write entities before work; roll back only after a definite work failure.

        All cooperating people writers share this lock. Existing work writers are
        invoked inside it; their own CAS/atomic batch protects work records. A
        process interruption may leave unreferenced entities, never dangling work
        references. Unknown/committed work failures retain dependency records and
        raise a recovery-required error, never a misleading success response.

        revision_ids optionally watches a larger identity set than the actual
        writes. Watched-only YAML is never serialized or touched, preserving
        comments and exact bytes for unselected preview candidates.
        """
        if not isinstance(records, list) or len(records) > 1000:
            raise ValueError("人物写入批次最多包含 1000 项")
        records = [validate_entity(row) for row in records]
        identities = [row["id"] for row in records]
        if len(set(identities)) != len(identities):
            raise ValueError("人物写入批次包含重复 ID")
        if revision_ids is None:
            watched = identities
        else:
            if not isinstance(revision_ids, list) or len(revision_ids) > 10000:
                raise ValueError("人物版本校验 ID 必须为最多 10000 项的数组")
            watched = [entity_id(identity) for identity in revision_ids]
            if len(set(watched)) != len(watched) or not set(identities).issubset(watched):
                raise ValueError("人物版本校验 ID 不能重复且必须覆盖所有写入人物")
        self.root.mkdir(parents=True, exist_ok=True)
        safe_path(self.root / ".nimda-catalog.lock")
        with directory_write_transaction(safe_path(self.root)):
            if self.revision(watched) != expected_revision:
                raise ValueError("人物数据库在预览后已变化，请重新预览")
            lookup = {row["id"]: row for row in records}
            for row in records:
                previous = self.read(row["id"])
                if previous and previous["type"] != row["type"]:
                    raise ValueError("已有的人物类型不允许修改")
                if "merged_into" in row:
                    existing_target = self.read(row["merged_into"])
                    target = lookup.get(row["merged_into"]) or existing_target
                    if not existing_target or not target or target["type"] != row["type"] or target.get("merged_into"):
                        raise ValueError("合并目标必须为同类型的既有有效人物，不能形成合并链")
                for actor in row.get("voice_actors", []):
                    target = lookup.get(actor["person_id"]) or self.read(actor["person_id"])
                    if not target or target["type"] != "person":
                        raise ValueError("角色引用了不存在或类型错误的声优")
            history = safe_path(self.root / "history")
            writes = []
            for row in records:
                path = self.path(row["id"])
                previous = self._bytes(row["id"])
                content = dump_yaml_string(row).encode("utf-8")
                if len(content) > MAX_ENTITY_BYTES:
                    raise ValueError("人物 YAML 超过安全大小限制")
                if content != previous:
                    writes.append(FileWrite(path, content, previous,
                        history / history_snapshot_name(path) if previous is not None else None))
            commit_file_writes(writes)
            try:
                result = after_write() if after_write else {"ok": True, "records": deepcopy(records)}
                if isinstance(result, dict) and "db_committed" in result and result["db_committed"] is not True:
                    if result["db_committed"] is False:
                        error = ValueError("作品数据库明确未提交，正在恢复本次人物修改")
                        error.db_committed = False
                    else:
                        error = PersistenceRollbackError("作品保存结果未能确认数据库是否提交")
                        error.db_committed = None
                    raise error
                return result
            except BaseException as failure:
                # The work writer explicitly marks an incomplete rollback with
                # db_committed=None. Removing dependencies in that situation
                # can leave an already-written work pointing at deleted people.
                # A bare PersistenceRollbackError/interrupt is also uncertain;
                # normal unmarked Exceptions follow the shared writer contract
                # (no commit or a completed rollback) and remain reversible.
                state = getattr(failure, "db_committed", False)
                if not hasattr(failure, "db_committed") and (
                        isinstance(failure, PersistenceRollbackError) or not isinstance(failure, Exception)):
                    state = None
                if state is not False:
                    confirmed = state is True
                    reason = "作品数据库已经提交，但后续处理失败" if confirmed else "作品数据库提交状态不确定，可能已有作品引用新人物"
                    error = PersistenceRollbackError(
                        f"{reason}；为避免悬空引用，本次人物数据已保留，未回滚或删除。"
                        f"请重新读取并核对作品数据库及人物数据库后再重试：{self.root}。原错误：{failure}")
                    error.db_committed = True if confirmed else None
                    error.people_records_retained = True
                    report_exception(error, context={"stage": "人物与作品复合保存", "action": "保留人物依赖并请求核对提交状态",
                        "path": str(self.root), "db_committed": error.db_committed,
                        "reason": str(failure), "person_count": len(records)})
                    raise error from failure
                errors = []
                for write in reversed(writes):
                    try:
                        if self._bytes(write.target.stem) != write.content:
                            raise ValueError("存在后续修改，拒绝覆盖")
                        if write.previous is None:
                            # The exact validated UUID file was created by this
                            # transaction; never delete a folder or use a glob.
                            self.path(write.target.stem).unlink()
                        else:
                            atomic_write_bytes(self.path(write.target.stem), write.previous)
                    except Exception as exc:
                        errors.append(f"{write.target}: {exc}")
                if errors:
                    error = PersistenceRollbackError("作品保存失败且明确未提交，人物回滚不完整：" + "; ".join(errors))
                    error.db_committed = False
                    error.people_rollback_complete = False
                    raise error from failure
                raise
