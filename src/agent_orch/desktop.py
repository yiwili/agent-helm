"""Privileged local desktop channel. Never expose these actions over MCP."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from .adapters.codex_cli import model_id, redact, unique_object
from .adapters.qoder_cn_cli import REASONING_EFFORTS, THINKING_MODES, normalize_context_window, resolve_executable_path
from .brain import decompose_or_passthrough_plan, fixture_plan
from .budget import Budget, create_budget
from .models import OrchError, digest
from .scheduler import Scheduler
from .service import Service
from .wire import build_chinese_orchestration_report

PROVIDERS = {
    "mock": "Mock",
    "codex": "Codex",
    "claude": "Claude CLI",
    "qoder-cn": "Qoder CN",
    "deepseek": "DeepSeek Harness",
    "antigravity": "Gemini / Antigravity CLI",
}

PRESET_MODELS = {
    "mock": [],
    "codex": ["gpt-5-codex", "o3", "o4-mini"],
    "claude": ["claude-sonnet-4-5", "claude-opus-4-5", "glm-5.3[1m]", "deepseek-v4-pro"],
    "qoder-cn": [
        "deepseek-flash",
        "DeepSeek-Flash",
        "deepseek-v4-pro",
        "DeepSeek-V4-Pro",
        "glm-5.3",
        "GLM-5.3",
        "glm-5.3-flash",
        "GLM-5.3-Flash",
        "kimi-k3.1-pro",
        "Kimi-K3.1-Pro",
        "Qwen3.8-Max",
        "Qwen3.8-Flash",
        "MiniMax-M3.1-Flash",
    ],
    "deepseek": ["deepseek-reasoner", "deepseek-chat", "deepseek-v4-pro", "deepseek-flash"],
    "antigravity": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-3-pro", "gemini-3-flash"],
}


def defaults():
    return {
        key: {
            "provider": key,
            "name": name,
            "enabled": key == "mock",
            "connection": "auto",
            "executable": "",
            "model": "deepseek-flash" if key == "qoder-cn" else "",
            "models": [],
            "preset_models": list(PRESET_MODELS.get(key, [])),
            "reasoning_effort": "high" if key == "qoder-cn" else "",
            "thinking": "",
            "context_window": "1m" if key == "qoder-cn" else "",
            "review_mode": "auto",
            "budget_ledger": "",
        }
        for key, name in PROVIDERS.items()
    }


def _clean_path(provider, raw_value):
    if not isinstance(raw_value, str):
        raise OrchError("Invalid configuration path")
    value = raw_value.strip().strip('"').strip("'")
    if len(value) > 2000:
        raise OrchError("Invalid configuration path")
    if not value:
        return ""
    if provider == "qoder-cn":
        resolved = resolve_executable_path(value) or value
    else:
        p = Path(value)
        if p.is_dir():
            names = {
                "codex": ("codex.exe", "codex"),
                "claude": ("claude.exe", "claude"),
                "deepseek": ("deepseek-harness.exe", "deepseek-cli.exe", "deepseek.exe"),
                "antigravity": ("agy.exe", "gemini.exe", "antigravity.exe"),
            }.get(provider, ())
            resolved = value
            for n in names:
                sub = p / n
                if sub.is_file():
                    resolved = str(sub)
                    break
        else:
            resolved = value
    return resolved


class Desktop:
    def __init__(self, root):
        self.service = Service(root)
        self.path = self.service.root / "desktop-profiles.json"
        self.profiles = defaults()
        if self.path.exists():
            body = json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
            for key, value in body.items():
                if key not in PROVIDERS or not isinstance(value, dict) or value.get("provider") != key:
                    raise OrchError("Invalid saved agent profile")
                self.profiles[key] = self.validate_profile(value)
        self.probes = {}

    def validate_profile(self, profile):
        if not isinstance(profile, dict) or set(profile) - set(defaults()["mock"]) or profile.get("provider") not in PROVIDERS:
            raise OrchError("Unknown agent profile")
        provider = profile["provider"]
        result = {**defaults()[provider], **profile}
        result["preset_models"] = list(PRESET_MODELS.get(provider, []))
        if type(result["enabled"]) is not bool or result["connection"] not in ("auto", "path") or not isinstance(result["name"], str) or not 1 <= len(result["name"]) <= 60:
            raise OrchError("Invalid agent connection configuration")
        result["model"] = model_id(result["model"]) or ""
        if not isinstance(result["models"], list) or len(result["models"]) > 30:
            raise OrchError("Model history limit exceeded")
        result["models"] = list(dict.fromkeys(model_id(m) for m in result["models"] if m))
        if result["model"] and result["model"] not in result["models"]:
            result["models"] = [result["model"], *result["models"]][:30]
        effort = (result.get("reasoning_effort") or "").strip().lower() if isinstance(result.get("reasoning_effort"), str) else ""
        if effort and effort not in REASONING_EFFORTS:
            raise OrchError("Invalid reasoning_effort; expected low, high, or max")
        result["reasoning_effort"] = effort
        thinking = (result.get("thinking") or "").strip().lower() if isinstance(result.get("thinking"), str) else ""
        if thinking and thinking not in THINKING_MODES:
            raise OrchError("Invalid thinking mode")
        result["thinking"] = thinking
        ctx_raw = (result.get("context_window") or "").strip().lower() if isinstance(result.get("context_window"), str) else ""
        if ctx_raw:
            normalize_context_window(ctx_raw)
        result["context_window"] = ctx_raw
        review_mode = (result.get("review_mode") or "auto").strip().lower() if isinstance(result.get("review_mode"), str) else "auto"
        if review_mode not in ("auto", "interactive"):
            raise OrchError("Invalid review_mode")
        result["review_mode"] = review_mode
        result["executable"] = _clean_path(provider, result["executable"])
        result["budget_ledger"] = _clean_path(provider, result["budget_ledger"])
        for key in ("executable", "budget_ledger"):
            value = result[key]
            if value and (not Path(value).is_absolute() or Path(value).suffix.lower() in (".cmd", ".bat", ".ps1")):
                raise OrchError("Use an absolute native executable or budget path, never a shell command")
        if result["connection"] == "path" and (not result["executable"] or not Path(result["executable"]).is_file()):
            raise OrchError("Selected CLI executable does not exist")
        if provider == "mock":
            result.update(connection="auto", executable="", model="", models=[], reasoning_effort="", thinking="", context_window="", review_mode="auto", enabled=True)
        return result

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.profiles, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def runtime(self, profiles, provider, leader=None):
        profile = profiles[provider]
        if not profile["enabled"]:
            raise OrchError("Enable this agent before creating its task")
        if leader and leader in profiles and leader not in ("mock", "interactive") and not profiles[leader]["enabled"]:
            raise OrchError(f"Enable leader agent '{leader}' before selecting it as Leader")
        paths = {key: value["executable"] if value["connection"] == "path" else None for key, value in profiles.items()}
        worker_options = {
            key: {
                "model": value["model"] or None,
                "reasoning_effort": value.get("reasoning_effort") or None,
                "thinking": value.get("thinking") or None,
                "context_window": value.get("context_window") or None,
            }
            for key, value in profiles.items()
        }
        review_mode = profile.get("review_mode", "auto")
        if leader in ("interactive", "human"):
            brain = "interactive"
        elif leader in ("codex", "qoder-cn", "claude", "deepseek", "antigravity"):
            brain = leader
        else:
            brain = "mock" if (provider == "mock" or review_mode == "auto" or leader == "mock") else "interactive"
        return {
            "enable_codex": profiles["codex"]["enabled"],
            "enable_claude": profiles["claude"]["enabled"],
            "enable_qoder_cn": profiles["qoder-cn"]["enabled"],
            "enable_deepseek": profiles["deepseek"]["enabled"],
            "enable_antigravity": profiles["antigravity"]["enabled"],
            "codex_bin": paths["codex"],
            "claude_bin": paths["claude"],
            "qoder_cn_bin": paths["qoder-cn"],
            "deepseek_bin": paths["deepseek"],
            "antigravity_bin": paths["antigravity"],
            "claude_budget_ledger": profiles["claude"]["budget_ledger"] or None,
            "worker_models": {key: value["model"] or None for key, value in profiles.items()},
            "worker_options": worker_options,
            "brain": brain,
        }

    def dashboard(self):
        proposals = self.service.index.execute("SELECT id,run FROM proposals ORDER BY rowid DESC LIMIT 50").fetchall()
        runs = []
        for row in proposals:
            proposal = self.service.proposal(row["id"])
            plan = proposal["plan"]
            status = self.status(row["run"]) if row["run"] else {"state": proposal["state"], "tasks": []}
            orch = plan.get("orchestration") if isinstance(plan.get("orchestration"), dict) else {}
            runs.append({"proposal_id": row["id"], "run_id": row["run"], "goal": plan["goal"], "provider": plan["tasks"][0]["provider"],
                         "leader": orch.get("leader", "mock"), "mode": orch.get("mode", "direct"),
                         "splittable": orch.get("splittable", False),
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
            if isinstance(payload.get("profile"), dict):
                saved = self.validate_profile(payload["profile"])
                self.profiles[saved["provider"]] = saved
                self.save()
                provider = saved["provider"]
            else:
                provider = payload.get("provider")
            if provider not in PROVIDERS:
                raise OrchError("Unknown agent")
            if provider == "mock":
                result = {"installed": True, "authenticated": True, "compatible": True, "version": "offline", "binary": "builtin"}
            else:
                runtime = self.runtime({key: {**value, "enabled": True} for key, value in self.profiles.items()}, provider)
                adapter = Scheduler(self.service.store, **runtime).adapters[provider]
                result = await adapter.probe(self.service.root)
            self.probes[provider] = {key: result.get(key) for key in ("installed", "authenticated", "compatible", "version", "binary")}
            return self.probes[provider]
        if action == "budget_create":
            if payload.get("confirmed") is not True:
                raise OrchError("Explicit budget confirmation required")
            path = self.service.root / "desktop-budgets" / ("budget-" + uuid.uuid4().hex + ".db")
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
            leader = payload.get("leader") or None
            mode = payload.get("mode", "direct")
            sub_reviewer = payload.get("sub_reviewer") or None
            if sub_reviewer and sub_reviewer not in PROVIDERS:
                raise OrchError("Unsupported sub_reviewer")
            if sub_reviewer and sub_reviewer != "mock" and not self.profiles[sub_reviewer]["enabled"]:
                raise OrchError(f"Enable sub-reviewer agent '{sub_reviewer}' before selecting it")
            role_assignments = payload.get("roles") if isinstance(payload.get("roles"), list) else None
            if role_assignments:
                for item in role_assignments:
                    if isinstance(item, dict) and item.get("provider") in PROVIDERS and item["provider"] != "mock":
                        if not self.profiles[item["provider"]]["enabled"]:
                            raise OrchError(f"Enable role worker '{item['provider']}' before creating multi-role task")
            runtime = self.runtime(self.profiles, provider, leader=leader)
            if provider == "claude" or leader == "claude" or sub_reviewer == "claude":
                budget = Budget(runtime["claude_budget_ledger"])
                budget.close()
            fixture = await self.service.call("helm_fixture_create", {})
            goal = payload.get("goal", "修复合成样例中的空列表求和")
            if not isinstance(goal, str) or not 1 <= len(goal) <= 2000:
                raise OrchError("Task goal must be 1..2000 characters")
            behavior = "bad-first" if payload.get("fault_demo") and provider == "mock" else "good"
            if leader or mode == "decompose" or role_assignments or sub_reviewer:
                plan = decompose_or_passthrough_plan(
                    goal=goal,
                    leader=leader or runtime["brain"],
                    mode=mode,
                    primary_worker=provider,
                    role_assignments=role_assignments,
                    sub_reviewer=sub_reviewer,
                    behavior=behavior,
                )
            else:
                plan = fixture_plan(goal=goal, provider=provider, behavior=behavior)
            for t in plan["tasks"]:
                t["milestone_review"] = False
            result = await self.service.call("helm_plan_prepare", {"workspace_id": fixture["workspace_id"], "plan": plan})
            # Freeze human-selected connection/model at proposal creation, not at a later run.
            path = self.service.root / (result["proposal_id"] + ".desktop.json")
            path.write_text(json.dumps({"runtime": runtime, "runtime_hash": digest(runtime)}), encoding="utf-8")
            result["runtime_hash"] = digest(runtime)
            result["requested_model"] = self.profiles[provider]["model"] or None
            return result
        if action == "proposal":
            result = self.service.proposal(payload["proposal_id"])
            frozen = json.loads((self.service.root / (result["proposal_id"] + ".desktop.json")).read_text(encoding="utf-8"))
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
            frozen = json.loads((self.service.root / (ident + ".desktop.json")).read_text(encoding="utf-8"))
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
            owned = self.service._owned(run)
            status = self.status(run)
            evidence_list = [await self.service.call("helm_evidence_get", {"run_id": run, "task_id": task["id"]}) for task in status["tasks"]]
            plan = json.loads(owned["plan"])
            orch = plan.get("orchestration") if isinstance(plan.get("orchestration"), dict) else {}
            attempts_by_task = {ev["task_id"]: ev["attempts"] for ev in evidence_list}
            reviews_by_task = {ev["task_id"]: ev["reviews"] for ev in evidence_list}
            chinese_report = build_chinese_orchestration_report(
                mode=orch.get("mode", "direct"),
                leader=orch.get("leader", "mock"),
                splittable=bool(orch.get("splittable", False)),
                decomposition_reason=orch.get("decomposition_reason", "Standard single-worker task execution"),
                tasks=plan["tasks"],
                attempts_by_task=attempts_by_task,
                reviews_by_task=reviews_by_task,
                final_state=status["state"],
            )
            return {
                "status": status,
                "events": await self.service.call("helm_events_get", {"run_id": run, "limit": 100}),
                "evidence": evidence_list,
                "orchestration": orch,
                "chinese_report": chinese_report,
            }
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
            frozen = json.loads((self.service.root / (row["id"] + ".desktop.json")).read_text(encoding="utf-8"))
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
            line = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_MESSAGE + 1)
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


async def serve_desktop_http(root, web_dir, host="127.0.0.1", port=1420, open_browser=False):
    import mimetypes
    import webbrowser
    from .mcp_server import MAX_MESSAGE

    controller = Desktop(root)
    web_root = Path(web_dir).resolve()
    lock = asyncio.Lock()

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            header_bytes = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15)
            header_text = header_bytes.decode("iso-8859-1")
            lines = header_text.split("\r\n")
            req_line = lines[0].split(" ")
            if len(req_line) < 2:
                return
            method, raw_path = req_line[0], req_line[1].split("?", 1)[0]
            headers = {}
            for h in lines[1:]:
                if ":" in h:
                    k, v = h.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            content_len = int(headers.get("content-length", "0"))
            if content_len > MAX_MESSAGE:
                writer.write(b"HTTP/1.1 413 Payload Too Large\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            body_bytes = await asyncio.wait_for(reader.readexactly(content_len), timeout=15) if content_len > 0 else b""

            if method == "POST" and raw_path == "/api/desktop":
                try:
                    msg = json.loads(body_bytes.decode("utf-8"), object_pairs_hook=unique_object)
                    if not isinstance(msg, dict):
                        raise OrchError("Desktop request must be an object")
                    async with lock:
                        val = await controller.call(msg.get("action"), msg.get("payload", {}))
                    resp_data = json.dumps({"result": val}, ensure_ascii=False).encode("utf-8")
                    status_line = "HTTP/1.1 200 OK"
                except Exception as exc:
                    resp_data = json.dumps({"error": redact(str(exc))}, ensure_ascii=False).encode("utf-8")
                    status_line = "HTTP/1.1 400 Bad Request"
                resp_headers = (
                    f"{status_line}\r\n"
                    f"Content-Type: application/json; charset=utf-8\r\n"
                    f"Content-Length: {len(resp_data)}\r\n"
                    f"Cache-Control: no-store\r\n"
                    f"Connection: close\r\n\r\n"
                ).encode("ascii")
                writer.write(resp_headers + resp_data)
                await writer.drain()
                return

            if method == "GET":
                rel = raw_path.lstrip("/") or "index.html"
                candidate = (web_root / rel).resolve()
                if not candidate.is_relative_to(web_root) or not candidate.is_file():
                    candidate = web_root / "index.html"
                if candidate.is_file():
                    data = candidate.read_bytes()
                    ctype, _ = mimetypes.guess_type(str(candidate))
                    ctype = ctype or "application/octet-stream"
                    if ctype.startswith(("text/", "application/javascript", "application/json")):
                        ctype += "; charset=utf-8"
                    resp_headers = (
                        f"HTTP/1.1 200 OK\r\n"
                        f"Content-Type: {ctype}\r\n"
                        f"Content-Length: {len(data)}\r\n"
                        f"Cache-Control: no-cache\r\n"
                        f"Connection: close\r\n\r\n"
                    ).encode("ascii")
                    writer.write(resp_headers + data)
                    await writer.drain()
                    return

            writer.write(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    server = None
    chosen_port = port
    for candidate_port in range(port, port + 20):
        try:
            server = await asyncio.start_server(handle_client, host, candidate_port)
            chosen_port = candidate_port
            break
        except OSError:
            continue
    if server is None:
        raise OrchError(f"Could not bind local Studio server on {host}:{port}..{port+19}")
    url = f"http://{host}:{chosen_port}"
    print(f"Agent Helm Studio running at: {url}", flush=True)
    if open_browser:
        try:
            if os.name == "nt":
                os.startfile(url)
            else:
                webbrowser.open(url)
        except Exception:
            pass
    try:
        async with server:
            await server.serve_forever()
    finally:
        await controller.service.shutdown()
    return 0
