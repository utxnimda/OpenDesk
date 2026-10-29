"""Deterministic subprocess fixture; never imports or invokes Codex itself."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--scenario", default="normal")
parser.add_argument("--report", required=True)
args = parser.parse_args()
report = Path(args.report)
scenario = args.scenario
thread_id = "thread-fixture"
turn_id = "turn-fixture"


def record(value):
    with report.open("a", encoding="utf-8") as output:
        output.write(json.dumps(value, ensure_ascii=False) + "\n")


def send(value):
    sys.stdout.buffer.write(json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def respond(request, result):
    send({"id": request["id"], "result": result})


def event(method, params):
    send({"method": method, "params": params})


def complete(text="你好，已完成。", status="completed"):
    event("item/completed", {"threadId": thread_id, "turnId": turn_id,
          "item": {"id": "item-fixture", "type": "agentMessage", "text": text, "phase": "final_answer"}})
    event("turn/completed", {"threadId": thread_id, "turn": {"id": turn_id, "status": status, "error": None}})


for line in sys.stdin.buffer:
    message = json.loads(line)
    record(message)
    method = message.get("method")
    params = message.get("params", {})
    if method == "initialize":
        if scenario == "boolean_id":
            send({"id": True, "result": {"userAgent": "fixture/1"}})
            continue
        if scenario == "timeout":
            continue
        if scenario == "invalid_json":
            sys.stdout.buffer.write(b"not json\n")
            sys.stdout.buffer.flush()
            continue
        if scenario == "large_frame":
            sys.stdout.buffer.write(b"x" * (1024 * 1024 + 5) + b"\n")
            sys.stdout.buffer.flush()
            continue
        if scenario == "exit":
            sys.stderr.write("Bearer private-token-not-for-the-ui\n")
            sys.exit(11)
        if scenario == "auth":
            send({"id": message["id"], "error": {"code": -32000, "message": "Unauthorized: sk-secret-auth-value"}})
            continue
        respond(message, {"userAgent": "fixture/1"})
    elif method == "initialized":
        continue
    elif method == "model/list":
        if not params.get("cursor"):
            respond(message, {"data": [{"id": "catalog-1", "model": "fixture-model", "displayName": "本地模型", "isDefault": True,
                                        "defaultReasoningEffort": "low", "supportedReasoningEfforts": [
                                            {"reasoningEffort": "low", "description": "Light reasoning"},
                                            {"reasoningEffort": "max", "description": "Maximum reasoning"},
                                            {"reasoningEffort": "ultra", "description": "Automatic delegation"}],
                                        "defaultServiceTier": None, "serviceTiers": [{"id": "priority", "name": "Fast", "description": "Increased usage"}]},
                                        {"id": "hidden", "model": "hidden-model", "hidden": True}], "nextCursor": "page-2"})
        else:
            respond(message, {"data": [{"id": "catalog-2", "model": "fixture-model-2", "displayName": "第二模型"}], "nextCursor": None})
    elif method == "project/list":
        respond(message, {"data": [{"id": "project-fixture", "name": "Fixture workspace",
                                    "roots": [{"path": str(report.parent)}]}], "nextCursor": None})
    elif method in ("thread/start", "thread/resume"):
        if scenario == "schema_error":
            send({"id": message["id"], "error": {"code": -32602, "message": "bad sandbox field secret=hidden"}})
            continue
        thread_id = params.get("threadId", thread_id)
        respond(message, {"thread": {"id": "wrong-thread" if scenario == "wrong_thread" else thread_id}})
    elif method == "turn/start":
        respond(message, {"turn": {"id": turn_id, "status": "inProgress"}})
        event("turn/started", {"threadId": thread_id, "turn": {"id": turn_id, "status": "inProgress"}})
        if scenario == "turn_exit":
            sys.stderr.write("password=never-show-this\n")
            sys.exit(12)
        if scenario == "turn_timeout":
            continue
        if scenario == "child":
            child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            record({"ownedChildPid": child.pid})
        if scenario in ("approval", "decline", "wrong_approval", "missing_approval_identity", "large_approval", "unknown_request"):
            approval_params = {"threadId": "foreign-thread" if scenario == "wrong_approval" else thread_id,
                               "turnId": turn_id, "command": "read file api_key=private-key sk-secret-value Bearer private-token", "reason": "读取需要批准"}
            if scenario == "missing_approval_identity":
                del approval_params["threadId"]
            if scenario == "large_approval":
                approval_params["command"] = "x" * 4001
            send({"id": "approval-fixture", "method": "unknown/request" if scenario == "unknown_request" else "item/commandExecution/requestApproval",
                  "params": approval_params})
            continue
        event("item/agentMessage/delta", {"threadId": "foreign-thread", "turnId": turn_id, "itemId": "foreign", "delta": "不得混入"})
        event("item/agentMessage/delta", {"threadId": thread_id, "turnId": turn_id, "itemId": "item-fixture", "delta": "你好，"})
        if scenario in ("cancel", "ignore_cancel"):
            continue
        event("item/agentMessage/delta", {"threadId": thread_id, "turnId": turn_id, "itemId": "item-fixture", "delta": "已完成。"})
        event("thread/tokenUsage/updated", {"threadId": thread_id, "tokenUsage": {"last": {"inputTokens": 5, "outputTokens": 3, "secret": "must-not-copy"}}})
        complete("" if scenario == "empty" else "你好，已完成。")
    elif method == "turn/interrupt":
        respond(message, {})
        if scenario != "ignore_cancel":
            event("turn/completed", {"threadId": thread_id, "turn": {"id": turn_id, "status": "interrupted", "error": None}})
    elif message.get("id") == "approval-fixture":
        if "error" in message:
            continue
        decision = message["result"]["decision"]
        complete("已拒绝操作。" if decision == "decline" else "已批准操作。")
