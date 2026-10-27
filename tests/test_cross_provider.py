import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_orch.adapters.base import ProviderBlocked
from agent_orch.adapters.qoder_cn_cli import QoderCNAdapter, parse_result
from agent_orch.brain import MockBrain, fixture_plan
from agent_orch.models import OrchError, digest, validate_milestone_review
from agent_orch.scheduler import Scheduler
from agent_orch.storage import Store
from agent_orch.supervisor import ProcessResult, safe_env
from agent_orch.workspace import make_fixture


def terminal(result=None):
    return {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "stop_reason": "end_turn",
            "result": json.dumps(result or {"summary": "offline", "files": []}), "session_id": "offline-fixture-session",
            "total_credits": 0.123, "total_cost_usd": 0, "usage": {"input_tokens": 0, "output_tokens": 0}, "permission_denials": []}


class QoderContracts(unittest.TestCase):
    def test_terminal_parses_with_credits_not_free_currency(self):
        result, meta = parse_result(json.dumps(terminal()), 0)
        self.assertEqual(result["summary"], "offline")
        self.assertEqual(meta["usage"]["credits"], 0.123)
        self.assertIsNone(meta["usage"]["tokens"])
        self.assertIsNone(meta["usage"]["amount_usd"])
        self.assertFalse(meta["billing_verified"])

    def test_exit_zero_and_success_subtype_do_not_override_error(self):
        for field, value in [("is_error", True), ("subtype", "error"), ("stop_reason", "max_tokens"), ("type", "assistant"), ("num_turns", 2), ("session_id", "")]:
            obj = terminal()
            obj[field] = value
            with self.subTest(field=field), self.assertRaises(OrchError):
                parse_result(json.dumps(obj), 0)

    def test_nonzero_failure_cannot_succeed(self):
        with self.assertRaises(OrchError):
            parse_result(json.dumps(terminal()), 1)

    def test_quota_and_permission_denials_fail_closed(self):
        obj = terminal()
        obj.update(is_error=True, result="usage limit reached")
        with self.assertRaises(ProviderBlocked):
            parse_result(json.dumps(obj), 0)
        obj = terminal()
        obj["permission_denials"] = [{"tool": "Write"}]
        with self.assertRaises(ProviderBlocked):
            parse_result(json.dumps(obj), 0)

    def test_no_markdown_or_duplicate_json_or_truncated_response(self):
        for text in ('{}', '{"type":"result","type":"assistant"}', '{', '[]'):
            with self.assertRaises(OrchError):
                parse_result(text, 0)
        obj = terminal()
        obj["result"] = '```json\n{}\n```'
        with self.assertRaises(OrchError):
            parse_result(json.dumps(obj), 0)

    def test_invalid_credit_amounts_rejected(self):
        for amount in (-1, True, float("nan"), "0"):
            obj = terminal()
            obj["total_credits"] = amount
            with self.assertRaises(OrchError):
                parse_result(json.dumps(obj), 0)

    def test_adapter_is_disabled_by_default(self):
        with self.assertRaises(ProviderBlocked):
            asyncio.run(QoderCNAdapter(executable="offline").call("request", {}, Path.cwd()))

    def test_argv_disables_tools_and_mcp_and_uses_stdin(self):
        calls = []
        async def runner(argv, cwd, **kwargs):
            calls.append((argv, kwargs))
            if "--version" in argv:
                return ProcessResult(0, "1.1.80\n", "")
            if "status" in argv:
                return ProcessResult(0, '{"logged_in":true}', "")
            return ProcessResult(0, json.dumps(terminal()), "")
        adapter = QoderCNAdapter(enabled=True, executable="offline", runner=runner)
        asyncio.run(adapter.call("SYNTHETIC_PROMPT_CANARY", {}, Path.cwd()))
        argv, kwargs = calls[-1]
        self.assertEqual(argv[argv.index("--tools")+1], "")
        self.assertEqual(argv[argv.index("--setting-sources")+1], "")
        self.assertIn("--strict-mcp-config", argv)
        self.assertNotIn("SYNTHETIC_PROMPT_CANARY", " ".join(argv))
        self.assertIn("SYNTHETIC_PROMPT_CANARY", kwargs["stdin"])
        self.assertNotIn("--dangerously-skip-permissions", argv)

    def test_windows_system_paths_preserved_without_model_keys(self):
        with patch.dict(os.environ, {"SystemDrive": "C:", "ProgramData": "C:/ProgramData", "ANTHROPIC_AUTH_TOKEN": "CANARY_NOT_REAL"}):
            env = safe_env(auth=True)
            folded = {k.upper():v for k,v in env.items()}
            self.assertEqual(folded["SYSTEMDRIVE"], "C:")
            self.assertEqual(folded["PROGRAMDATA"], "C:/ProgramData")
            self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)


