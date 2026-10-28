import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_orch.adapters.base import ProviderBlocked
from agent_orch.adapters.cursor_cli import CONFIG, PINNED_VERSION, CursorAdapter, check_config, parse_stream, prepare_config
from agent_orch.brain import MockBrain, fixture_plan
from agent_orch.cli import parser
from agent_orch.models import OrchError, validate_plan
from agent_orch.scheduler import Scheduler
from agent_orch.storage import Store
from agent_orch.supervisor import ProcessResult, safe_env
from agent_orch.workspace import make_fixture


def stream(result=None):
    return [{"type": "system", "subtype": "init", "apiKeySource": "login", "session_id": "offline-session", "model": "offline-model"},
            {"type": "result", "subtype": "success", "is_error": False, "session_id": "offline-session",
             "result": json.dumps(result or {"summary": "offline", "files": []})}]


def encoded(events):
    return "\n".join(json.dumps(e) for e in events)


class CursorProtocol(unittest.TestCase):
    def test_success_records_identity_without_inventing_billing(self):
        value, meta = parse_stream(encoded(stream()), 0)
        self.assertEqual(value["summary"], "offline")
        self.assertEqual(meta["model"], "offline-model")
        self.assertEqual(meta["tool_calls"], 0)
        self.assertIsNone(meta["usage"]["amount_usd"])
        self.assertFalse(meta["billing_verified"])

    def test_rejects_tool_calls_including_read_only_tools(self):
        for name in ("readToolCall", "shellToolCall", "mcpToolCall", "taskToolCall"):
            events = stream()
            events.insert(1, {"type": "tool_call", "subtype": "started", "tool_call": {name: {}}})
            with self.subTest(name=name), self.assertRaises(ProviderBlocked):
                parse_stream(encoded(events), 0)

    def test_api_key_authentication_cannot_pass_as_login(self):
        for source in ("env", "flag", None):
            events = stream()
            events[0]["apiKeySource"] = source
            with self.assertRaises(ProviderBlocked):
                parse_stream(encoded(events), 0)

    def test_requires_terminal_success_same_session_and_exit_zero(self):
        for key, value in (("session_id", "different"), ("is_error", True), ("subtype", "failed")):
            events = stream()
            events[-1][key] = value
            with self.subTest(key=key), self.assertRaises(OrchError):
                parse_stream(encoded(events), 0)
        for events, code in ((stream()[:1], 0), (stream()[1:], 0), (stream(), 1), (stream()+stream()[-1:], 0)):
            with self.assertRaises(OrchError):
                parse_stream(encoded(events), code)

    def test_refuses_malformed_duplicate_and_fenced_json(self):
        for text in ('{', '[]', '{"type":"system","type":"result"}'):
            with self.assertRaises(OrchError):
                parse_stream(text, 0)
        for result in ('```json\n{}\n```', '{"x":1,"x":2}', '[]'):
            events = stream()
            events[-1]["result"] = result
            with self.assertRaises(OrchError):
                parse_stream(encoded(events), 0)

    def test_permission_denial_overrides_success(self):
        events = stream()
        events[-1]["permission_denials"] = [{"tool": "Shell"}]
        with self.assertRaises(ProviderBlocked):
            parse_stream(encoded(events), 0)


