"""Offline MCP client smoke. Only new synthetic fixtures and Mock workers."""
import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


def smoke(command, root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    state = root/"state"
    env = dict(os.environ)
    if len(command) == 1:
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
    else:
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]/"src")
    proc = subprocess.Popen([*command, "--state-root", str(state), "mcp"], cwd=root, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    inbox = queue.Queue()
    def reader():
        for line in proc.stdout:
            inbox.put(line)
    threading.Thread(target=reader, daemon=True).start()
    number = 0
    def request(method, params=None, notification=False):
        nonlocal number
        number += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            body["id"] = number
        proc.stdin.write((json.dumps(body)+"\n").encode())
        proc.stdin.flush()
        if notification:
            return None
        result = json.loads(inbox.get(timeout=30).decode("utf-8"))
        assert result.get("id") == number and "error" not in result, result
        return result["result"]
    def call(name, **arguments):
        result = request("tools/call", {"name": name, "arguments": arguments})
        assert not result.get("isError"), result
        return result["structuredContent"]
    try:
        initialized = request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "stdlib-offline-smoke", "version": "1"}})
        request("notifications/initialized", notification=True)
        listed = request("tools/list")["tools"]
        assert not any(t["name"] in ("approve", "budgets_create", "shell", "push") for t in listed)
        capabilities = call("helm_capabilities")
        assert capabilities["enabled_workers"] == ["mock"]
        assert capabilities["version"] == initialized["serverInfo"]["version"]
        fixture = call("helm_fixture_create")
        plan = {"schema_version": 1, "goal": "Offline synthetic MCP repair", "max_rework_rounds": 2, "api_budget": 0,
                "tasks": [{"id": "fix-total", "goal": "Fix total([])", "provider": "mock", "dependencies": [],
                           "allowed_paths": ["stats.py"], "acceptance": ["Empty returns zero", "Other sums remain correct"],
                           "verifier_profile": "fixture-stats", "wall_seconds": 120, "milestone_review": False, "mock_behavior": "bad-first"}]}
        proposal = call("helm_plan_prepare", workspace_id=fixture["workspace_id"], plan=plan)
        assert call("helm_submit", proposal_id=proposal["proposal_id"])["state"] == "approval_required"
        # Trusted test harness acts as the human; this is not an MCP tool.
        approved = subprocess.run([*command, "--state-root", str(state), "service-approve", proposal["proposal_id"], "--yes"],
                                  cwd=root, env=env, capture_output=True, timeout=30)
        assert approved.returncode == 0, approved.stderr.decode("utf-8", errors="replace")
        run = call("helm_submit", proposal_id=proposal["proposal_id"])["run_id"]
        assert call("helm_submit", proposal_id=proposal["proposal_id"])["run_id"] == run
        call("helm_run_start", run_id=run)
        deadline = time.monotonic()+60
        while True:
            status = call("helm_run_get", run_id=run)
            if not status["active_in_this_session"]:
                break
            assert time.monotonic() < deadline, status
            time.sleep(0.05)
        assert status["state"] == "accepted", status
        evidence = call("helm_evidence_get", run_id=run, task_id="fix-total")
        assert [r["decision"] for r in evidence["reviews"]] == ["rework", "accept"]
        integration = call("helm_integrate", run_id=run)
        assert integration["verification_passed"] and integration["merged"] is False
        assert call("helm_integrate", run_id=run)["id"] == integration["id"]
        assert "else None" in (state/"service-workspaces"/fixture["workspace_id"]/"stats.py").read_text(encoding="utf-8")
        result = {"passed": True, "version": capabilities["version"], "transport": "stdio", "tool_count": len(listed), "human_approval_gate": True,
                  "mock_rework": True, "integration_passed": True, "source_unchanged": True, "real_provider_calls": 0}
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=12)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        stderr = proc.stderr.read().decode("utf-8", errors="replace")
        proc.stdout.close()
        proc.stderr.close()
        assert proc.returncode == 0, stderr
        assert inbox.empty(), "Non-protocol output after stdio shutdown"
    (root/"report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    command = [str(args.executable.resolve())] if args.executable else [sys.executable, "-m", "agent_orch"]
    print(json.dumps(smoke(command, args.output), indent=2))
