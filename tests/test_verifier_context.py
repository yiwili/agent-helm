import asyncio
import ast
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_orch.brain import CodexBrain, MockBrain, fixture_plan
from agent_orch.models import digest
from agent_orch.scheduler import Scheduler
from agent_orch.storage import Store
from agent_orch.verifier import STATS_CHECK, fixture_source_allowed
from agent_orch.verifier_context import verifier_context
from agent_orch.workspace import make_fixture


class VerifierContextTests(unittest.TestCase):
    def test_actual_none_comparison_rejection_is_preserved(self):
        cases = next(ast.literal_eval(node.value) for node in ast.parse(STATS_CHECK).body
                     if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "cases" for t in node.targets))
        self.assertEqual(verifier_context("fixture-stats")["checks"], [{"input": value, "expected": expected} for value, expected in cases])
        with tempfile.TemporaryDirectory() as name:
            root = make_fixture(Path(name)/"synthetic")
            (root/"stats.py").write_text("def total(values):\n    return sum(values) if values is not None else None\n")
            self.assertFalse(fixture_source_allowed(root))
            (root/"stats.py").write_text("def total(values):\n    return sum(values)\n")
            self.assertTrue(fixture_source_allowed(root))

    def test_registered_verifier_does_not_inherit_stats_assumptions(self):
        context = verifier_context("custom-user-checks")
        self.assertEqual(context["profile_id"], "custom-user-checks")
        self.assertNotIn("sum(values)", json.dumps(context))
        self.assertNotIn("None input", json.dumps(context))

    def test_planning_and_both_reviews_receive_controller_constraints(self):
        prompts = []
        class Adapter:
            async def call(self, prompt, schema, workspace, **kwargs):
                prompts.append(prompt)
                if "tasks" in schema["properties"]:
                    return fixture_plan(provider="claude")
                if "milestone_hash" in schema["properties"]:
                    return {"decision": "continue", "milestone_hash": digest(bundle), "reasons": ["offline"]}
                return {"decision": "accept", "candidate_hash": "offline-hash", "reasons": ["offline"], "evidence_refs": ["verification"]}
        task = fixture_plan()["tasks"][0]
        bundle = {"task": task, "diagnosis": {"needs_scope_change": False, "proposed_paths": ["stats.py"]},
                  "verifier_contract": verifier_context("fixture-stats")}
        evidence = {"task": task, "candidate_hash": "offline-hash", "scope_ok": True, "verification": {"passed": True}}
        async def run():
            brain = CodexBrain(Adapter())
            await brain.plan("offline", Path.cwd(), worker_provider="claude")
            await brain.review_milestone(bundle, Path.cwd())
            await brain.review(evidence, Path.cwd())
        asyncio.run(run())
        contract = json.dumps(verifier_context("fixture-stats"))
        self.assertEqual(len(prompts), 3)
        self.assertTrue(all(contract in prompt for prompt in prompts))

    def run_pipeline(self, *, drift=False):
        observed = []
        context = verifier_context("fixture-stats")
        class Worker:
            last_metadata = {"source": "offline"}
            async def diagnose(self, task, workspace, cancelled):
                observed.append(("diagnose", task["verifier_contract"]))
                return {"summary": "offline", "proposed_paths": ["stats.py"], "checks": ["empty list"], "risks": [], "needs_scope_change": False}
            async def execute(self, task, workspace, attempt, cancelled):
                observed.append(("execute", task["verifier_contract"]))
                code = "sum(values) if values else 999" if drift else "sum(values)"
                return {"summary": "offline", "files": [{"path": "stats.py", "content": "def total(values):\n    return "+code+"\n"}]}
        class Reviewer(MockBrain):
            async def review_milestone(self, bundle, workspace, cancelled):
                observed.append(("milestone", bundle["verifier_contract"]))
                return {"decision": "continue", "milestone_hash": digest(bundle), "reasons": ["offline"]}
            async def review(self, evidence, workspace, cancelled):
                observed.append(("review", evidence["verifier_contract"]))
                return await super().review(evidence, workspace, cancelled)
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = make_fixture(root/"synthetic")
            store = Store(root/"state")
            try:
                plan = fixture_plan()
                plan["tasks"][0]["milestone_review"] = True
                original = copy.deepcopy(plan)
                approval = store.approve(plan, source)
                run = store.submit(plan, source, approval, "offline-context")
                scheduler = Scheduler(store)
                scheduler.adapters["mock"] = Worker()
                scheduler.brain = Reviewer()
                changed = {**context, "contract_version": 2}
                sequence = [context, changed] if drift else [context]
                with patch("agent_orch.scheduler.verifier_context", side_effect=sequence):
                    result = asyncio.run(scheduler.execute(run))
                self.assertEqual(json.loads(store.run(run)["plan"]), original)
                if not drift:
                    row = store.db.execute("SELECT evidence FROM attempts WHERE evidence IS NOT NULL").fetchone()
                    evidence = json.loads(row[0])
                    self.assertEqual(evidence["task"], original["tasks"][0])
                    self.assertEqual(evidence["verifier_contract"], context)
                return result, observed
            finally:
                store.close()

    def test_diagnosis_worker_and_reviews_share_context_without_mutating_plan(self):
        result, observed = self.run_pipeline()
        self.assertEqual(result["state"], "accepted")
        self.assertEqual([kind for kind, value in observed], ["diagnose", "milestone", "execute", "review"])
        self.assertTrue(all(value == verifier_context("fixture-stats") for kind, value in observed))

    def test_milestone_reuse_refuses_changed_verifier_context(self):
        result, observed = self.run_pipeline(drift=True)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(sum(kind == "execute" for kind, value in observed), 1)


if __name__ == "__main__":
    unittest.main()
