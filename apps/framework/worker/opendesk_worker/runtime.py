"""Framework lifecycle handlers. No application or feature dependencies."""

from __future__ import annotations

import os
import platform
from typing import Any

from .protocol import MAX_FRAME_BYTES, PROTOCOL_VERSION, ProtocolError
from .registry import MethodRegistry, MethodResult


def _require_empty_params(params: dict[str, Any]) -> None:
    if params:
        raise ProtocolError("INVALID_PARAMS", "This runtime method accepts an empty params object.")


def register_runtime(registry: MethodRegistry) -> None:
    def hello(params: dict[str, Any]) -> MethodResult:
        _require_empty_params(params)
        return MethodResult({
            "protocolVersion": PROTOCOL_VERSION,
            "worker": "opendesk-python",
            "capabilities": registry.capabilities,
            "maxFrameBytes": MAX_FRAME_BYTES,
        })

    def ping(params: dict[str, Any]) -> MethodResult:
        _require_empty_params(params)
        return MethodResult({
            "message": "pong",
            "protocolVersion": PROTOCOL_VERSION,
            "pid": os.getpid(),
            "pythonVersion": platform.python_version(),
        })

    def shutdown(params: dict[str, Any]) -> MethodResult:
        _require_empty_params(params)
        return MethodResult({"status": "stopping"}, stop_worker=True)

    registry.register("runtime.hello", hello)
    registry.register("runtime.ping", ping)
    registry.register("runtime.shutdown", shutdown)
