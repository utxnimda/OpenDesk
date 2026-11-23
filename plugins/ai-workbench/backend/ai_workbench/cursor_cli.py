"""Cursor print-mode NDJSON adapter. See cursor.com/docs/cli/reference/output-format."""
from __future__ import annotations

import json
import time

from . import cursor_provider as provider


def run_cli(*, executable, workspace, model, prompt, thread_id, emit, cancelled):
    if thread_id and thread_id.startswith("-"):
        raise provider.CursorError("INVALID_PARAMS", "请使用本对话已保存的 Cursor 会话标识。")
    catalog = provider._cli_models(executable, workspace, cancelled)
    selected = next((entry for entry in catalog if entry["id"] == model), None)
    if selected is None:
        raise provider.CursorError("CURSOR_MODEL_UNAVAILABLE", "所选模型不在 agent models 中，请刷新模型目录。")
    if cancelled.is_set():
        raise provider.CursorError("CANCELLED", "Cursor 请求已停止。")
    arguments = ["--disable-auto-update", "--model", model, "--mode", "ask", "--print",
                 "--output-format", "stream-json", "--stream-partial-output"]
    if thread_id:
        arguments += ["--resume", thread_id]
    process = provider._Process(executable, workspace, arguments)
    try:
        process.write_prompt(prompt)
        emit({"type": "status", "status": "running"})
        session, text, terminal = None, "", None
        deadline = time.monotonic() + provider.TURN_TIMEOUT
        while True:
            if cancelled.is_set():
                emit({"type": "status", "status": "cancelling"})
                process.close()
                raise provider.CursorError("CANCELLED", "Cursor 进程已停止，本轮已取消。")
            line = process.read(deadline)
            if line is None:
                break
            if not line:
                continue
            try:
                value = json.loads(line)
            except (ValueError, UnicodeDecodeError, RecursionError) as error:
                raise provider.CursorError("CURSOR_PROTOCOL_ERROR", "Cursor CLI 返回了无效 JSON。") from error
            if not isinstance(value, dict) or not isinstance(value.get("type"), str):
                raise provider.CursorError("CURSOR_PROTOCOL_ERROR", "Cursor CLI 事件格式无效。")
            kind = value["type"]
            if kind == "system" and value.get("subtype") == "init":
                if session is not None:
                    raise provider.CursorError("CURSOR_PROTOCOL_ERROR", "Cursor CLI 重复初始化会话。")
                session = provider._identifier(value.get("session_id"), "CLI 会话标识")
                if thread_id and session != thread_id:
                    raise provider.CursorError("CURSOR_PROTOCOL_ERROR", "Cursor CLI 恢复了不同的会话。")
                if value.get("model") != selected["label"]:
                    raise provider.CursorError("CURSOR_MODEL_UNAVAILABLE", "Cursor CLI 返回的模型与所选模型不一致。")
                continue
            # New metadata events may be ignored. Replies must belong to this run.
            if kind not in ("assistant", "result", "error"):
                continue
            if session is None or value.get("session_id", session) != session:
                raise provider.CursorError("CURSOR_PROTOCOL_ERROR", "Cursor CLI 回答的会话身份不一致。")
            if terminal is not None:
                raise provider.CursorError("CURSOR_PROTOCOL_ERROR", "Cursor CLI 终态之后仍返回回答。")
            if kind == "error":
                raise provider._provider_error(value)
            if kind == "result":
                terminal = value
                continue
            # Partial deltas have a timestamp but no model_call_id. Both other
            # assistant flush forms repeat already streamed text (official CLI).
            if "timestamp_ms" not in value or "model_call_id" in value:
                continue
            message = value.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list) or message.get("role") != "assistant":
                raise provider.CursorError("CURSOR_PROTOCOL_ERROR", "Cursor CLI 回答格式无效。")
            for part in content:
                if not isinstance(part, dict):
                    raise provider.CursorError("CURSOR_PROTOCOL_ERROR", "Cursor CLI 回答分块无效。")
                if part.get("type") != "text":
                    continue
                delta = part.get("text")
                if not isinstance(delta, str):
                    raise provider.CursorError("CURSOR_PROTOCOL_ERROR", "Cursor CLI 文本分块无效。")
                text += delta
                if len(text) > provider.MAX_OUTPUT_CHARS:
                    raise provider.CursorError("CURSOR_OUTPUT_LIMIT", "Cursor 回答超过单次任务上限。")
                emit({"type": "delta", "text": delta})
        # EOF, the successful terminal event and process exit must all agree.
        process.close()
        if cancelled.is_set():
            raise provider.CursorError("CANCELLED", "Cursor 进程已停止，本轮已取消。")
        if process.process.returncode != 0:
            raise provider._provider_error(process.stderr_tail.decode("utf-8", "replace"))
        if process.input_failed:
            raise provider.CursorError("CURSOR_DISCONNECTED", "Cursor 未完整接收提示词。")
        if terminal is None:
            raise provider.CursorError("CURSOR_INCOMPLETE", "Cursor CLI 未返回完成事件，请检查连接或额度。")
        if terminal.get("subtype") != "success" or terminal.get("is_error") is not False:
            raise provider._provider_error(terminal)
        final_text = terminal.get("result")
        if not isinstance(final_text, str) or not final_text:
            raise provider.CursorError("CURSOR_EMPTY_RESULT", "Cursor 已结束，但没有返回可展示的回答。")
        if len(final_text) > provider.MAX_OUTPUT_CHARS:
            raise provider.CursorError("CURSOR_OUTPUT_LIMIT", "Cursor 回答超过单次任务上限。")
        if not final_text.startswith(text):
            raise provider.CursorError("CURSOR_PROTOCOL_ERROR", "Cursor CLI 完整回答与流式回答不一致。")
        if len(final_text) > len(text):
            emit({"type": "delta", "text": final_text[len(text):]})
        return {"text": final_text, "threadId": session}
    finally:
        process.close()
