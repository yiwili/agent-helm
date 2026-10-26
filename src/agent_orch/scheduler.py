from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from .adapters.base import ProviderBlocked
from .adapters.codex_cli import CodexAdapter
from .adapters.manual import ManualAdapter
from .adapters.mock import MockAdapter
from .brain import CodexBrain, MockBrain
from .models import OrchError, digest, validate_review
from .verifier import verify
from .supervisor import ProcessTreeUnconfirmed
from .workspace import apply_files, copy_snapshot, diff, manifest, change_details


class Scheduler:
    def __init__(self, store, *, enable_codex=False, brain="mock", manual_result=None):
        self.store = store
        self.codex = CodexAdapter(enabled=enable_codex)
        self.adapters = {"mock": MockAdapter(), "manual": ManualAdapter(manual_result), "codex": self.codex}
        self.brain_name = brain
        self.brain = CodexBrain(self.codex) if brain == "codex" else MockBrain()
        self.owner = uuid.uuid4().hex

    def cancelled(self, run, task):
        t = next(t for t in self.store.tasks(run) if t["id"] == task)
        return t["state"] in ("cancel_requested", "needs_reconciliation") or (t["lease"] or 0) < time.time() or not self.store.authorized(run)

    def _settle_error(self, run, task, fence, error):
        current = next(x for x in self.store.tasks(run) if x["id"] == task)
        if current["state"] in ("needs_reconciliation", "accepted", "cancelled", "failed", "timed_out", "escalated"):
            return
        if isinstance(error, ProcessTreeUnconfirmed) or (current["lease"] or 0) < time.time():
            self.store.transition(run, task, "needs_reconciliation", payload={"reason": "process stop or lease ownership unconfirmed"})
        elif current["state"] == "cancel_requested":
            self.store.transition(run, task, "cancelled", fence, {"local_stop_confirmed": True, "remote_session_stop": "unverified"})
        elif not self.store.authorized(run):
            self.store.transition(run, task, "cancel_requested", payload={"reason": "approval expired or revoked"})
            self.store.transition(run, task, "cancelled", fence, {"local_stop_confirmed": True, "remote_session_stop": "unverified"})
        elif isinstance(error, asyncio.CancelledError):
            self.store.transition(run, task, "needs_reconciliation", payload={"reason": "supervisor interrupted; verify external state"})
        elif isinstance(error, TimeoutError):
            self.store.transition(run, task, "timed_out", fence, {"reason": "deadline; supervised local process tree stopped", "remote_session_stop": "unverified"})
        else:
            from .adapters.codex_cli import redact
            state = "blocked" if isinstance(error, ProviderBlocked) and current["state"] == "running" else "failed"
            self.store.transition(run, task, state, fence, {"reason": redact(str(error))[:1000], "error_type": type(error).__name__})

    async def execute(self, run):
        self.store.recover()
        r = self.store.run(run)
        plan = json.loads(r["plan"])
        for t in self.store.ordered_tasks(run):
            if t["state"] != "queued":
                continue
            task = json.loads(t["spec"])
            if self.brain_name == "mock" and task["provider"] != "mock":
                self.store.transition(run, t["id"], "blocked", payload={"reason": "Real/manual workers require codex or interactive review"})
                continue
            if task["provider"] == "codex" and not self.codex.enabled:
                self.store.transition(run, t["id"], "blocked", payload={"reason": "Codex disabled; no API fallback"})
                continue
            while True:
                claimed = self.store.claim(run, t["id"], self.owner, task["wall_seconds"] + 360)
                if not claimed:
                    break
                attempt, fence = claimed
                try:
                    await self._attempt(r, task, attempt, fence)
                except asyncio.CancelledError:
                    self._settle_error(run, t["id"], fence, asyncio.CancelledError())
                    break
                except Exception as e:
                    self._settle_error(run, t["id"], fence, e)
                    break
                current = next(x for x in self.store.tasks(run) if x["id"] == t["id"])
                if current["state"] != "rework":
                    break
                if attempt > plan["max_rework_rounds"]:
                    self.store.transition(run, t["id"], "escalated", fence)
                    break
                self.store.transition(run, t["id"], "queued", fence)
        return self.status(run)

    async def _attempt(self, r, task, attempt, fence):
        run, tid = r["id"], task["id"]
        cancelled = lambda: self.cancelled(run, tid)
        def process_event(kind, data):
            with self.store.transaction():
                self.store.event(run, tid, attempt, kind, {**data, "fence": fence})
        self.codex.process_observer = process_event
        root = self.store.root / "runs" / run
        root.mkdir(parents=True, exist_ok=True)
        original = root / "original"
        if not original.exists():
            from .git_workspace import source_snapshot
            expected = self.store.submission(run).get("source_snapshot")
            if expected and source_snapshot(r["source"]) != expected:
                raise OrchError("Source Git baseline or tree changed since submit")
            snapshot = copy_snapshot(r["source"], original)
            if digest(snapshot) != r["source_hash"]:
                raise OrchError("Source changed since submit")
            if expected and source_snapshot(r["source"]) != expected:
                raise OrchError("Source changed during snapshot copy")
        if digest(manifest(original)) != r["source_hash"]:
            raise OrchError("Original snapshot integrity mismatch")
        # v1 is serialized. Start each task on all earlier accepted candidates.
        previous = original
        for row in self.store.accepted_tasks(run):
            previous = root / row["id"] / str(row["attempt"]) / "candidate"
            self._check_accepted_hash(run, row, previous)
        base = root / tid / str(attempt) / "baseline"
        candidate = base.parent / "candidate"
        base.parent.mkdir(parents=True, exist_ok=True)
        copy_snapshot(previous, base)
        copy_snapshot(base, candidate)
        profile = self.store.verifier_profile(run, task["verifier_profile"])
        baseline = await verify(base, cancelled=cancelled, profile=profile)
        before_worker = digest(manifest(candidate))
        adapter = self.adapters[task["provider"]]
        result = await asyncio.wait_for(adapter.execute(task, candidate, attempt, cancelled), task["wall_seconds"])
        if digest(manifest(candidate)) != before_worker:
            raise OrchError("Read-only worker unexpectedly changed workspace")
        if cancelled():
            raise asyncio.CancelledError()
        apply_files(candidate, result, task["allowed_paths"])
        self.store.transition(run, tid, "verifying", fence)
        verification = await verify(candidate, cancelled=cancelled, profile=profile)
        changed, patch = diff(base, candidate)
        tree = manifest(candidate)
        evidence = {"task": task, "attempt": attempt, "provider": task["provider"], "brain": self.brain_name,
                    "worker_claim": result["summary"], "provider_metadata": getattr(adapter, "last_metadata", None),
                    "baseline": baseline, "baseline_hash": digest(manifest(base)), "candidate_hash": digest(tree),
                    "manifest": tree, "changed_paths": changed, "scope_ok": set(changed) <= set(task["allowed_paths"]),
                    "changes": change_details(base, candidate), "source_snapshot": self.store.submission(run).get("source_snapshot"),
                    "diff": patch, "verification": verification,
                    "usage": {"tokens": None, "amount": None, "source": "unavailable"}}
        (base.parent / "evidence.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
        self.store.transition(run, tid, "reviewing", fence)
        if self.brain_name == "interactive":
            self.store.save_evidence(run, tid, attempt, evidence, {"decision": "pending", "candidate_hash": evidence["candidate_hash"]}, fence)
            return
        review = validate_review(await self.brain.review(evidence, candidate, cancelled=cancelled), evidence)
        if self.brain_name == "codex":
            evidence["reviewer_metadata"] = self.codex.last_metadata
        if digest(manifest(candidate)) != evidence["candidate_hash"]:
            raise OrchError("Candidate changed during review")
        if cancelled():
            raise asyncio.CancelledError()
        self.store.complete_review(run, tid, attempt, fence, evidence, review, self.brain_name)

    def _check_accepted_hash(self, run, task, path):
        row = self.store.db.execute("SELECT evidence FROM attempts WHERE run_id=? AND task_id=? AND number=?", (run, task["id"], task["attempt"])).fetchone()
        if not row or not row[0] or digest(manifest(path)) != json.loads(row[0])["candidate_hash"]:
            raise OrchError("Accepted candidate has drifted")

    def status(self, run):
        r = self.store.run(run)
        return {"run_id": run, "state": r["state"], "tasks": [{k: t[k] for k in ("id", "state", "attempt")} for t in self.store.tasks(run)]}

    def interactive_review(self, run, task, decision):
        t = next((x for x in self.store.tasks(run) if x["id"] == task), None)
        if not t or t["state"] != "reviewing":
            raise OrchError("Task is not awaiting review")
        r = self.store.run(run)
        self.store.check_approval(r["approval"], json.loads(r["plan"]), r["source"])
        row = self.store.db.execute("SELECT evidence FROM attempts WHERE run_id=? AND task_id=? AND number=?", (run, task, t["attempt"])).fetchone()
        evidence = json.loads(row[0])
        validate_review(decision, evidence)
        candidate = self.store.root / "runs" / run / task / str(t["attempt"]) / "candidate"
        if digest(manifest(candidate)) != evidence["candidate_hash"]:
            raise OrchError("Candidate drift invalidates review")
        state = self.store.complete_review(run, task, t["attempt"], t["fence"], evidence, decision, "local-user")
        if state == "rework":
            cap = json.loads(r["plan"])["max_rework_rounds"]
            self.store.transition(run, task, "queued" if t["attempt"] <= cap else "escalated")
        return self.status(run)
