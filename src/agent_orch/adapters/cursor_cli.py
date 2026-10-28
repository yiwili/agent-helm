"""Opt-in Cursor worker using its official Node package and a dedicated config.

Ask mode alone is not isolation. Deny tools in a private CLI configuration and
reject all tool-call events before accepting any returned candidate content.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .base import ProviderBlocked
from .codex_cli import CodexAdapter, blocked_error, unique_object
from ..models import OrchError

PINNED_VERSION = "2026.10.22-27dcf5f"
DOCS = "https://cursor.com/docs/cli/reference/parameters"
DENY = ["Shell(*)", "Read(*)", "Read(**)", "Write(*)", "Write(**)", "WebFetch(*)", "Mcp(*:*)"]
CONFIG = {"version": 1, "editor": {"vimMode": False}, "permissions": {"allow": [], "deny": DENY},
          "approvalMode": "allowlist", "sandbox": {"mode": "enabled", "readBoundary": "workspace"}}


def prepare_config(directory):
    """Explicit local setup only; never overwrite a user's existing config."""
    root = Path(directory).resolve()
    if root.is_relative_to(Path(__file__).resolve().parents[3]):
        raise OrchError("Cursor private config must stay outside this repository")
    root.mkdir(parents=True, exist_ok=False)
    (root/"cli-config.json").write_text(json.dumps(CONFIG, indent=2), encoding="utf-8")
    return root


def check_config(directory, workspace):
    if directory is None:
        raise ProviderBlocked("Cursor requires a dedicated config directory; no default-user config fallback")
    if Path(directory).is_symlink():
        raise OrchError("Cursor config cannot be a symlink")
    root = Path(directory).resolve()
    workspace = Path(workspace).resolve()
    repository = Path(__file__).resolve().parents[3]
    if root.is_relative_to(workspace) or workspace.is_relative_to(root) or root.is_relative_to(repository):
        raise OrchError("Cursor private config must be outside source, runtime workspace and repository")
    if root.is_symlink() or not root.is_dir() or any(p.is_symlink() for p in root.rglob("*")):
        raise OrchError("Cursor config must be a regular private directory")
    try:
        config = json.loads((root/"cli-config.json").read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (OSError, ValueError) as e:
        raise ProviderBlocked("Initialize a dedicated Cursor config before login") from e
    if not isinstance(config, dict) or config.get("permissions") != CONFIG["permissions"] or config.get("approvalMode") != "allowlist" or config.get("sandbox") != CONFIG["sandbox"]:
        raise ProviderBlocked("Cursor tool-denial config changed; explicit reconciliation required")
    # No imported user/project MCP servers, hooks, rules, plugins or custom endpoints.
    if set(config) - set(CONFIG) or any((root/name).exists() for name in ("mcp.json", "hooks.json", "plugins", "rules", "skills")):
        raise ProviderBlocked("Cursor custom settings/extensions are not supported by this bounded worker")
    return root


def parse_stream(text, exit_code):
    if len(text.encode("utf-8")) > 2_000_000:
        raise OrchError("Cursor output limit exceeded")
    initialized = terminal = None
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line, object_pairs_hook=unique_object)
        except (ValueError, TypeError) as e:
            raise OrchError("Malformed Cursor JSONL") from e
        if not isinstance(event, dict) or terminal is not None:
            raise OrchError("Invalid Cursor event or event after terminal")
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init" and initialized is None:
            if event.get("apiKeySource") != "login":
                raise ProviderBlocked("Cursor official login required; API keys and fallback are disabled")
            if not isinstance(event.get("session_id"), str) or not event["session_id"] or not isinstance(event.get("model"), str) or not event["model"]:
                raise OrchError("Cursor initialization lacks session/model identity")
            initialized = event
        elif kind in ("user", "assistant", "result") and initialized is not None:
            if event.get("session_id") != initialized["session_id"]:
                raise OrchError("Cursor session mismatch")
            if kind == "result":
                terminal = event
        elif kind == "tool_call":
            raise ProviderBlocked("Cursor attempted a tool call; candidate not accepted")
        elif kind == "error" and blocked_error(json.dumps(event)):
            raise ProviderBlocked("Cursor quota/permission/authentication error; no fallback")
        else:
            raise OrchError("Unexpected Cursor protocol event")
    if exit_code != 0 or terminal is None or terminal.get("subtype") != "success" or terminal.get("is_error") is not False:
        raise OrchError("Cursor lacks a successful terminal response")
    if terminal.get("permission_denials", []) != []:
        raise ProviderBlocked("Cursor permission denial")
    try:
        result = json.loads(terminal["result"], object_pairs_hook=unique_object)
    except (KeyError, ValueError, TypeError) as e:
        raise OrchError("Cursor final response must be exact JSON without Markdown fences") from e
    if not isinstance(result, dict):
        raise OrchError("Cursor structured response must be an object")
    return result, {"provider": "cursor", "version": PINNED_VERSION, "session_id": initialized["session_id"],
                    "model": initialized["model"], "terminal": "success", "tool_calls": 0,
                    "billing_source": "cursor_official_login", "billing_verified": False,
                    "usage": {"tokens": None, "amount_usd": None, "source": "unavailable"}}


