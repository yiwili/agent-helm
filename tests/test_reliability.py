import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_orch.adapters.base import ProviderBlocked
from agent_orch.adapters.codex_cli import CodexAdapter, parse_jsonl
from agent_orch.backup import backup_bundle, restore_bundle, verify_backup
from agent_orch.brain import MockBrain, fixture_plan
from agent_orch.integrator import integrate
from agent_orch.maintenance import maintenance_lock
from agent_orch.models import OrchError, digest
from agent_orch.scheduler import Scheduler
from agent_orch.storage import Store
from agent_orch.supervisor import ProcessTreeUnconfirmed, run_process
from agent_orch.workspace import make_fixture, manifest

FIXTURES = Path(__file__).parent/"fixtures"/"codex"


class Reliability(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="helm-reliability-")
        self.root = Path(self.temp.name)
        self.store = Store(self.root/"state")
        self.source = make_fixture(self.store.root/"fixtures"/"synthetic")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def submit(self, plan=None):
        plan = plan or fixture_plan()
        scope = self.store.approve(plan, self.source)
        return self.store.submit(plan, self.source, scope, "request")

    def run_good(self):
        run = self.submit()
        self.assertEqual(asyncio.run(Scheduler(self.store).execute(run))["state"], "accepted")
        return run

    def fake_adapter(self, scenario, observer=None):
        (self.source/"scenario.txt").write_text(scenario)
        async def runner(argv, cwd, **kwargs):
            return await run_process([sys.executable, "-B", str(FIXTURES/"fake_cli.py"), *argv[1:]], cwd, **kwargs)
        return CodexAdapter(enabled=True, executable="fake-codex", runner=runner, process_observer=observer)

    def test_offline_failure_replays(self):
        cases = json.loads((FIXTURES/"failures.json").read_text())
        for case in cases:
            with self.subTest(case["name"]):
                expected = ProviderBlocked if case.get("blocked") else OrchError
                with self.assertRaises(expected):
                    parse_jsonl("\n".join(json.dumps(e) for e in case["events"]), case["exit_code"])

    def test_duplicate_json_fields_rejected(self):
        with self.assertRaises(OrchError):
            parse_jsonl('{"type":"turn.failed","type":"turn.completed"}', 0)

    def test_fake_cli_uses_real_process_supervisor(self):
        adapter = self.fake_adapter("success")
        result = asyncio.run(adapter.execute(fixture_plan()["tasks"][0], self.source, 1, lambda: False))
        self.assertEqual(result["files"][0]["path"], "stats.py")
        self.assertTrue(adapter.last_process["local_stop_confirmed"])
        self.assertEqual(adapter.last_process["stage"], "exec")

    def test_nonzero_quota_and_permission_classification(self):
        for scenario in ("quota", "permission"):
            with self.subTest(scenario):
                adapter = self.fake_adapter(scenario)
                with self.assertRaises(ProviderBlocked):
                    asyncio.run(adapter.execute(fixture_plan()["tasks"][0], self.source, 1, lambda: False))
                self.assertTrue(adapter.last_process["local_stop_confirmed"])

    def test_missing_terminal_and_bad_json_never_succeed(self):
        for scenario in ("missing-terminal", "malformed"):
            adapter = self.fake_adapter(scenario)
            with self.assertRaises(OrchError):
                asyncio.run(adapter.execute(fixture_plan()["tasks"][0], self.source, 1, lambda: False))

    def test_cancel_during_metadata_does_not_launch_exec(self):
        flag = False
        stages = []
        def observer(kind, data):
            nonlocal flag
            if kind == "process_started":
                stages.append(data["stage"])
                flag = True
        adapter = self.fake_adapter("success", observer)
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(adapter.execute(fixture_plan()["tasks"][0], self.source, 1, lambda: flag))
        self.assertEqual(stages, ["metadata.version"])
        self.assertTrue(adapter.last_process["local_stop_confirmed"])

    def test_cancel_during_fake_codex_exec(self):
        flag = False
        def observer(kind, data):
            nonlocal flag
            if kind == "process_started" and data["stage"] == "exec":
                flag = True
        adapter = self.fake_adapter("slow", observer)
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(adapter.execute(fixture_plan()["tasks"][0], self.source, 1, lambda: flag))
        self.assertTrue(adapter.last_process["local_stop_confirmed"])
        self.assertIsNone(adapter.last_metadata)

    def test_cancel_reviewer_and_leave_no_acceptance(self):
        run = self.submit()
        store = self.store
        class Reviewer:
            async def review(self, evidence, workspace, cancelled):
                store.cancel(run)
                await run_process([sys.executable, "-c", "import time; time.sleep(60)"], workspace, cancelled=cancelled)
        scheduler = Scheduler(self.store)
        scheduler.brain = Reviewer()
        self.assertEqual(asyncio.run(scheduler.execute(run))["state"], "cancelled")
        self.assertFalse(any(e["type"] == "accepted" for e in self.store.events(run)))

    def test_revocation_during_review_rejects_accept(self):
        run = self.submit()
        store = self.store
        class Reviewer:
            async def review(self, evidence, workspace, cancelled):
                result = await MockBrain().review(evidence, workspace)
                store.revoke(store.run(run)["approval"])
                return result
        scheduler = Scheduler(self.store)
        scheduler.brain = Reviewer()
        self.assertEqual(asyncio.run(scheduler.execute(run))["state"], "cancelled")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 0)

    def test_atomic_review_rolls_back_evidence_on_event_failure(self):
        run = self.submit()
        scheduler = Scheduler(self.store, brain="interactive")
        asyncio.run(scheduler.execute(run))
        t = self.store.tasks(run)[0]
        ev = json.loads(self.store.db.execute("SELECT evidence FROM attempts").fetchone()[0])
        review = asyncio.run(MockBrain().review(ev, self.source))
        original_event = self.store.event
        def fail_event(run, task, attempt, kind, payload, **kwargs):
            if kind == "accepted":
                raise RuntimeError("injected transaction failure")
            return original_event(run, task, attempt, kind, payload, **kwargs)
        with patch.object(self.store, "event", side_effect=fail_event):
            with self.assertRaises(RuntimeError):
                self.store.complete_review(run, t["id"], t["attempt"], t["fence"], ev, review, "local-user")
        self.assertEqual(self.store.tasks(run)[0]["state"], "reviewing")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 1)
        self.assertEqual(json.loads(self.store.db.execute("SELECT body FROM reviews").fetchone()[0])["decision"], "pending")

    def test_unknown_process_stop_needs_reconciliation(self):
        run = self.submit()
        class Worker:
            async def execute(self, *args):
                raise ProcessTreeUnconfirmed("injected failed job query")
        scheduler = Scheduler(self.store)
        scheduler.adapters["mock"] = Worker()
        self.assertEqual(asyncio.run(scheduler.execute(run))["state"], "needs_reconciliation")

    def test_uncertain_attempt_blocks_other_independent_tasks(self):
        plan = fixture_plan()
        second = dict(plan["tasks"][0], id="other")
        plan["tasks"].append(second)
        run = self.submit(plan)
        self.store.claim(run, "fix-total", "dead", -1)
        self.store.recover()
        self.assertIsNone(self.store.claim(run, "other", "new", 10))
        self.assertEqual(self.store.tasks(run)[1]["attempt"], 0)

    def test_live_lease_renewal_and_recovery_fencing(self):
        run = self.submit()
        self.store.claim(run, "fix-total", "owner", 30)
        self.assertTrue(self.store.renew(run, "fix-total", 1, 60))
        self.assertEqual(self.store.recover(), [])
        self.store.db.execute("UPDATE tasks SET lease=?", (time.time()-1,))
        self.store.recover()
        self.assertEqual(self.store.tasks(run)[0]["fence"], 2)
        self.assertFalse(self.store.renew(run, "fix-total", 1, 60))

    def test_real_process_crash_persists_unknown_without_replay(self):
        run = self.submit(fixture_plan(behavior="timeout"))
        code = "from agent_orch.storage import Store; from agent_orch.scheduler import Scheduler; import asyncio,sys; s=Store(sys.argv[1]); asyncio.run(Scheduler(s).execute(sys.argv[2]))"
        proc = subprocess.Popen([sys.executable, "-B", "-c", code, str(self.store.root), run], env={**os.environ, "PYTHONPATH":str(Path(__file__).parents[1]/"src")}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic()+10
            while time.monotonic() < deadline:
                if self.store.tasks(run)[0]["state"] == "running":
                    break
                time.sleep(0.02)
            self.assertEqual(self.store.tasks(run)[0]["state"], "running")
            time.sleep(0.4)
        finally:
            proc.kill()
            proc.communicate(timeout=10)
        self.store.db.execute("UPDATE tasks SET lease=?", (time.time()-1,))  # advance the crash lease deadline without a long sleep
        other = Store(self.store.root)
        try:
            self.assertEqual(len(other.recover()), 1)
            self.assertEqual(asyncio.run(Scheduler(other).execute(run))["state"], "needs_reconciliation")
            self.assertEqual(other.tasks(run)[0]["attempt"], 1)
        finally:
            other.close()

    def test_backup_hash_detects_artifact_tampering(self):
        self.run_good()
        target = self.root/"backup"
        backup_bundle(self.store, target)
        (target/"fixtures"/"synthetic"/"stats.py").write_text("tampered")
        with self.assertRaises(OrchError):
            verify_backup(target)
        with self.assertRaises(OrchError):
            restore_bundle(target, self.root/"restore")
        self.assertFalse((self.root/"restore").exists())

    def test_backup_hash_detects_database_tampering_and_unexpected_files(self):
        self.run_good()
        target = self.root/"backup"
        backup_bundle(self.store, target)
        (target/"state.db-wal").write_bytes(b"unexpected")
        with self.assertRaises(OrchError):
            verify_backup(target)
        (target/"state.db-wal").unlink()
        with (target/"state.db").open("ab") as out:
            out.write(b"tampered")
        with self.assertRaises(OrchError):
            verify_backup(target)

    def test_backup_refuses_unknown_writers(self):
        run = self.submit()
        self.store.claim(run, "fix-total", "dead", -1)
        self.store.recover()
        with self.assertRaises(OrchError):
            backup_bundle(self.store, self.root/"backup")

    def test_restore_new_root_rebases_paths_without_execution(self):
        run = self.run_good()
        asyncio.run(integrate(self.store, run))
        target = self.root/"backup"
        backup_bundle(self.store, target)
        before = manifest(self.source)
        result = restore_bundle(target, self.root/"restored")
        self.assertEqual(result["replayed_tasks"], 0)
        restored = Store(self.root/"restored")
        try:
            self.assertEqual(restored.run(run)["source"], str(restored.root/"fixtures"/"synthetic"))
            ev = json.loads(restored.db.execute("SELECT evidence FROM attempts").fetchone()[0])
            self.assertTrue(ev["verification"]["workspace"].startswith(str(restored.root)))
            self.assertEqual(restored.tasks(run)[0]["attempt"], 1)
            self.assertTrue(asyncio.run(integrate(restored, run))["verification"]["passed"])
        finally:
            restored.close()
        self.assertEqual(before, manifest(self.source))
        verify_backup(target)

    def test_restore_never_overwrites_existing_directory(self):
        self.run_good()
        backup = self.root/"backup"
        backup_bundle(self.store, backup)
        marker = self.source/"preserve.txt"
        marker.write_text("preserve")
        with self.assertRaises(OrchError):
            restore_bundle(backup, self.source)
        self.assertEqual(marker.read_text(), "preserve")

    def test_maintenance_lock_excludes_backup_and_integration(self):
        run = self.run_good()
        with maintenance_lock(self.store.root):
            with self.assertRaises(OrchError):
                backup_bundle(self.store, self.root/"backup")
            with self.assertRaises(OrchError):
                asyncio.run(integrate(self.store, run))

    def test_blocked_stdin_still_times_out(self):
        with self.assertRaises(TimeoutError):
            asyncio.run(run_process([sys.executable, "-c", "import time; time.sleep(60)"], self.source, stdin="x"*2_000_000, timeout=0.2))

    def test_unknown_schema_is_rejected_without_modification(self):
        root = self.root/"future"
        future = Store(root)
        future.db.execute("INSERT INTO schema_migrations VALUES(2)")
        future.close()
        before = (root/"state.db").read_bytes()
        with self.assertRaises(OrchError):
            Store(root)
        self.assertEqual(before, (root/"state.db").read_bytes())


if __name__ == "__main__":
    unittest.main()
