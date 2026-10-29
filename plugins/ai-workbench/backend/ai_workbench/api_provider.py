"""Bounded streaming adapters for Responses and Chat Completions HTTP APIs."""
from __future__ import annotations

import http.client
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from .errors import ApiError, Cancelled

MAX_RESPONSE = 4 * 1024 * 1024
MAX_OUTPUT = 256 * 1024


def _strict_json(raw):
    def invalid(_):
        raise ValueError("Non-finite JSON.")
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("Duplicate JSON key.")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def validate_endpoint(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except (ValueError, TypeError) as error:
        raise ApiError("INVALID_ENDPOINT", "请填写有效的 API 地址。") from error
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or any(ord(c) < 33 for c in value)):
        raise ApiError("INVALID_ENDPOINT", "API 地址不得包含账号、查询参数或片段。")
    if parsed.scheme != "https" and not (parsed.hostname == "127.0.0.1" and port):
        raise ApiError("INVALID_ENDPOINT", "远程 API 必须使用 HTTPS；本地服务可使用明确端口的 127.0.0.1。")
    return value.rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ApiError("UPSTREAM_REDIRECT", "API 返回重定向，已停止发送凭据；请检查配置地址。", 502)


class ApiProvider:
    def __init__(self, connection: dict, key: str, *, timeout: float = 15) -> None:
        self.connection = connection
        self.base = validate_endpoint(connection["baseUrl"])
        self.key = key
        self.timeout = timeout
        self.opener = urllib.request.build_opener(_NoRedirect())

    def _open(self, path: str, body: dict | None = None):
        headers = {"Accept": "text/event-stream" if body else "application/json",
                   "Authorization": "Bearer " + self.key}
        data = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            return self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            messages = {401: "API 凭据无效，请重新配置。", 403: "当前账号没有此 API 或模型的权限。",
                        404: "API 路径或模型不存在，请检查接口类型与地址。", 429: "API 请求受限或额度不足，请稍后检查账户。"}
            raise ApiError("API_HTTP_" + str(status), messages.get(status, f"API 请求失败（HTTP {status}），请检查服务状态。"), 502) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise ApiError("API_CONNECTION", "无法连接 API，或等待响应超时；请检查地址和网络。", 502) from None

    def models(self) -> list[dict]:
        with self._open("/models") as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ApiError("API_RESPONSE_TOO_LARGE", "模型列表超过大小限制。", 502)
        try:
            payload = _strict_json(raw)
            values = payload["data"]
            if not isinstance(values, list):
                raise ValueError()
            result = []
            seen = set()
            for item in values[:1000]:
                model = item.get("id") if isinstance(item, dict) else None
                if isinstance(model, str) and 0 < len(model) <= 200 and model not in seen:
                    seen.add(model)
                    result.append({"id": model, "label": model})
            return result
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise ApiError("API_PROTOCOL", "服务未返回有效模型列表；可以手动填写模型 ID。", 502) from None

    def _events(self, response, cancelled: threading.Event):
        total = 0
        data_lines: list[str] = []
        while True:
            if cancelled.is_set():
                raise Cancelled()
            try:
                raw = response.readline(256 * 1024 + 1)
            except (TimeoutError, socket.timeout, OSError, http.client.HTTPException):
                if cancelled.is_set():
                    raise Cancelled() from None
                raise ApiError("API_STREAM_INTERRUPTED", "流式连接中断，已收到的内容保留，不能确认模型正常完成。", 502) from None
            if not raw:
                if data_lines:
                    yield self._decode_event("\n".join(data_lines))
                return
            total += len(raw)
            if total > MAX_RESPONSE or len(raw) > 256 * 1024:
                raise ApiError("API_RESPONSE_TOO_LARGE", "API 输出超过本次任务限制。", 502)
            try:
                line = raw.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError:
                raise ApiError("API_PROTOCOL", "API 流返回无效 UTF-8。", 502) from None
            if not line:
                if data_lines:
                    yield self._decode_event("\n".join(data_lines))
                    data_lines = []
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))

    @staticmethod
    def _decode_event(value: str):
        if value == "[DONE]":
            return None
        try:
            result = _strict_json(value)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, TypeError):
            raise ApiError("API_PROTOCOL", "API 流包含无效事件，任务已停止。", 502) from None

    def run(self, model: str, messages: list[dict], tools: list[dict], *, emit: Callable,
            cancelled: threading.Event, request_tool: Callable) -> dict:
        aliases = {"od_tool_" + str(index): tool for index, tool in enumerate(tools)}
        is_responses = self.connection["kind"] == "openai-responses"
        history = [{"role": entry["role"], "content": entry["text"]} for entry in messages if entry["role"] in ("user", "assistant")]
        total_text = ""
        usage = None
        for round_index in range(8):
            if cancelled.is_set():
                raise Cancelled()
            body = {"model": model, "stream": True}
            if is_responses:
                body.update(input=history, store=False, max_output_tokens=4096)
                if aliases:
                    body["tools"] = [{"type": "function", "name": alias,
                                      "description": tool["description"], "parameters": tool["inputSchema"],
                                      "strict": False} for alias, tool in aliases.items()]
                path = "/responses"
            else:
                body.update(messages=history)
                if aliases:
                    body["tools"] = [{"type": "function", "function": {"name": alias,
                                      "description": tool["description"], "parameters": tool["inputSchema"]}}
                                     for alias, tool in aliases.items()]
                path = "/chat/completions"
            chunks: list[str] = []
            calls: dict[int, dict] = {}
            response_output = []
            completed = False
            with self._open(path, body) as response:
                for event in self._events(response, cancelled):
                    if event is None:
                        continue
                    if "error" in event:
                        raise ApiError("API_FAILED", "模型服务报告错误，本次任务未完成。", 502)
                    delta = ""
                    if is_responses:
                        kind = event.get("type")
                        if kind == "response.output_text.delta":
                            delta = event.get("delta", "")
                        elif kind == "response.completed":
                            completed = True
                            result = event.get("response", {})
                            if not isinstance(result, dict) or result.get("status", "completed") != "completed":
                                raise ApiError("API_INCOMPLETE", "模型结果不是正常完成状态。", 502)
                            response_output = result.get("output", [])
                            usage = result.get("usage")
                        elif kind in ("response.failed", "response.incomplete", "error"):
                            raise ApiError("API_INCOMPLETE", "模型没有正常完成回答；部分内容已保留。", 502)
                    else:
                        choices = event.get("choices", [])
                        if event.get("usage"):
                            usage = event["usage"]
                        if not choices:
                            continue
                        choice = choices[0]
                        if choice.get("finish_reason") is not None:
                            if choice["finish_reason"] not in ("stop", "tool_calls"):
                                raise ApiError("API_INCOMPLETE", "模型输出被截断或未正常结束。", 502)
                            completed = True
                        patch = choice.get("delta", {})
                        delta = patch.get("content") or ""
                        for call in patch.get("tool_calls", []):
                            index = call.get("index")
                            if not isinstance(index, int) or index < 0 or index >= 8:
                                raise ApiError("API_PROTOCOL", "模型工具调用数量或标识无效。", 502)
                            target = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            if call.get("id"):
                                target["id"] = call["id"]
                            fn = call.get("function", {})
                            target["name"] += fn.get("name", "")
                            target["arguments"] += fn.get("arguments", "")
                    if delta:
                        if not isinstance(delta, str):
                            raise ApiError("API_PROTOCOL", "模型文本事件无效。", 502)
                        total_text += delta
                        if len(total_text) > MAX_OUTPUT:
                            raise ApiError("OUTPUT_LIMIT", "回答超过本地显示限制，任务已停止。", 502)
                        chunks.append(delta)
                        emit({"type": "delta", "text": delta})
            if not completed:
                raise ApiError("API_STREAM_INTERRUPTED", "连接已结束但未收到正常完成事件。", 502)
            if is_responses:
                if not isinstance(response_output, list):
                    raise ApiError("API_PROTOCOL", "模型输出结构无效。", 502)
                if not chunks:
                    final_text = "".join(part.get("text", "") for item in response_output
                                         if isinstance(item, dict) and item.get("type") == "message"
                                         for part in item.get("content", [])
                                         if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str))
                    if final_text:
                        if len(total_text) + len(final_text) > MAX_OUTPUT:
                            raise ApiError("OUTPUT_LIMIT", "回答超过本地显示限制，任务已停止。", 502)
                        total_text += final_text
                        chunks.append(final_text)
                        emit({"type": "delta", "text": final_text})
                tool_calls = [{"id": item.get("call_id"), "name": item.get("name"), "arguments": item.get("arguments", "")}
                              for item in response_output if isinstance(item, dict) and item.get("type") == "function_call"]
                history.extend(response_output)
            else:
                tool_calls = list(calls.values())
                if tool_calls:
                    history.append({"role": "assistant", "content": "".join(chunks) or None,
                                    "tool_calls": [{"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["arguments"]}} for call in tool_calls]})
            if not tool_calls:
                if not "".join(chunks).strip():
                    raise ApiError("API_EMPTY_RESULT", "模型未返回可显示的回答，请检查模型能力或重试。", 502)
                return {"text": total_text, "usage": usage}
            if len(tool_calls) > 8:
                raise ApiError("TOOL_LIMIT", "模型请求过多工具。", 502)
            for call in tool_calls:
                if call["name"] not in aliases or not isinstance(call["id"], str) or not call["id"] or len(call["id"]) > 200:
                    raise ApiError("UNKNOWN_TOOL", "模型请求了本次未授权的工具。", 403)
                try:
                    if not isinstance(call["arguments"], str) or len(call["arguments"]) > 65536:
                        raise ValueError()
                    arguments = _strict_json(call["arguments"])
                    if not isinstance(arguments, dict) or arguments:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise ApiError("TOOL_ARGUMENTS", "模型工具参数无效，未执行。", 502) from None
                tool = aliases[call["name"]]
                result = request_tool(tool["name"], arguments)
                output = json.dumps(result, ensure_ascii=False, allow_nan=False)
                if len(output.encode("utf-8")) > 256 * 1024:
                    raise ApiError("TOOL_RESULT_TOO_LARGE", "工具结果超过允许大小。", 502)
                if is_responses:
                    history.append({"type": "function_call_output", "call_id": call["id"], "output": output})
                else:
                    history.append({"role": "tool", "tool_call_id": call["id"], "content": output})
        raise ApiError("TOOL_ROUND_LIMIT", "工具循环达到上限，请检查结果后继续。", 409)
