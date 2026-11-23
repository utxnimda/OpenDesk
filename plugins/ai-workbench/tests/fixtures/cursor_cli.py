"""Synthetic Cursor print-mode stream with real pipes and process exits."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

parser = argparse.ArgumentParser()
parser.add_argument("--scenario", default="normal")
parser.add_argument("--report", required=True)
args, command = parser.parse_known_args()
scenario = args.scenario
with Path(args.report).open("a", encoding="utf-8") as report:
    report.write(json.dumps({"command": command}) + "\n")
if "models" in command:
    if scenario == "catalog_failure":
        sys.stderr.write("Authentication required sk-secret-value")
        raise SystemExit(1)
    if scenario == "catalog_empty":
        print("Available models")
        raise SystemExit(0)
    if scenario == "catalog_timeout":
        time.sleep(20)
    print("Available models\n")
    print("\x1b[32mclaude-fixture-high - Claude Fixture High (default)\x1b[0m")
    print("claude-fixture-low - Claude Fixture Low")
    print("claude-fixture-high - Duplicate")
    print("Tip: use --model <id>")
    raise SystemExit(0)

if scenario == "blocked_input":
    time.sleep(20)
prompt = sys.stdin.read()
with Path(args.report).open("a", encoding="utf-8") as report:
    report.write(json.dumps({"prompt": prompt}) + "\n")
if scenario in ("quota", "untrusted"):
    sys.stderr.write("ActionRequiredError: Your team has reached its usage limit sk-secret-value" if scenario == "quota"
                     else "Workspace is not trusted. Pass --trust.")
    raise SystemExit(1)

session = command[command.index("--resume") + 1] if "--resume" in command else "cli-fixture-session"


def send(value):
    print(json.dumps({"session_id": session, **value}, ensure_ascii=False), flush=True)


send({"type": "system", "subtype": "init", "model": "Other" if scenario == "wrong_model" else "Claude Fixture High"})
if scenario == "invalid_json":
    print("broken", flush=True)
    raise SystemExit(0)
if scenario == "timeout":
    time.sleep(20)


def assistant(text, **fields):
    send({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}, **fields})


send({"type": "user", "message": {"content": [{"type": "text", "text": prompt}]}})
send({"type": "thinking", "text": "private reasoning"})
if scenario != "final_only":
    assistant("你好", timestamp_ms=1, **({"session_id":"other"} if scenario == "wrong_session" else {}))
    if scenario in ("cancel", "cancel_after_result"):
        time.sleep(.2)
    if scenario == "cancel":
        time.sleep(20)
    assistant("你好", timestamp_ms=2, model_call_id="duplicate-buffered-flush")
    assistant("，完成。", timestamp_ms=3)
    assistant("你好，完成。")
if scenario == "truncated":
    raise SystemExit(0)
send({"type": "result", "subtype": "success", "is_error": False,
      "result": "" if scenario == "empty" else "different" if scenario == "inconsistent" else "你好，完成。"})
if scenario == "nonzero_after_result":
    raise SystemExit(1)
if scenario == "cancel_after_result":
    time.sleep(20)
