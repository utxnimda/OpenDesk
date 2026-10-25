"""One worker-owned polling session; pages only request snapshots and controls."""
from __future__ import annotations

import hmac
from collections import OrderedDict
from pathlib import Path
import re
import threading
import time

from . import capture_install as installation
from .capture_install import CaptureError, STATE_DIR
from .output_statistics import OutputStatistics, StatisticsError, validate_event

MAX_STREAM_BYTES = 2 * 1024 * 1024
MAX_AGE_MS = 5000
COVERAGE = ["人物伐木、采矿、种植、研究、操练、锻造、工艺、炼丹、建造与修墙的完整工作输出",
            "已核实人物直接伤害的完整输出与有效伤害；其他伤害来源不混入统计"]


def _integer(value, label: str) -> int:
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise CaptureError(f"采集{label}无效。")
    return value


def read_stream(root: Path, *, now=time.time, expected_profile: str | None = None) -> dict:
    state = root / STATE_DIR
    config = installation.read_json(state / "config.json")
    if (set(config) != {"schemaVersion", "token", "ruleProfile"}
            or type(config.get("schemaVersion")) is not int or config["schemaVersion"] != 1
            or not isinstance(config.get("ruleProfile"), str) or config["ruleProfile"] not in installation.PROFILES
            or not isinstance(config.get("token"), str)
            or re.fullmatch(r"[0-9a-f]{64}", config["token"]) is None):
        raise CaptureError("采集配置无效，请退出游戏后重新安装采集器。")
    if expected_profile is not None and config["ruleProfile"] != expected_profile:
        raise CaptureError("采集配置与计时或安装时的规则版本不一致，请重新核查采集器。")
    envelope = installation.read_json(state / "stream.json", MAX_STREAM_BYTES)
    keys = {"schemaVersion", "token", "ruleProfile", "streamId", "lastSequence", "dropped", "heartbeatMs", "error", "events"}
    if (set(envelope) != keys or type(envelope.get("schemaVersion")) is not int or envelope["schemaVersion"] != 1
            or envelope.get("ruleProfile") != config["ruleProfile"]):
        raise CaptureError("采集流版本或字段不受支持。")
    if (not isinstance(envelope["token"], str) or re.fullmatch(r"[0-9a-f]{64}", envelope["token"]) is None
            or not hmac.compare_digest(envelope["token"], config["token"])):
        raise CaptureError("采集流身份不匹配，请重新启动游戏。")
    if (not isinstance(envelope["streamId"], str) or not 1 <= len(envelope["streamId"]) <= 128
            or not envelope["streamId"].isprintable()):
        raise CaptureError("采集流编号无效。")
    for name in ("lastSequence", "dropped", "heartbeatMs"):
        _integer(envelope[name], name)
    if envelope["error"] is not None:
        if not isinstance(envelope["error"], str) or not 1 <= len(envelope["error"]) <= 1000:
            raise CaptureError("采集错误信息无效。")
        raise CaptureError("游戏采集已中断：" + envelope["error"])
    age = now() * 1000 - envelope["heartbeatMs"]
    if age > MAX_AGE_MS or age < -2000:
        raise CaptureError("采集心跳已过期或时钟异常，请检查游戏是否仍在运行。")
    events = envelope["events"]
    if not isinstance(events, list) or len(events) > 1000:
        raise CaptureError("采集事件批次超过上限。")
    try:
        rows = [validate_event(event) for event in events]
    except StatisticsError as error:
        raise CaptureError(str(error)) from error
    if any("formula" in row and row["formula"]["expression"] != installation.FORMULA_EXPRESSIONS.get(config["ruleProfile"])
           for row in rows):
        raise CaptureError("伤害公式表达式与采集规则版本不一致，拒绝混用版本。")
    if any("workFormula" in row and row["workFormula"]["expression"] not in
           installation.WORK_FORMULA_EXPRESSIONS.get(config["ruleProfile"], ()) for row in rows):
        raise CaptureError("工作公式表达式与采集规则版本不一致，拒绝混用版本。")
    if (not rows and envelope["lastSequence"] != 0) or (rows and (
            rows[-1]["sequence"] != envelope["lastSequence"] or rows[0]["sequence"] < 1
            or any(b["sequence"] != a["sequence"] + 1 for a, b in zip(rows, rows[1:])))):
        raise CaptureError("采集文件事件序号不完整，统计不能继续。")
    return {**envelope, "events": rows}


