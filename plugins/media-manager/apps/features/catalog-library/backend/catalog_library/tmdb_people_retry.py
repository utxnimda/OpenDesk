"""Structured, bounded retry of failed TMDB people requests.

Successful raw responses and local portraits remain immutable inputs. Replaying
normal adaptation avoids a second, subtly different cast/crew interpretation.
"""
from __future__ import annotations

from copy import deepcopy
import re
import time
from threading import Lock

from work_catalog_yaml.operation_progress import report_progress

from . import providers, tmdb_source

_FAILURE_LOCK = Lock()


def _identity(path, language, field):
    if not isinstance(path, str) or not tmdb_source._PATH.fullmatch(path):
        raise ValueError("TMDB 失败重试路径无效")
    if not isinstance(language, str) or (language and not re.fullmatch(r"[a-z]{2,3}(?:-[A-Z]{2})?", language)):
        raise ValueError("TMDB 失败重试语言无效")
    if path.startswith("/person/"):
        kind = "person-avatar" if field == "avatar" else "person-detail"
    elif path.endswith("credits"):
        kind = "credits"
    elif "/season/" in path:
        kind = "season"
    else:
        kind = "subject"
    return kind, f"tmdb:{kind}:{path}:{language}"


def record_failure(failures, *, path, language, field, reason, warning):
    kind, identity = _identity(path, language, field)
    row = {"id": identity, "kind": kind, "path": path, "language": language,
           "object_id": path.rsplit("/", 1)[-1], "label": f"{path} · {field}",
           "field": field, "reason": str(reason)[:1000], "warning": warning,
           "retryable": True}
    with _FAILURE_LOCK:
        failures[:] = [previous for previous in failures if previous["id"] != identity]
        failures.append(row)
    return row


def label_failures(snapshot):
    """Attach only already-confirmed credit names, never infer identity by text."""
    fallback = snapshot.get("raw", {}).get("fallback", {})
    for row in snapshot.get("retry_failures", []):
        if row["kind"] in {"person-detail", "person-avatar"}:
            profile = fallback.get(row["object_id"], {})
            name = profile.get("name") or profile.get("original_name")
            if isinstance(name, str) and name.strip():
                row["label"] = f"{name[:1000]}（{row['object_id']}）· {row['field']}"


def _cached_requests(snapshot):
    raw = snapshot["raw"]
    subject_id, season = snapshot["subject_id"], snapshot["season_number"]
    cached = {(f"/tv/{subject_id}/season/{season}", "zh-CN"): raw["season"]}
    if raw.get("subject") is not None:
        cached[(f"/tv/{subject_id}", "zh-CN")] = raw["subject"]
    language = snapshot.get("original_language", "")
    language = "zh-CN" if language in {"zh", "zh-CN"} else language
    for key, query in (("credits", "zh-CN"), ("credits_original", language)):
        rows = raw.get(key, [])
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("TMDB 人物重试列表缓存无效")
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 3:
                raise ValueError("TMDB 人物重试列表缓存无效")
            if query:
                cached[(row[0], query)] = row[2]
    for identity, detail in raw["details"].items():
        if str(providers._positive_id(identity)) != identity:
            raise ValueError("TMDB 人物重试详情身份无效")
        cached[(f"/person/{identity}", "zh-CN")] = detail
    return cached