class CursorAdapter(CodexAdapter):
    def __init__(self, *, enabled=False, package=None, config_dir=None, **kwargs):
        super().__init__(enabled=enabled, executable="cursor-package-unavailable", **kwargs)
        self.package = Path(package or os.environ["AGENT_ORCH_CURSOR_DIR"]).resolve() if package or os.environ.get("AGENT_ORCH_CURSOR_DIR") else None
        self.config_dir = config_dir or os.environ.get("AGENT_ORCH_CURSOR_CONFIG")

    def command(self):
        if self.package:
            node = self.package/("node.exe" if os.name == "nt" else "node")
            entry = self.package/"index.js"
            if node.is_file() and entry.is_file() and not node.is_symlink() and not entry.is_symlink():
                return [str(node), str(entry)]
        raise ProviderBlocked("Cursor official CLI package directory with node and index.js required")

    async def probe(self, cwd, cancelled=None):
        report = {"provider": "cursor", "documented": True, "installed": False, "authenticated": False,
                  "compatible": False, "smoke_passed": False, "billing_verified": False, "production_ready": False,
                  "billing_source": "unknown", "version": None, "docs": DOCS}
        try:
            command = self.command()
        except ProviderBlocked:
            return report
        cancelled = cancelled or self.cancelled
        version = await self._run([*command, "--version"], cwd, "metadata.version", timeout=30, cancelled=cancelled)
        report.update(installed=version.exit_code == 0, version=version.stdout.strip())
        report["compatible"] = report["version"] == PINNED_VERSION
        config = check_config(self.config_dir, cwd)
        status = await self._run([*command, "status", "--format", "json"], cwd, "metadata.auth", timeout=30,
                                 cancelled=cancelled, auth=True, env_overrides={"CURSOR_CONFIG_DIR": str(config)})
        try:
            body = json.loads(status.stdout, object_pairs_hook=unique_object)
        except (ValueError, TypeError) as e:
            raise OrchError("Invalid Cursor authentication status") from e
        report["authenticated"] = status.exit_code == 0 and isinstance(body, dict) and body.get("isAuthenticated") is True and body.get("status") == "authenticated"
        if report["authenticated"]:
            report["billing_source"] = "cursor_official_login"
        return report

    async def call(self, prompt, schema, workspace, timeout=120, cancelled=lambda: False):
        if not self.enabled:
            raise ProviderBlocked("Cursor disabled; enable explicitly for synthetic fixtures")
        self.last_metadata = None
        if (Path(workspace)/".cursor").exists():
            raise ProviderBlocked("Cursor worker workspace must not contain project extensions/configuration")
        report = await self.probe(workspace, cancelled)
        if not report.get("compatible") or not report["authenticated"]:
            raise ProviderBlocked("Pinned Cursor CLI with dedicated official login required")
        config = check_config(self.config_dir, workspace)
        message = prompt + "\nUse no tools. Return only an exact JSON object, without Markdown fences. Schema:\n" + json.dumps(schema)
        result = await self._run([*self.command(), "--mode", "ask", "--sandbox", "enabled", "--trust", "--print", "--output-format", "stream-json"],
                                 workspace, "exec", stdin=message, timeout=timeout, cancelled=cancelled, auth=True,
                                 env_overrides={"CURSOR_CONFIG_DIR": str(config)})
        if result.exit_code != 0 and blocked_error(result.stderr):
            raise ProviderBlocked("Cursor quota/permission/authentication error; no API fallback")
        value, self.last_metadata = parse_stream(result.stdout, result.exit_code)
        check_config(self.config_dir, workspace)
        return value