class CaptureManager:
    def __init__(self, *, now=time.time, auto_poll: bool = True):
        self._now, self._auto_poll = now, auto_poll
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._thread = None
        self._root: Path | None = None
        self._sessions: OrderedDict[Path, OutputStatistics] = OrderedDict()
        self._installation_cache = None
        self._running_cache = None
        self._audit_signature = None
        self._statistics = OutputStatistics()
        self._stream_id = ""
        self._profile = ""
        self._token = ""
        self._package_signature = None
        self._dropped = 0

    def _select(self, game_dir: str | Path) -> Path:
        root = installation.root_path(game_dir)
        if root != self._root:
            if self._statistics.state == "running":
                raise CaptureError("统计正在进行，不能切换游戏目录；请先停止计时。")
            if self._root is not None:
                self._sessions[self._root] = self._statistics
            self._root = root
            self._statistics = self._sessions.pop(root, None) or OutputStatistics()
            while len(self._sessions) > 7:  # Current directory plus seven inactive histories.
                self._sessions.popitem(last=False)
            self._installation_cache = self._running_cache = self._audit_signature = None
        return root

    @staticmethod
    def _signature(path: Path):
        installation.safe_path(path, missing=True)
        if not path.exists():
            return None
        value = path.stat()
        return (value.st_size, value.st_mtime_ns, value.st_ino)

    def _installed(self, root: Path, *, fresh=False):
        directory = self._signature(root / STATE_DIR)
        metadata = self._signature(root / STATE_DIR / "meta.json")
        # Atomic stream replacement changes the directory mtime every 250 ms;
        # that is not a package identity change. Still detect a replaced directory.
        identity = (directory[2],) if directory and metadata else directory
        key = (self._signature(root / "package.nw"), identity, metadata)
        if fresh or self._installation_cache is None or self._installation_cache[0] != key:
            meta = installation.installation(root)
            if meta and meta["installed"]:
                meta = {**meta, "updateRequired": installation.needs_update(root)}
            self._installation_cache = (key, meta)
        return self._installation_cache[1]

    def _running(self, root: Path):
        now = time.monotonic()
        if self._running_cache is None or now - self._running_cache[0] > 2:
            self._running_cache = (now, installation.game_running(root))
        return self._running_cache[1]

    def _poll(self) -> None:
        if not self._root or self._statistics.state != "running":
            return
        try:
            stat = installation.safe_path(self._root / "package.nw").stat()
            signature = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
            if signature != self._package_signature:
                meta = installation.installation(self._root)
                if not meta or not meta["installed"] or meta["ruleProfile"] != self._profile:
                    raise CaptureError("游戏采集包已改变，统计已中断。")
                self._package_signature = signature
            envelope = read_stream(self._root, now=self._now, expected_profile=self._profile)
            last = self._statistics.last_sequence
            if envelope["streamId"] != self._stream_id:
                raise CaptureError("游戏采集会话已改变，请重新开始计时。")
            if not hmac.compare_digest(envelope["token"], self._token):
                raise CaptureError("游戏采集身份已改变，请重新开始计时。")
            if envelope["dropped"] != self._dropped:
                raise CaptureError("采集器报告事件丢失，当前结果只覆盖中断前的完整区间。")
            if envelope["lastSequence"] < last:
                raise CaptureError("采集事件序号回退，统计已中断。")
            rows = [row for row in envelope["events"] if row["sequence"] > last]
            self._statistics.accept(self._stream_id, rows)
        except (CaptureError, StatisticsError, OSError) as error:
            self._statistics.interrupt(str(error))

    def _loop(self) -> None:
        while not self._shutdown.wait(0.2):
            with self._lock:
                try:
                    self._poll()
                except Exception as error:
                    # Unexpected failures remain visible and freeze the interval.
                    self._statistics.interrupt(f"采集轮询失败（{type(error).__name__}），统计已停止。")

    def _ensure_thread(self) -> None:
        if self._auto_poll and (self._thread is None or not self._thread.is_alive()):
            self._thread = threading.Thread(target=self._loop, name="tower-capture", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def _response(self, root: Path) -> dict:
        capture = {"state": "not-installed", "message": "尚未安装采集器。请先在游戏中保存并退出，再点击安装。",
                   "installed": None, "gameRunning": None, "canInstall": False, "canRemove": False,
                   "updateRequired": None,
                   "coverage": COVERAGE, "gameDir": str(root)}
        errors = []
        # Installation and process identity are independent observations. A
        # failed observation must stay unknown, not become a negative result.
        try:
            meta = self._installed(root)
            capture["installed"] = bool(meta and meta["installed"])
            capture["updateRequired"] = meta.get("updateRequired", False) if meta else False
        except (CaptureError, OSError) as error:
            errors.append(str(error))
        try:
            capture["gameRunning"] = self._running(root)
        except (CaptureError, OSError) as error:
            errors.append(str(error))
        if errors:
            capture["state"], capture["message"] = "error", "；".join(errors)
            return {"schemaVersion": 1, "capture": capture, "statistics": self._statistics.snapshot()}
        try:
            if capture["installed"]:
                capture["canRemove"] = not capture["gameRunning"]
                capture["state"] = "waiting"
                capture["message"] = ("采集入口已安装，游戏进程已检测到，等待采集心跳。"
                                      if capture["gameRunning"] else "采集入口已安装，等待手动启动游戏。")
                if (root / STATE_DIR / "stream.json").exists():
                    try:
                        read_stream(root, now=self._now, expected_profile=meta["ruleProfile"])
                        capture["state"], capture["message"] = "connected", "游戏采集已连接，可以开始计时。"
                    except (CaptureError, OSError) as error:
                        capture["state"] = "stale" if "心跳" in str(error) else "error"
                        capture["message"] = str(error)
            else:
                signature = self._signature(root / "package.nw")
                if signature != self._audit_signature:
                    installation.audit_package(root / "package.nw")
                    self._audit_signature = signature
                capture["canInstall"] = not capture["gameRunning"]
        except (CaptureError, OSError) as error:
            capture["state"], capture["message"] = "error", str(error)
        return {"schemaVersion": 1, "capture": capture, "statistics": self._statistics.snapshot()}

    def status(self, game_dir: str | Path) -> dict:
        with self._lock:
            root = self._select(game_dir)
            self._poll()
            return self._response(root)

    def install(self, game_dir: str | Path) -> dict:
        with self._lock:
            root = self._select(game_dir)
            if self._statistics.state == "running":
                raise CaptureError("请先停止计时，再安装采集器。")
            installation.install(root)
            self._installation_cache = self._running_cache = None
            return self._response(root)

    def remove(self, game_dir: str | Path) -> dict:
        with self._lock:
            root = self._select(game_dir)
            if self._statistics.state == "running":
                raise CaptureError("请先停止计时，再移除采集器。")
            installation.remove(root)
            self._installation_cache = self._running_cache = None
            return self._response(root)

    def start(self, game_dir: str | Path) -> dict:
        with self._lock:
            root = self._select(game_dir)
            meta = self._installed(root, fresh=True)
            if not meta or not meta["installed"]:
                raise CaptureError("请先安装采集器。")
            envelope = read_stream(root, now=self._now, expected_profile=meta["ruleProfile"])
            self._statistics.start(envelope["streamId"], envelope["lastSequence"])
            self._stream_id, self._dropped = envelope["streamId"], envelope["dropped"]
            self._profile = envelope["ruleProfile"]
            self._token = envelope["token"]
            stat = (root / "package.nw").stat()
            self._package_signature = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
            self._ensure_thread()
            return self._response(root)

    def stop(self, game_dir: str | Path) -> dict:
        with self._lock:
            root = self._select(game_dir)
            self._poll()
            self._statistics.stop()
            return self._response(root)

    def explain(self, game_dir: str | Path, sequence: int, session_id: str | None = None) -> dict:
        with self._lock:
            self._select(game_dir)
            _integer(sequence, "事件序号")
            self._poll()
            return self._statistics.explain(sequence, session_id)

    def explain_latest(self, game_dir: str | Path, selector: dict, session_id: str) -> dict:
        with self._lock:
            self._select(game_dir)
            self._poll()
            return self._statistics.explain_latest(selector, session_id)

    def explain_snapshot(self, game_dir: str | Path, sequence: int, session_id: str, snapshot: dict) -> dict:
        with self._lock:
            root = installation.root_path(game_dir)
            # A frozen historical view must not switch the live recording root,
            # advance its stream, or create a new authority to "recover" a receipt.
            statistics = self._statistics if root == self._root else self._sessions.get(root)
            if statistics is None:
                raise StatisticsError("事件快照所属的统计进程或目录会话已失效，请重新选择当前记录。")
            return statistics.explain_snapshot(sequence, session_id, snapshot)


manager = CaptureManager()
