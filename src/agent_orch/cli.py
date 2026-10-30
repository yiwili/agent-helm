from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import sys
from pathlib import Path

from .adapters.codex_cli import CodexAdapter
from .adapters.qoder_cn_cli import QoderCNAdapter
from .adapters.cursor_cli import CursorAdapter, prepare_config
from .adapters.claude_cli import ClaudeAdapter
from .adapters.deepseek_cli import DeepSeekAdapter
from .adapters.antigravity_cli import AntigravityAdapter
from .brain import fixture_plan
from .backup import backup_bundle, restore_bundle, verify_backup
from .integrator import integrate
from .models import OrchError, digest, validate_plan
from .scheduler import Scheduler
from .storage import Store
from .workspace import make_fixture


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def state_default():
    if os.environ.get("AGENT_ORCH_STATE"):
        return os.environ["AGENT_ORCH_STATE"]
    if os.name == "nt":
        return str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "agent-helm")
    return str(Path(os.environ.get("XDG_STATE_HOME", Path.home()/".local/state")) / "agent-helm")


def parser():
    p = argparse.ArgumentParser(prog="agent-orch", description="Local single-user fixture orchestrator; real providers disabled by default")
    p.add_argument("--state-root", default=state_default())
    sub = p.add_subparsers(dest="command", required=True)
    d = sub.add_parser("doctor")
    d.add_argument("--read-only", action="store_true")
    providers = sub.add_parser("providers").add_subparsers(dest="provider_command", required=True)
    prepare = providers.add_parser("prepare-cursor", help="Create a new dedicated deny-tools config outside the repository; no login or model calls")
    prepare.add_argument("--config-dir", type=Path, required=True)
    probe = providers.add_parser("probe")
    probe.add_argument("provider", choices=["codex", "qoder-cn", "cursor", "claude", "deepseek", "antigravity", "qoder", "traecli", "codebuddy"])
    probe.add_argument("--mode", choices=["metadata", "smoke"], default="metadata")
    probe.add_argument("--enable-codex", action="store_true")
    probe.add_argument("--enable-qoder-cn", action="store_true")
    probe.add_argument("--qoder-cn-bin")
    probe.add_argument("--enable-cursor", action="store_true")
    probe.add_argument("--cursor-cli-dir", type=Path)
    probe.add_argument("--cursor-config-dir", type=Path)
    probe.add_argument("--enable-claude", action="store_true")
    probe.add_argument("--claude-bin")
    probe.add_argument("--claude-budget-ledger", type=Path)
    probe.add_argument("--enable-deepseek", action="store_true")
    probe.add_argument("--deepseek-bin")
    probe.add_argument("--enable-antigravity", action="store_true")
    probe.add_argument("--antigravity-bin")
    probe.add_argument("--workspace", type=Path)
    plan = sub.add_parser("plan")
    plan.add_argument("--request", required=True)
    plan.add_argument("--output", required=True)
    plan.add_argument("--brain", choices=["interactive", "codex", "qoder-cn"], default="interactive")
    plan.add_argument("--worker-provider", choices=["codex", "qoder-cn", "cursor", "claude", "deepseek", "antigravity", "manual", "mock"], default="codex")
    plan.add_argument("--enable-codex", action="store_true")
    plan.add_argument("--enable-qoder-cn", action="store_true")
    plan.add_argument("--qoder-cn-bin")
    plan.add_argument("--workspace", type=Path)
    fixture = sub.add_parser("fixture")
    fixture.add_argument("--output", required=True)
    fixture.add_argument("--git", action="store_true", help="Initialize only the newly generated synthetic fixture")
    verifiers = sub.add_parser("verifiers").add_subparsers(dest="verifier_command", required=True)
    registration = verifiers.add_parser("register", help="Trusted local user entry; never expose to workers")
    registration.add_argument("--profile", required=True)
    registration.add_argument("--definition", required=True)
    registration.add_argument("--yes", action="store_true")
    approval = sub.add_parser("approve", help="Trusted local user entry; never expose as a model tool")
    approval.add_argument("--plan", required=True)
    approval.add_argument("--source", required=True)
    approval.add_argument("--yes", action="store_true", help="Explicit local user approval for this exact plan")
    approval.add_argument("--ttl", type=int, default=3600)
    revoke = sub.add_parser("revoke")
    revoke.add_argument("scope")
    submit = sub.add_parser("submit")
    submit.add_argument("--plan", required=True)
    submit.add_argument("--source", required=True)
    submit.add_argument("--approval-scope", required=True)
    submit.add_argument("--idempotency-key", required=True)
    execute = sub.add_parser("run", help="Execute persistently submitted tasks; interruption is reconciled, never blindly replayed")
    execute.add_argument("run_id")
    execute.add_argument("--enable-codex", action="store_true")
    execute.add_argument("--enable-qoder-cn", action="store_true")
    execute.add_argument("--qoder-cn-bin")
    execute.add_argument("--qoder-cn-model")
    execute.add_argument("--qoder-cn-context-window")
    execute.add_argument("--qoder-cn-reasoning-effort", choices=["low", "high", "max"])
    execute.add_argument("--qoder-cn-thinking", choices=["auto", "adaptive", "enabled", "disabled"])
    execute.add_argument("--enable-cursor", action="store_true")
    execute.add_argument("--cursor-cli-dir", type=Path)
    execute.add_argument("--cursor-config-dir", type=Path)
    execute.add_argument("--enable-claude", action="store_true")
    execute.add_argument("--claude-bin")
    execute.add_argument("--claude-budget-ledger", type=Path)
    execute.add_argument("--enable-deepseek", action="store_true")
    execute.add_argument("--deepseek-bin")
    execute.add_argument("--deepseek-model")
    execute.add_argument("--enable-antigravity", action="store_true")
    execute.add_argument("--antigravity-bin")
    execute.add_argument("--antigravity-model")
    execute.add_argument("--brain", choices=["mock", "interactive", "codex", "qoder-cn"], default="mock")
    execute.add_argument("--manual-result", type=Path)
    execute.add_argument("--retry-blocked", action="store_true")
    for name in ("status", "cancel", "integrate"):
        sub.add_parser(name).add_argument("run_id")
    events = sub.add_parser("events")
    events.add_argument("run_id")
    events.add_argument("--after-seq", type=int, default=0)
    review = sub.add_parser("review")
    review.add_argument("run_id")
    review.add_argument("--show-evidence", action="store_true")
    review.add_argument("--task")
    review.add_argument("--decision", type=Path)
    export = sub.add_parser("export")
    export.add_argument("run_id")
    export.add_argument("--output", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--output", required=True)
    check = sub.add_parser("verify-backup")
    check.add_argument("--backup", required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--output", required=True)
    sub.add_parser("recover")
    budgets = sub.add_parser("budgets", help="Trusted local cost authorization; never expose to workers").add_subparsers(dest="budget_command", required=True)
    budget_create = budgets.add_parser("create")
    budget_create.add_argument("--ledger", type=Path, required=True)
    budget_create.add_argument("--total-usd", required=True)
    budget_create.add_argument("--per-call-usd", required=True)
    budget_create.add_argument("--yes", action="store_true")
    budget_status = budgets.add_parser("status")
    budget_status.add_argument("--ledger", type=Path, required=True)
    demo = sub.add_parser("demo")
    demo.add_argument("--bad-first", action="store_true")
    demo.add_argument("--git", action="store_true", help="Use a newly created real Git repository as synthetic fixture")
    demo.add_argument("--real-codex", action="store_true", help="Explicitly run two ChatGPT-login CLI calls on generated synthetic data; never API fallback")
    demo.add_argument("--failure", choices=["bad", "timeout", "quota", "permission"])
    mcp = sub.add_parser("mcp", help="Local stdio MCP server; managed synthetic fixtures only; human approvals stay outside MCP")
    desk = sub.add_parser("desktop", help="Private Tauri desktop transport or local Studio UI server; not an MCP endpoint")
    desk.add_argument("--http", action="store_true", help="Serve local Studio UI and desktop API over 127.0.0.1")
    desk.add_argument("--web-dir", type=Path, help="Compiled frontend web assets directory")
    desk.add_argument("--host", default="127.0.0.1")
    desk.add_argument("--port", type=int, default=1420)
    desk.add_argument("--open", action="store_true", help="Open browser automatically")
    mcp.add_argument("--brain", choices=["mock", "interactive", "codex", "qoder-cn"], default="mock")
    for provider in ("codex", "qoder-cn", "claude", "cursor", "deepseek", "antigravity"):
        mcp.add_argument("--enable-"+provider, action="store_true")
    for flag in ("qoder-cn-bin", "qoder-cn-model", "qoder-cn-context-window", "qoder-cn-reasoning-effort", "qoder-cn-thinking", "claude-bin", "claude-budget-ledger", "cursor-cli-dir", "cursor-config-dir", "deepseek-bin", "deepseek-model", "antigravity-bin", "antigravity-model"):
        mcp.add_argument("--"+flag)
    approval_service = sub.add_parser("service-approve", help="Human-only terminal approval of a service proposal")
    approval_service.add_argument("proposal_id")
    approval_service.add_argument("--yes", action="store_true", help="Explicit trusted local approval for tests/automation; never expose to models")
    return p


async def dispatch(args):
    command = args.command
    if command == "service-approve":
        from .service import Service
        service = Service(args.state_root)
        try:
            proposal = service.proposal(args.proposal_id)
            if not args.yes:
                output(proposal)
                if not sys.stdin.isatty() or input("Type APPROVE for this exact plan and managed fixture: ") != "APPROVE":
                    raise OrchError("Human terminal approval required")
            return service.approve(args.proposal_id)
        finally:
            service.close()
    if command == "budgets":
        from .budget import Budget, create_budget
        if args.budget_command == "create":
            if not args.yes:
                if not sys.stdin.isatty() or input(f"Type AUTHORIZE for total USD {args.total_usd}, per-call USD {args.per_call_usd}: ") != "AUTHORIZE":
                    raise OrchError("Budget requires explicit local authorization or --yes")
            return create_budget(args.ledger, args.total_usd, args.per_call_usd)
        budget = Budget(args.ledger)
        try:
            return budget.report()
        finally:
            budget.close()
    if command == "verify-backup":
        meta = verify_backup(args.backup)
        return {"verified": True, "manifest_hash": meta["manifest_hash"], "files": len(meta["files"])}
    if command == "restore":
        return restore_bundle(args.backup, args.output)
    if command == "doctor":
        # Intentionally no state directory or DB creation for read-only metadata commands.
        return {"platform": platform.platform(), "python": sys.version, "state_root": str(Path(args.state_root).resolve()),
                "binaries": {n: shutil.which(n) for n in ("git", "codex", "claude", "qoder", "traecli", "codebuddy")},
                "api_enabled": False, "gui_enabled": False, "merge_enabled": False,
                "execution_scope": "synthetic fixture only; trusted local single user; snapshot is not an OS sandbox"}
    if command == "providers":
        if args.provider_command == "prepare-cursor":
            return {"provider": "cursor", "config_dir": str(prepare_config(args.config_dir)), "authenticated": False, "model_calls": 0}
        if args.provider not in ("codex", "qoder-cn", "cursor", "claude", "deepseek", "antigravity"):
            return {"provider": args.provider, "binary": shutil.which(args.provider), "adapter": "unsupported", "billing_verified": False, "production_ready": False}
        if args.provider == "cursor":
            adapter = CursorAdapter(enabled=args.enable_cursor, package=args.cursor_cli_dir, config_dir=args.cursor_config_dir)
        elif args.provider == "claude":
            adapter = ClaudeAdapter(enabled=args.enable_claude, executable=args.claude_bin, budget_ledger=args.claude_budget_ledger)
        elif args.provider == "deepseek":
            adapter = DeepSeekAdapter(enabled=args.enable_deepseek, executable=args.deepseek_bin)
        elif args.provider == "antigravity":
            adapter = AntigravityAdapter(enabled=args.enable_antigravity, executable=args.antigravity_bin)
        else:
            adapter = CodexAdapter(enabled=args.enable_codex) if args.provider == "codex" else QoderCNAdapter(enabled=args.enable_qoder_cn, executable=args.qoder_cn_bin)
        report = await adapter.probe(Path.cwd())
        if args.mode == "smoke":
            if not args.workspace or not args.workspace.is_dir():
                raise OrchError("Smoke requires --workspace pointing to a disposable fixture")
            from .schemas import RESULT_SCHEMA
            result = await adapter.call('Return {"summary":"ORCH_PROBE_OK","files":[]}. Do not use tools.', RESULT_SCHEMA, args.workspace)
            report["smoke_passed"] = result == {"summary": "ORCH_PROBE_OK", "files": []}
            report["metadata"] = adapter.last_metadata
        return report
    if command == "plan":
        request = load(args.request)
        # Interactive brain submits a structured plan. No hidden model calls or synthetic reasoning claims.
        if args.brain in ("codex", "qoder-cn"):
            from .brain import CodexBrain
            if not args.workspace or not args.workspace.is_dir():
                raise OrchError("Real planning requires disposable --workspace")
            adapter = CodexAdapter(enabled=args.enable_codex) if args.brain == "codex" else QoderCNAdapter(enabled=args.enable_qoder_cn, executable=args.qoder_cn_bin)
            plan = await CodexBrain(adapter).plan(request, args.workspace, worker_provider=args.worker_provider)
        else:
            plan = validate_plan(request)
        dest = Path(args.output)
        if dest.exists():
            raise OrchError("Plan output already exists")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"plan": str(dest.resolve()), "plan_hash": digest(plan), "source": args.brain}
    if command == "fixture":
        from .git_workspace import make_git_fixture
        return {"source": str((make_git_fixture(args.output) if args.git else make_fixture(args.output)).resolve())}
    store = Store(args.state_root)
    try:
        if command == "verifiers":
            from .verifier_registry import register_profile
            definition = load(args.definition)
            if not args.yes:
                if not sys.stdin.isatty():
                    raise OrchError("Verifier registration requires local confirmation or explicit --yes")
                output({"profile": args.profile, "definition": definition})
                if input("Type REGISTER to trust this fixed verifier command: ") != "REGISTER":
                    raise OrchError("Verifier registration declined")
            return register_profile(store.root, args.profile, definition)
        if command == "approve":
            plan = validate_plan(load(args.plan))
            if not 1 <= args.ttl <= 86400:
                raise OrchError("Approval TTL must be 1..86400 seconds")
            if not args.yes:
                if not sys.stdin.isatty():
                    raise OrchError("Approval requires interactive local user or explicit --yes")
                output({"plan": plan, "source": str(Path(args.source).resolve()), "plan_hash": digest(plan)})
                if input("Type APPROVE to approve this exact scope: ") != "APPROVE":
                    raise OrchError("Approval declined")
            return {"scope_id": store.approve(plan, args.source), "plan_hash": digest(plan)}
        if command == "revoke":
            store.revoke(args.scope)
            return {"revoked": args.scope}
        if command == "submit":
            return {"run_id": store.submit(load(args.plan), args.source, args.approval_scope, args.idempotency_key)}
        if command == "run":
            if args.retry_blocked:
                for t in store.tasks(args.run_id):
                    if t["state"] == "blocked":
                        store.transition(args.run_id, t["id"], "queued", payload={"source": "explicit-local-retry"})
            worker_models = {
                k: v for k, v in (
                    ("qoder-cn", args.qoder_cn_model),
                    ("deepseek", args.deepseek_model),
                    ("antigravity", args.antigravity_model),
                ) if v
            }
            worker_options = {
                "qoder-cn": {
                    "model": args.qoder_cn_model,
                    "context_window": args.qoder_cn_context_window,
                    "reasoning_effort": args.qoder_cn_reasoning_effort,
                    "thinking": args.qoder_cn_thinking,
                },
                "deepseek": {"model": args.deepseek_model},
                "antigravity": {"model": args.antigravity_model},
            }
            scheduler = Scheduler(store, enable_codex=args.enable_codex, enable_qoder_cn=args.enable_qoder_cn,
                                  qoder_cn_bin=args.qoder_cn_bin, enable_cursor=args.enable_cursor,
                                  cursor_cli_dir=args.cursor_cli_dir, cursor_config_dir=args.cursor_config_dir,
                                  enable_claude=args.enable_claude, claude_bin=args.claude_bin,
                                  claude_budget_ledger=args.claude_budget_ledger,
                                  enable_deepseek=args.enable_deepseek, deepseek_bin=args.deepseek_bin,
                                  enable_antigravity=args.enable_antigravity, antigravity_bin=args.antigravity_bin,
                                  worker_models=worker_models, worker_options=worker_options,
                                  brain=args.brain, manual_result=args.manual_result)
            return await scheduler.execute(args.run_id)
        if command == "status":
            return Scheduler(store).status(args.run_id)
        if command == "cancel":
            return {"run_id": args.run_id, "state": store.cancel(args.run_id)}
        if command == "events":
            return store.events(args.run_id, args.after_seq)
        if command == "recover":
            return {"needs_reconciliation": store.recover()}
        if command == "review":
            if args.decision:
                if not args.task:
                    raise OrchError("Review decision requires --task")
                return Scheduler(store).interactive_review(args.run_id, args.task, load(args.decision))
            store.run(args.run_id)
            return [{"task_id": r["task_id"], "attempt": r["number"], "state": r["state"], "evidence": json.loads(r["evidence"]) if r["evidence"] else None}
                    for r in store.db.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY task_id,number", (args.run_id,))]
        if command == "integrate":
            return await integrate(store, args.run_id)
        if command == "backup":
            return backup_bundle(store, args.output)
        if command == "export":
            return export_run(store, args.run_id, args.output)
        if command == "demo":
            import uuid
            ident = uuid.uuid4().hex
            from .git_workspace import make_git_fixture
            source = (make_git_fixture if args.git else make_fixture)(store.root / "fixtures" / ident)
            plan = fixture_plan(provider="codex" if args.real_codex else "mock",
                                behavior=args.failure or ("bad-first" if args.bad_first else "good"))
            if args.failure == "timeout":
                plan["tasks"][0]["wall_seconds"] = 0.1
            scope = store.approve(plan, source, actor="explicit-synthetic-demo")
            run = store.submit(plan, source, scope, "demo-"+ident)
            result = await Scheduler(store, enable_codex=args.real_codex, brain="codex" if args.real_codex else "mock").execute(run)
            result["mock"] = not args.real_codex
            result["source"] = str(source)
            result["evidence_root"] = str(store.root / "runs" / run)
            if result["state"] == "accepted":
                result["integration"] = await integrate(store, run)
            return result
        raise OrchError("Unknown command")
    finally:
        store.close()


def export_run(store, run, target):
    r = store.run(run)
    target = Path(target).resolve()
    if target.exists() or target.is_relative_to(store.root):
        raise OrchError("Export target must be new and outside state root")
    target.mkdir(parents=True)
    bundle = {"schema_version": 1, "run": r, "tasks": store.tasks(run), "events": store.events(run),
              "attempts": [dict(x) for x in store.db.execute("SELECT * FROM attempts WHERE run_id=?", (run,))],
              "reviews": [dict(x) for x in store.db.execute("SELECT * FROM reviews WHERE run_id=?", (run,))],
              "integrations": [dict(x) for x in store.db.execute("SELECT * FROM integrations WHERE run_id=?", (run,))]}
    (target/"run.json").write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
    source = store.root / "runs" / run
    if source.exists():
        shutil.copytree(source, target/"workspaces")
    from .workspace import manifest
    index = manifest(target)
    (target/"manifest.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    return {"export": str(target), "manifest_hash": digest(index), "credentials_included": False}


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        arguments = list(sys.argv[1:] if argv is None else argv)
        if arguments and arguments[0] == "_process-host":
            from .process_host import main as host
            sys.argv = [sys.argv[0], *arguments[1:]]
            return host()
        if arguments and arguments[0] == "_fixture-check":
            from .verifier import frozen_fixture_check
            if len(arguments) != 2:
                return 2
            return frozen_fixture_check(arguments[1])
        args = parser().parse_args(arguments)
        if args.command == "desktop":
            from .desktop import serve_desktop, serve_desktop_http
            try:
                if getattr(args, "http", False):
                    if not args.web_dir or not args.web_dir.is_dir():
                        raise OrchError("--http requires --web-dir pointing to built frontend assets")
                    return asyncio.run(serve_desktop_http(args.state_root, args.web_dir, host=args.host, port=args.port, open_browser=args.open))
                return asyncio.run(serve_desktop(args.state_root))
            except (Exception, asyncio.CancelledError, KeyboardInterrupt) as error:
                print("Desktop core stopped: "+str(error)[:500], file=sys.stderr)
                return 2
        if args.command == "mcp":
            from .mcp_server import serve
            runtime = {key: getattr(args, key) for key in ("brain", "enable_codex", "enable_qoder_cn", "enable_claude", "enable_cursor", "enable_deepseek", "enable_antigravity", "qoder_cn_bin", "claude_bin", "claude_budget_ledger", "cursor_cli_dir", "cursor_config_dir", "deepseek_bin", "antigravity_bin")}
            runtime["worker_models"] = {
                k: v for k, v in (
                    ("qoder-cn", args.qoder_cn_model),
                    ("deepseek", args.deepseek_model),
                    ("antigravity", args.antigravity_model),
                ) if v
            }
            runtime["worker_options"] = {
                "qoder-cn": {
                    "model": args.qoder_cn_model,
                    "context_window": args.qoder_cn_context_window,
                    "reasoning_effort": args.qoder_cn_reasoning_effort,
                    "thinking": args.qoder_cn_thinking,
                },
                "deepseek": {"model": args.deepseek_model},
                "antigravity": {"model": args.antigravity_model},
            }
            try:
                return asyncio.run(serve(args.state_root, **runtime))
            except (Exception, asyncio.CancelledError, KeyboardInterrupt) as error:
                from .adapters.codex_cli import redact
                print("MCP stopped: "+redact(str(error)), file=sys.stderr)
                return 2
        result = asyncio.run(dispatch(args))
        output(result)
        if isinstance(result, dict) and result.get("state") in ("failed", "blocked", "timed_out", "escalated", "needs_reconciliation"):
            return 2
        return 0
    except (OrchError, OSError, ValueError, RuntimeError, TimeoutError) as e:
        output({"error": type(e).__name__, "message": str(e)[:1000]})
        return 2
    except KeyboardInterrupt:
        output({"state": "interrupted", "next": "recover after lease expiry; never replay uncertain side effects"})
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
