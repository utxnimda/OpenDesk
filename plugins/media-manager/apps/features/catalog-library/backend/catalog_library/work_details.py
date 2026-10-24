"""Explicit clearing of one work's own descriptive fields, never related DBs."""
from copy import deepcopy
import hashlib
import time

from work_catalog_yaml.operation_progress import report_progress
from . import providers, service

GROUPS = (
    ("description", "介绍与别名", ("metadata.summary", "metadata.aliases", "metadata.tags")),
    ("cover", "作品封面", ("metadata.cover",)),
    ("chapters", "章节与播放对应缓存", ("metadata.episodes", "local_playback")),
    ("ratings", "作品评分", ("metadata.ratings",)),
    ("sources", "来源关联", ("source_refs", "source_policy_config")),
    ("people", "解除本作品的人物关联（不清空人物资料）", ("people_refs",)),
    ("classifications", "解除系列与分类关联（不删除分类）", ("classifications",)),
)


def _value(record, field):
    if field.startswith("metadata."):
        return record.get("metadata", {}).get(field.split(".", 1)[1])
    return record.get(field)


def _plan(record):
    return [{"id": identity, "label": label, "fields": list(fields),
             "selected": False, "present": any(_value(record, field) not in (None, "", [], {}) for field in fields),
             "counts": {field: len(value) if isinstance(value, (dict, list)) else int(bool(value))
                        for field in fields if (value := _value(record, field)) is not None}}
            for identity, label, fields in GROUPS]


def preview(body, *, settings, data_root=None):
    body = providers._body(body, {"ref"})
    item = providers._catalog_item(body.get("ref"), settings)
    providers._check_version(item, body["ref"])
    groups = _plan(item["record"])
    payload = {"kind": "work-details-clear", "ref": deepcopy(item["ref"]),
        "scope": providers._catalog_scope(settings), "plan_sha256": hashlib.sha256(providers._json_bytes(groups)).hexdigest(),
        "expires": int(time.time()) + providers.PREVIEW_LIFETIME}
    return {"ok": True, "ref": deepcopy(item["ref"]), "name": item.get("name", ""), "groups": groups,
        "preview_token": providers._sign(payload), "warnings": [
            "只处理这部作品明确选择的资料。作品名称、日期、收集记录、目录和媒体文件保持不变。",
            "解除人物或分类关联不会清空、删除公共人物资料或其他作品。已下载图片文件与历史备份保留。"]}


def apply(body, *, settings, data_root=None):
    body = providers._body(body, {"ref", "preview_token", "groups"})
    payload = providers._verify_token(body.get("preview_token"))
    if payload.get("kind") != "work-details-clear" or payload.get("ref") != body.get("ref") or payload.get("scope") != providers._catalog_scope(settings):
        raise ValueError("作品清空预览与当前作品或数据库不一致")
    item = providers._catalog_item(body["ref"], settings)
    providers._check_version(item, body["ref"])
    if hashlib.sha256(providers._json_bytes(_plan(item["record"]))).hexdigest() != payload.get("plan_sha256"):
        raise ValueError("作品资料在预览后已变化，请重新预览")
    selected = body.get("groups")
    allowed = {group["id"] for group in _plan(item["record"]) if group["present"]}
    if (not isinstance(selected, list) or not selected or len(selected) > len(GROUPS) or any(not isinstance(value, str) for value in selected)
            or len(set(selected)) != len(selected) or set(selected) - allowed):
        raise ValueError("请明确选择当前作品存在的资料分组；不能清空收集目录或其他数据库")
    record = deepcopy(item["record"])
    for identity, label, fields in GROUPS:
        if identity not in selected:
            continue
        for field in fields:
            if field.startswith("metadata."):
                record.get("metadata", {}).pop(field.split(".", 1)[1], None)
                record.get("metadata", {}).get("field_sources", {}).pop(field, None)
            elif field in {"people_refs", "classifications", "source_refs"}:
                # The shared writer deliberately preserves omitted extension
                # keys for legacy editors. Explicit empties express this clear.
                record[field] = []
            elif field == "local_playback":
                record[field] = {"version": 1, "resources": {}}
            else:
                record.pop(field, None)
        report_progress("待清空作品资料：" + item.get("name", "") + " / " + label,
            context={"action": "清空已选作品资料", "fields": list(fields), "name": item.get("name", "")})
    if "sources" in selected:
        record.get("metadata", {}).pop("field_sources", None)
        # An explicit empty policy prevents domain defaults from silently
        # restoring the user's deliberately removed source choices.
        record["source_refs"] = []
        record["source_policy_config"] = {"custom": True, "targets": []}
    result = service.apply_edits({"edits": [{"ref": item["ref"], "record": record}]}, settings=settings, data_root=data_root)
    report_progress(f"作品资料清空已保存：{item.get('name', '')}，共 {len(selected)} 个明确选择分组；人物库与媒体文件保持不变",
        context={"stage": "作品资料清空完成", "action": "保存作品资料清空", "name": item.get("name", "")})
    return {**result, "cleared_groups": selected, "warnings": [*result.get("warnings", []), "仅清空所选作品资料；公共人物库、收集目录与媒体文件未修改。"]}
