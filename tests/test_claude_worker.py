import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_orch.adapters.base import ProviderBlocked
from agent_orch.adapters.claude_cli import PINNED_VERSION, ClaudeAdapter, parse_terminal
from agent_orch.brain import fixture_plan
from agent_orch.budget import Budget, cents, create_budget
from agent_orch.cli import parser
from agent_orch.models import OrchError, validate_plan
from agent_orch.scheduler import Scheduler
from agent_orch.storage import Store
from agent_orch.supervisor import ProcessResult
from agent_orch.workspace import make_fixture


def terminal():
    return {"type": "result", "subtype": "success", "is_error": False, "terminal_reason": "completed",
            "stop_reason": "end_turn", "num_turns": 2, "session_id": "offline-session", "permission_denials": [],
            "result": "", "structured_output": {"summary": "offline", "files": []}, "total_cost_usd": 0.01,
            "modelUsage": {"offline-glm": {"inputTokens": 1}}}


class ClaudeProtocol(unittest.TestCase):
    def test_uses_native_field_and_keeps_actual_model_identity(self):
        for stop_reason in ("end_turn", "tool_use"):
            event = terminal()
            event["result"] = "```json\nwrong text field\n```"
            event["stop_reason"] = stop_reason
            value, meta = parse_terminal(json.dumps(event), 0)
            self.assertEqual(value, event["structured_output"])
            self.assertEqual(meta["models"], ["offline-glm"])
            self.assertFalse(meta["billing_verified"])

    def test_no_result_or_fence_extraction_fallback(self):
        for value in (None, [], '{"summary":"fake","files":[]}'):
            event = terminal()
            event["structured_output"] = value
            event["result"] = '{"summary":"fake","files":[]}'
            with self.assertRaises(OrchError):
                parse_terminal(json.dumps(event), 0)

    def test_partial_error_and_permission_terminal_cannot_pass(self):
        for key, value in (("is_error", True), ("subtype", "error"), ("terminal_reason", "max_budget"),
                           ("stop_reason", "max_tokens"), ("num_turns", 4), ("session_id", "")):
            event = terminal()
            event[key] = value
            with self.subTest(key=key), self.assertRaises(OrchError):
                parse_terminal(json.dumps(event), 0)
        event = terminal()
        event["permission_denials"] = ["Write"]
        with self.assertRaises(ProviderBlocked):
            parse_terminal(json.dumps(event), 0)
        with self.assertRaises(OrchError):
            parse_terminal(json.dumps(terminal()), 1)

    def test_malformed_duplicate_and_invalid_cost_refused(self):
        for text in ('{', '[]', '{"type":"result","type":"other"}'):
            with self.assertRaises(OrchError):
                parse_terminal(text, 0)
        for cost in (-1, True, float("nan"), "0.01"):
            event = terminal()
            event["total_cost_usd"] = cost
            with self.assertRaises(OrchError):
                parse_terminal(json.dumps(event), 0)


class BudgetContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="helm-budget-offline-")
        self.root = Path(self.temp.name)
        self.path = self.root/"authorization.db"
        create_budget(self.path, "0.30", "0.10")

    def tearDown(self):
        self.temp.cleanup()

    def test_decimal_cents_and_invalid_amounts(self):
        self.assertEqual(cents("0.30"), 30)
        for value in (0, -1, True, "NaN", "Infinity", "0.001"):
            with self.assertRaises(OrchError):
                cents(value)

    def test_authorization_never_overwrites_or_creates_on_missing_open(self):
        with self.assertRaises(FileExistsError):
            create_budget(self.path, "1.00", "0.10")
        missing = self.root/"missing.db"
        with self.assertRaises(ProviderBlocked):
            Budget(missing)
        self.assertFalse(missing.exists())

    def test_total_reservations_do_not_float_overrun_or_refund_failure(self):
        budget = Budget(self.path)
        try:
            for index in range(3):
                token = budget.reserve("offline")
                budget.finish(token, state="contract_rejected" if index == 0 else "completed", cost=None if index == 0 else 0.01)
            with self.assertRaises(ProviderBlocked):
                budget.reserve("fourth")
            report = budget.report()
            self.assertEqual(report["reserved_usd"], 0.3)
            self.assertEqual(report["remaining_reservation_usd"], 0)
            self.assertEqual(report["unknown_cost_calls"], 1)
        finally:
            budget.close()

    def test_atomic_reservation_across_concurrent_connections(self):
        def reserve_one(index):
            budget = Budget(self.path)
            try:
                try:
                    budget.reserve("offline parallel")
                    return True
                except ProviderBlocked:
                    return False
            finally:
                budget.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(reserve_one, range(8))), 3)

    def test_reported_overrun_halts_remaining_calls(self):
        budget = Budget(self.path)
        try:
            token = budget.reserve("offline")
            with self.assertRaises(ProviderBlocked):
                budget.finish(token, state="completed", cost=0.100001)
            self.assertTrue(budget.report()["halted"])
            with self.assertRaises(ProviderBlocked):
                budget.reserve("not allowed")
        finally:
            budget.close()

    def test_budget_inside_source_is_rejected(self):
        with self.assertRaises(OrchError):
            Budget(self.path, self.root)


