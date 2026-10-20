"""Read-only game capabilities exposed by the application composition root."""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Callable

from nimdadesk_worker.protocol import ProtocolError
from nimdadesk_worker.registry import MethodRegistry, MethodResult
from . import reader, planner, visuals


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
