from __future__ import annotations

import json
import sqlite3
import time
import uuid
import re
from contextlib import contextmanager
from pathlib import Path

from .models import OrchError, TERMINAL, TRANSITIONS, canonical, digest, utcnow, validate_plan, validate_review


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        existed = (self.root / "state.db").exists()
        self.db = sqlite3.connect(self.root / "state.db", timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        if existed:
            try:
                version = self.db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
                if version != 1:
                    raise OrchError("Unsupported database version")
            except BaseException:
                self.db.close()
                raise
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
          INSERT OR IGNORE INTO schema_migrations VALUES(1);
          CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, plan_hash TEXT, source TEXT,
            expires REAL, revoked INTEGER DEFAULT 0, actor TEXT);
          CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, idem TEXT UNIQUE, body_hash TEXT,
            plan TEXT, source TEXT, source_hash TEXT, approval TEXT, state TEXT, created TEXT);
          CREATE TABLE IF NOT EXISTS tasks(run_id TEXT, id TEXT, spec TEXT, state TEXT,
            attempt INTEGER DEFAULT 0, fence INTEGER DEFAULT 0, lease REAL, owner TEXT,
            PRIMARY KEY(run_id,id), FOREIGN KEY(run_id) REFERENCES runs(id));
          CREATE TABLE IF NOT EXISTS attempts(run_id TEXT, task_id TEXT, number INTEGER,
            fence INTEGER, state TEXT, evidence TEXT, PRIMARY KEY(run_id,task_id,number));
          CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE, run_id TEXT, task_id TEXT, attempt INTEGER, attempt_seq INTEGER,
            utc TEXT, type TEXT, payload TEXT);
          CREATE TABLE IF NOT EXISTS artifacts(run_id TEXT, task_id TEXT, attempt INTEGER,
            path TEXT, sha256 TEXT, size INTEGER);
          CREATE TABLE IF NOT EXISTS reviews(run_id TEXT, task_id TEXT, attempt INTEGER, body TEXT);
          CREATE TABLE IF NOT EXISTS provider_health(provider TEXT PRIMARY KEY, body TEXT);
          CREATE TABLE IF NOT EXISTS integrations(id TEXT PRIMARY KEY, run_id TEXT, body TEXT);
        ''')
        if self.db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] != 1:
            raise OrchError("Unsupported database version")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    def event(self, run, task, attempt, kind, payload, event_id=None):
        event_id = event_id or uuid.uuid4().hex
        existing = self.db.execute("SELECT run_id,task_id,attempt,type,payload FROM events WHERE event_id=?", (event_id,)).fetchone()
        if existing:
            if tuple(existing) != (run, task, attempt, kind, canonical(payload)):
                raise OrchError("Event ID reused with conflicting content")
            return
        seq = self.db.execute("SELECT COALESCE(MAX(attempt_seq),0)+1 FROM events WHERE run_id=? AND task_id=? AND attempt=?", (run, task, attempt)).fetchone()[0]
        self.db.execute("INSERT OR IGNORE INTO events(event_id,run_id,task_id,attempt,attempt_seq,utc,type,payload) VALUES(?,?,?,?,?,?,?,?)", (event_id, run, task, attempt, seq, utcnow(), kind, canonical(payload)))

    def approve(self, plan, source, actor="local-user", ttl=3600):
        validate_plan(plan)
        from .git_workspace import source_snapshot
        from .verifier_registry import load_profile
        source = str(Path(source).resolve())
        scope = "scope-" + uuid.uuid4().hex
        frozen = {"source_snapshot": source_snapshot(source),
                  "verifiers": {t["verifier_profile"]: load_profile(self.root, t["verifier_profile"]) for t in plan["tasks"]}}
        for profile in frozen["verifiers"].values():
            if any(Path(p).is_relative_to(Path(source)) for p in profile.get("pinned_files", {})):
                raise OrchError("Trusted acceptance programs must be outside the source workspace")
        path = self.root/"approvals"/(scope+".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical(frozen), encoding="utf-8")
        with self.transaction():
            self.db.execute("INSERT INTO approvals VALUES(?,?,?,?,0,?)", (scope, digest(plan), source, time.time() + ttl, actor))
        return scope

    def submit(self, plan, source, scope, idem):
        validate_plan(plan)
        from .git_workspace import source_snapshot
        source = str(Path(source).resolve())
        if not idem or len(idem) > 200:
            raise OrchError("Idempotency key required (max 200 characters)")
        if not isinstance(scope, str) or not re.fullmatch(r"scope-[0-9a-f]{32}", scope):
            raise OrchError("Invalid approval scope ID")
        body_hash = digest({"plan": plan, "source": source, "approval": scope})
        old = self.db.execute("SELECT id,body_hash FROM runs WHERE idem=?", (idem,)).fetchone()
        if old:
            if old["body_hash"] != body_hash:
                raise OrchError("Idempotency key conflicts with another request")
            return old["id"]
        # Reject unauthorized sources before scanning files or reading approval metadata.
        self.check_approval(scope, plan, source)
        # Capture the exact user source at submission; never stash/reset it.
        snapshot = source_snapshot(source)
        source_hash = snapshot["tree_hash"]
        approval_file = self.root/"approvals"/(scope+".json")
        frozen = json.loads(approval_file.read_text(encoding="utf-8")) if approval_file.exists() else None
        with self.transaction():
            old = self.db.execute("SELECT id,body_hash FROM runs WHERE idem=?", (idem,)).fetchone()
            if old:
                if old["body_hash"] != body_hash:
                    raise OrchError("Idempotency key conflicts with another request")
                return old["id"]
            self.check_approval(scope, plan, source)
            if frozen and snapshot != frozen["source_snapshot"]:
                raise OrchError("Source tree, Git HEAD, or index changed after approval; reapprove")
            if not frozen and any(t["verifier_profile"] != "fixture-stats" for t in plan["tasks"]):
                raise OrchError("Legacy approvals cannot authorize registered verifiers")
            run = "run-" + uuid.uuid4().hex
            self.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)", (run, idem, body_hash, canonical(plan), source, source_hash, scope, "queued", utcnow()))
            for task in plan["tasks"]:
                self.db.execute("INSERT INTO tasks(run_id,id,spec,state) VALUES(?,?,?,'queued')", (run, task["id"], canonical(task)))
            self.event(run, "", 0, "submitted", {"plan_hash": digest(plan), "scope_id": scope,
                       "source_snapshot": snapshot, "verifiers": frozen["verifiers"] if frozen else {}})
        return run

    def submission(self, run):
        row = self.db.execute("SELECT payload FROM events WHERE run_id=? AND type='submitted' ORDER BY seq LIMIT 1", (run,)).fetchone()
        return json.loads(row[0]) if row else {}

    def verifier_profile(self, run, ident):
        from .verifier_registry import builtin_profile, check_profile
        frozen = self.submission(run).get("verifiers", {})
        profile = frozen.get(ident)
        if profile is None and ident == "fixture-stats":
            profile = builtin_profile()  # compatibility with v0.1/v0.2 builtin-only runs
        if profile is None:
            raise OrchError("Verifier was not frozen in the approved submission")
        check_profile(profile)
        return profile

    def check_approval(self, scope, plan, source):
        a = self.db.execute("SELECT * FROM approvals WHERE id=?", (scope,)).fetchone()
        if not a or a["revoked"] or a["expires"] < time.time() or a["plan_hash"] != digest(plan) or a["source"] != source:
            raise OrchError("No valid approval for this exact plan and source")

    def authorized(self, run):
        r = self.run(run)
        try:
            self.check_approval(r["approval"], json.loads(r["plan"]), r["source"])
        except OrchError:
            return False
        return True

    def revoke(self, scope):
        with self.transaction():
            if not self.db.execute("UPDATE approvals SET revoked=1 WHERE id=?", (scope,)).rowcount:
                raise OrchError("Unknown approval")
            for row in self.db.execute("SELECT id FROM runs WHERE approval=?", (scope,)).fetchall():
                for task in self.tasks(row["id"]):
                    if task["state"] not in TERMINAL | {"cancel_requested", "needs_reconciliation"}:
                        old = task["state"]
                        self._transition(row["id"], task["id"], "cancel_requested", payload={"reason": "approval revoked"})
                        if old in ("queued", "blocked", "rework"):
                            self._transition(row["id"], task["id"], "cancelled")

    def run(self, run):
        r = self.db.execute("SELECT * FROM runs WHERE id=?", (run,)).fetchone()
        if not r:
            raise OrchError("Unknown run")
        return dict(r)

    def tasks(self, run):
        return [dict(r) for r in self.db.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY rowid", (run,))]

    def ordered_tasks(self, run):
        pending = self.tasks(run)
        result, done = [], set()
        while pending:
            ready = [t for t in pending if set(json.loads(t["spec"])["dependencies"]) <= done]
            if not ready:
                raise OrchError("Stored DAG is invalid")
            for t in ready:
                result.append(t)
                done.add(t["id"])
                pending.remove(t)
        return result

    def accepted_tasks(self, run):
        return [dict(t) for t in self.db.execute("SELECT t.* FROM tasks t JOIN events e ON e.run_id=t.run_id AND e.task_id=t.id AND e.type='accepted' WHERE t.run_id=? AND t.state='accepted' ORDER BY e.seq", (run,))]

    def claim(self, run, task, owner, lease_seconds, allow_concurrent=False):
        with self.transaction():
            r = self.run(run)
            if r["state"] in ("needs_reconciliation", "cancel_requested", "cancelled"):
                return None
            self.check_approval(r["approval"], json.loads(r["plan"]), r["source"])
            t = self.db.execute("SELECT * FROM tasks WHERE run_id=? AND id=?", (run, task)).fetchone()
            if not t or t["state"] != "queued":
                return None
            spec = json.loads(t["spec"])
            states = {x["id"]: x["state"] for x in self.tasks(run)}
            if any(states[d] != "accepted" for d in spec["dependencies"]):
                return None
            if any(x["state"] in ("cancel_requested", "needs_reconciliation") for x in self.tasks(run)):
                return None
            if not allow_concurrent and any(x["state"] in ("running", "verifying", "reviewing") for x in self.tasks(run)):
                return None
            fence, number = t["fence"] + 1, t["attempt"] + 1
            self.db.execute("UPDATE tasks SET state='running',attempt=?,fence=?,lease=?,owner=? WHERE run_id=? AND id=?", (number, fence, time.time()+lease_seconds, owner, run, task))
            self.db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,NULL)", (run, task, number, fence, "running"))
            self.db.execute("UPDATE runs SET state='running' WHERE id=?", (run,))
            self.event(run, task, number, "running", {"fence": fence, "owner": owner})
            return number, fence

    def transition(self, run, task, state, fence=None, payload=None):
        with self.transaction():
            self._transition(run, task, state, fence, payload)

    def _transition(self, run, task, state, fence=None, payload=None):
        t = self.db.execute("SELECT * FROM tasks WHERE run_id=? AND id=?", (run, task)).fetchone()
        if not t or state not in TRANSITIONS.get(t["state"], set()):
            raise OrchError("Illegal or late state transition")
        if fence is not None and (t["fence"] != fence or (t["lease"] or 0) < time.time()):
            raise OrchError("Stale fencing token or expired lease")
        if state == "accepted":
            if not self.authorized(run):
                raise OrchError("Approval revoked or expired before acceptance")
            ev = self.db.execute("SELECT evidence FROM attempts WHERE run_id=? AND task_id=? AND number=?", (run, task, t["attempt"])).fetchone()
            review = self.db.execute("SELECT body FROM reviews WHERE run_id=? AND task_id=? AND attempt=? ORDER BY rowid DESC LIMIT 1", (run, task, t["attempt"])).fetchone()
            if not ev or not ev[0] or not review:
                raise OrchError("Acceptance requires persisted verification and review")
            decision = validate_review(json.loads(review[0]), json.loads(ev[0]))
            if decision["decision"] != "accept":
                raise OrchError("No accepted review for this candidate")
        self.db.execute("UPDATE tasks SET state=?,fence=fence+? WHERE run_id=? AND id=?", (state, int(state == "needs_reconciliation"), run, task))
        if state != "queued":
            self.db.execute("UPDATE attempts SET state=? WHERE run_id=? AND task_id=? AND number=?", (state, run, task, t["attempt"]))
        self.event(run, task, t["attempt"], state, payload or {})
        self._refresh(run)

    def renew(self, run, task, fence, seconds):
        with self.transaction():
            if not self.authorized(run):
                return False
            count = self.db.execute("UPDATE tasks SET lease=? WHERE run_id=? AND id=? AND fence=? AND lease>? AND state IN ('running','verifying','reviewing')", (time.time()+seconds, run, task, fence, time.time())).rowcount
            return count == 1

    def _refresh(self, run):
        states = {x["state"] for x in self.tasks(run)}
        if states == {"accepted"}:
            state = "accepted"
        elif "needs_reconciliation" in states:
            state = "needs_reconciliation"
        elif "cancel_requested" in states:
            state = "cancel_requested"
        elif states & {"running", "verifying", "reviewing"}:
            state = "running"
        elif states <= TERMINAL:
            state = "cancelled" if "cancelled" in states else "failed"
        elif states & {"failed", "timed_out", "escalated", "blocked"}:
            state = "blocked"
        else:
            state = "queued"
        self.db.execute("UPDATE runs SET state=? WHERE id=?", (state, run))

    def cancel(self, run):
        with self.transaction():
            self.run(run)
            for t in self.tasks(run):
                if t["state"] not in TERMINAL | {"cancel_requested"}:
                    self._transition(run, t["id"], "cancel_requested")
                    if t["state"] in ("queued", "blocked", "rework"):
                        self._transition(run, t["id"], "cancelled")
        return self.run(run)["state"]

    def recover(self):
        recovered = []
        with self.transaction():
            for row in self.db.execute("SELECT * FROM tasks WHERE state IN ('running','verifying','reviewing','cancel_requested') AND lease < ?", (time.time(),)).fetchall():
                self._transition(row["run_id"], row["id"], "needs_reconciliation", payload={"reason": "expired lease; side effects unknown; not retried"})
                recovered.append([row["run_id"], row["id"]])
        return recovered

    def events(self, run, after=0):
        self.run(run)
        result = []
        for row in self.db.execute("SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq", (run, after)):
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            result.append(item)
        return result

    def attempt_event(self, run, task, attempt, fence, kind, payload):
        with self.transaction():
            current = self.db.execute("SELECT * FROM tasks WHERE run_id=? AND id=?", (run, task)).fetchone()
            if not current or current["attempt"] != attempt or current["fence"] != fence or current["state"] != "running" or (current["lease"] or 0) < time.time() or not self.authorized(run):
                raise OrchError("Cannot record a milestone for a stale or unauthorized attempt")
            self.event(run, task, attempt, kind, payload)

    def save_evidence(self, run, task, attempt, evidence, review, fence=None):
        with self.transaction():
            self._save_evidence(run, task, attempt, evidence, review, fence)

    def _save_evidence(self, run, task, attempt, evidence, review, fence):
        t = self.db.execute("SELECT * FROM tasks WHERE run_id=? AND id=?", (run, task)).fetchone()
        if not t or t["attempt"] != attempt or t["state"] != "reviewing" or (t["lease"] or 0) < time.time() or (fence is not None and fence != t["fence"]):
            raise OrchError("Evidence belongs to an inactive or stale attempt")
        self.db.execute("UPDATE attempts SET evidence=? WHERE run_id=? AND task_id=? AND number=?", (canonical(evidence), run, task, attempt))
        self.db.execute("INSERT INTO reviews VALUES(?,?,?,?)", (run, task, attempt, canonical(review)))
        self.db.execute("DELETE FROM artifacts WHERE run_id=? AND task_id=? AND attempt=?", (run, task, attempt))
        for path, info in evidence["manifest"].items():
            self.db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?)", (run, task, attempt, path, info["sha256"], info["size"]))

    def complete_review(self, run, task, attempt, fence, evidence, review, source):
        validate_review(review, evidence)
        state = {"accept": "accepted", "rework": "rework", "escalate": "escalated"}[review["decision"]]
        with self.transaction():
            if not self.authorized(run):
                raise OrchError("Approval revoked or expired before review commit")
            self._save_evidence(run, task, attempt, evidence, review, fence)
            self._transition(run, task, state, fence, {"candidate_hash": evidence["candidate_hash"], "decision": review["decision"], "decision_source": source})
        return state

    def backup(self, target):
        target = Path(target).resolve()
        if target.exists() or target == self.root / "state.db":
            raise OrchError("Backup target must be new")
        target.parent.mkdir(parents=True, exist_ok=True)
        dest = sqlite3.connect(target)
        try:
            self.db.backup(dest)
        finally:
            dest.close()
