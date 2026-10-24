"""Desktop-owned single-page Douban rating reader; no people or app JS bridge.

Only the desktop shell supplies the window factory. API callers pass opaque
handles, never scripts or HTML. The fixed projection keeps only work identity,
title, score and vote count. Login/verification remains entirely user-operated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from html import escape
import json
import logging
import re
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import uuid4

MAX_CAPTURE_BYTES = 32 * 1024
MAX_WINDOWS = 4
CAPTURE_TIMEOUT = 8.0
LOAD_TIMEOUT = 30.0
_LOG = logging.getLogger("nimda.source_browser")
_PATH = re.compile(r"/subject/([1-9][0-9]{0,11})/?\Z")
_BLOCKED_TITLE = re.compile(r"登录豆瓣|豆瓣登录|安全验证|访问异常|验证码|captcha|access denied|robot check", re.I)
_MESSAGES = {
    "window-loading": "评分窗口正在打开，请稍后继续",
    "page-loading": "评分页面正在加载，请稍后继续",
    "capture-loading": "正在读取作品评分，请稍后继续",
    "capture-busy": "已有评分页面正在读取，请稍后继续",
    "load-timeout": "评分页面加载超时，请检查窗口；页面就绪后可继续，或重新打开条目",
    "capture-timeout": "评分读取超时，请关闭并重新打开来源窗口",
    "unexpected-url": "评分窗口不在所选作品页面；请完成登录或验证后返回原条目",
    "page-verification": "请在来源窗口自行完成登录或安全验证后继续；应用不会代填或绕过验证",
    "identity-mismatch": "页面身份与所选作品不一致，未采用评分",
    "rating-conflict": "页面中的有效评分或评分人数互相不一致，未采用评分；请等待页面加载完成后重试",
    "no-rating": "页面未提供有效作品评分；保留已有评分，可在页面就绪后重试",
    "projection-limit": "页面评分资料超过安全大小限制，未采用评分",
    "invalid-jsonld": "未读取到有效评分，且页面附加评分结构存在格式错误；保留已有评分，可在页面加载完成后重试",
    "javascript-unavailable": "浏览器未返回固定评分脚本的结果，请重新打开条目后重试",
    "invalid-payload": "浏览器返回的评分格式无效，未采用评分",
    "invalid-public-data": "评分未通过作品身份或数值校验，未采用评分",
    "native-error": "原生浏览器读取失败，请重新打开条目后重试",
    "handle-unavailable": "评分窗口登记暂时无法核实；请保留当前窗口并重试，应用未重新打开页面",
    "closed": "评分窗口已关闭",
}
_SCRIPT_STATES = {"page-loading": "loading", "page-verification": "verification", "unexpected-url": "verification",
                  "identity-mismatch": "failed", "rating-conflict": "failed", "no-rating": "failed", "projection-limit": "failed", "invalid-jsonld": "failed"}


def _state(status: str, url: str, code: str, *, reason_code: str | None = None) -> dict:
    return {"status": status, "url": url, "code": code, "message": _MESSAGES[code],
            "reason_code": reason_code or code.replace("-", "_")}


def _url(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048 or "\\" in value or any(ord(c) < 33 for c in value):
        raise ValueError("评分浏览器仅允许豆瓣 HTTPS 作品条目，不能读取人物或其他页面")
    try:
        parts = urlsplit(value)
    except ValueError:
        raise ValueError("评分浏览器地址无效") from None
    match = _PATH.fullmatch(parts.path)
    if (parts.scheme != "https" or parts.netloc != "movie.douban.com" or parts.query or parts.fragment
            or "?" in value or "#" in value or not match):
        raise ValueError("评分浏览器仅允许豆瓣 HTTPS 作品条目，不能读取人物或其他页面")
    return f"https://movie.douban.com/subject/{match[1]}/"


def _same_url(value: Any, expected: str) -> bool:
    try:
        return _url(value) == expected
    except (ValueError, TypeError):
        return False


_CAPTURE_SCRIPT = r"""(() => {
  'use strict';
  const expected = __EXPECTED_URL__;
  const reply = (status, code) => JSON.stringify({status, code});
  const canonical = value => {
    if (typeof value !== 'string' || !value || /[\\\s?#]/.test(value)) return '';
    try {
      const u = new URL(value, expected);
      const m = u.pathname.match(/^\/subject\/([1-9][0-9]{0,11})\/?$/);
      return u.protocol === 'https:' && u.host === 'movie.douban.com' && !u.username && !u.password && !u.search && !u.hash && m ?
        'https://movie.douban.com/subject/' + m[1] + '/' : '';
    } catch (_) { return ''; }
  };
  if (canonical(location.href) !== expected) return reply('verification','unexpected-url');
  if (document.readyState === 'loading') return reply('loading','page-loading');
  if (/登录豆瓣|豆瓣登录|安全验证|访问异常|验证码|captcha|access denied|robot check/i.test(document.title)) return reply('verification','page-verification');
  const challenge = document.querySelector('input[name*="captcha" i],input[name*="verifycode" i],form[action*="sec.douban.com"],form[action*="accounts.douban.com"]');
  if (challenge && (challenge.getClientRects().length || challenge.offsetWidth || challenge.offsetHeight)) return reply('verification','page-verification');
  const identityNodes = Array.from(document.querySelectorAll('link[rel="canonical"],meta[property="og:url"]'));
  if (identityNodes.length > 16) return reply('failed','projection-limit');
  const identities = identityNodes.map(el => canonical(el.getAttribute(el.tagName.toLowerCase() === 'link' ? 'href' : 'content')));
  if (identities.some(value => value !== expected)) return reply('failed','identity-mismatch');
  const text = el => el ? String(el.textContent || '').trim() : '';
  const scalar = value => typeof value === 'string' || (typeof value === 'number' && Number.isFinite(value)) ? String(value).trim() : '';
  const score = value => { const raw=scalar(value); return /^(?:\d(?:\.\d{1,3})?|10(?:\.0{1,3})?)$/.test(raw) && Number(raw)>0 && Number(raw)<=10 ? raw : ''; };
  const votes = value => { const raw=scalar(value).replace(/[,\s]/g,''); return /^\d{1,12}$/.test(raw) ? raw : ''; };
  const candidates = [];
  const areas = Array.from(document.querySelectorAll('#interest_sectl'));
  if (areas.length > 1) return reply('failed','identity-mismatch');
  if (areas.length) {
    const averages=Array.from(areas[0].querySelectorAll('[property="v:average"]'));
    const counts=Array.from(areas[0].querySelectorAll('[property="v:votes"]'));
    if (averages.length > 1 || counts.length > 1) return reply('failed','identity-mismatch');
    const rating=score(text(averages[0]));
    if (rating) candidates.push({rating, votes:votes(text(counts[0])), source:'dom'});
  }
  const scripts=Array.from(document.querySelectorAll('script[type="application/ld+json"]'));
  if (scripts.length>32) return reply('failed','projection-limit');
  let budget=0, visited=0, sourceName='', invalidJsonld=0;
  const inspect=(node,depth=0)=>{
    if (depth>6 || ++visited>128) throw new Error('limit');
    if (Array.isArray(node)) { node.forEach(value=>inspect(value,depth+1)); return; }
    if (!node || typeof node!=='object') return;
    if (Array.isArray(node['@graph'])) node['@graph'].forEach(value=>inspect(value,depth+1));
    const rawTypes=Array.isArray(node['@type']) ? node['@type'] : [node['@type']];
    if (!rawTypes.some(value=>typeof value==='string' && /^(?:https?:\/\/schema\.org\/)?(?:Movie|TVSeries)$/.test(value))) return;
    const idValues=[node.url,node['@id']].filter(value=>value!=null);
    if (idValues.length && idValues.some(value=>canonical(value)!==expected)) return;
    if (!idValues.length && !identities.length) return;
    const aggregate=node.aggregateRating;
    if (!aggregate || typeof aggregate!=='object' || Array.isArray(aggregate)) return;
    if (aggregate.bestRating!=null && Number(scalar(aggregate.bestRating))!==10) return;
    const rating=score(aggregate.ratingValue);
    if (!rating) return;
    if (typeof node.name==='string' && node.name.trim().length<=1000) sourceName=node.name.trim();
    candidates.push({rating,votes:votes(aggregate.ratingCount),source:'jsonld'});
  };
  for (const script of scripts) {
    const raw=script.textContent||'';
    budget+=raw.length;
    if (budget>1024*1024) return reply('failed','projection-limit');
    let parsed;
    // A broken optional metadata block is not evidence against an otherwise
    // valid visible score. Never repair it or extract numbers from raw text.
    try { parsed=JSON.parse(raw); } catch (error) {
      if (!(error instanceof SyntaxError)) return reply('failed','invalid-jsonld');
      invalidJsonld+=1; continue;
    }
    // Keep resource limits fail-closed: an incomplete inspection cannot prove
    // that all valid rating representations agree.
    try { inspect(parsed); } catch (_) { return reply('failed','projection-limit'); }
  }
  if (!candidates.length) return reply('failed',invalidJsonld ? 'invalid-jsonld' : 'no-rating');
  if (new Set(candidates.map(value=>Number(value.rating))).size!==1 || new Set(candidates.filter(value=>value.votes).map(value=>Number(value.votes))).size>1) return reply('failed','rating-conflict');
  const heading=text(document.querySelector('h1 span[property="v:itemreviewed"]'));
  const title=String(document.title||'').trim();
  if (!title || title.length>1000) return reply('failed','projection-limit');
  const row=candidates.find(value=>value.votes)||candidates[0];
  const ratingSource=Array.from(new Set(candidates.map(value=>value.source))).sort().join('+');
  return JSON.stringify({status:'ready',url:expected,title,name:heading.slice(0,1000)||sourceName||title,rating:row.rating,votes:row.votes,
    diagnostics:{jsonld_blocks:scripts.length,invalid_jsonld_blocks:invalidJsonld,rating_source:ratingSource}});
})()"""


def _projection_diagnostics(value: Any) -> dict:
    """Bounded counters only; never return raw page metadata or JS errors."""
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) != {"jsonld_blocks", "invalid_jsonld_blocks", "rating_source"}:
        raise ValueError("评分读取诊断字段无效")
    total, invalid = value["jsonld_blocks"], value["invalid_jsonld_blocks"]
    if (type(total) is not int or type(invalid) is not int or not 0 <= invalid <= total <= 32
            or not isinstance(value["rating_source"], str) or value["rating_source"] not in {"dom", "jsonld", "dom+jsonld"}):
        raise ValueError("评分读取诊断值无效")
    return dict(value)


def _sanitize_payload(data: dict, expected: str) -> str:
    if (set(data) - {"status", "url", "title", "name", "rating", "votes"} or data.get("status") != "ready"
            or not _same_url(data.get("url"), expected)):
        raise ValueError("评分投影身份或字段无效")
    title, name = data.get("title"), data.get("name")
    if (not isinstance(title, str) or not title.strip() or len(title) > 1000 or _BLOCKED_TITLE.search(title)
            or not isinstance(name, str) or not name.strip() or len(name) > 1000):
        raise ValueError("作品身份标题无效")
    value = data.get("rating")
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("评分无效")
    try:
        score = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("评分无效") from None
    if not score.is_finite() or not 0 < score <= 10:
        raise ValueError("评分范围无效")
    aggregate = {"@type": "AggregateRating", "ratingValue": float(score), "bestRating": 10}
    votes = data.get("votes", "")
    if votes != "":
        if isinstance(votes, bool) or not isinstance(votes, (str, int)) or not re.fullmatch(r"[0-9]{1,12}", str(votes)):
            raise ValueError("评分人数无效")
        aggregate["ratingCount"] = int(votes)
    node = {"@type": "Movie", "url": expected, "name": name, "aggregateRating": aggregate}
    encoded = json.dumps(node, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return ('<!doctype html><html><head><title>' + escape(title) + '</title><link rel="canonical" href="' +
            escape(expected, quote=True) + '"><script type="application/ld+json">' + encoded + '</script></head><body></body></html>')

@dataclass
class _Page:
    window: Any
    expected: str
    handle: str = ""
    epoch: int = 0
    close_reason: str = ""
    generation: int = 0
    closed: bool = False
    pending: bool = False
    started: float = 0.0
    load_started: float = field(default_factory=time.monotonic)
    result: dict | None = None
    lock: Any = field(default_factory=threading.RLock)


_lock = threading.RLock()
_factory: Callable | None = None
_pages: dict[str, _Page] = {}
# Ownership, unlike the active lookup index, lasts until native closure is
# confirmed. It also keeps the exact Window alive while destroy is in flight.
_owned: dict[str, _Page] = {}
_closed: OrderedDict[str, tuple[str, str, str]] = OrderedDict()
_CLOSED_LIMIT = 128
_CLOSE_REASONS = {"explicit_close", "application_shutdown", "session_expired", "session_replaced", "session_cleanup"}
_starting = 0
_epoch = 0
_capture_slots = threading.BoundedSemaphore(MAX_WINDOWS)


def configure(*, window_factory: Callable, main_window: Any = None) -> None:
    """Install a desktop-only factory; tests may supply an inert fake factory."""
    global _factory
    shutdown()
    with _lock:
        _factory = window_factory
        epoch = _epoch
    if main_window is not None:
        def main_closed(window=None, *_args):
            if window is None or window is main_window:
                shutdown(_expected_epoch=epoch)
        main_window.events.closed += main_closed


def available() -> bool:
    with _lock:
        return _factory is not None


def _event_set(window, name: str) -> bool:
    event = getattr(getattr(window, "events", None), name, None)
    return bool(event is not None and event.is_set())


def _diagnostic(action: str, page: _Page | None = None, *, handle: str = "", reason_code: str = "", status: str = "", window_uid: str = "") -> None:
    handle = page.handle if page is not None else handle
    uid = getattr(page.window, "uid", "") if page is not None else window_uid
    # Only generated handles and native identifiers, never page addresses,
    # remote exception text, tokens, titles, DOM or arbitrary caller strings.
    handle = handle if isinstance(handle, str) and re.fullmatch(r"[0-9a-f]{32}", handle) else "unavailable"
    uid = uid if isinstance(uid, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", uid) else "unavailable"
    _LOG.info("source_browser action=%s browser_handle=%s window_uid=%s reason_code=%s status=%s",
              action, handle, uid, reason_code or "none", status or "none")


def _native_state(page: _Page) -> str:
    """Inspect only this owned Window's pinned WinForms registry, never OS windows.

    pywebview 6.1 removes BrowserView.instances before dispatching closed.
    Event.set dispatches callbacks asynchronously, before setting its flag, so
    a false event flag alone is not proof of liveness or of closure.
    """
    window = page.window
    gui = getattr(window, "gui", None)
    registry = getattr(getattr(gui, "BrowserView", None), "instances", None)
    if getattr(gui, "renderer", None) == "edgechromium" and isinstance(registry, dict):
        native = registry.get(getattr(window, "uid", None))
        if native is not None and getattr(native, "pywebview_window", None) is window:
            if getattr(native, "IsDisposed", False) is True or getattr(native, "Disposing", False) is True:
                return "closed"
            return "alive"
        if _event_set(window, "shown") or _event_set(window, "closed"):
            return "closed"
    if _event_set(window, "closed"):
        return "closed"
    return "unknown"


def _remember_closed(page: _Page) -> None:
    uid = getattr(page.window, "uid", "")
    _closed[page.handle] = (page.expected, page.close_reason, uid if isinstance(uid, str) else "")
    _closed.move_to_end(page.handle)
    while len(_closed) > _CLOSED_LIMIT:
        _closed.popitem(last=False)


def _mark_closed(page: _Page, reason_code: str, *, native_confirmed: bool) -> None:
    """Called with _lock held; never let a stale callback erase another owner."""
    if _owned.get(page.handle) is not page:
        return
    with page.lock:
        if not page.closed:
            page.closed, page.close_reason = True, reason_code
            page.result = None
            page.generation += 1
        _remember_closed(page)
        if _pages.get(page.handle) is page:
            _pages.pop(page.handle)
        if native_confirmed:
            _owned.pop(page.handle)
    _diagnostic("native_closed" if native_confirmed else "close_requested", page,
                reason_code=page.close_reason, status="closed")


def _on_native_closed(page: _Page, window=None) -> None:
    with _lock:
        if _owned.get(page.handle) is not page or window is not None and window is not page.window:
            _diagnostic("close_event_ignored", page, reason_code="stale_registration")
            return
        native = _native_state(page)
        if native != "closed":
            _diagnostic("close_event_ignored", page,
                        reason_code="native_still_alive" if native == "alive" else "close_event_unconfirmed")
            return
        _mark_closed(page, page.close_reason or "native_closed", native_confirmed=True)


def open_page(url: str) -> str:
    global _starting
    expected = _url(url)
    with _lock:
        factory, epoch = _factory, _epoch
        if factory is None:
            raise ValueError("浏览器资料采集仅在作品管理器桌面应用中可用")
        if len(_owned) + _starting >= MAX_WINDOWS:
            raise ValueError("来源窗口数量已达上限，请先关闭已有来源窗口")
        _starting += 1
    window = None
    try:
        # Explicit None prevents accidental inheritance of any local app bridge.
        window = factory("作品管理器 · 豆瓣评分（登录或验证请自行完成）", expected,
                         width=1100, height=820, min_size=(720, 560), hidden=False,
                         text_select=True, js_api=None)
        if window is None:
            raise ValueError("无法打开来源浏览器窗口")
        handle = uuid4().hex
        page = _Page(window, expected, handle=handle, epoch=epoch)

        def closed(window=None, *_args):
            _on_native_closed(page, window)

        window.events.closed += closed
        with _lock:
            if epoch != _epoch or _factory is None:
                raise ValueError("桌面应用正在关闭")
            if any(owned.window is window for owned in _owned.values()):
                raise ValueError("来源浏览器返回已登记窗口，未更改现有登记")
            _owned[handle] = page
            _pages[handle] = page
            if _native_state(page) == "closed":
                _mark_closed(page, "native_closed", native_confirmed=True)
        _diagnostic("registered", page, reason_code="window_registered")
        return handle
    except Exception:
        with _lock:
            already_owned = any(owned.window is window for owned in _owned.values())
        if window is not None and not already_owned:
            window.destroy()
        raise
    finally:
        with _lock:
            _starting -= 1


def _current_url(window) -> str | None:
    # pywebview 6.1 Window.get_current_url waits for the JS bridge's loaded
    # event. The pinned Edge backend's getter only reads its native URL cache,
    # so a failed bridge injection must not block our bridge-free fixed script.
    gui = getattr(window, "gui", None)
    if gui is not None and getattr(gui, "renderer", None) == "edgechromium":
        return gui.get_current_url(window.uid)
    return window.get_current_url()


def _changed_url(actual, expected: str) -> dict:
    return _state("verification", expected, "unexpected-url")


def _bounded_loading(page: _Page, result: dict) -> dict:
    if result.get("status") == "loading" and time.monotonic() - page.load_started > LOAD_TIMEOUT:
        return _state("failed", page.expected, "load-timeout")
    return result


def _capture_once(page: _Page, expected: str) -> dict:
    result = _state("loading", expected, "page-loading")
    if not _event_set(page.window, "shown"):
        return result
    before = _current_url(page.window)
    if not before or before == "about:blank":
        return result
    if not _same_url(before, expected):
        return _changed_url(before, expected)
    # run_js's actual 6.1 decorator requires before_load, not loaded or bridge
    # readiness. Do not invoke it before that event (the decorator waits 20s).
    if not _event_set(page.window, "before_load"):
        return result
    raw = page.window.run_js(_CAPTURE_SCRIPT.replace("__EXPECTED_URL__", json.dumps(expected)))
    after = _current_url(page.window)
    if not _same_url(after, expected):
        return _changed_url(after, expected)
    if not isinstance(raw, str):
        return _state("failed", expected, "javascript-unavailable")
    if len(raw.encode("utf-8")) > MAX_CAPTURE_BYTES:
        return _state("failed", expected, "projection-limit")
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        return _state("failed", expected, "invalid-payload")
    if not isinstance(data, dict):
        return _state("failed", expected, "invalid-payload")
    if data.get("status") != "ready":
        code = data.get("code")
        # Older inert adapters may omit codes. Their text is still never used.
        if not code:
            code = {"loading": "page-loading", "verification": "page-verification"}.get(data.get("status"))
        if isinstance(code, str) and _SCRIPT_STATES.get(code) == data.get("status"):
            return _state(data["status"], expected, code)
        return _state("failed", expected, "invalid-payload")
    try:
        diagnostics = _projection_diagnostics(data.get("diagnostics"))
        html = _sanitize_payload({key: value for key, value in data.items() if key != "diagnostics"}, expected)
    except (ValueError, RecursionError):
        return _state("failed", expected, "invalid-public-data")
    return {"status": "ready", "url": expected, "html": html, "reason_code": "rating_ready",
            **({"diagnostics": diagnostics} if diagnostics else {})}


def _capture_worker(page: _Page, expected: str, generation: int) -> None:
    try:
        result = _capture_once(page, expected)
    except Exception:
        # Never leak remote JS exceptions, tokens, raw page URLs or HTML to logs.
        result = _state("failed", expected, "native-error")
    with page.lock:
        page.pending = False
        if not page.closed and generation == page.generation:
            page.result = result
    _capture_slots.release()


def capture(handle: str) -> dict:
    """Poll bounded capture; never block an API worker on loading or verification."""
    result = _capture(handle)
    with _lock:
        page = _owned.get(handle) if isinstance(handle, str) else None
        uid = _closed.get(handle, ("", "", ""))[2] if isinstance(handle, str) else ""
    _diagnostic("capture", page, handle=handle, window_uid=uid,
                reason_code=result.get("reason_code", ""), status=result["status"])
    return result


def _capture(handle: str) -> dict:
    with _lock:
        page = _owned.get(handle) if isinstance(handle, str) else None
        if page is None:
            terminal = _closed.get(handle) if isinstance(handle, str) else None
            if terminal is None:
                # An unknown handle says nothing about the native window. Only
                # an owned-window tombstone is evidence for a closed response.
                return _state("failed", "", "handle-unavailable", reason_code="handle_missing")
            expected, reason, _uid = terminal
            return _state("closed", expected, "closed", reason_code=reason)
        if not page.closed:
            native = _native_state(page)
            if native == "closed":
                _mark_closed(page, "native_closed", native_confirmed=True)
            elif page.epoch != _epoch or _factory is None:
                _mark_closed(page, "application_shutdown", native_confirmed=False)
            elif _pages.get(handle) is not page:
                # Only the exact independently owned, still-native Window can
                # recover its existing handle. Never search or open a window.
                if native == "alive" and _pages.get(handle) is None:
                    _pages[handle] = page
                    _diagnostic("handle_restored", page, reason_code="same_native_window")
                else:
                    return _state("failed", page.expected, "handle-unavailable", reason_code="handle_unconfirmed")
    with page.lock:
        if page.closed:
            return _state("closed", page.expected, "closed", reason_code=page.close_reason)
        if page.result is not None:
            result, page.result = page.result, None
            return _bounded_loading(page, result)
        if page.pending:
            timed_out = time.monotonic() - page.started > CAPTURE_TIMEOUT
            return _state("failed", page.expected, "capture-timeout") if timed_out else _state("loading", page.expected, "capture-loading")
        if not _event_set(page.window, "shown"):
            return _bounded_loading(page, _state("loading", page.expected, "window-loading"))
        if not _capture_slots.acquire(blocking=False):
            return _bounded_loading(page, _state("loading", page.expected, "capture-busy"))
        page.pending, page.started = True, time.monotonic()
        try:
            threading.Thread(target=_capture_worker, args=(page, page.expected, page.generation),
                             name="nimda-source-capture", daemon=True).start()
        except Exception:
            page.pending = False
            _capture_slots.release()
            raise
        return _state("loading", page.expected, "capture-loading")


def close_page(handle: str, *, reason_code: str = "explicit_close") -> None:
    if not isinstance(reason_code, str) or reason_code not in _CLOSE_REASONS:
        raise ValueError("来源窗口关闭原因无效")
    with _lock:
        page = _owned.get(handle)
        if page is not None:
            _mark_closed(page, reason_code, native_confirmed=False)
    if page is None:
        return
    page.window.destroy()
    with _lock:
        if _native_state(page) == "closed":
            _mark_closed(page, reason_code, native_confirmed=True)


def shutdown(*, _expected_epoch: int | None = None) -> None:
    """Disable new windows and destroy every owned source window before server stop."""
    global _factory, _epoch
    with _lock:
        if _expected_epoch is not None and _expected_epoch != _epoch:
            return
        _factory = None
        _epoch += 1
        handles = list(_owned)
    for handle in handles:
        try:
            close_page(handle, reason_code="application_shutdown")
        except Exception:
            _diagnostic("close_failed", handle=handle, reason_code="application_shutdown", status="failed")
