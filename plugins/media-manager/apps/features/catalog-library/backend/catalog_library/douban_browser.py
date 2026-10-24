"""One-page desktop rating previews; never collect people or commit work data.

The API accepts catalog/subject identities only. HTML comes exclusively from
the desktop-owned, allowlisted rating projection, never from request bodies.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Lock, RLock
import re
import time
from uuid import uuid4

from work_catalog_yaml.common import source_browser
from work_catalog_yaml.operation_progress import report_progress

from . import douban_source, providers, service


SESSION_LIFETIME = 30 * 60
_lock = RLock()
_sessions = {}


@dataclass
class _Session:
    identity: str
    body: dict
    scope: str
    handle: str
    created: float = field(default_factory=time.monotonic)
    snapshot: dict | None = None
    preview: dict | None = None
    cancelled: bool = False
    busy: Lock = field(default_factory=Lock)


def _report(row, message, *, action, status, code):
    report_progress(message, completed=int(row.preview is not None and not row.cancelled), total=1, unit="页面",
                    context={"stage": "豆瓣评分读取", "action": action, "status": status, "code": code,
                             "object": row.identity, "actual": row.handle,
                             "name": f"豆瓣作品 {row.body['subject_id']}",
                             "source_path": douban_source.source_url(row.body["subject_id"])})


def _report_capture_diagnostics(row, captured):
    """Log fixed projection facts only, never arbitrary browser fields or HTML."""
    diagnostic = captured.get("diagnostics")
    if not isinstance(diagnostic, dict):
        return
    blocks, skipped = diagnostic.get("jsonld_blocks"), diagnostic.get("invalid_jsonld_blocks")
    source = diagnostic.get("rating_source")
    labels = {"dom": "页面评分区域", "jsonld": "结构化评分数据", "dom+jsonld": "页面评分与结构化评分一致"}
    if (type(blocks) is not int or type(skipped) is not int or not 0 <= skipped <= blocks <= 32
            or not isinstance(source, str) or source not in labels):
        return
    _report(row, f"评分读取校验通过；获取方式：{labels[source]}；JSON-LD 共 {blocks} 块；"
            f"跳过语法无效的附加 JSON-LD {skipped} 块，保留已校验的有效评分",
            action="capture", status="ready",
            code="rating_ready_invalid_jsonld_skipped" if skipped else "rating_ready_diagnostics")


def _close(row, *, reason_code="session_cleanup"):
    if row.handle:
        _report(row, "结束当前评分窗口会话", action="close", status="closed", code=reason_code)
        handle, row.handle = row.handle, ""
        source_browser.close_page(handle, reason_code=reason_code)


def _trim():
    with _lock:
        expired = [row for row in _sessions.values() if time.monotonic() - row.created > SESSION_LIFETIME]
        completed = sorted((row for row in _sessions.values() if row.preview), key=lambda row: row.created)
        for row in [*expired, *completed[:-4]]:
            if row.busy.acquire(blocking=False):
                try:
                    _sessions.pop(row.identity, None)
                    _close(row, reason_code="session_expired" if row in expired else "session_cleanup")
                finally:
                    row.busy.release()


def _result(row, status, message, *, auto=False, action="continue", code=""):
    if row.cancelled:
        status, message, auto = "closed", "已取消评分读取，未保存作品数据", False
        code = "session_cancelled"
    code = code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", code) else "rating_" + status
    value = {"ok": True, "result_type": "douban-rating-read", "session_id": row.identity, "kind": "work", "ref": deepcopy(row.body["ref"]),
             "status": status, "code": code, "message": message, "completed": int(status == "complete"), "total": 1,
             "source_url": douban_source.source_url(row.body["subject_id"]), "auto_continue": auto,
             "can_skip": False, "can_finish": False}
    if status in {"failed", "verification", "closed"}:
        value["warnings"] = [{"message": message, "stage": "豆瓣评分读取", "code": code,
                              "object": row.identity, "source_path": value["source_url"]}]
    if row.preview is not None and not row.cancelled:
        value["preview"] = deepcopy(row.preview)
    _report(row, message, action=action, status=status, code=code)
    return value


def _current(row, settings):
    ref = row.body["ref"]
    item = next((item for item in service._records(settings)
                 if (item["ref"]["yaml_source_rel"], item["ref"]["index_in_file"]) ==
                    (ref["yaml_source_rel"], ref["index_in_file"])), None)
    if item is None:
        raise ValueError("作品已不存在，请重新打开评分读取")
    providers._validate_local_extensions(item["record"], item.get("work_key", ""))
    providers._check_version(item, ref)
    return item


def start(body, *, settings, source_root=None, people_root=None, data_root=None):
    body = providers._body(body, {"ref", "kind", "subject_id", "replace_binding_id", "binding_role"})
    if body.get("kind", "work") != "work":
        raise ValueError("豆瓣仅支持作品评分，不支持人物采集")
    item = providers._catalog_item(body.get("ref"), settings)
    providers._check_version(item, body["ref"])
    identity = providers._positive_id(body.get("subject_id"))
    if body.get("binding_role", "supplement") != "supplement":
        raise ValueError("豆瓣评分只能作为补充来源")
    if not source_browser.available():
        raise ValueError("浏览器评分读取仅在新版桌面应用中可用；请重新打包后打开桌面版")
    _trim()
    with _lock:
        if any(not row.preview and not row.cancelled for row in _sessions.values()):
            raise ValueError("已有评分读取窗口，请先完成或取消")
        clean = {**deepcopy(body), "subject_id": identity, "kind": "work", "binding_role": "supplement"}
        handle = source_browser.open_page(douban_source.source_url(identity))
        row = _Session(str(uuid4()), clean, providers._catalog_scope(settings), handle)
        _sessions[row.identity] = row
    return _result(row, "loading", "正在打开豆瓣作品页，仅获取评分和评分人数；登录或安全验证请自行完成",
                   auto=True, action="start", code="window_opened")


def _get(body, settings, *, acquire=True):
    _trim()
    identity = body.get("session_id")
    if not isinstance(identity, str) or not identity or len(identity) > 64:
        raise ValueError("评分读取会话标识无效")
    with _lock:
        row = _sessions.get(identity)
    if row is None or row.scope != providers._catalog_scope(settings) or body.get("ref") != row.body["ref"]:
        raise ValueError("评分会话已过期或不属于当前作品，请重新打开")
    if acquire and not row.busy.acquire(blocking=False):
        raise ValueError("评分读取尚未完成，请稍后继续")
    return row


def cancel(body, *, settings, source_root=None, people_root=None, data_root=None):
    body = providers._body(body, {"ref", "session_id"})
    row = _get(body, settings, acquire=False)
    with _lock:
        row.cancelled = True
        _sessions.pop(row.identity, None)
    _close(row, reason_code="explicit_close")
    return _result(row, "closed", "已取消评分读取", action="cancel")


def _finish(row, *, settings, source_root):
    if row.cancelled:
        return _result(row, "closed", "已取消评分读取")
    _current(row, settings)
    body = {key: deepcopy(row.body[key]) for key in ("ref", "subject_id", "replace_binding_id", "binding_role") if key in row.body}
    row.preview = providers.preview_provider_import({**body, "provider": "douban"}, settings=settings,
                                                   source_root=source_root, _source_snapshot=deepcopy(row.snapshot))
    _close(row)
    return _result(row, "complete", "已生成评分预览，尚未保存作品数据", code="preview_ready")


def step(body, *, settings, source_root=None, people_root=None, data_root=None):
    body = providers._body(body, {"ref", "session_id", "action"})
    action = body.get("action", "continue")
    if not isinstance(action, str) or action not in {"continue", "reopen"}:
        raise ValueError("评分读取仅支持继续或重新打开当前作品页")
    row = _get(body, settings)
    try:
        try:
            _current(row, settings)
        except (ValueError, OSError):
            _close(row)
            with _lock:
                _sessions.pop(row.identity, None)
            raise
        if row.cancelled:
            return _result(row, "closed", "已取消评分读取")
        if row.preview is not None:
            return _result(row, "complete", "已生成评分预览，尚未保存作品数据")
        if action == "reopen":
            _close(row, reason_code="session_replaced")
            row.snapshot = None
            handle = source_browser.open_page(douban_source.source_url(row.body["subject_id"]))
            with _lock:
                keep = not row.cancelled and _sessions.get(row.identity) is row
                if keep:
                    row.handle = handle
            if not keep:
                source_browser.close_page(handle, reason_code="session_cleanup")
                return _result(row, "closed", "已取消评分读取")
            return _result(row, "loading", "正在重新打开所选豆瓣作品页", auto=True, action="reopen", code="window_reopened")
        if row.snapshot is not None:
            return _finish(row, settings=settings, source_root=source_root)
        if not row.handle:
            return _result(row, "closed", "评分窗口会话已结束，请重新打开或取消", code="session_handle_missing")
        captured = source_browser.capture(row.handle)
        if row.cancelled:
            return _result(row, "closed", "已取消评分读取")
        status = captured.get("status")
        if status != "ready":
            status = status if status in {"loading", "verification", "closed"} else "failed"
            message = captured.get("message") or {
                "loading": "页面正在加载，评分尚未读取",
                "verification": "请在豆瓣窗口中正常完成登录或验证，然后点击继续读取",
                "closed": "评分窗口已关闭，请重新打开或取消",
                "failed": "当前页面未能读取评分，请核对后重试",
            }[status]
            return _result(row, status, str(message)[:1000], auto=status == "loading",
                           code=captured.get("reason_code") or captured.get("code"))
        try:
            if captured.get("url") != douban_source.source_url(row.body["subject_id"]):
                raise ValueError("浏览器当前页面与所选作品不一致，请返回所选条目")
            snapshot = douban_source.parse_html(captured.get("html"), row.body["subject_id"])
            if not snapshot.get("candidates", {}).get("metadata.ratings.douban"):
                raise ValueError("当前作品页没有有效评分；保留本地评分，不以零分覆盖")
            snapshot["source_metadata"].update(evidence_kind="browser_page", attribution="豆瓣浏览器评分（用户发起读取）")
            row.snapshot = snapshot
        except (ValueError, OSError) as error:
            return _result(row, "failed", str(error)[:1000], code="rating_validation_failed")
        _report_capture_diagnostics(row, captured)
        return _finish(row, settings=settings, source_root=source_root)
    finally:
        row.busy.release()


def shutdown():
    with _lock:
        rows = list(_sessions.values())
        _sessions.clear()
    for row in rows:
        row.cancelled = True
        _close(row, reason_code="application_shutdown")
