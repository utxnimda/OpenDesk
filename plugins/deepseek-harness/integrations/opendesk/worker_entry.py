"""Frozen DeepSeek Harness hostApi 2 worker entry; identity is baked at build time."""
from __future__ import annotations

import os
from pathlib import Path
import time

from deepseek_harness.config import ConfigError
from deepseek_harness.service import DshService
from opendesk_worker.__main__ import main as serve
from opendesk_worker.protocol import ProtocolError
from opendesk_worker.registry import MethodResult
from _build_identity import IDENTITY


def _wrap_errors(fn):
    def wrapped(params):
        try:
            return MethodResult(fn(params))
        except ConfigError as error:
            raise ProtocolError(error.code, error.message) from error
        except ProtocolError:
            raise
        except (RuntimeError, ValueError) as error:
            text = str(error)
            if ":" in text:
                code, message = text.split(":", 1)
                if code.isupper() and all(c.isalnum() or c == "_" for c in code):
                    raise ProtocolError(code, message.strip()) from error
            raise ProtocolError("DSH_ERROR", text) from error
    return wrapped


def _no_args(fn):
    def call(params):
        if params:
            raise ProtocolError("INVALID_PARAMS", "该操作不接受额外参数。")
        return fn()
    return _wrap_errors(call)


def main() -> int:
    data = os.environ.get("OPENDESK_MODULE_DATA_DIR", "")
    if not data or not Path(data).is_absolute():
        raise RuntimeError("Host must provide an absolute module data directory.")
    service = DshService(Path(data), version=IDENTITY["version"])

    def configure_registry(registry):
        registry.register("module.identity", _no_args(lambda: IDENTITY))
        registry.register("module.webapp", _no_args(lambda: {"url": service.start_webapp()}))
        registry.register("module.prepareShutdown", _no_args(service.prepare_shutdown))

        registry.register("dsh.status", _no_args(service.status))
        registry.register("dsh.probe", _no_args(lambda: {"probe": service.refresh_probe(), "status": service.status()}))

        def handle_configure(params):
            if not isinstance(params, dict):
                raise ProtocolError("INVALID_PARAMS", "配置参数必须是对象。")
            return service.configure(params)

        def handle_logs(params):
            if not isinstance(params, dict):
                raise ProtocolError("INVALID_PARAMS", "日志查询参数必须是对象。")
            unknown = set(params.keys()) - {"limit"}
            if unknown:
                raise ProtocolError("INVALID_PARAMS", "日志查询包含未知参数。")
            limit = params.get("limit", 200)
            if type(limit) is not int or not (1 <= limit <= 400):
                raise ProtocolError("INVALID_PARAMS", "limit 必须是 1 到 400 的整数。")
            return service.logs(limit=limit)

        def handle_mcp_sync(params):
            if not isinstance(params, dict):
                raise ProtocolError("INVALID_PARAMS", "MCP 同步参数必须是对象。")
            unknown = set(params.keys()) - {"tools"}
            if unknown or not isinstance(params.get("tools"), list):
                raise ProtocolError("INVALID_PARAMS", "MCP 同步仅接受 tools 数组。")
            return service.mcp_sync(params["tools"])

        def handle_mcp_respond(params):
            if not isinstance(params, dict):
                raise ProtocolError("INVALID_PARAMS", "MCP 响应参数必须是对象。")
            unknown = set(params.keys()) - {"callId", "ok", "result", "error"}
            if unknown:
                raise ProtocolError("INVALID_PARAMS", "MCP 响应包含未知参数。")
            if not isinstance(params.get("callId"), str) or not isinstance(params.get("ok"), bool):
                raise ProtocolError("INVALID_PARAMS", "MCP 响应缺少 callId 或 ok 字段。")
            return service.mcp_respond(
                params["callId"],
                params["ok"],
                result=params.get("result"),
                error=str(params.get("error") or ""),
            )

        def handle_source_build(params):
            if not isinstance(params, dict):
                raise ProtocolError("INVALID_PARAMS", "构建参数必须是对象。")
            unknown = set(params.keys()) - {"restartAfter"}
            if unknown:
                raise ProtocolError("INVALID_PARAMS", "构建参数包含未知字段。")
            restart_after = params.get("restartAfter", False)
            if not isinstance(restart_after, bool):
                raise ProtocolError("INVALID_PARAMS", "restartAfter 必须是布尔值。")
            return service.source_build(restart_after=restart_after)

        registry.register("dsh.configure", _wrap_errors(handle_configure))
        registry.register("dsh.start", _no_args(service.start_dsh))
        registry.register("dsh.stop", _no_args(service.stop_dsh))
        registry.register("dsh.restart", _no_args(service.restart_dsh))
        registry.register("dsh.install", _no_args(service.install_cli))
        registry.register("dsh.install.cancel", _no_args(service.cancel_install))
        registry.register("dsh.source.install", _no_args(service.source_install))
        registry.register("dsh.source.build", _wrap_errors(handle_source_build))
        registry.register("dsh.source.cancel", _no_args(service.cancel_source_task))
        registry.register("dsh.logs", _wrap_errors(handle_logs))
        registry.register("dsh.browser.open", _no_args(service.open_external_browser))
        registry.register("dsh.mcp.sync", _wrap_errors(handle_mcp_sync))
        registry.register("dsh.mcp.poll", _no_args(service.mcp_poll))
        registry.register("dsh.mcp.respond", _wrap_errors(handle_mcp_respond))

    try:
        return serve(configure_registry)
    finally:
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and not service.prepare_shutdown()["ready"]:
            time.sleep(0.05)
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
