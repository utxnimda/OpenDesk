"""Deterministic Cursor protocol peer. Does not invoke Cursor or a model."""
import argparse
import json
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--scenario", default="normal")
parser.add_argument("--report", required=True)
args, command = parser.parse_known_args()
report = Path(args.report)
scenario = args.scenario
session = "cursor-fixture"
active = None
model_config = {"model":"fixture-b","effort":"high"}


def record(value):
    with report.open("a", encoding="utf-8") as output:
        output.write(json.dumps(value, ensure_ascii=False) + "\n")


def send(value):
    sys.stdout.buffer.write(json.dumps({"jsonrpc": "2.0", **value}, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def reply(message, result):
    send({"id": message["id"], "result": result})


def delta(text, selected=None, category="agent_message_chunk"):
    send({"method": "session/update", "params": {"sessionId": selected or session,
          "update": {"sessionUpdate": category, "content": {"type": "text", "text": text}}}})


record({"command": command})
if "--list-models" in command:
    if scenario == "models_failure":
        sys.stderr.write("login required sk-secret-value\n")
        sys.exit(1)
    print("Available models")
    print("\x1b[32mfixture-a - Fixture A\x1b[0m")
    print("fixture-b[effort=high] - Fixture B")
    print("fixture-a - Duplicate")
    sys.exit(0)

for raw in sys.stdin.buffer:
    value = json.loads(raw)
    record(value)
    method, params = value.get("method"), value.get("params", {})
    if method == "initialize":
        if scenario == "invalid_json":
            sys.stdout.buffer.write(b"broken\n")
            sys.stdout.buffer.flush()
        elif scenario == "timeout":
            continue
        else:
            reply(value, {"protocolVersion": 2 if scenario == "wrong_version" else 1,
                          "agentCapabilities": {"loadSession": scenario != "no_resume"}})
    elif method == "cursor/list_available_models":
        if scenario == "models_failure":
            send({"id": value["id"], "error": {"code": -32000, "message": "Authentication required sk-secret-value"}})
        else:
            reply(value, {"models": [{"value": "fixture-a", "name": "Fixture A", "configOptions": []},
                  {"value": "fixture-b", "name": "Fixture B", "configOptions": [{"id": "effort", "name": "Effort", "currentValue": "high", "options": [{"value":"low","name":"Low"},{"value":"high","name":"High"}]}]},
                  {"value": "fixture-a", "name": "Duplicate", "configOptions": []}]})
    elif method in ("session/new", "session/load"):
        if scenario == "auth":
            send({"id": value["id"], "error": {"code": -32000, "message": "auth_required sk-secret-value"}})
        else:
            session = params.get("sessionId", session)
            if method == "session/load":
                delta("Previous answer must not replay into new reply")
            reply(value, {"sessionId": session, "modes": {"currentModeId": "ask"}})
    elif method == "session/set_mode":
        reply(value, {})
    elif method == "session/set_model":
        if scenario == "unsupported_model":
            send({"id": value["id"], "error": {"code": -32601, "message": "unsupported"}})
        elif not params.get("modelId", "").endswith("]"):
            send({"id": value["id"], "error": {"code": -32602, "message": "Invalid params", "data": {"message": "Invalid model value"}}})
        else:
            reply(value, {})
    elif method == "session/set_config_option":
        model_config[params["configId"]]=params["value"]
        if scenario=="options_mismatch":
            model_config["effort"]="high"
        reply(value,{"configOptions":[{"id":key,"currentValue":current} for key,current in model_config.items()]})
    elif method == "session/prompt":
        active = value
        if scenario == "exit":
            sys.exit(0)
        if scenario == "empty":
            reply(value, {"stopReason": "end_turn"})
            continue
        if scenario == "mode_change":
            send({"method": "session/update", "params": {"sessionId": session,
                  "update": {"sessionUpdate": "current_mode_update", "currentModeId": "agent"}}})
            continue
        if scenario in ("approval", "wrong_approval", "always_only", "unknown_request"):
            options = [{"optionId": "once", "name": "Allow once", "kind": "allow_once"},
                       {"optionId": "deny", "name": "Reject once", "kind": "reject_once"}]
            if scenario == "always_only":
                options = [{"optionId": "all", "name": "Allow always", "kind": "allow_always"}]
            send({"id": "permission-fixture", "method": "terminal/create" if scenario == "unknown_request" else "session/request_permission",
                  "params": {"sessionId": "other-session" if scenario == "wrong_approval" else session,
                             "options": options, "toolCall": {"title": "Read selected file", "rawInput": "api_key=private-token"}}})
            continue
        delta("private reasoning", category="agent_thought_chunk")
        delta("other conversation", selected="other-session")
        delta("你好")
        if scenario in ("cancel", "ignore_cancel", "turn_timeout"):
            continue
        delta("，已完成。")
        reply(value, {"stopReason": "max_tokens" if scenario == "incomplete" else "end_turn"})
    elif method == "session/cancel":
        if scenario != "ignore_cancel":
            reply(active, {"stopReason": "cancelled"})
    elif value.get("id") == "permission-fixture":
        delta("权限请求已处理。")
        reply(active, {"stopReason": "end_turn"})