class CrossProviderState(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="helm-cross-provider-")
        self.root = Path(self.temp.name)
        self.source = make_fixture(self.root/"synthetic")
        self.store = Store(self.root/"state")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def submit(self, milestones=False, provider="mock"):
        plan = fixture_plan(provider=provider)
        if milestones:
            plan["tasks"][0]["milestone_review"] = True
        scope = self.store.approve(plan, self.source)
        return self.store.submit(plan, self.source, scope, "request")

    def test_disabled_qoder_does_not_claim_attempt(self):
        run = self.submit(provider="qoder-cn")
        result = asyncio.run(Scheduler(self.store, brain="interactive").execute(run))
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(self.store.tasks(run)[0]["attempt"], 0)

    def test_disabled_reviewer_does_not_consume_worker_attempt(self):
        run = self.submit()
        result = asyncio.run(Scheduler(self.store, brain="qoder-cn").execute(run))
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(self.store.tasks(run)[0]["attempt"], 0)

    def test_real_review_feedback_reaches_next_attempt(self):
        run = self.submit()
        observed = []
        class Worker:
            async def execute(self, task, workspace, attempt, cancelled):
                observed.append(copy.deepcopy(task))
                value = "999" if attempt == 1 else "0"
                return {"summary": "offline", "files": [{"path": "stats.py", "content": "def total(values):\n    return sum(values) if values else "+value+"\n"}]}
        scheduler = Scheduler(self.store)
        scheduler.adapters["mock"] = Worker()
        self.assertEqual(asyncio.run(scheduler.execute(run))["state"], "accepted")
        self.assertNotIn("rework_context", observed[0])
        feedback = observed[1]["rework_context"]
        self.assertEqual(feedback["review"]["decision"], "rework")
        self.assertFalse(feedback["verification"]["passed"])
        self.assertIn("999", feedback["previous_diff"])
        self.assertEqual(feedback["review"]["candidate_hash"], feedback["previous_candidate_hash"])
        self.assertEqual(self.store.db.execute("SELECT state FROM attempts WHERE number=1").fetchone()[0], "rework")

    def setup_milestone(self, diagnosis_paths=None, review_decision="continue", bad_first=False):
        record = {"diagnosed": 0, "executed": 0, "reviews": 0}
        class Worker:
            last_metadata = {"session_id": "offline-worker-session"}
            async def diagnose(self, task, workspace, cancelled):
                record["diagnosed"] += 1
                return {"summary": "empty list returns None", "proposed_paths": diagnosis_paths or ["stats.py"], "checks": ["empty list is zero"], "risks": [], "needs_scope_change": False}
            async def execute(self, task, workspace, attempt, cancelled):
                record["executed"] += 1
                value = "999" if bad_first and attempt == 1 else "0"
                return {"summary": "offline", "files": [{"path": "stats.py", "content": "def total(values):\n    return sum(values) if values else "+value+"\n"}]}
        class Reviewer(MockBrain):
            async def review_milestone(self, bundle, workspace, cancelled):
                record["reviews"] += 1
                return {"decision": review_decision, "milestone_hash": digest(bundle), "reasons": ["scoped repair and fixed checks reviewed"]}
        scheduler = Scheduler(self.store)
        scheduler.adapters["mock"] = Worker()
        scheduler.brain = Reviewer()
        return scheduler, record

    def test_milestone_precedes_implementation_and_is_reused_on_rework(self):
        run = self.submit(milestones=True)
        scheduler, record = self.setup_milestone(bad_first=True)
        self.assertEqual(asyncio.run(scheduler.execute(run))["state"], "accepted")
        self.assertEqual(record, {"diagnosed": 1, "reviews": 1, "executed": 2})
        kinds = [e["type"] for e in self.store.events(run)]
        self.assertLess(kinds.index("milestone_approved"), kinds.index("verifying"))
        self.assertIn("milestone_reused", kinds)

    def test_milestone_cannot_expand_scope(self):
        run = self.submit(milestones=True)
        scheduler, record = self.setup_milestone(diagnosis_paths=["outside.py"])
        self.assertEqual(asyncio.run(scheduler.execute(run))["state"], "blocked")
        self.assertEqual(record["executed"], 0)
        self.assertEqual(record["reviews"], 0)

    def test_rejected_milestone_never_runs_worker(self):
        run = self.submit(milestones=True)
        scheduler, record = self.setup_milestone(review_decision="escalate")
        self.assertEqual(asyncio.run(scheduler.execute(run))["state"], "blocked")
        self.assertEqual(record["executed"], 0)

    def test_milestone_hash_mismatch_is_not_accepted(self):
        bundle = {"task": {"allowed_paths": ["stats.py"]}, "diagnosis": {"needs_scope_change": False, "proposed_paths": ["stats.py"]}}
        with self.assertRaises(OrchError):
            validate_milestone_review({"decision": "continue", "milestone_hash": "wrong", "reasons": ["trust me"]}, bundle)

    def test_late_milestone_cannot_mutate_terminal_task(self):
        run = self.submit()
        asyncio.run(Scheduler(self.store).execute(run))
        with self.assertRaises(OrchError):
            self.store.attempt_event(run, "fix-total", 1, 1, "milestone_approved", {})


if __name__ == "__main__":
    unittest.main()