class CursorWorker(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="helm-cursor-offline-")
        self.root = Path(self.temp.name)
        self.config = prepare_config(self.root/"private-config")
        self.workspace = make_fixture(self.root/"synthetic")
        self.package = self.root/"official-package-shape"
        self.package.mkdir()
        (self.package/("node.exe" if os.name == "nt" else "node")).write_text("offline fixture")
        (self.package/"index.js").write_text("offline fixture")

    def tearDown(self):
        self.temp.cleanup()

    def test_config_setup_never_overwrites_existing_directory(self):
        with self.assertRaises(FileExistsError):
            prepare_config(self.config)
        self.assertEqual(check_config(self.config, self.workspace), self.config)

    def test_config_must_be_external_and_deny_tools(self):
        with self.assertRaises(OrchError):
            check_config(self.config, self.root)
        changed = copy.deepcopy(CONFIG)
        changed["permissions"]["allow"] = ["Shell(*)"]
        (self.config/"cli-config.json").write_text(json.dumps(changed))
        with self.assertRaises(ProviderBlocked):
            check_config(self.config, self.workspace)

    def test_global_extensions_and_custom_endpoints_are_blocked(self):
        for name in ("mcp.json", "hooks.json", "plugins", "rules", "skills"):
            path = self.config/name
            path.write_text("{}")
            with self.subTest(name=name), self.assertRaises(ProviderBlocked):
                check_config(self.config, self.workspace)
            path.unlink()
        changed = {**CONFIG, "endpoint": "https://example.invalid"}
        (self.config/"cli-config.json").write_text(json.dumps(changed))
        with self.assertRaises(ProviderBlocked):
            check_config(self.config, self.workspace)

    def test_environment_override_cannot_forward_credentials_or_node_options(self):
        env = safe_env(True, {"CURSOR_CONFIG_DIR": str(self.config)})
        self.assertEqual(env["CURSOR_CONFIG_DIR"], str(self.config))
        for overrides in ({"CURSOR_API_KEY": "OFFLINE_CANARY"}, {"NODE_OPTIONS": "--require fake"}, {"CURSOR_CONFIG_DIR": "relative"}):
            with self.assertRaises(OrchError):
                safe_env(True, overrides)
        with self.assertRaises(OrchError):
            safe_env(False, {"CURSOR_CONFIG_DIR": str(self.config)})

    def test_default_disabled_never_starts_a_process(self):
        async def forbidden(*args, **kwargs):
            self.fail("Default adapter invoked a provider")
        adapter = CursorAdapter(package=self.package, config_dir=self.config, runner=forbidden)
        with self.assertRaises(ProviderBlocked):
            asyncio.run(adapter.call("offline", {}, self.workspace))

    def test_native_node_launch_stdin_and_deny_configuration(self):
        calls = []
        async def runner(argv, cwd, **kwargs):
            calls.append((argv, kwargs))
            if "--version" in argv:
                return ProcessResult(0, PINNED_VERSION, "")
            if "status" in argv:
                return ProcessResult(0, '{"status":"authenticated","isAuthenticated":true}', "")
            return ProcessResult(0, encoded(stream()), "")
        adapter = CursorAdapter(enabled=True, package=self.package, config_dir=self.config, runner=runner)
        asyncio.run(adapter.call("PROMPT_CANARY", {}, self.workspace))
        argv, kwargs = calls[-1]
        self.assertEqual(argv[1], str(self.package/"index.js"))
        self.assertEqual(argv[argv.index("--mode")+1], "ask")
        self.assertEqual(argv[argv.index("--sandbox")+1], "enabled")
        self.assertNotIn("PROMPT_CANARY", " ".join(argv))
        self.assertIn("PROMPT_CANARY", kwargs["stdin"])
        self.assertEqual(kwargs["env_overrides"], {"CURSOR_CONFIG_DIR": str(self.config)})
        self.assertFalse(set(argv) & {"--force", "--yolo", "--approve-mcps", "--api-key"})

    def test_version_drift_and_missing_login_prevent_inference(self):
        for version, authenticated in (("different-build", True), (PINNED_VERSION, False)):
            calls = []
            async def runner(argv, cwd, **kwargs):
                calls.append(argv)
                if "--version" in argv:
                    return ProcessResult(0, version, "")
                if "status" in argv:
                    return ProcessResult(0, json.dumps({"status": "authenticated" if authenticated else "unauthenticated", "isAuthenticated": authenticated}), "")
                self.fail("Unapproved inference started")
            adapter = CursorAdapter(enabled=True, package=self.package, config_dir=self.config, runner=runner)
            with self.assertRaises(ProviderBlocked):
                asyncio.run(adapter.call("offline", {}, self.workspace))
            self.assertTrue(all("--print" not in argv for argv in calls))

    def test_project_cursor_settings_block_before_process_launch(self):
        (self.workspace/".cursor").mkdir()
        async def forbidden(*args, **kwargs):
            self.fail("Project settings were loaded")
        adapter = CursorAdapter(enabled=True, package=self.package, config_dir=self.config, runner=forbidden)
        with self.assertRaises(ProviderBlocked):
            asyncio.run(adapter.call("offline", {}, self.workspace))

    def test_cursor_plan_and_cli_worker_selection(self):
        plan = validate_plan(fixture_plan(provider="cursor"))
        self.assertEqual(plan["tasks"][0]["provider"], "cursor")
        args = parser().parse_args(["plan", "--request", "input.json", "--output", "out.json", "--worker-provider", "cursor"])
        self.assertEqual(args.worker_provider, "cursor")

    def test_disabled_cursor_does_not_claim_an_attempt(self):
        store = Store(self.root/"state")
        try:
            plan = fixture_plan(provider="cursor")
            approval = store.approve(plan, self.workspace)
            run = store.submit(plan, self.workspace, approval, "offline-request")
            self.assertEqual(asyncio.run(Scheduler(store, brain="interactive").execute(run))["state"], "blocked")
            self.assertEqual(store.tasks(run)[0]["attempt"], 0)
        finally:
            store.close()

    def test_codex_and_cursor_are_both_downstream_workers(self):
        for provider in ("codex", "cursor"):
            store = Store(self.root/("state-"+provider))
            try:
                plan = fixture_plan(provider=provider)
                approval = store.approve(plan, self.workspace)
                run = store.submit(plan, self.workspace, approval, "offline-role-test")
                scheduler = Scheduler(store, enable_codex=True, enable_cursor=True, brain="codex")
                worker = scheduler.adapters[provider]
                async def execute(task, workspace, attempt, cancelled):
                    self.assertEqual(task["provider"], provider)
                    return {"summary": "offline candidate", "files": [{"path": "stats.py", "content": "def total(values):\n    return sum(values)\n"}]}
                worker.execute = execute
                worker.last_metadata = {"source": "offline-test"}
                scheduler.brain = MockBrain()
                self.assertEqual(asyncio.run(scheduler.execute(run))["state"], "accepted")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