def retry_snapshot(snapshot, failure_ids=None, *, repository, deadline=None):
    from . import tmdb_people
    if not isinstance(snapshot, dict) or snapshot.get("provider") != "tmdb" or snapshot.get("kind") != "people":
        raise ValueError("仅支持 TMDB 人物快照中的失败项重试")
    tmdb_people.scope(snapshot.get("subject_id"), snapshot.get("season_number"), snapshot.get("episode_range"))
    raw = snapshot.get("raw")
    if not isinstance(raw, dict) or "season" not in raw or not isinstance(raw.get("details"), dict) or not isinstance(raw.get("fallback"), dict):
        raise ValueError("旧人物预览没有独立重试缓存；请重新获取一次人物资料")
    failures = snapshot.get("retry_failures", [])
    if not isinstance(failures, list) or len(failures) > 1000:
        raise ValueError("TMDB 人物重试失败项无效")
    indexed = {}
    for row in failures:
        if not isinstance(row, dict):
            raise ValueError("TMDB 人物重试失败项无效")
        kind, identity = _identity(row.get("path"), row.get("language"), row.get("field"))
        if row.get("id") != identity or row.get("kind") != kind or identity in indexed or row.get("retryable") is not True:
            raise ValueError("TMDB 人物重试失败身份无效")
        prefix = f"/tv/{snapshot['subject_id']}/season/{snapshot['season_number']}"
        if ((kind.startswith("person-") and row["path"].split("/")[-1] not in raw["fallback"])
                or (kind == "subject" and row["path"] != f"/tv/{snapshot['subject_id']}")
                or (kind == "season" and row["path"] != prefix)
                or (kind == "credits" and not row["path"].startswith(prefix + "/"))
                or row.get("object_id") != row["path"].rsplit("/", 1)[-1]):
            raise ValueError("TMDB 失败重试项不属于当前作品、季或人物")
        indexed[identity] = row
    if failure_ids is None:
        failure_ids = list(indexed)
    if (not isinstance(failure_ids, list) or not failure_ids or len(failure_ids) > 1000
            or any(not isinstance(identity, str) or identity not in indexed for identity in failure_ids)
            or len(set(failure_ids)) != len(failure_ids)):
        raise ValueError("请选择当前预览中至少一个未完成的 TMDB 失败项")
    selected = set(failure_ids)
    cached = _cached_requests(snapshot)
    failed_requests = {(row["path"], row["language"]): row for row in failures if row["kind"] != "person-avatar"}
    for key in failed_requests:
        cached.setdefault(key, None)
    deadline = min(time.monotonic() + tmdb_people.TOTAL_TIMEOUT, deadline) if deadline is not None else time.monotonic() + tmdb_people.TOTAL_TIMEOUT
    counts = {"reused": 0, "requested": 0, "succeeded": 0, "failed": 0}
    count_lock = Lock()
    report_progress(f"开始单独重试 {len(selected)} 个 TMDB 失败项；已成功数据与本地图片将复用", context={
        "stage": "TMDB 人物失败项重试", "action": "开始", "count": len(selected)})

    def replay(path, **kwargs):
        key = (path, kwargs.get("language", "zh-CN"))
        previous_failure = failed_requests.get(key)
        if previous_failure is not None and previous_failure["id"] not in selected:
            kwargs["warnings"].append(previous_failure["warning"])
            kwargs["failures"].append(deepcopy(previous_failure))
            return None
        if previous_failure is None and key in cached:
            with count_lock:
                counts["reused"] += 1
            return deepcopy(cached[key])
        with count_lock:
            counts["requested"] += 1
        report_progress(f"正在重试 TMDB {path} / {kwargs['field']}", context={
            "stage": "TMDB 人物失败项重试", "object": path, "action": kwargs["field"], "status": "running"})
        value = tmdb_people._optional(path, **kwargs)
        succeeded = value is not None
        with count_lock:
            counts["succeeded" if succeeded else "failed"] += 1
        report_progress(f"TMDB {path} / {kwargs['field']} 重试{'成功' if succeeded else '仍失败'}", context={
            "stage": "TMDB 人物失败项重试", "object": path, "action": kwargs["field"],
            "status": "success" if succeeded else "warning"})
        return value

    # Fetching the subject is necessary only when its original-language request
    # failed; all other already-known language metadata stays local.
    retry_language = any(row["kind"] == "subject" for row in failures) or raw["season"] is None
    result = tmdb_people.fetch_snapshot(snapshot["subject_id"], season_number=snapshot["season_number"],
        episode_range=snapshot.get("episode_range"), deadline=deadline,
        original_language=None if retry_language else snapshot.get("original_language", ""), _requester=replay)
    if not retry_language:
        result["raw"]["subject"] = deepcopy(raw.get("subject"))
    for key, value in raw.items():
        if key not in result["raw"]:
            result["raw"][key] = deepcopy(value)
    for key, value in snapshot.items():
        if key not in result and key not in {"avatars", "import_entries", "normalization_warnings", "_snapshot_id"}:
            result[key] = deepcopy(value)
    old_fetch_warnings = set(snapshot.get("fetch_warnings", [])) | set(snapshot.get("normalization_warnings", []))
    old_failure_warnings = {row["warning"] for row in failures}
    extra_warnings = [value for value in snapshot.get("warnings", []) if value not in old_fetch_warnings and value not in old_failure_warnings]
    result["warnings"].extend(extra_warnings)
    result["avatars"] = deepcopy(snapshot.get("avatars", {}))
    portrait_ids = set()
    for row in failures:
        if row["kind"] != "person-avatar":
            continue
        if row["id"] in selected:
            portrait_ids.add(row["object_id"])
        else:
            result["retry_failures"].append(deepcopy(row))
            result["warnings"].append(row["warning"])
    # Newly discovered people/images have never succeeded before. Existing
    # successful portraits are preserved even when fresh details change a URL.
    old_avatar_failures = {row["object_id"] for row in failures if row["kind"] == "person-avatar"}
    for identity, fallback in result["raw"]["fallback"].items():
        if result["avatars"].get(identity) or identity in old_avatar_failures:
            continue
        before = raw["details"].get(identity) or {}
        after = result["raw"]["details"].get(identity) or {}
        old_image = before.get("profile_path") or raw["fallback"].get(identity, {}).get("profile_path")
        new_image = after.get("profile_path") or fallback.get("profile_path")
        if new_image and (identity not in result["avatars"] or not old_image):
            portrait_ids.add(identity)
    tmdb_people.prepare_portraits(result, repository, deadline=deadline, identities=portrait_ids)
    label_failures(result)
    result["warnings"] = sorted(set(result["warnings"]))
    result["retry_failures"].sort(key=lambda row: row["id"])
    if len(providers._json_bytes(result)) > providers.MAX_SNAPSHOT_BYTES:
        raise ValueError("TMDB 人物重试快照超过安全大小限制")
    preserved_avatars = sum(bool(value) for key, value in snapshot.get("avatars", {}).items() if key not in portrait_ids)
    failed_portraits = sum(row["kind"] == "person-avatar" and row["object_id"] in portrait_ids for row in result["retry_failures"])
    report_progress(f"TMDB 失败项重试完成：本次请求 {counts['requested']} 项（成功 {counts['succeeded']}，仍失败 {counts['failed']}）；"
                    f"图片尝试 {len(portrait_ids)} 项（仍失败 {failed_portraits}）；"
                    f"复用 {counts['reused']} 项已成功请求及 {preserved_avatars} 张图片；待重试 {len(result['retry_failures'])} 项", context={
        "stage": "TMDB 人物失败项重试", "action": "结束", "status": "warning" if result["retry_failures"] else "success",
        "count": len(result["retry_failures"])})
    return result
