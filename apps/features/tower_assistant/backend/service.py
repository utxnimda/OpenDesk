"""Named game operations; capture installation is always explicitly requested."""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Callable

from opendesk_worker.protocol import ProtocolError
from opendesk_worker.registry import MethodRegistry, MethodResult
from . import reader, planner, visuals, capture
from .output_statistics import StatisticsError, validate_detail_snapshot, validate_latest_selector


def _params(params: dict[str, Any], optional: set[str]) -> tuple[Path, dict[str, str]]:
    if set(params) - ({"gameDir"} | optional):
        raise ProtocolError("INVALID_PARAMS", "请求含有未支持的参数。")
    directory = params.get("gameDir")
    if not isinstance(directory, str) or not directory.strip() or len(directory) > 4096:
        raise ProtocolError("INVALID_PARAMS", "请填写游戏安装目录。")
    if any(ord(char) < 32 for char in directory) or directory.startswith(("\\\\", "//")):
        raise ProtocolError("INVALID_PARAMS", "请使用本机游戏目录。")
    path = Path(directory)
    if not path.is_absolute():
        raise ProtocolError("INVALID_PARAMS", "游戏目录必须是绝对路径。")
    try:
        path = path.resolve(strict=True)
        if not path.is_dir() or not (path / "package.nw").is_file():
            raise ValueError
    except (OSError, ValueError):
        raise ProtocolError("GAME_NOT_FOUND", "该目录中没有找到游戏 package.nw，请选择游戏安装目录。") from None
    values: dict[str, str] = {}
    for field in optional:
        if field in params:
            value = params[field]
            if not isinstance(value, str) or not value.strip() or len(value) > 256 or not value.isprintable():
                raise ProtocolError("INVALID_PARAMS", "存档或渡劫台编号无效。")
            values[field] = value
    return path, values


def discover(params: dict[str, Any]) -> dict[str, Any]:
    if params:
        raise ProtocolError("INVALID_PARAMS", "自动查找不接受额外参数。")
    # Only inspect explicit ancestors; never scan drives or read arbitrary files.
    anchors = [Path(sys.executable)] if getattr(sys, "frozen", False) else [Path(__file__)]
    directories: list[str] = []
    for anchor in anchors:
        for path in anchor.resolve().parents:
            if (path / "game.exe").is_file() and (path / "package.nw").is_file():
                value = str(path)
                if value not in directories:
                    directories.append(value)
    return {"gameDirs": directories}


def inspect(params: dict[str, Any]) -> dict[str, Any]:
    directory, values = _params(params, {"saveId"})
    saves = reader.discover_saves(directory)
    snapshot = reader.load_latest(directory, values.get("saveId"))
    return {"schemaVersion": 1, "gameDir": str(directory), "saves": saves, "snapshot": snapshot}


def analyze(params: dict[str, Any]) -> dict[str, Any]:
    directory, values = _params(params, {"saveId", "platformId"})
    snapshot = reader.load_latest(directory, values.get("saveId"))
    return planner.analyze(snapshot, values.get("platformId"))


def read_visuals(params: dict[str, Any]) -> dict[str, Any]:
    if set(params) != {"gameDir", "iconKeys"}:
        raise ProtocolError("INVALID_PARAMS", "图标读取仅接受游戏目录和图标编号列表。")
    directory, _ = _params({"gameDir": params["gameDir"]}, set())
    keys = params["iconKeys"]
    if not isinstance(keys, list) or len(keys) > 32 or any(not isinstance(key, str) or not key or len(key) > 128 or not key.isprintable() for key in keys):
        raise ProtocolError("INVALID_PARAMS", "每次最多读取 32 个有效图标编号。")
    return visuals.load(directory, keys)


def capture_operation(params: dict[str, Any], method: str) -> dict[str, Any]:
    _params(params, set())
    # Preserve the original lexical path so capture rejects symlink/junction
    # components instead of accepting a resolved redirect from the reader helper.
    return getattr(capture.manager, method)(params["gameDir"])


def statistics_explain(params: dict[str, Any]) -> dict[str, Any]:
    if set(params) not in ({"gameDir", "sequence"}, {"gameDir", "sequence", "sessionId"},
                           {"gameDir", "sequence", "sessionId", "snapshot"}, {"gameDir", "latest", "sessionId"}):
        raise ProtocolError("INVALID_PARAMS", "公式详情须指定事件序号，或指定最近分类与计时会话；两种选择不能混用。")
    _params({"gameDir": params["gameDir"]}, set())
    session = None
    if "sessionId" in params:
        session = params["sessionId"]
        if not isinstance(session, str) or not session.strip() or len(session) > 128 or not session.isprintable():
            raise ProtocolError("INVALID_PARAMS", "计时会话编号无效。")
    if "latest" in params:
        try:
            selector = validate_latest_selector(params["latest"])
        except StatisticsError as error:
            raise ProtocolError("INVALID_PARAMS", str(error)) from error
        return capture.manager.explain_latest(params["gameDir"], selector, session)
    sequence = params["sequence"]
    if type(sequence) is not int or not 0 <= sequence <= 2**53 - 1:
        raise ProtocolError("INVALID_PARAMS", "事件序号必须是非负安全整数。")
    if "snapshot" in params:
        try:
            snapshot = validate_detail_snapshot(params["snapshot"])
        except StatisticsError as error:
            raise ProtocolError("INVALID_PARAMS", str(error)) from error
        return capture.manager.explain_snapshot(params["gameDir"], sequence, session, snapshot)
    if session is not None:
        return capture.manager.explain(params["gameDir"], sequence, session)
    return capture.manager.explain(params["gameDir"], sequence)


def register(registry: MethodRegistry) -> None:
    def handler(operation: Callable[[dict[str, Any]], dict[str, Any]]):
        def call(params: dict[str, Any]) -> MethodResult:
            try:
                return MethodResult(operation(params))
            except ProtocolError:
                raise
            except ValueError as error:
                raise ProtocolError("TOWER_ANALYSIS_REJECTED", str(error)) from error
            except OSError as error:
                raise ProtocolError("TOWER_READ_FAILED", "无法读取游戏数据，请检查目录权限，或等游戏保存结束后重试。") from error
        return call

    registry.register("tower.discover", handler(discover))
    registry.register("tower.inspect", handler(inspect))
    registry.register("tower.analyze", handler(analyze))
    registry.register("tower.visuals", handler(read_visuals))
    for name, method in (("capture.status", "status"), ("capture.install", "install"),
                         ("capture.remove", "remove"), ("statistics.start", "start"),
                         ("statistics.stop", "stop"), ("statistics.status", "status")):
        registry.register("tower." + name, handler(lambda params, method=method: capture_operation(params, method)))
    registry.register("tower.statistics.explain", handler(statistics_explain))
