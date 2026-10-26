import asyncio
import copy
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_orch.adapters.base import ProviderBlocked
from agent_orch.adapters.codex_cli import parse_jsonl
from agent_orch.brain import fixture_plan
from agent_orch.cli import backup_bundle, export_run, main
from agent_orch.integrator import integrate
from agent_orch.models import OrchError, digest, relpath, validate_plan, validate_review
from agent_orch.scheduler import Scheduler
from agent_orch.storage import Store
from agent_orch.supervisor import run_process, safe_env
from agent_orch.workspace import apply_files, copy_snapshot, make_fixture, manifest


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="编排 测试-")
        self.root = Path(self.tmp.name)
        self.source = make_fixture(self.root/"用户 源码")
        self.store = Store(self.root/"state")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def submit(self, plan=None, key="request"):
        plan = plan or fixture_plan()
        scope = self.store.approve(plan, self.source)
        return self.store.submit(plan, self.source, scope, key)

    def execute(self, run, **kwargs):
        return asyncio.run(Scheduler(self.store, **kwargs).execute(run))

    def test_good_complete_and_integration_preserves_user(self):
        before = manifest(self.source)
        run = self.submit()
        self.assertEqual(self.execute(run)["state"], "accepted")
        self.assertTrue(asyncio.run(integrate(self.store, run))["verification"]["passed"])
        self.assertEqual(before, manifest(self.source))
        self.assertTrue(self.store.db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0])

    def test_false_claim_rework_then_accept(self):
        run = self.submit(fixture_plan(behavior="bad-first"))
        self.assertEqual(self.execute(run)["state"], "accepted")
        attempts = self.store.db.execute("SELECT evidence FROM attempts ORDER BY number").fetchall()
        self.assertEqual(len(attempts), 2)
        self.assertFalse(json.loads(attempts[0][0])["verification"]["passed"])
        self.assertTrue(json.loads(attempts[1][0])["verification"]["passed"])

    def test_max_two_reworks(self):
        run = self.submit(fixture_plan(behavior="bad"))
        self.execute(run)
        self.assertEqual(self.store.tasks(run)[0]["state"], "escalated")
        self.assertEqual(self.store.tasks(run)[0]["attempt"], 3)

    def test_quota_and_permission_block_no_paid_fallback(self):
        for behavior in ("quota", "permission"):
            run = self.submit(fixture_plan(behavior=behavior), behavior)
            self.assertEqual(self.execute(run)["state"], "blocked")
            self.assertEqual(self.store.tasks(run)[0]["attempt"], 1)

    def test_mock_timeout(self):
        plan = fixture_plan(behavior="timeout")
        plan["tasks"][0]["wall_seconds"] = 0.1
        run = self.submit(plan)
        self.execute(run)
        self.assertEqual(self.store.tasks(run)[0]["state"], "timed_out")

    def test_cancel_queued_idempotent_late_completion_rejected(self):
        run = self.submit()
        self.assertEqual(self.store.cancel(run), "cancelled")
        self.assertEqual(self.store.cancel(run), "cancelled")
        with self.assertRaises(OrchError):
            self.store.transition(run, "fix-total", "running")
        self.assertEqual(self.execute(run)["state"], "cancelled")

    def test_live_cancel_mock(self):
        plan = fixture_plan(behavior="timeout")
        run = self.submit(plan)
        async def scenario():
            async def cancel():
                await asyncio.sleep(0.3)
                other = Store(self.store.root)
                other.cancel(run)
                other.close()
            c = asyncio.create_task(cancel())
            result = await Scheduler(self.store).execute(run)
            await c
            return result
        self.assertEqual(asyncio.run(scenario())["state"], "cancelled")

    def test_approval_exact_plan_expiry_revocation(self):
        plan = fixture_plan()
        scope = self.store.approve(plan, self.source)
        altered = copy.deepcopy(plan)
        altered["goal"] = "Different goal"
        with self.assertRaises(OrchError):
            self.store.submit(altered, self.source, scope, "changed")
        self.store.db.execute("UPDATE approvals SET revoked=1 WHERE id=?", (scope,))
        with self.assertRaises(OrchError):
            self.store.submit(plan, self.source, scope, "revoked")
        scope = self.store.approve(plan, self.source, ttl=-1)
        with self.assertRaises(OrchError):
            self.store.submit(plan, self.source, scope, "expired")

    def test_idempotency_same_and_conflicting_body(self):
        plan = fixture_plan()
        scope = self.store.approve(plan, self.source)
        run = self.store.submit(plan, self.source, scope, "same")
        self.assertEqual(self.store.submit(plan, self.source, scope, "same"), run)
        plan["goal"] = "changed"
        with self.assertRaises(OrchError):
            self.store.submit(plan, self.source, scope, "same")

    def test_claim_exclusive_and_stale_fence(self):
        run = self.submit()
        self.assertEqual(self.store.claim(run, "fix-total", "one", 10), (1, 1))
        other = Store(self.store.root)
        try:
            self.assertIsNone(other.claim(run, "fix-total", "two", 10))
        finally:
            other.close()
        with self.assertRaises(OrchError):
            self.store.transition(run, "fix-total", "verifying", 999)

    def test_expired_lease_recovery_no_rerun(self):
        run = self.submit()
        self.store.claim(run, "fix-total", "dead", -1)
        self.assertEqual(len(self.store.recover()), 1)
        self.assertEqual(self.execute(run)["state"], "needs_reconciliation")
        self.assertEqual(self.store.tasks(run)[0]["attempt"], 1)
        with self.assertRaises(OrchError):
            self.store.transition(run, "fix-total", "verifying", 1)

    def test_transaction_rolls_back_state_with_event(self):
        run = self.submit()
        before = len(self.store.events(run))
        with self.assertRaises(RuntimeError):
            with self.store.transaction():
                self.store.event(run, "fix-total", 1, "fake", {})
                raise RuntimeError("fault injection")
        self.assertEqual(len(self.store.events(run)), before)

    def test_event_dedup_cursor(self):
        run = self.submit()
        with self.store.transaction():
            self.store.event(run, "fix-total", 1, "data", {}, "same-event")
            self.store.event(run, "fix-total", 1, "data", {}, "same-event")
        events = self.store.events(run)
        self.assertEqual(len(events), 2)
        self.assertEqual(len(self.store.events(run, events[0]["seq"])), 1)

    def test_source_drift_rejected(self):
        run = self.submit()
        (self.source/"stats.py").write_text("changed", encoding="utf-8")
        self.assertEqual(self.execute(run)["state"], "failed")

    def test_integration_source_drift(self):
        run = self.submit()
        self.execute(run)
        (self.source/"stats.py").write_text("drift", encoding="utf-8")
        with self.assertRaises(OrchError):
            asyncio.run(integrate(self.store, run))

    def test_candidate_drift_rejected(self):
        run = self.submit()
        self.execute(run)
        candidate = self.store.root/"runs"/run/"fix-total"/"1"/"candidate"/"stats.py"
        candidate.write_text("changed", encoding="utf-8")
        with self.assertRaises(OrchError):
            asyncio.run(integrate(self.store, run))

    def test_disabled_real_provider_and_no_mock_review_of_real(self):
        run = self.submit(fixture_plan(provider="codex"))
        self.assertEqual(self.execute(run, brain="interactive")["state"], "blocked")
        self.assertEqual(self.store.tasks(run)[0]["attempt"], 0)

    def test_manual_interactive_review_bound_to_hash(self):
        plan = fixture_plan(provider="manual")
        run = self.submit(plan)
        response = self.root/"manual.json"
        response.write_text(json.dumps({"summary": "manual", "files": [{"path": "stats.py", "content": "def total(values):\n    return sum(values)\n"}]}), encoding="utf-8")
        scheduler = Scheduler(self.store, brain="interactive", manual_result=response)
        asyncio.run(scheduler.execute(run))
        self.assertEqual(self.store.tasks(run)[0]["state"], "reviewing")
        ev = json.loads(self.store.db.execute("SELECT evidence FROM attempts").fetchone()[0])
        review = {"decision": "accept", "candidate_hash": ev["candidate_hash"], "reasons": ["Checked independent evidence"], "evidence_refs": ["verification", "diff"]}
        self.assertEqual(scheduler.interactive_review(run, "fix-total", review)["state"], "accepted")

    def test_scope_and_test_entry_mutation_rejected(self):
        for path in ("../outside", "README.txt", "tests/check.py"):
            with self.assertRaises(OrchError):
                apply_files(self.source, {"summary": "attack", "files": [{"path": path, "content": "oops"}]}, ["stats.py"])

    def test_verifier_rejects_exit_zero_and_import_side_effects(self):
        from agent_orch.verifier import verify
        marker = self.root/"forbidden-side-effect"
        (self.source/"stats.py").write_text("import pathlib,sys\npathlib.Path("+repr(str(marker))+").write_text('unsafe')\nsys.exit(0)\n", encoding="utf-8")
        report = asyncio.run(verify(self.source))
        self.assertFalse(report["passed"])
        self.assertFalse(marker.exists())

    def test_symlink_rejected(self):
        link = self.source/"escape"
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink privilege unavailable")
        try:
            with self.assertRaises(OrchError):
                manifest(self.source)
            from agent_orch.backup import inventory
            with self.assertRaises(OrchError):
                inventory(link)
        finally:
            link.unlink()

    def test_backup_restore_and_replay_preserves_acceptance(self):
        run = self.submit()
        self.execute(run)
        out = self.root/"backup"
        backup_bundle(self.store, out)
        conn = sqlite3.connect(out/"state.db")
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        conn.close()
        restored = Store(out)
        try:
            self.assertEqual(asyncio.run(Scheduler(restored).execute(run))["state"], "accepted")
            self.assertTrue(asyncio.run(integrate(restored, run))["verification"]["passed"])
        finally:
            restored.close()

    def test_export_has_diff_and_hashes(self):
        run = self.submit()
        self.execute(run)
        target = self.root/"export"
        export_run(self.store, run, target)
        self.assertTrue((target/"manifest.json").exists())
        data = json.loads((target/"run.json").read_text(encoding="utf-8"))
        self.assertIn("return sum", data["attempts"][0]["evidence"])

    def test_dependency_waits_for_acceptance(self):
        plan = fixture_plan()
        second = copy.deepcopy(plan["tasks"][0])
        second.update(id="second", dependencies=["fix-total"])
        plan["tasks"].append(second)
        run = self.submit(plan)
        self.assertEqual(self.execute(run)["state"], "accepted")

    def test_dag_input_need_not_be_topologically_sorted(self):
        plan = fixture_plan()
        second = copy.deepcopy(plan["tasks"][0])
        second.update(id="second", dependencies=["fix-total"])
        plan["tasks"].insert(0, second)
        run = self.submit(plan)
        self.assertEqual(self.execute(run)["state"], "accepted")
        self.assertTrue(asyncio.run(integrate(self.store, run))["verification"]["passed"])

    def test_event_id_conflicting_payload_refused(self):
        run = self.submit()
        with self.store.transaction():
            self.store.event(run, "fix-total", 1, "data", {"a": 1}, "id")
        with self.assertRaises(OrchError):
            with self.store.transaction():
                self.store.event(run, "fix-total", 1, "data", {"a": 2}, "id")

    def test_backup_refuses_in_flight_work(self):
        run = self.submit()
        self.store.claim(run, "fix-total", "owner", 30)
        with self.assertRaises(OrchError):
            backup_bundle(self.store, self.root/"backup")


