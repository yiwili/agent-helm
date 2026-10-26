"""Offline process fixture. No credentials, network, or actual Codex invocation."""
import json
import sys
import time
from pathlib import Path

if "--version" in sys.argv:
    print("codex-cli 0.164.0-alpha.11.1")
elif sys.argv[1:] == ["login", "status"]:
    print("Logged in using ChatGPT")
else:
    sys.stdin.read()
    scenario = (Path.cwd()/"scenario.txt").read_text().strip()
    print(json.dumps({"type": "thread.started", "thread_id": "offline-fixture-session"}), flush=True)
    print(json.dumps({"type": "turn.started"}), flush=True)
    if scenario == "slow":
        time.sleep(60)
    elif scenario in ("quota", "permission"):
        print(json.dumps({"type": "turn.failed", "error": {"message": scenario}}), flush=True)
        raise SystemExit(1)
    elif scenario == "malformed":
        print("not json", flush=True)
        raise SystemExit(0)
    elif scenario == "missing-terminal":
        raise SystemExit(0)
    final = {"summary": "offline fixture only", "files": [{"path": "stats.py", "content": "def total(values):\n    return sum(values)\n"}]}
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(final)}}), flush=True)
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}), flush=True)
