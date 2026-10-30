"""Privileged local desktop channel. Never expose these actions over MCP."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from .adapters.codex_cli import model_id, redact, unique_object
from .brain import fixture_plan
from .budget import Budget, create_budget
from .models import OrchError, digest
from .scheduler import Scheduler
from .service import Service

PROVIDERS = {"mock": "Mock", "codex": "Codex", "claude": "Claude CLI", "qoder-cn": "Qoder CN"}


def defaults():
    return {key: {"provider": key, "name": name, "enabled": key == "mock", "connection": "auto", "executable": "", "model": "", "models": [], "budget_ledger": ""}
            for key, name in PROVIDERS.items()}


class Desktop:
    def __init__(self, root):
        self.service = Service(root)
        self.path = self.service.root/"desktop-profiles.json"
        self.profiles = defaults()
        if self.path.exists():
            body = json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
            for key, value in body.items():
                if key not in PROVIDERS or not isinstance(value, dict) or value.get("provider") != key:
                    raise OrchError("Invalid saved agent profile")
                self.profiles[key] = self.validate_profile(value)
        self.probes = {}

    def validate_profile(self, profile):
        if not isinstance(profile, dict) or set(profile)-set(defaults()["mock"]) or profile.get("provider") not in PROVIDERS:
            raise OrchError("Unknown agent profile")
        result = {**defaults()[profile["provider"]], **profile}
        if type(result["enabled"]) is not bool or result["connection"] not in ("auto", "path") or not isinstance(result["name"], str) or not 1 <= len(result["name"]) <= 60:
            raise OrchError("Invalid agent connection configuration")
        result["model"] = model_id(result["model"]) or ""
        if not isinstance(result["models"], list) or len(result["models"]) > 30:
            raise OrchError("Model history limit exceeded")
        result["models"] = list(dict.fromkeys(model_id(m) for m in result["models"] if m))
        if result["model"] and result["model"] not in result["models"]:
            result["models"] = [result["model"], *result["models"]][:30]
        for key in ("executable", "budget_ledger"):
            value = result[key]
            if not isinstance(value, str) or len(value) > 2000:
                raise OrchError("Invalid configuration path")
            if value and (not Path(value).is_absolute() or Path(value).suffix.lower() in (".cmd", ".bat", ".ps1")):
                raise OrchError("Use an absolute native executable or budget path, never a shell command")
        if result["connection"] == "path" and (not result["executable"] or not Path(result["executable"]).is_file()):
            raise OrchError("Selected CLI executable does not exist")
        if result["provider"] == "mock":
            result.update(connection="auto", executable="", model="", models=[], enabled=True)
        return result

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.profiles, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def runtime(self, profiles, provider):
        profile = profiles[provider]
        if not profile["enabled"]:
            raise OrchError("Enable this agent before creating its task")
        paths = {key: value["executable"] if value["connection"] == "path" else None for key, value in profiles.items()}
        return {"enable_codex": profiles["codex"]["enabled"], "enable_claude": profiles["claude"]["enabled"],
                "enable_qoder_cn": profiles["qoder-cn"]["enabled"], "codex_bin": paths["codex"],
                "claude_bin": paths["claude"], "qoder_cn_bin": paths["qoder-cn"],
                "claude_budget_ledger": profiles["claude"]["budget_ledger"] or None,
                "worker_models": {key: value["model"] or None for key, value in profiles.items()},
                "brain": "mock" if provider == "mock" else "interactive"}

    def dashboard(self):
        proposals = self.service.index.execute("SELECT id,run FROM proposals ORDER BY rowid DESC LIMIT 50").fetchall()
        runs = []
        for row in proposals:
            proposal = self.service.proposal(row["id"])
            plan = proposal["plan"]
            status = self.status(row["run"]) if row["run"] else {"state": proposal["state"], "tasks": []}
            runs.append({"proposal_id": row["id"], "run_id": row["run"], "goal": plan["goal"], "provider": plan["tasks"][0]["provider"],
                         "state": status["state"], "tasks": status["tasks"]})
        return {"agents": list(self.profiles.values()), "runs": runs, "probes": self.probes, "scope": "synthetic-only", "state_root": str(self.service.root),
                "active_runs": sum(not job.done() for job in self.service.jobs.values())}

    def status(self, run):
        status = self.service.status(run)
        # Core run aggregation calls an unfinished human review "running".
        # Present the actionable task state without changing the persisted core state.
        if status["state"] == "running" and any(task["state"] == "reviewing" for task in status["tasks"]):
            status.update(engine_state="running", state="reviewing")
        return status

    async def call(self, action, payload):
        if not isinstance(payload, dict):
            raise OrchError("Desktop payload must be an object")
        if action == "dashboard":
            return self.dashboard()
        if action == "save_agent":
            profile = self.validate_profile(payload.get("profile"))
            self.profiles[profile["provider"]] = profile
            self.save()
            return self.dashboard()
        if action == "probe_agent":
            provider = payload.get("provider")
            if provider not in PROVIDERS:
                raise OrchError("Unknown agent")
            if provider == "mock":
                result = {"installed": True, "authenticated": True, "compatible": True, "version": "offline"}
            else:
                runtime = self.runtime({key: {**value, "enabled": True} for key, value in self.profiles.items()}, provider)
                adapter = Scheduler(self.service.store, **runtime).adapters[provider]
                result = await adapter.probe(self.service.root)
            self.probes[provider] = {key: result.get(key) for key in ("installed", "authenticated", "compatible", "version")}
            return self.probes[provider]
        if action == "budget_create":
            if payload.get("confirmed") is not True:
                raise OrchError("Explicit budget confirmation required")
            path = self.service.root/"desktop-budgets"/("budget-"+uuid.uuid4().hex+".db")
            result = create_budget(path, payload["total_usd"], payload["per_call_usd"])
            self.profiles["claude"]["budget_ledger"] = str(path)
            self.save()
            return result
        if action == "budget_status":
            budget = Budget(self.profiles["claude"]["budget_ledger"])
            try:
                return budget.report()
            finally:
                budget.close()
        if action == "create_task":
            provider = payload.get("provider", "mock")
            if provider not in PROVIDERS:
                raise OrchError("Unsupported worker")
            runtime = self.runtime(self.profiles, provider)
            if provider == "claude":
                budget = Budget(runtime["claude_budget_ledger"])
                budget.close()
            fixture = await self.service.call("helm_fixture_create", {})
            goal = payload.get("goal", "修复合成样例中的空列表求和")
            if not isinstance(goal, str) or not 1 <= len(goal) <= 2000:
                raise OrchError("Task goal must be 1..2000 characters")
            plan = fixture_plan(goal=goal, provider=provider, behavior="bad-first" if payload.get("fault_demo") and provider == "mock" else "good")
            plan["tasks"][0]["milestone_review"] = False  # Desktop live tasks use explicit human final review.
            result = await self.service.call("helm_plan_prepare", {"workspace_id": fixture["workspace_id"], "plan": plan})
            # Freeze human-selected connection/model at proposal creation, not at a later run.
            path = self.service.root/(result["proposal_id"]+".desktop.json")
            path.write_text(json.dumps({"runtime": runtime, "runtime_hash": digest(runtime)}), encoding="utf-8")
            result["runtime_hash"] = digest(runtime)
            result["requested_model"] = self.profiles[provider]["model"] or None
            return result
        if action == "proposal":
            result = self.service.proposal(payload["proposal_id"])
            frozen = json.loads((self.service.root/(result["proposal_id"]+".desktop.json")).read_text(encoding="utf-8"))
            result.update(runtime_hash=frozen["runtime_hash"], agent_configuration=frozen["runtime"])
            return result
        if action == "approve_start":
            if payload.get("confirmed") is not True:
                raise OrchError("Review the exact plan and explicitly approve it")
            if sum(not job.done() for job in self.service.jobs.values()) >= 4:
                raise OrchError("Four runs are already active; wait before starting another")
            ident = payload["proposal_id"]
            if not isinstance(ident, str) or not __import__("re").fullmatch(r"proposal-[0-9a-f]{32}", ident):
                raise OrchError("Invalid proposal handle")
            frozen = json.loads((self.service.root/(ident+".desktop.json")).read_text(encoding="utf-8"))
            runtime = frozen["runtime"]
            if payload.get("plan_hash") != self.service.proposal(ident)["plan_hash"] or payload.get("runtime_hash") != digest(runtime) or frozen["runtime_hash"] != digest(runtime):
                raise OrchError("Plan or connection/model changed; review the proposal again")
            existing = self.service.proposal(ident)["run_id"]
            if existing:
                return {"run_id": existing}
            self.service.approve(ident)
            run = (await self.service.call("helm_submit", {"proposal_id": ident}))["run_id"]
            if run not in self.service.jobs and self.service.store.run(run)["state"] == "queued":
                self.service.jobs[run] = asyncio.create_task(self.execute(run, runtime))
            return {"run_id": run}
        if action == "run_detail":
            run = payload["run_id"]
            self.service._owned(run)
            status = self.status(run)
            return {"status": status, "events": await self.service.call("helm_events_get", {"run_id": run, "limit": 100}),
                    "evidence": [await self.service.call("helm_evidence_get", {"run_id": run, "task_id": task["id"]}) for task in status["tasks"]]}
        if action == "review":
            if payload.get("confirmed") is not True or payload.get("decision") not in ("accept", "rework", "escalate"):
                raise OrchError("Explicit review decision required")
            run = payload["run_id"]
            self.service._owned(run)
            scheduler = Scheduler(self.service.store, brain="interactive")
            scheduler.interactive_review(run, payload["task_id"], {"decision": payload["decision"], "candidate_hash": payload["candidate_hash"],
                                                                  "reasons": ["Human desktop review"], "evidence_refs": ["diff", "verification", "baseline", "scope"]})
            # A rework is not silently resumed; the UI asks the human again.
            return self.status(run)
        if action == "resume":
            if payload.get("confirmed") is not True:
                raise OrchError("Explicit rework execution confirmation required")
            run = payload["run_id"]
            row = self.service._owned(run)
            frozen = json.loads((self.service.root/(row["id"]+".desktop.json")).read_text(encoding="utf-8"))
            runtime = frozen["runtime"]
            if frozen["runtime_hash"] != digest(runtime):
                raise OrchError("Frozen runtime changed; reconciliation required")
            if sum(not job.done() for job in self.service.jobs.values()) >= 4:
                raise OrchError("Four runs are already active")
            if self.service.store.run(run)["state"] != "queued" or (run in self.service.jobs and not self.service.jobs[run].done()):
                raise OrchError("Run is not ready for explicitly requested rework")
            self.service.jobs[run] = asyncio.create_task(self.execute(run, runtime))
            return self.service.status(run)
        if action == "cancel":
            run = payload["run_id"]
            self.service._owned(run)
            finished = run in self.service.jobs and self.service.jobs[run].done()
            reviewing = {t["id"] for t in self.service.store.tasks(run) if t["state"] == "reviewing"}
            result = await self.service.call("helm_cancel", payload)
            # Only a known finished worker may settle a waiting human review locally.
            # Missing/lost jobs remain subject to the existing reconciliation rules.
            if finished:
                for task in self.service.store.tasks(run):
                    if task["id"] in reviewing and task["state"] == "cancel_requested":
                        self.service.store.transition(run, task["id"], "cancelled", payload={"human_review_cancelled": True})
                result["state"] = self.service.store.run(run)["state"]
            return result
        if action == "integrate":
            return await self.service.call("helm_integrate", payload)
        raise OrchError("Unknown desktop action")

    async def execute(self, run, runtime):
        try:
            await Scheduler(self.service.store, **runtime).execute(run)
        except Exception:
            self.service.store.event(run, "", 0, "service_job_error", {"requires_reconciliation": True})


async def serve_desktop(root):
    from .mcp_server import MAX_MESSAGE
    controller = Desktop(root)
    try:
        while True:
            line = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_MESSAGE+1)
            if not line:
                break
            if len(line) > MAX_MESSAGE:
                break
            message = None
            try:
                message = json.loads(line.decode("utf-8"), object_pairs_hook=unique_object)
                if not isinstance(message, dict):
                    raise OrchError("Desktop request must be an object")
                value = await controller.call(message["action"], message.get("payload", {}))
                result = {"id": message.get("id"), "result": value}
            except Exception as error:
                result = {"id": message.get("id") if isinstance(locals().get("message"), dict) else None, "error": redact(str(error))}
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        await controller.service.shutdown()
    return 0
