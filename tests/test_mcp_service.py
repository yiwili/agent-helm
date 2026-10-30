import asyncio
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

from agent_orch.brain import fixture_plan
from agent_orch.mcp_server import Protocol
from agent_orch.models import OrchError
from agent_orch.service import Service, tools
from agent_orch.verifier import frozen_fixture_check


class MCPServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="helm-service-")
        self.root = Path(self.temp.name)
        self.service = Service(self.root/"state")

    async def asyncTearDown(self):
        await self.service.shutdown()
        self.temp.cleanup()

    async def prepare(self, behavior="good", provider="mock"):
        workspace = await self.service.call("helm_fixture_create", {})
        plan = fixture_plan(provider=provider, behavior=behavior)
        plan["tasks"][0]["milestone_review"] = False
        proposal = await self.service.call("helm_plan_prepare", {"workspace_id": workspace["workspace_id"], "plan": plan})
        return workspace, proposal

    async def submitted(self, behavior="good", provider="mock"):
        workspace, proposal = await self.prepare(behavior, provider)
        self.service.approve(proposal["proposal_id"])
        result = await self.service.call("helm_submit", {"proposal_id": proposal["proposal_id"]})
        return workspace, proposal, result["run_id"]

    async def test_no_model_authority_or_arbitrary_path_tools(self):
        names = {t["name"] for t in tools()}
        self.assertEqual(len(names), 11)
        self.assertFalse(names & {"approve", "budget_create", "shell", "push", "merge"})
        with self.assertRaises(OrchError):
            await self.service.call("helm_fixture_create", {"path": str(self.root)})
        self.assertEqual((await self.service.call("helm_capabilities", {}))["enabled_workers"], ["mock"])

    async def test_unapproved_proposal_cannot_submit(self):
        workspace, proposal = await self.prepare()
        result = await self.service.call("helm_submit", {"proposal_id": proposal["proposal_id"]})
        self.assertEqual(result["state"], "approval_required")
        self.assertEqual(self.service.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)

    async def test_submit_is_one_run_per_approved_proposal(self):
        workspace, proposal, run = await self.submitted()
        again = await self.service.call("helm_submit", {"proposal_id": proposal["proposal_id"]})
        self.assertEqual(again["run_id"], run)
        self.assertEqual(self.service.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)

    async def test_managed_source_drift_is_rejected_before_approval(self):
        workspace, proposal = await self.prepare()
        path = self.service.root/"service-workspaces"/workspace["workspace_id"]/"stats.py"
        path.write_text("company data is never used; this is a synthetic drift fixture")
        with self.assertRaises(OrchError):
            self.service.approve(proposal["proposal_id"])

    async def test_rework_evidence_and_idempotent_integration(self):
        workspace, proposal, run = await self.submitted("bad-first")
        await self.service.call("helm_run_start", {"run_id": run})
        await self.service.jobs[run]
        self.assertEqual((await self.service.call("helm_run_get", {"run_id": run}))["state"], "accepted")
        evidence = await self.service.call("helm_evidence_get", {"run_id": run, "task_id": "fix-total"})
        self.assertEqual([r["decision"] for r in evidence["reviews"]], ["rework", "accept"])
        self.assertNotIn("argv", json.dumps(evidence))
        integrated = await self.service.call("helm_integrate", {"run_id": run})
        self.assertTrue(integrated["verification_passed"])
        self.assertFalse(integrated["merged"])
        self.assertEqual(await self.service.call("helm_integrate", {"run_id": run}), integrated)
        (Path(integrated["workspace"])/"stats.py").write_text("tampered synthetic copy")
        with self.assertRaises(OrchError):
            await self.service.call("helm_integrate", {"run_id": run})

    async def test_cancel_works_while_background_run_is_active(self):
        workspace, proposal, run = await self.submitted("timeout")
        await self.service.call("helm_run_start", {"run_id": run})
        for index in range(100):
            if self.service.store.tasks(run)[0]["state"] == "running":
                break
            await asyncio.sleep(0.01)
        await self.service.call("helm_cancel", {"run_id": run})
        await asyncio.wait_for(self.service.jobs[run], 5)
        self.assertEqual(self.service.status(run)["state"], "cancelled")

    async def test_foreign_runs_and_invalid_handles_are_rejected(self):
        for run in ("../state.db", "run-"+"0"*32):
            with self.assertRaises(OrchError):
                await self.service.call("helm_run_get", {"run_id": run})

    async def test_real_workers_remain_disabled_before_claim(self):
        workspace, proposal, run = await self.submitted(provider="claude")
        await self.service.call("helm_run_start", {"run_id": run})
        await self.service.jobs[run]
        self.assertEqual(self.service.status(run)["state"], "blocked")
        self.assertEqual(self.service.store.tasks(run)[0]["attempt"], 0)

    async def test_events_are_paginated_and_no_raw_payload_is_exposed(self):
        workspace, proposal, run = await self.submitted()
        result = await self.service.call("helm_events_get", {"run_id": run, "limit": 1})
        self.assertEqual(len(result["events"]), 1)
        self.assertNotIn("payload", result["events"][0])
        with self.assertRaises(OrchError):
            await self.service.call("helm_events_get", {"run_id": run, "limit": 101})

    async def test_restart_preserves_proposals_without_replaying_runs(self):
        workspace, proposal, run = await self.submitted()
        other = Service(self.service.root)
        try:
            self.assertEqual(other.proposal(proposal["proposal_id"])["run_id"], run)
            self.assertEqual(other.jobs, {})
        finally:
            other.close()


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Service(Path(self.temp.name)/"state")
        self.protocol = Protocol(self.service)

    async def asyncTearDown(self):
        await self.service.shutdown()
        self.temp.cleanup()

    async def ready(self, version="2025-11-25"):
        result = await self.protocol.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "offline", "version": "1"}}})
        await self.protocol.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    async def test_lifecycle_and_version_negotiation(self):
        early = await self.protocol.handle({"jsonrpc": "2.0", "id": 0, "method": "tools/list"})
        self.assertIn("error", early)
        result = await self.ready("future-unsupported")
        self.assertEqual(result["result"]["protocolVersion"], "2025-11-25")
        result = await self.protocol.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(result["result"]["tools"]), 11)

    async def test_tool_errors_are_results_not_transport_errors(self):
        await self.ready()
        result = await self.protocol.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "approve", "arguments": {}}})
        self.assertTrue(result["result"]["isError"])
        self.assertFalse(result["result"]["structuredContent"]["retry_automatically"])

    async def test_bad_requests_unknown_methods_and_notifications(self):
        for message in ([], {}, {"jsonrpc": "2.0", "id": True, "method": "ping"}):
            self.assertEqual((await self.protocol.handle(message))["error"]["code"], -32600)
        self.assertIsNone(await self.protocol.handle({"jsonrpc": "2.0", "method": "unknown-notification"}))
        await self.ready()
        self.assertEqual((await self.protocol.handle({"jsonrpc": "2.0", "id": 4, "method": "unknown"}))["error"]["code"], -32601)


class FrozenAndTransportTests(unittest.TestCase):
    def test_bundled_fixture_entry_runs_only_guarded_source(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root/"stats.py").write_text("def total(values):\n    return sum(values)\n")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(frozen_fixture_check(root), 0)
            self.assertEqual(len(json.loads(output.getvalue())), 5)
            (root/"stats.py").write_text("import os\ndef total(values): return 0\n")
            self.assertEqual(frozen_fixture_check(root), 1)

    def test_real_stdio_mock_round_trip(self):
        script = Path(__file__).resolve().parents[1]/"scripts/verify_mcp.py"
        spec = importlib.util.spec_from_file_location("offline_mcp_smoke", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import sys
        with tempfile.TemporaryDirectory() as name:
            result = module.smoke([sys.executable, "-m", "agent_orch"], Path(name)/"new-smoke")
        self.assertTrue(result["passed"])
        self.assertEqual(result["real_provider_calls"], 0)


if __name__ == "__main__":
    unittest.main()
