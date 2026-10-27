"""Explicit live Codex/Qoder CN pipeline on a newly generated synthetic Git repo.

No existing repository input, API fallback, GUI, token extraction, or publication.
Optional fault injection is clearly recorded and never attributed to the model.
"""
import argparse
import asyncio
import copy
import json
import sys
from pathlib import Path

from agent_orch.brain import CodexBrain
from agent_orch.cli import export_run
from agent_orch.git_workspace import git, make_git_fixture, source_snapshot
from agent_orch.integrator import integrate
from agent_orch.models import OrchError, digest
from agent_orch.scheduler import Scheduler
from agent_orch.storage import Store


class InjectOneCandidateFault:
    def __init__(self, adapter, output):
        self.adapter = adapter
        self.output = output
        self.enabled = adapter.enabled
        self.last_metadata = None

    async def diagnose(self, *args):
        result = await self.adapter.diagnose(*args)
        self.last_metadata = self.adapter.last_metadata
        return result

    async def execute(self, task, workspace, attempt, cancelled):
        result = await self.adapter.execute(task, workspace, attempt, cancelled)
        self.last_metadata = copy.deepcopy(self.adapter.last_metadata)
        if attempt == 1:
            (self.output/"original-worker-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            self.last_metadata["fault_injection"] = {"source": "explicit-test-harness", "original_result_hash": digest(result), "kind": "wrong-empty-list-result"}
            result = {"summary": "Test harness deliberately corrupted this candidate; not a model failure claim", "files": [{"path": "stats.py", "content": "def total(values):\n    return sum(values) if values else 999\n"}]}
        return result


async def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--qoder-cn-bin", required=True)
    p.add_argument("--brain", choices=["codex", "qoder-cn"], default="codex")
    p.add_argument("--worker", choices=["codex", "qoder-cn"], default="qoder-cn")
    p.add_argument("--inject-first-fault", action="store_true")
    args = p.parse_args()
    if args.brain == args.worker:
        raise OrchError("This cross-provider smoke requires different brain and worker products")
    args.output = args.output.resolve()
    if args.output.is_relative_to(Path(__file__).resolve().parents[1]):
        raise OrchError("Live evidence and generated workspaces must be outside this repository")
    args.output.mkdir(parents=True, exist_ok=False)
    source = make_git_fixture(args.output/"synthetic-git")
    (source/"README.txt").write_text("staged synthetic note\n", encoding="utf-8")
    git(source, "add", "--", "README.txt")
    (source/"README.txt").write_text("unstaged synthetic note\n", encoding="utf-8")
    (source/"untracked 中文.txt").write_text("synthetic only", encoding="utf-8")
    before = source_snapshot(source)
    store = Store(args.output/"state")
    try:
        scheduler = Scheduler(store, enable_codex=True, enable_qoder_cn=True, qoder_cn_bin=args.qoder_cn_bin, brain=args.brain)
        planner = CodexBrain(scheduler.brain_adapter)
        plan = await planner.plan({"goal": "Fix total([]) to return 0 and preserve other totals", "source": (source/"stats.py").read_text(encoding="utf-8")}, source, worker_provider=args.worker)
        planning_metadata = copy.deepcopy(scheduler.brain_adapter.last_metadata)
        if len(plan["tasks"]) != 1 or any(t["allowed_paths"] != ["stats.py"] or t["verifier_profile"] != "fixture-stats" or not t.get("milestone_review") or t["wall_seconds"] > 120 for t in plan["tasks"]):
            raise OrchError("Planner exceeded this smoke's fixed one-task fixture scope")
        (args.output/"plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
        scope = store.approve(plan, source, actor="explicit-cross-provider-synthetic-smoke")
        run = store.submit(plan, source, scope, "cross-provider-smoke")
        if args.inject_first_fault:
            scheduler.adapters[args.worker] = InjectOneCandidateFault(scheduler.adapters[args.worker], args.output)
        status = await scheduler.execute(run)
        attempts = [dict(r) for r in store.db.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY number", (run,))]
        reviews = [json.loads(r["body"]) for r in store.db.execute("SELECT body FROM reviews WHERE run_id=? ORDER BY rowid", (run,))]
        events = store.events(run)
        result = {**status, "brain": args.brain, "worker": args.worker, "planning_metadata": planning_metadata,
                  "source_unchanged": before == source_snapshot(source), "billing_verified": False,
                  "fault_injection": args.inject_first_fault, "review_decisions": [r["decision"] for r in reviews],
                  "milestone_approved": any(e["type"] == "milestone_approved" for e in events),
                  "rework_feedback_delivered": any(json.loads(a["evidence"]).get("rework_context") for a in attempts if a["evidence"])}
        if status["state"] == "accepted":
            integration = await integrate(store, run)
            result.update(integration_passed=integration["verification"]["passed"], candidate_hash=integration["candidate_hash"],
                          shared_git_metadata=(Path(integration["workspace"])/".git").exists())
        result["passed"] = result["state"] == "accepted" and result["source_unchanged"] and result["milestone_approved"] and result.get("integration_passed") and not result.get("shared_git_metadata", True)
        if args.inject_first_fault:
            result["passed"] = result["passed"] and len(attempts) >= 2 and result["review_decisions"][0] == "rework" and bool(result["rework_feedback_delivered"])
        export_run(store, run, args.output/"evidence")
        (args.output/"report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["passed"] else 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
