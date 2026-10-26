"""Synthetic Git smoke; real Codex requires explicit --real-codex.

Creates its own repository with staged, unstaged, untracked, and ignored content.
Never accepts a business repository path as input. Reports remain private.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from agent_orch.brain import fixture_plan
from agent_orch.git_workspace import git, make_git_fixture, source_snapshot
from agent_orch.integrator import integrate
from agent_orch.scheduler import Scheduler
from agent_orch.storage import Store


async def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--real-codex", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = make_git_fixture(args.output/"synthetic-git")
    (source/"README.txt").write_text("staged synthetic note\n", encoding="utf-8")
    git(source, "add", "--", "README.txt")
    (source/"README.txt").write_text("unstaged synthetic note\n", encoding="utf-8")
    (source/"untracked 中文.txt").write_text("synthetic untracked file", encoding="utf-8")
    (source/".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (source/"ignored.txt").write_text("synthetic ignored content", encoding="utf-8")
    before = source_snapshot(source)
    store = Store(args.output/"state")
    try:
        plan = fixture_plan(provider="codex" if args.real_codex else "mock")
        scope = store.approve(plan, source, actor="explicit-synthetic-git-smoke")
        run = store.submit(plan, source, scope, "synthetic-git-smoke")
        status = await Scheduler(store, enable_codex=args.real_codex, brain="codex" if args.real_codex else "mock").execute(run)
        result = {**status, "real_codex": args.real_codex, "source_unchanged": source_snapshot(source) == before,
                  "head_commit": before["head_commit"], "original_status": before["status"], "billing_verified": False}
        if status["state"] == "accepted":
            integration = await integrate(store, run)
            target = Path(integration["workspace"])
            result.update(integration_passed=integration["verification"]["passed"],
                          shared_git_metadata=(target/".git").exists(),
                          ignored_file_copied=(target/"ignored.txt").exists(),
                          candidate_hash=integration["candidate_hash"])
        result["passed"] = result["state"] == "accepted" and result["source_unchanged"] and result.get("integration_passed") and not result.get("shared_git_metadata", True) and not result.get("ignored_file_copied", True)
        (args.output/"report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["passed"] else 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
