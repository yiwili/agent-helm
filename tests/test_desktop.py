import asyncio
import json
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from agent_orch.desktop import Desktop
from agent_orch.models import OrchError
from agent_orch.service import tools
from agent_orch.scheduler import Scheduler


class DesktopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="helm-desktop-")
        self.desktop = Desktop(Path(self.temp.name)/"state")

    async def asyncTearDown(self):
        await self.desktop.service.shutdown()
        self.temp.cleanup()

    async def proposal(self, **payload):
        created = await self.desktop.call("create_task", payload)
        return await self.desktop.call("proposal", {"proposal_id": created["proposal_id"]})

    async def test_safe_defaults_and_privilege_separation(self):
        dashboard = await self.desktop.call("dashboard", {})
        self.assertEqual([a["provider"] for a in dashboard["agents"] if a["enabled"]], ["mock"])
        self.assertFalse({"approve_start", "budget_create", "review"} & {t["name"] for t in tools()})
        with self.assertRaises(OrchError):
            await self.desktop.call("create_task", {"provider": "claude"})

    async def test_profile_model_persistence_and_native_adapter_selection(self):
        profile = {**self.desktop.profiles["claude"], "enabled": True, "model": "fixture-model-v2"}
        await self.desktop.call("save_agent", {"profile": profile})
        saved = json.loads(self.desktop.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["claude"]["models"], ["fixture-model-v2"])
        runtime = self.desktop.runtime(self.desktop.profiles, "claude")
        scheduler = Scheduler(self.desktop.service.store, **runtime)
        self.assertEqual(scheduler.claude.model, "fixture-model-v2")
        self.assertIsNone(scheduler.codex.model)

    async def test_model_and_connection_reject_commands_and_accidental_secrets(self):
        for model in ("--danger", "model; command", "sk-private", "a\nb"):
            with self.subTest(model=model), self.assertRaises(OrchError):
                self.desktop.validate_profile({**self.desktop.profiles["codex"], "model": model})
        with self.assertRaises(OrchError):
            self.desktop.validate_profile({**self.desktop.profiles["codex"], "connection": "path", "executable": "cmd /c anything"})

    async def test_approval_binds_frozen_model_and_rejects_changed_hashes(self):
        await self.desktop.call("save_agent", {"profile": {**self.desktop.profiles["codex"], "enabled": True, "model": "fixture-a"}})
        p = await self.proposal(provider="codex")
        await self.desktop.call("save_agent", {"profile": {**self.desktop.profiles["codex"], "model": "fixture-b"}})
        frozen = await self.desktop.call("proposal", {"proposal_id": p["proposal_id"]})
        self.assertEqual(frozen["agent_configuration"]["worker_models"]["codex"], "fixture-a")
        for extra in ({"confirmed": False}, {"runtime_hash": "changed"}, {"plan_hash": "changed"}):
            with self.assertRaises(OrchError):
                await self.desktop.call("approve_start", {"proposal_id": p["proposal_id"], "plan_hash": p["plan_hash"], "runtime_hash": p["runtime_hash"], "confirmed": True, **extra})
        self.assertEqual(self.desktop.service.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)

    async def test_mock_full_workflow_rework_evidence_and_integration(self):
        p = await self.proposal(fault_demo=True)
        payload = {"proposal_id": p["proposal_id"], "plan_hash": p["plan_hash"], "runtime_hash": p["runtime_hash"], "confirmed": True}
        run = (await self.desktop.call("approve_start", payload))["run_id"]
        await asyncio.wait_for(self.desktop.service.jobs[run], 30)
        detail = await self.desktop.call("run_detail", {"run_id": run})
        self.assertEqual(detail["status"]["state"], "accepted")
        self.assertEqual([r["decision"] for r in detail["evidence"][0]["reviews"]], ["rework", "accept"])
        again = await self.desktop.call("approve_start", payload)
        self.assertEqual(again["run_id"], run)
        result = await self.desktop.call("integrate", {"run_id": run})
        self.assertTrue(result["verification_passed"])
        self.assertFalse(result["merged"])

    async def test_budget_needs_confirmation_and_does_not_infer(self):
        with self.assertRaises(OrchError):
            await self.desktop.call("budget_create", {"total_usd": .3, "per_call_usd": .1})
        created = await self.desktop.call("budget_create", {"total_usd": .3, "per_call_usd": .1, "confirmed": True})
        self.assertTrue(Path(created["ledger"]).is_relative_to(self.desktop.service.root))
        self.assertEqual(self.desktop.service.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)
        self.assertIsInstance(await self.desktop.call("budget_status", {}), dict)

    async def test_human_review_and_explicit_rework_resume_without_live_provider(self):
        runtime = {**self.desktop.runtime(self.desktop.profiles, "mock"), "brain": "interactive"}
        with patch.object(self.desktop, "runtime", return_value=runtime):
            p = await self.proposal()
        run = (await self.desktop.call("approve_start", {"proposal_id": p["proposal_id"], "plan_hash": p["plan_hash"], "runtime_hash": p["runtime_hash"], "confirmed": True}))["run_id"]
        await self.desktop.service.jobs[run]
        detail = await self.desktop.call("run_detail", {"run_id": run})
        self.assertEqual(detail["status"]["state"], "reviewing")
        task = detail["evidence"][0]
        review = {"run_id": run, "task_id": task["task_id"], "candidate_hash": task["attempts"][-1]["candidate_hash"], "decision": "rework", "confirmed": True}
        with self.assertRaises(OrchError):
            await self.desktop.call("review", {**review, "candidate_hash": "stale"})
        result = await self.desktop.call("review", review)
        self.assertEqual(result["state"], "queued")
        self.assertTrue(self.desktop.service.jobs[run].done())
        await self.desktop.call("resume", {"run_id": run, "confirmed": True})
        await self.desktop.service.jobs[run]
        detail = await self.desktop.call("run_detail", {"run_id": run})
        await self.desktop.call("review", {**review, "candidate_hash": detail["evidence"][0]["attempts"][-1]["candidate_hash"], "decision": "accept"})
        self.assertEqual(self.desktop.service.status(run)["state"], "accepted")

    async def test_mock_probe_is_offline(self):
        self.assertTrue((await self.desktop.call("probe_agent", {"provider": "mock"}))["compatible"])

    async def test_private_stdio_entrypoint_and_malformed_message_correlation(self):
        result = await asyncio.to_thread(subprocess.run,
            [sys.executable, "-m", "agent_orch", "--state-root", str(Path(self.temp.name)/"stdio-state"), "desktop"],
            input='{"id":1,"action":"dashboard"}\nnot-json\n{"id":3,"action":"probe_agent","payload":{"provider":"mock"}}\n',
            text=True, encoding="utf-8", capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([line["id"] for line in lines], [1, None, 3])
        self.assertEqual(lines[0]["result"]["scope"], "synthetic-only")
        self.assertTrue(lines[2]["result"]["installed"])

    async def test_model_selection_reaches_all_native_cli_argv_without_inference(self):
        from agent_orch.adapters.codex_cli import CodexAdapter
        from agent_orch.adapters.qoder_cn_cli import QoderCNAdapter
        from agent_orch.adapters.claude_cli import ClaudeAdapter
        from agent_orch.adapters.deepseek_cli import DeepSeekAdapter
        from agent_orch.adapters.antigravity_cli import AntigravityAdapter
        from agent_orch.budget import create_budget
        ledger = Path(self.temp.name)/"budget.db"
        create_budget(ledger, .3, .1)
        workspace = Path(self.temp.name)/"workspace"
        workspace.mkdir()
        for adapter_type in (CodexAdapter, QoderCNAdapter, ClaudeAdapter):
            kwargs = {"budget_ledger": ledger} if adapter_type is ClaudeAdapter else {}
            adapter = adapter_type(enabled=True, executable="offline-native-cli", model="fixture-model", **kwargs)
            adapter.probe = AsyncMock(return_value={"compatible": True, "authenticated": True})
            adapter._run = AsyncMock(side_effect=RuntimeError("offline interception"))
            with self.assertRaisesRegex(RuntimeError, "offline interception"):
                await adapter.call("synthetic prompt", {}, workspace)
            argv = adapter._run.call_args.args[0]
            self.assertEqual(argv[argv.index("--model")+1], "fixture-model")

    async def test_qoder_cn_thinking_and_context_window_and_unified_worker_result(self):
        from agent_orch.adapters.qoder_cn_cli import QoderCNAdapter
        from agent_orch.models import normalize_worker_result
        exe = Path(self.temp.name) / "qoderclicn.exe"
        exe.write_bytes(b"MZ")
        profile = {
            **self.desktop.profiles["qoder-cn"],
            "enabled": True,
            "connection": "path",
            "executable": f'"{exe}"',
            "model": "deepseek-flash",
            "context_window": "1m",
            "reasoning_effort": "high",
        }
        await self.desktop.call("save_agent", {"profile": profile})
        saved = json.loads(self.desktop.path.read_text(encoding="utf-8"))["qoder-cn"]
        self.assertEqual(saved["executable"], str(exe))
        self.assertEqual(saved["model"], "deepseek-flash")
        self.assertEqual(saved["context_window"], "1m")
        self.assertEqual(saved["reasoning_effort"], "high")
        runtime = self.desktop.runtime(self.desktop.profiles, "qoder-cn")
        scheduler = Scheduler(self.desktop.service.store, **runtime)
        self.assertEqual(scheduler.qoder_cn.model, "deepseek-flash")
        self.assertEqual(scheduler.qoder_cn.context_window, "1000000")
        self.assertEqual(scheduler.qoder_cn.reasoning_effort, "high")
        unified = normalize_worker_result(
            {"summary": "fixed empty list sum", "files": [{"path": "stats.py", "content": "def total(xs): return sum(xs)\n"}]},
            ["stats.py"],
            provider="qoder-cn",
            attempt=1,
            thinking={"model": "deepseek-flash", "reasoning_effort": "high", "context_window": "1m"},
        )
        self.assertEqual(unified["protocol"], "agent-helm.worker-result.v2")
        self.assertEqual(unified["schema_version"], 2)
        self.assertEqual(unified["provider"], "qoder-cn")
        self.assertEqual(unified["thinking"]["model"], "deepseek-flash")
        self.assertEqual(unified["thinking"]["reasoning_effort"], "high")
        self.assertEqual(unified["thinking"]["context_window"], "1m")
        self.assertEqual(len(unified["operations"]), 1)
        self.assertEqual(unified["operations"][0]["action"], "write")

    async def test_leader_worker_modes_ahcp_wire_and_chinese_report(self):
        from agent_orch.wire import expand_wire_result, expand_wire_review, parse_ahcp_text
        # 1. Verify AHCP-1 compact wire expansion
        compact_res = expand_wire_result({"s": "Fixed empty list fallback", "f": [{"p": "stats.py", "content": "x"}] if False else [{"p": "stats.py", "c": "def total(values):\n    return sum(values) if values else 0\n"}]})
        self.assertEqual(compact_res["summary"], "Fixed empty list fallback")
        self.assertEqual(compact_res["files"][0]["path"], "stats.py")
        compact_rev = expand_wire_review({"d": "A", "h": "hash123", "r": ["All checks pass"], "e": ["D", "V"]})
        self.assertEqual(compact_rev["decision"], "accept")
        self.assertEqual(compact_rev["evidence_refs"], ["diff", "verification"])
        parsed_wire = parse_ahcp_text("AHCP1|REV|R|hash456|D,S|Need empty list check")
        self.assertEqual(parsed_wire["decision"], "rework")

        # 2. Mode 2 unsplittable task -> verbatim passthrough to upstream worker
        p_single = await self.proposal(
            goal="修复合成样例中的空列表求和",
            mode="decompose",
            leader="mock",
            provider="mock",
            roles=[{"role": "upstream-coder", "provider": "mock"}, {"role": "reviewer-helper", "provider": "mock"}],
        )
        self.assertFalse(p_single["plan"]["orchestration"]["splittable"])
        self.assertTrue(p_single["plan"]["orchestration"]["passthrough_verbatim"])
        self.assertEqual(len(p_single["plan"]["tasks"]), 1)

        # 3. Mode 2 splittable multi-role concurrent/DAG task -> executes and produces Chinese final report
        p_split = await self.proposal(
            goal="阶段一：实现空列表返回 0；阶段二：复核正负数与浮点数求和保持一致",
            mode="decompose",
            leader="mock",
            provider="mock",
            sub_reviewer="mock",
            roles=[
                {"role": "implementer", "provider": "mock", "parallel": True},
                {"role": "verifier-guard", "provider": "mock", "parallel": True},
            ],
        )
        self.assertTrue(p_split["plan"]["orchestration"]["splittable"])
        self.assertEqual(len(p_split["plan"]["tasks"]), 2)
        run = (await self.desktop.call("approve_start", {
            "proposal_id": p_split["proposal_id"],
            "plan_hash": p_split["plan_hash"],
            "runtime_hash": p_split["runtime_hash"],
            "confirmed": True,
        }))["run_id"]
        await asyncio.wait_for(self.desktop.service.jobs[run], 30)
        detail = await self.desktop.call("run_detail", {"run_id": run})
        self.assertEqual(detail["status"]["state"], "accepted")
        self.assertEqual(detail["chinese_report"]["inter_agent_language"], "en")
        self.assertEqual(detail["chinese_report"]["final_leader_language"], "zh-CN")
        self.assertIn("Leader (mock) 最终中文结项报告", detail["chinese_report"]["leader_final_response_zh"])
        self.assertEqual(len(detail["chinese_report"]["tasks_zh"]), 2)
        self.assertIn("已通过 Leader 终审并验收", detail["chinese_report"]["final_state_zh"])
        first_attempt = detail["evidence"][0]["attempts"][0]
        self.assertNotIn("summary_zh", first_attempt["worker_result"]["extensions"])
        self.assertEqual(first_attempt["worker_result"]["extensions"]["wire_language"], "en")
        hops = first_attempt["agent_hops"]
        self.assertGreaterEqual(len(hops), 3)
        for hop in hops[:-1]:
            self.assertEqual(hop["language"], "en")
            self.assertIn("thinking_state", hop)
            self.assertIn("return_content", hop)
        self.assertEqual(hops[-1]["stage"], "leader_final_zh")
        self.assertEqual(hops[-1]["language"], "zh-CN")
        self.assertIn("最终中文审查结论", hops[-1]["return_content"])