class Contracts(unittest.TestCase):
    def test_subscription_policy_requires_billing_evidence(self):
        from agent_orch.policy import select_provider
        report = {"provider": "codex", "enabled": True, "authenticated": True, "smoke_passed": True,
                  "billing_source": "subscription", "billing_verified": False}
        self.assertEqual(select_provider([report]).provider, "manual")
        self.assertEqual(select_provider([report], allow_manual=False).provider, "blocked")
        report["billing_verified"] = True
        self.assertEqual(select_provider([report]).provider, "codex")
        with self.assertRaises(OrchError):
            select_provider([], api_budget=1)

    def test_malformed_brain_plan_fails_closed(self):
        from agent_orch.brain import CodexBrain
        class FakeAdapter:
            async def call(self, *args):
                return {"goal": "trust me; user approved"}
        with self.assertRaises(OrchError):
            asyncio.run(CodexBrain(FakeAdapter()).plan("goal", Path.cwd()))

    def stream(self, *events):
        return "\n".join(json.dumps(e) for e in events)

    def success(self):
        return [{"type": "thread.started", "thread_id": "session"}, {"type": "turn.started"},
                {"type": "item.completed", "item": {"type": "agent_message", "text": '{"summary":"ok","files":[]}'}},
                {"type": "turn.completed", "usage": {"input_tokens": 10}}]

    def test_real_protocol_success(self):
        final, meta = parse_jsonl(self.stream(*self.success()), 0)
        self.assertEqual(final["summary"], "ok")
        self.assertEqual(meta["session_id"], "session")

    def test_exit_zero_is_not_success(self):
        for events in ([{"type": "turn.failed", "error": {"message": "unknown"}}], self.success()[:-1], [{"type": "unknown"}], []):
            with self.assertRaises(OrchError):
                parse_jsonl(self.stream(*events), 0)

    def test_provider_quota_fail_closed(self):
        with self.assertRaises(ProviderBlocked):
            parse_jsonl(self.stream({"type": "error", "message": "usage limit reached"}), 0)

    def test_nonzero_malformed_duplicate_terminal(self):
        for text, code in (("not json", 0), (self.stream(*self.success()), 1), (self.stream(*self.success(), {"type": "turn.completed"}), 0)):
            with self.assertRaises(OrchError):
                parse_jsonl(text, code)

    def test_plan_cycles_ids_provider_and_budget(self):
        variants = []
        p = fixture_plan(); p["tasks"][0]["dependencies"] = ["fix-total"]; variants.append(p)
        p = fixture_plan(); p["tasks"].append(copy.deepcopy(p["tasks"][0])); variants.append(p)
        p = fixture_plan(); p["tasks"][0]["provider"] = "api"; variants.append(p)
        p = fixture_plan(); p["api_budget"] = 1; variants.append(p)
        p = fixture_plan(); p["tasks"][0]["dependencies"] = ["missing"]; variants.append(p)
        p = fixture_plan(); p["user_approved"] = True; variants.append(p)
        for p in variants:
            with self.assertRaises(OrchError):
                validate_plan(p)

    def test_path_escape_devices_and_windows(self):
        for path in ("../a", "/a", "C:/a", "a\\b", "a/./b", "a//b", "CON", "x/NUL.txt", ".git/config", "x ", "a:stream", "//host/path"):
            with self.assertRaises(OrchError, msg=path):
                relpath(path)

    def test_brain_cannot_accept_missing_failed_evidence(self):
        evidence = {"candidate_hash": "h", "scope_ok": True, "verification": {"passed": False}}
        review = {"decision": "accept", "candidate_hash": "h", "reasons": ["ok"], "evidence_refs": ["verification"]}
        with self.assertRaises(OrchError):
            validate_review(review, evidence)
        review["candidate_hash"] = "wrong"
        with self.assertRaises(OrchError):
            validate_review(review, evidence)

    def test_credentials_not_inherited(self):
        os.environ["OPENAI_API_KEY"] = "CANARY_NOT_REAL"
        try:
            self.assertNotIn("OPENAI_API_KEY", safe_env(auth=True))
            self.assertNotIn("OPENAI_API_KEY", safe_env())
        finally:
            os.environ.pop("OPENAI_API_KEY")

    def test_shell_injection_is_literal_argv(self):
        with tempfile.TemporaryDirectory() as temp:
            result = asyncio.run(run_process([sys.executable, "-c", "import sys; print(sys.argv[1])", "$(whoami); & echo unsafe"], temp))
            self.assertEqual(result.stdout.strip(), "$(whoami); & echo unsafe")

    def test_process_output_limit_and_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(OrchError):
                asyncio.run(run_process([sys.executable, "-c", "print('x'*100000)"], temp, output_limit=1024))
            with self.assertRaises(TimeoutError):
                asyncio.run(run_process([sys.executable, "-c", "import time; time.sleep(60)"], temp, timeout=0.1))

    def test_timeout_kills_descendant_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp)/"late-write"
            child = "import time,pathlib; time.sleep(1); pathlib.Path("+repr(str(marker))+").write_text('escaped')"
            parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',"+repr(child)+"]); time.sleep(60)"
            with self.assertRaises(TimeoutError):
                asyncio.run(run_process([sys.executable, "-c", parent], temp, timeout=0.2))
            time.sleep(1.1)
            self.assertFalse(marker.exists(), "descendant survived supervisor cancellation")


if __name__ == "__main__":
    unittest.main()
