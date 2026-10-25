"""Session statistics for observed game actions, never estimates from save deltas.

The capture adapter supplies ordered events after the user-selected start point.
Full work/attack output and effective progress/HP loss have separate ownership.
Only the most recent event details are retained; totals cover every accepted event.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from copy import deepcopy
import hashlib
import hmac
import json
import math
import secrets
import time
import uuid
from typing import Any
from .damage_formula import explain_formula, validate_formula
from .work_formula import explain_formula as explain_work_formula, validate_formula as validate_work_formula

MAX_PEOPLE = 512
MAX_PRESENTATION_GROUPS = 1024
MAX_PERSON_BYTES = 4096
MAX_PEOPLE_BYTES = 256 * 1024
MAX_SUMMARY_TEXT_BYTES = 768 * 1024
MAX_DETAIL_BYTES = 256 * 1024
MAX_LATEST_DETAIL_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_PAYLOAD_BYTES = 12 * 1024
MAX_ACTION_IDS = 8192
DAMAGE_TYPES = ("physical", "magic", "piercing", "unknown")
ATTACK_KINDS = ("basic", "skill", "periodic", "unknown")
WORK_ORDER = ("伐木", "采矿", "种植", "研究", "操练", "锻造", "工艺", "炼丹", "建造", "城墙修复")


class StatisticsError(ValueError):
    pass


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1e100 or not math.isfinite(value):
        raise StatisticsError(f"采集事件的{name}无效，统计已停止。")
    return float(value)


def _text(value: Any, name: str, limit: int = 100) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise StatisticsError(f"采集事件的{name}无效，统计已停止。")
    return value


def _sequence(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**53 - 1:
        raise StatisticsError("采集事件序号无效，统计已停止。")
    return value


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _person(value: Any) -> dict[str, Any]:
    text_fields = {"professionId", "profession", "professionDescription", "appearance", "iconKey", "job"}
    if not isinstance(value, dict) or set(value) - (text_fields | {"attributes", "equipment"}):
        raise StatisticsError("人物快照字段无效。")
    normalized = {key: _text(value[key], "人物详情", 128) for key in text_fields if key in value}
    if "iconKey" in normalized and not normalized["iconKey"].startswith("person:"):
        raise StatisticsError("人物头像标识无效。")
    for field, limit in (("attributes", 16), ("equipment", 6)):
        if field not in value:
            continue
        entries = value[field]
        if not isinstance(entries, list) or len(entries) > limit:
            raise StatisticsError("人物属性或装备数量超出支持范围。")
        result = []
        for entry in entries:
            if field == "attributes":
                if not isinstance(entry, dict) or set(entry) != {"id", "label", "value"}:
                    raise StatisticsError("人物属性字段无效。")
                attribute_value = entry["value"]
                if type(attribute_value) not in (int, float) or not -1e100 <= attribute_value <= 1e100 or not math.isfinite(attribute_value):
                    raise StatisticsError("人物属性数值无效。")
                result.append({"id": _text(entry["id"], "人物属性编号", 64),
                               "label": _text(entry["label"], "人物属性名称", 64), "value": float(attribute_value)})
            else:
                if not isinstance(entry, dict) or set(entry) - {"slot", "name", "iconKey", "type", "level", "qualityName"} or not {"slot", "name"} <= entry.keys():
                    raise StatisticsError("人物装备字段无效。")
                item = {key: _text(item_value, "人物装备", 100) for key, item_value in entry.items() if key != "level"}
                if "level" in entry:
                    item["level"] = _number(entry["level"], "装备等级")
                if "iconKey" in item and not item["iconKey"].startswith("item:"):
                    raise StatisticsError("装备图标标识无效。")
                result.append(item)
        normalized[field] = result
    if _json_size(normalized) > MAX_PERSON_BYTES:
        raise StatisticsError("人物快照超出大小限制。")
    return normalized


def validate_event(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict) or type(event.get("schemaVersion")) is not int or event["schemaVersion"] != 1:
        raise StatisticsError("不支持的采集事件版本。")
    required = {"schemaVersion", "sequence", "kind", "personId", "personName", "category", "amount"}
    optional = {"effectiveAmount", "gameTime", "targetName", "formula", "formulaCoverage", "workFormula",
                "person", "attackKind", "damageType", "skillId", "skillName", "actionId"}
    if set(event) - (required | optional) or required - set(event):
        raise StatisticsError("采集事件字段不完整或包含未知字段。")
    if event["kind"] not in ("work", "damage"):
        raise StatisticsError("未知的工作或战斗事件。")
    row = dict(event)
    row["sequence"] = _sequence(event["sequence"])
    for key, label in (("personId", "人物编号"), ("personName", "人物名称"), ("category", "工作或攻击类型")):
        row[key] = _text(event[key], label)
    row["amount"] = _number(event["amount"], "完整输出")
    if "effectiveAmount" in row:
        row["effectiveAmount"] = _number(row["effectiveAmount"], "有效输出")
        if row["effectiveAmount"] > row["amount"]:
            raise StatisticsError("有效输出大于完整输出，采集口径不一致。")
    if "gameTime" in row:
        row["gameTime"] = _number(row["gameTime"], "游戏时间")
    if "targetName" in row:
        row["targetName"] = _text(row["targetName"], "目标名称")
    if "person" in row:
        row["person"] = _person(row["person"])
    if row["kind"] == "work" and any(key in row for key in ("attackKind", "damageType", "skillId", "skillName", "actionId")):
        raise StatisticsError("工作事件不能携带战斗分类。")
    if row["kind"] == "damage":
        for field, choices in (("attackKind", ATTACK_KINDS), ("damageType", DAMAGE_TYPES)):
            if row.get(field, "unknown") not in choices:
                raise StatisticsError("战斗分类未经支持。")
            row[field] = row.get(field, "unknown")
        for field in ("skillId", "skillName", "actionId"):
            if field in row:
                row[field] = _text(row[field], "攻击身份", 100)
    if "workFormula" in row:
        if row["kind"] != "work":
            raise StatisticsError("只有工作事件可以携带工作公式。")
        try:
            row["workFormula"] = validate_work_formula(row["workFormula"])
        except ValueError as error:
            raise StatisticsError(str(error)) from error
    coverage = row.get("formulaCoverage", "unavailable")
    if coverage not in ("main-product", "special-return", "stored-buff-value-only", "unavailable"):
        raise StatisticsError("伤害公式覆盖类型无效。")
    if "formula" in row:
        if row["kind"] != "damage" or coverage != "main-product":
            raise StatisticsError("只有主伤害事件可以携带当前公式快照。")
        try:
            row["formula"] = validate_formula(row["formula"])
        except ValueError as error:
            raise StatisticsError(str(error)) from error
    elif coverage == "main-product":
        raise StatisticsError("主伤害公式快照缺失。")
    if row["kind"] == "damage":
        row["formulaCoverage"] = coverage
    if _json_size(row) > 8192:
        raise StatisticsError("单条采集事件超出大小限制。")
    return row


def _metric(**fields) -> dict[str, Any]:
    return {**fields, "amount": 0.0, "count": 0, "latestAmount": 0.0, "latestSequence": None}


def _add(metric: dict[str, Any], event: dict[str, Any]) -> None:
    metric["amount"] += event["amount"]
    metric["count"] += 1
    metric["latestAmount"] = event["amount"]
    metric["latestSequence"] = event["sequence"]


def _display_metric(metric: dict[str, Any], elapsed: float) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in metric.items() if not key.startswith("_")} | {
        "perRealSecond": metric["amount"] / elapsed if elapsed > 0 else None,
        "averageAmount": metric["amount"] / metric["count"] if metric["count"] else None}


def validate_latest_selector(value: Any) -> dict[str, Any]:
    """Identity of one displayed aggregate, never a replacement for a history ID."""
    if not isinstance(value, dict):
        raise StatisticsError("最近事件选择器必须是对象。")
    kind = value.get("kind")
    expected = {"kind", "personId", "category"} if kind == "work" else {"kind", "personId", "attackKind", "skillId"}
    if kind not in ("work", "damage") or set(value) != expected:
        raise StatisticsError("最近事件选择器字段不完整或含有未知字段。")
    text_fields = ("personId", "category") if kind == "work" else ("personId",)
    for field in text_fields:
        text = value[field]
        if not isinstance(text, str) or not text.strip() or len(text) > 100 or not text.isprintable():
            raise StatisticsError("最近事件的人物或分类标识无效。")
    if kind == "damage":
        skill = value["skillId"]
        if value["attackKind"] not in ATTACK_KINDS or (skill is not None and (
                not isinstance(skill, str) or not skill.strip() or len(skill) > 100 or not skill.isprintable())):
            raise StatisticsError("最近攻击的类别或技能编号无效。")
    return dict(value)


def _latest_key(selector: dict[str, Any]) -> tuple[str | None, ...]:
    if selector["kind"] == "work":
        return ("work", selector["personId"], selector["category"])
    return ("damage", selector["personId"], selector["attackKind"], selector["skillId"])


def validate_detail_snapshot(value: Any) -> dict[str, Any]:
    """Validate the opaque transport capsule without trusting or parsing it."""
    if (not isinstance(value, dict) or set(value) != {"schemaVersion", "payload", "signature"}
            or type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1):
        raise StatisticsError("事件快照字段或版本无效。")
    payload, signature = value["payload"], value["signature"]
    if (not isinstance(payload, str) or not payload or not isinstance(signature, str)
            or len(signature) != 64 or any(character not in "0123456789abcdef" for character in signature)):
        raise StatisticsError("事件快照内容或签名格式无效。")
    try:
        size = len(payload.encode("utf-8"))
    except UnicodeEncodeError:
        raise StatisticsError("事件快照不是有效的 UTF-8 文本。") from None
    if size > MAX_SNAPSHOT_PAYLOAD_BYTES:
        raise StatisticsError("事件快照超出大小限制。")
    return dict(value)


class OutputStatistics:
    """In-memory authority for one explicit recording interval."""

    def __init__(self, *, clock=time.monotonic, detail_limit: int = 500):
        if not 1 <= detail_limit <= 1000:
            raise ValueError("detail_limit must be between 1 and 1000")
        self._clock = clock
        self._events: deque[dict[str, Any]] = deque(maxlen=detail_limit)
        self._event_sizes: deque[int] = deque(maxlen=detail_limit)
        self._detail_bytes = 0
        self._latest_events: OrderedDict[tuple[str | None, ...], tuple[dict[str, Any], int]] = OrderedDict()
        self._latest_detail_bytes = 0
        self._totals: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._people: dict[str, dict[str, Any]] = {}
        self._person_sizes: dict[str, int] = {}
        self._presentation_groups: set[tuple[str, ...]] = set()
        self._summary_text_sizes: dict[tuple[str, ...], int] = {}
        self._action_ids: dict[tuple[str, str, str | None], set[str]] = {}
        self._action_id_count = 0
        self._state = "idle"
        self._reason = ""
        self._stream = ""
        self._sequence = 0
        self._started = self._ended = 0.0
        self._count = 0
        self._session = ""
        # One authority per worker-owned directory instance. Starting another
        # interval keeps receipts usable; discarded instances cannot verify them.
        self._snapshot_secret = secrets.token_bytes(32)

    @property
    def state(self) -> str:
        return self._state

    @property
    def last_sequence(self) -> int:
        return self._sequence

    def start(self, stream: str, sequence: int) -> None:
        stream = _text(stream, "采集会话", 128)
        sequence = _sequence(sequence)
        if self._state == "running":
            raise StatisticsError("统计正在进行，请先停止当前计时。")
        self._stream, self._sequence = stream, sequence
        self._session = str(uuid.uuid4())
        self._events.clear()
        self._event_sizes.clear()
        self._detail_bytes = 0
        self._latest_events.clear()
        self._latest_detail_bytes = 0
        self._totals.clear()
        self._people.clear()
        self._person_sizes.clear()
        self._presentation_groups.clear()
        self._summary_text_sizes.clear()
        self._action_ids.clear()
        self._action_id_count = 0
        self._state, self._reason, self._count = "running", "", 0
        self._started = self._clock()
        self._ended = self._started

    def interrupt(self, reason: str) -> None:
        if self._state == "running":
            self._state = "interrupted"
            self._reason = reason
            self._ended = self._clock()

    def stop(self) -> None:
        if self._state == "running":
            self._state = "stopped"
            self._ended = self._clock()

    def accept(self, stream: str, events: list[dict[str, Any]]) -> None:
        if self._state != "running":
            raise StatisticsError("没有正在计时的采集会话。")
        try:
            if stream != self._stream:
                raise StatisticsError("游戏采集会话已改变，请重新开始计时。")
            if not isinstance(events, list) or len(events) > 1000:
                raise StatisticsError("采集批次超出上限，无法保证统计完整。")
            rows = [validate_event(row) for row in events]
            for offset, row in enumerate(rows, 1):
                if row["sequence"] != self._sequence + offset:
                    raise StatisticsError("采集事件缺失或顺序异常，当前结果只覆盖中断前的完整区间。")
            keys = {(r["kind"], r["personId"], r["category"]) for r in rows}
            if len(keys | self._totals.keys()) > 512:
                raise StatisticsError("统计人物或类型超过支持范围。")
            if len({row["personId"] for row in rows} | self._people.keys()) > MAX_PEOPLE:
                raise StatisticsError("统计人物超过 512 人，当前结果保留到中断前。")
            person_sizes = dict(self._person_sizes)
            new_groups = set()
            for row in rows:
                if "person" in row:
                    person_sizes[row["personId"]] = _json_size(row["person"])
                if row["kind"] == "work":
                    new_groups.add(("work", row["personId"], row["category"]))
                else:
                    new_groups.add(("type", row["personId"], row["damageType"]))
                    new_groups.add(("attack", row["personId"], row["attackKind"], row.get("skillId")))
            if sum(person_sizes.values()) > MAX_PEOPLE_BYTES:
                raise StatisticsError("人物详情总量超出支持范围，当前结果保留到中断前。")
            if len(new_groups | self._presentation_groups) > MAX_PRESENTATION_GROUPS:
                raise StatisticsError("工作或攻击分类超过支持范围，当前结果保留到中断前。")
            # Count every repeated label in the public projections, including
            # 4-byte Unicode characters. Reserve the remaining response budget
            # for bounded numeric groups, JSON keys and retained event details.
            text_sizes = dict(self._summary_text_sizes)
            for row in rows:
                person_id = row["personId"]
                text_sizes[("total", row["kind"], person_id, row["category"])] = _json_size(
                    [row["kind"], person_id, row["personName"], row["category"]])
                text_sizes[("person", person_id)] = _json_size([person_id, row["personName"]]) + person_sizes.get(person_id, 0)
                if row["kind"] == "work":
                    text_sizes[("work", person_id, row["category"])] = _json_size(row["category"])
                    text_sizes[("category", row["category"])] = _json_size(row["category"])
                else:
                    text_sizes[("type", person_id, row["damageType"])] = _json_size(row["damageType"])
                    key = ("attack", person_id, row["attackKind"], row.get("skillId"))
                    if key not in text_sizes or "skillName" in row:
                        text_sizes[key] = _json_size([row["attackKind"], row.get("skillId"), row.get("skillName")])
            if sum(text_sizes.values()) > MAX_SUMMARY_TEXT_BYTES:
                raise StatisticsError("统计名称与人物详情总量超出响应预算，当前结果保留到中断前。")
        except StatisticsError as error:
            self.interrupt(str(error))
            raise
        elapsed = max(0.0, self._clock() - self._started)
        self._person_sizes = person_sizes
        self._presentation_groups.update(new_groups)
        self._summary_text_sizes = text_sizes
        for row in rows:
            key = (row["kind"], row["personId"], row["category"])
            total = self._totals.setdefault(key, {
                "kind": row["kind"], "personId": row["personId"], "personName": row["personName"],
                "category": row["category"], "count": 0, "amount": 0.0,
                "effectiveAmount": 0.0, "effectiveCount": 0, "latestAmount": 0.0,
            })
            total["personName"] = row["personName"]
            total["count"] += 1
            total["amount"] += row["amount"]
            total["latestAmount"] = row["amount"]
            if "effectiveAmount" in row:
                total["effectiveCount"] += 1
                total["effectiveAmount"] += row["effectiveAmount"]
            self._accept_person(row)
            detail = {**row, "observedAfterSeconds": elapsed,
                      "overflowAmount": row["amount"] - row["effectiveAmount"] if "effectiveAmount" in row else None}
            size = _json_size(detail)
            while self._events and (len(self._events) == self._events.maxlen or self._detail_bytes + size > MAX_DETAIL_BYTES):
                self._events.popleft()
                self._detail_bytes -= self._event_sizes.popleft()
            self._events.append(detail)
            self._event_sizes.append(size)
            self._detail_bytes += size
            # Match the displayed cell identity. Different skills can share a
            # category/name (especially periodic damage) and must not replace
            # each other's latest explainable hit.
            latest_key = key if row["kind"] == "work" else (
                "damage", row["personId"], row["attackKind"], row.get("skillId"))
            previous = self._latest_events.pop(latest_key, None)
            if previous is not None:
                self._latest_detail_bytes -= previous[1]
            self._latest_events[latest_key] = (detail, size)
            self._latest_detail_bytes += size
            while self._latest_events and (len(self._latest_events) > 512 or self._latest_detail_bytes > MAX_LATEST_DETAIL_BYTES):
                _evicted_key, (_evicted_detail, evicted_size) = self._latest_events.popitem(last=False)
                self._latest_detail_bytes -= evicted_size
            self._sequence = row["sequence"]
            self._count += 1

    def _accept_person(self, row: dict[str, Any]) -> None:
        person = self._people.setdefault(row["personId"], {
            "personId": row["personId"], "personName": row["personName"], "person": None,
            "personSequence": None, "work": {}, "damage": _metric(), "types": {}, "attacks": {}})
        person["personName"] = row["personName"]
        if "person" in row:
            person["person"] = deepcopy(row["person"])
            person["personSequence"] = row["sequence"]
        if row["kind"] == "work":
            _add(person["work"].setdefault(row["category"], _metric(category=row["category"])), row)
            return
        _add(person["damage"], row)
        _add(person["types"].setdefault(row["damageType"], _metric(damageType=row["damageType"])), row)
        key = (row["attackKind"], row.get("skillId"))
        attack = person["attacks"].setdefault(key, _metric(
            attackKind=row["attackKind"], skillId=row.get("skillId"), skillName=row.get("skillName"), _actionsComplete=True))
        if "skillName" in row:
            attack["skillName"] = row["skillName"]
        _add(attack, row)
        identity = (row["personId"], *key)
        action_id = row.get("actionId")
        ids = self._action_ids.setdefault(identity, set())
        if not action_id or (action_id not in ids and self._action_id_count >= MAX_ACTION_IDS):
            attack["_actionsComplete"] = False
            self._action_id_count -= len(ids)
            ids.clear()
        elif attack["_actionsComplete"] and action_id not in ids:
            ids.add(action_id)
            self._action_id_count += 1

    def _people_snapshot(self, elapsed: float) -> tuple[list[dict[str, Any]], list[str]]:
        rows = []
        categories = set()
        work_sort = lambda value: (WORK_ORDER.index(value) if value in WORK_ORDER else len(WORK_ORDER), value)
        for person_id, person in sorted(self._people.items()):
            work = [_display_metric(value, elapsed) for key, value in sorted(person["work"].items(), key=lambda item: work_sort(item[0]))]
            for cell in work:
                cell.pop("perRealSecond")
            categories.update(person["work"])
            attacks = []
            for key, value in sorted(person["attacks"].items(), key=lambda item: (ATTACK_KINDS.index(item[0][0]), item[0][1] or "")):
                ids = self._action_ids[(person_id, *key)]
                actions = len(ids) if value["_actionsComplete"] else None
                attacks.append({**_display_metric(value, elapsed), "actionCount": actions,
                                "averagePerAction": value["amount"] / actions if actions else None})
            damage = {"amount": person["damage"]["amount"], "count": person["damage"]["count"],
                      "perRealSecond": person["damage"]["amount"] / elapsed if elapsed > 0 else None}
            damage["types"] = [_display_metric(person["types"][kind], elapsed) for kind in DAMAGE_TYPES if kind in person["types"]]
            damage["attacks"] = attacks
            rows.append({"personId": person_id, "personName": person["personName"],
                         "person": deepcopy(person["person"]), "personSequence": person["personSequence"],
                         "work": work, "damage": damage})
        return rows, sorted(categories, key=work_sort)

    def snapshot(self) -> dict[str, Any]:
        end = self._clock() if self._state == "running" else self._ended
        elapsed = max(0.0, end - self._started)
        totals = []
        for total in sorted(self._totals.values(), key=lambda r: (r["kind"], -r["amount"], r["personId"], r["category"])):
            effective_complete = total["effectiveCount"] == total["count"]
            totals.append({**total, "averageAmount": total["amount"] / total["count"],
                           "perRealSecond": total["amount"] / elapsed if elapsed > 0 else None,
                           "effectiveComplete": effective_complete,
                           "overflowAmount": total["amount"] - total["effectiveAmount"] if effective_complete else None})
        people, categories = self._people_snapshot(elapsed)
        summary = {kind: {"amount": sum(row["amount"] for row in totals if row["kind"] == kind),
                          "count": sum(row["count"] for row in totals if row["kind"] == kind)} for kind in ("work", "damage")}
        summary["damage"]["perRealSecond"] = summary["damage"]["amount"] / elapsed if elapsed > 0 else None
        return {"schemaVersion": 1, "sessionId": self._session, "state": self._state, "reason": self._reason,
                "elapsedRealSeconds": elapsed, "eventCount": self._count,
                "retainedEventCount": len(self._events), "lastSequence": self._sequence,
                "totals": totals, "people": people, "workCategories": categories, "summary": summary,
                # Display fields stay light; the opaque signed receipt also
                # preserves full event-time metadata for a later exact lookup.
                "events": [{**deepcopy({key: value for key, value in event.items() if key != "person"}),
                            "detailSnapshot": self._detail_snapshot(event, self._session)} for event in self._events]}

    def _detail_snapshot(self, event: dict[str, Any], session_id: str) -> dict[str, Any]:
        # Preserve the exact string across Python -> JavaScript -> Python, rather
        # than signing a reserialized object whose 1.0/1 representation may change.
        payload = json.dumps({"sessionId": session_id, "event": event}, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False)
        signature = hmac.new(self._snapshot_secret, b"tower-event-snapshot-v1\0" + payload.encode("utf-8"),
                             hashlib.sha256).hexdigest()
        return {"schemaVersion": 1, "payload": payload, "signature": signature}

    def explain_snapshot(self, sequence: int, session_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Explain an exact event previously issued by this directory authority.

        The caller holds the snapshot; no history grows on the worker. Signatures
        bind the original session and complete event, including event-time person
        metadata. A receipt never authorizes a different sequence or directory.
        """
        sequence = _sequence(sequence)
        snapshot = validate_detail_snapshot(snapshot)
        expected = hmac.new(self._snapshot_secret, b"tower-event-snapshot-v1\0" + snapshot["payload"].encode("utf-8"),
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(snapshot["signature"], expected):
            raise StatisticsError("事件快照签名无效或所属统计进程已失效，请重新选择当前记录。")
        # Authenticate before parsing: untrusted input cannot choose formula or
        # person data merely by supplying a valid-looking JSON document.
        try:
            value = json.loads(snapshot["payload"])
        except (ValueError, RecursionError):
            raise StatisticsError("事件快照内容无法读取。") from None
        if (not isinstance(value, dict) or set(value) != {"sessionId", "event"}
                or not isinstance(session_id, str) or not session_id or value["sessionId"] != session_id
                or not isinstance(value["event"], dict) or value["event"].get("sequence") != sequence):
            raise StatisticsError("事件快照与所选会话或序号不一致，不会替换为其他事件。")
        return self._explain_event(value["event"], session_id=session_id, detail_snapshot=snapshot)

    def explain(self, sequence: int, session_id: str | None = None) -> dict[str, Any]:
        sequence = _sequence(sequence)
        if session_id is not None and session_id != self._session:
            raise StatisticsError("计时会话已改变，不能用新会话的同序号事件替换已选详情。")
        event = next((row for row in self._events if row["sequence"] == sequence), None)
        if event is None:
            event = next((row for row, _size in self._latest_events.values() if row["sequence"] == sequence), None)
        if event is None:
            raise StatisticsError("该事件已不在当前保留的明细或分类最近快照中，无法补取公式；已打开的快照仍可查看。")
        return self._explain_event(event)

    def explain_latest(self, selector: dict[str, Any], session_id: str) -> dict[str, Any]:
        """Resolve a current aggregate atomically under CaptureManager's lock.

        Status and explain are separate requests: a new event can replace the
        displayed sequence between them. Aggregate cells request the current
        group snapshot explicitly; history rows continue using exact explain().
        """
        selector = validate_latest_selector(selector)
        if not isinstance(session_id, str) or not session_id or session_id != self._session:
            raise StatisticsError("计时会话已改变，不能用新会话的最近事件替换已选详情。")
        key = _latest_key(selector)
        cached = self._latest_events.get(key)
        event = cached[0] if cached else None
        if event is None:
            # The independent latest-cache byte budget may evict a group that
            # still has its newest event in the rolling details. Never substitute
            # an older hit or another skill/person just because its name matches.
            person = self._people.get(selector["personId"])
            metric = None
            if person is not None:
                metric = (person["work"].get(selector["category"]) if selector["kind"] == "work"
                          else person["attacks"].get((selector["attackKind"], selector["skillId"])))
            sequence = metric["latestSequence"] if metric is not None else None
            event = next((row for row in self._events if row["sequence"] == sequence), None)
        if event is None:
            raise StatisticsError("所选人物与分类的最近事件已超出保留容量或尚未采集，无法补取公式；不会替换为其他分类。")
        return self._explain_event(event) | {"selection": {"mode": "latest", **selector}}

    def _explain_event(self, event: dict[str, Any], *, session_id: str | None = None,
                       detail_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        session_id = self._session if session_id is None else session_id
        coverage = event.get("formulaCoverage", "unavailable")
        formula = explain_formula(event["formula"], event["amount"]) if "formula" in event else None
        work_formula = explain_work_formula(event["workFormula"], event["amount"]) if "workFormula" in event else None
        if formula:
            message = "本次命中已按原输入复算一致。" if formula["matches"] else "本次伤害与公式复算不一致，已停用收益建议。"
        elif event["kind"] == "work":
            message = ("本次工作已按实际输入复算一致。" if work_formula["matches"] else "本次工作与公式复算不一致，保留实际输出。") if work_formula else "此工作事件未采集完整公式；旧采集入口需要更新后才能记录公式。"
        else:
            message = {"special-return": "本次属于特殊返回分支，尚无已核验的独立公式。",
                       "stored-buff-value-only": "已记录持续伤害数值；其最初生成公式未采集，不能套用普通命中公式。"}.get(coverage,
                       "此事件未采集完整伤害公式，无法计算属性收益。")
        snapshot = detail_snapshot if detail_snapshot is not None else self._detail_snapshot(event, session_id)
        return {"schemaVersion": 1, "sessionId": session_id, "sequence": event["sequence"],
                "formulaCoverage": coverage, "formula": formula, "workFormula": work_formula,
                "event": {**deepcopy(event), "detailSnapshot": deepcopy(snapshot)}, "message": message}
