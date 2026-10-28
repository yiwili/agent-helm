"""Explicitly budgeted Claude CLI worker. Existing CLI config owns auth/model."""
from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path

from .base import ProviderBlocked
from .codex_cli import CodexAdapter, unique_object, blocked_error
from ..budget import Budget
from ..models import OrchError

PINNED_VERSION = "2.1.311 (Claude Code)"
DOCS = "https://code.claude.com/docs/en/headless"


def discover():
    if os.environ.get("AGENT_ORCH_CLAUDE"):
        return os.environ["AGENT_ORCH_CLAUDE"]
    executable = shutil.which("claude")
    if executable and not executable.lower().endswith((".cmd", ".bat", ".ps1")):
        return executable
    native = Path.home()/".local/bin/claude.exe"
    return str(native) if native.is_file() else None


def parse_terminal(text, exit_code):
    if len(text.encode("utf-8")) > 2_000_000:
        raise OrchError("Claude output limit exceeded")
    try:
        event = json.loads(text, object_pairs_hook=unique_object)
    except (ValueError, TypeError) as e:
        raise OrchError("Malformed Claude terminal JSON") from e
    if not isinstance(event, dict):
        raise OrchError("Claude terminal must be an object")
    if exit_code != 0 or event.get("type") != "result" or event.get("subtype") != "success" or event.get("is_error") is not False:
        if blocked_error(json.dumps({k: event.get(k) for k in ("result", "error", "errors")})):
            raise ProviderBlocked("Claude quota/permission/authentication error; no fallback")
        raise OrchError("Claude did not finish successfully")
    if event.get("terminal_reason") != "completed" or event.get("stop_reason") not in (None, "end_turn", "tool_use"):
        raise OrchError("Claude turn incomplete")
    if type(event.get("num_turns")) is not int or not 1 <= event["num_turns"] <= 3:
        raise OrchError("Claude exceeded bounded turn contract")
    if event.get("permission_denials") != []:
        raise ProviderBlocked("Claude reported permission denials")
    if not isinstance(event.get("session_id"), str) or not event["session_id"]:
        raise OrchError("Claude session identity missing")
    value = event.get("structured_output")
    if not isinstance(value, dict):
        raise OrchError("Native structured_output required; no text/fence extraction fallback")
    cost = event.get("total_cost_usd")
    if cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0):
        raise OrchError("Invalid Claude estimated cost")
    models = event.get("modelUsage", {})
    if not isinstance(models, dict) or any(not isinstance(k, str) or not k or len(k) > 200 for k in models):
        raise OrchError("Invalid Claude model usage metadata")
    return value, {"provider": "claude", "version": PINNED_VERSION, "session_id": event["session_id"],
                   "terminal": "success", "structured_output": True, "models": sorted(models),
                   "billing_source": "existing_user_config_unverified", "billing_verified": False,
                   "usage": {"amount_usd": cost, "source": "cli_estimate", "tokens": None}}


class ClaudeAdapter(CodexAdapter):
    def __init__(self, *, enabled=False, executable=None, budget_ledger=None, **kwargs):
        path = executable or discover()
        super().__init__(enabled=enabled, executable=path or "claude-unavailable", **kwargs)
        self.executable = path
        self.budget_ledger = budget_ledger

    async def probe(self, cwd, cancelled=None):
        report = {"provider": "claude", "documented": True, "installed": False, "authenticated": False,
                  "compatible": False, "smoke_passed": False, "billing_verified": False, "production_ready": False,
                  "billing_source": "existing_user_config_unverified", "version": None, "docs": DOCS}
        if not self.executable:
            return report
        cancelled = cancelled or self.cancelled
        version = await self._run([self.executable, "--version"], cwd, "metadata.version", timeout=20, cancelled=cancelled)
        report.update(installed=version.exit_code == 0, version=version.stdout.strip())
        report["compatible"] = report["version"] == PINNED_VERSION
        status = await self._run([self.executable, "auth", "status", "--json"], cwd, "metadata.auth", timeout=20, auth=True, cancelled=cancelled)
        try:
            body = json.loads(status.stdout, object_pairs_hook=unique_object)
        except (ValueError, TypeError) as e:
            raise OrchError("Invalid Claude authentication status") from e
        report["authenticated"] = status.exit_code == 0 and isinstance(body, dict) and body.get("loggedIn") is True
        # CLI authMethod does not prove the effective upstream, model or billing.
        return report

    async def call(self, prompt, schema, workspace, timeout=120, cancelled=lambda: False):
        if not self.enabled:
            raise ProviderBlocked("Claude worker disabled; explicit enable and budget required")
        self.last_metadata = None
        budget = Budget(self.budget_ledger, workspace)
        try:
            report = await self.probe(workspace, cancelled)
            if not report.get("compatible") or not report["authenticated"]:
                raise ProviderBlocked("Pinned Claude CLI and existing authenticated configuration required")
            if cancelled():
                raise ProviderBlocked("Claude cancelled before reservation")
            token = budget.reserve("explicit Claude CLI worker structured call")
            cost = None
            state = "unknown"
            try:
                argv = [self.executable, "--safe-mode", "--tools", "", "--setting-sources", "user",
                        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--no-session-persistence",
                        "--disable-slash-commands", "--permission-mode", "dontAsk", "--permission-prompts", "none",
                        "--max-turns", "3", "--max-budget-usd", f"{budget.per_call:.2f}",
                        "--json-schema", json.dumps(schema), "-p", "--output-format", "json"]
                process = await self._run(argv, workspace, "exec", stdin=prompt, timeout=timeout, cancelled=cancelled, auth=True)
                state = "contract_rejected"
                try:
                    raw = json.loads(process.stdout, object_pairs_hook=unique_object)
                    cost = raw.get("total_cost_usd") if isinstance(raw, dict) else None
                except (ValueError, TypeError):
                    pass
                value, self.last_metadata = parse_terminal(process.stdout, process.exit_code)
                state = "completed"
                self.last_metadata["budget_reservation_id"] = token
                return value
            finally:
                budget.finish(token, state=state, cost=cost)
        finally:
            budget.close()