class ClaudeWorker(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="helm-claude-offline-")
        self.root = Path(self.temp.name)
        self.path = self.root/"budget.db"
        create_budget(self.path, "0.30", "0.10")
        self.source = make_fixture(self.root/"synthetic")

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, calls, *, output=None, version=PINNED_VERSION, error=None):
        async def run(argv, cwd, **kwargs):
            calls.append((argv, kwargs))
            if "--version" in argv:
                return ProcessResult(0, version, "")
            if "auth" in argv:
                return ProcessResult(0, '{"loggedIn":true,"authMethod":"oauth_token"}', "")
            if error:
                raise error
            return ProcessResult(0, json.dumps(output or terminal()), "")
        return run

    def test_disabled_or_missing_budget_prevents_any_provider_process(self):
        calls = []
        for enabled, ledger in ((False, self.path), (True, None)):
            adapter = ClaudeAdapter(enabled=enabled, executable="offline", budget_ledger=ledger, runner=self.runner(calls))
            with self.assertRaises(ProviderBlocked):
                asyncio.run(adapter.call("offline", {}, self.source))
        self.assertEqual(calls, [])

    def test_argv_uses_schema_stdin_safe_mode_and_explicit_budget(self):
        calls = []
        adapter = ClaudeAdapter(enabled=True, executable="offline", budget_ledger=self.path, runner=self.runner(calls))
        self.assertEqual(asyncio.run(adapter.call("PROMPT_CANARY", {"type": "object"}, self.source))["summary"], "offline")
        argv, kwargs = calls[-1]
        self.assertIn("--json-schema", argv)
        self.assertIn("--safe-mode", argv)
        self.assertEqual(argv[argv.index("--tools")+1], "")
        self.assertEqual(argv[argv.index("--max-budget-usd")+1], "0.10")
        self.assertEqual(argv[argv.index("--setting-sources")+1], "user")
        self.assertEqual(kwargs["stdin"], "PROMPT_CANARY")
        self.assertNotIn("PROMPT_CANARY", " ".join(argv))
        self.assertFalse(set(argv) & {"--bare", "--restricted", "--fallback-model", "--dangerously-skip-permissions"})
        budget = Budget(self.path)
        try:
            self.assertEqual(budget.report()["reserved_usd"], 0.1)
        finally:
            budget.close()

    def test_timeout_keeps_reservation_and_never_retries(self):
        calls = []
        adapter = ClaudeAdapter(enabled=True, executable="offline", budget_ledger=self.path, runner=self.runner(calls, error=TimeoutError()))
        with self.assertRaises(TimeoutError):
            asyncio.run(adapter.call("offline", {}, self.source))
        self.assertEqual(sum("-p" in argv for argv, kwargs in calls), 1)
        budget = Budget(self.path)
        try:
            self.assertEqual(budget.report()["calls"][0]["state"], "unknown")
            self.assertEqual(budget.report()["reserved_usd"], 0.1)
            self.assertTrue(budget.report()["halted"])
            with self.assertRaises(ProviderBlocked):
                budget.reserve("automatic retry forbidden")
        finally:
            budget.close()

    def test_version_drift_does_not_reserve_or_infer(self):
        calls = []
        adapter = ClaudeAdapter(enabled=True, executable="offline", budget_ledger=self.path, runner=self.runner(calls, version="different"))
        with self.assertRaises(ProviderBlocked):
            asyncio.run(adapter.call("offline", {}, self.source))
        self.assertTrue(all("-p" not in argv for argv, kwargs in calls))
        budget = Budget(self.path)
        try:
            self.assertEqual(budget.report()["reserved_usd"], 0)
        finally:
            budget.close()

    def test_claude_is_selectable_as_worker_but_not_default(self):
        plan = validate_plan(fixture_plan(provider="claude"))
        self.assertEqual(plan["tasks"][0]["provider"], "claude")
        args = parser().parse_args(["run", "offline", "--enable-claude", "--claude-budget-ledger", str(self.path), "--brain", "codex"])
        self.assertTrue(args.enable_claude)
        self.assertFalse(parser().parse_args(["run", "offline"]).enable_claude)

    def test_missing_budget_blocks_before_claim(self):
        store = Store(self.root/"state")
        try:
            plan = fixture_plan(provider="claude")
            approval = store.approve(plan, self.source)
            run = store.submit(plan, self.source, approval, "offline")
            status = asyncio.run(Scheduler(store, enable_claude=True, brain="interactive").execute(run))
            self.assertEqual(status["state"], "blocked")
            self.assertEqual(store.tasks(run)[0]["attempt"], 0)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
