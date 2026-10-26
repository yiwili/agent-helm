"""Explicit live smoke. Only generated synthetic fixtures; never part of default tests.

Use: PYTHONPATH=src python scripts/verify_codex_lifecycle.py --output NEW_DIRECTORY
Starts at most two Codex exec calls and cancels each locally. Remote cancellation
and billing settlement remain unverified. No API fallback or credential changes.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path

from agent_orch.brain import fixture_plan
from agent_orch.models import digest
from agent_orch.scheduler import Scheduler
from agent_orch.storage import Store
from agent_orch.workspace import make_fixture, manifest


async def scenario(root, role):
    root.mkdir()
    source = make_fixture(root/"synthetic-fixture")
    before = digest(manifest(source))
    store = Store(root/"state")
    timer = None
    try:
        plan = fixture_plan(provider="codex" if role == "worker" else "manual")
        scope = store.approve(plan, source, actor="explicit-live-synthetic-lifecycle-smoke")
        run = store.submit(plan, source, scope, "lifecycle-"+role)
        original_event = store.event
        def observe(run_id, task, attempt, kind, payload, **kwargs):
            nonlocal timer
            result = original_event(run_id, task, attempt, kind, payload, **kwargs)
            if kind == "process_started" and payload.get("stage") == "exec" and timer is None:
                timer = asyncio.get_running_loop().call_later(0.75, store.cancel, run)
            return result
        store.event = observe
        response = root/"manual-result.json"
        response.write_text(json.dumps({"summary": "synthetic manual fixture", "files": [{"path": "stats.py", "content": "def total(values):\n    return sum(values)\n"}]}), encoding="utf-8")
        scheduler = Scheduler(store, enable_codex=True, brain="interactive" if role == "worker" else "codex", manual_result=response)
        status = await scheduler.execute(run)
        events = store.events(run)
        stopped = [e["payload"] for e in events if e["type"] == "process_stopped" and e["payload"].get("stage") == "exec"]
        result = {"role": role, **status, "local_process_stop_confirmed": bool(stopped) and all(x["local_stop_confirmed"] for x in stopped),
                  "source_unchanged": before == digest(manifest(source)), "accepted_event_count": sum(e["type"] == "accepted" for e in events),
                  "process_reports": stopped, "remote_session_stop": "unverified", "billing_verified": False}
        result["provider_process_observed"] = bool(stopped) and (os.name != "nt" or all((x.get("observed_process_count") or 0) >= 2 for x in stopped))
        result["passed"] = result["state"] == "cancelled" and result["local_process_stop_confirmed"] and result["source_unchanged"] and result["accepted_event_count"] == 0 and result["provider_process_observed"]
        (root/"report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    finally:
        if timer:
            timer.cancel()
        store.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    results = [await scenario(args.output/role, role) for role in ("worker", "reviewer")]
    (args.output/"summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
