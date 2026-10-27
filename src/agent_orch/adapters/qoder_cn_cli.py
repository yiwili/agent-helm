"""Official Qoder CN CLI, pinned protocol, tools disabled. No token extraction."""
from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path

from .base import ProviderBlocked
from .codex_cli import CodexAdapter, unique_object, blocked_error, redact
from ..models import OrchError

PINNED_VERSION = "1.1.80"
DOCS = "https://docs.qoder.cn/cli/cli-reference"


def discover():
    if os.environ.get("AGENT_ORCH_QODER_CN"):
        return os.environ["AGENT_ORCH_QODER_CN"]
    for name in ("qoderclicn", "qodercn"):
        path = shutil.which(name)
        if path and not path.lower().endswith((".cmd", ".bat", ".ps1")):
            return path
    path = Path.home()/".qoder-cn"/"bin"/"qoderclicn"/"qoderclicn.exe"
    return str(path) if path.is_file() else None


def parse_result(text, exit_code):
    if len(text.encode("utf-8")) > 2_000_000:
        raise OrchError("Qoder output limit exceeded")
    try:
        event = json.loads(text, object_pairs_hook=unique_object)
    except (ValueError, TypeError) as e:
        raise OrchError("Malformed Qoder terminal JSON") from e
    if not isinstance(event, dict):
        raise OrchError("Qoder terminal must be an object")
    if exit_code != 0 or event.get("is_error") is not False or event.get("subtype") != "success":
        if blocked_error(json.dumps({k: event.get(k) for k in ("result", "error", "errors", "message")})):
            raise ProviderBlocked("Qoder quota/permission/authentication error; no API fallback")
        raise OrchError("Qoder reported failure or lacks a successful terminal")
    if event.get("type") != "result" or event.get("stop_reason") != "end_turn" or type(event.get("num_turns")) is not int or event["num_turns"] != 1:
        raise OrchError("Qoder turn did not finish as a single bounded response")
    if event.get("permission_denials") != []:
        raise ProviderBlocked("Qoder reported permission denials")
    if not isinstance(event.get("session_id"), str) or not event["session_id"] or not isinstance(event.get("result"), str):
        raise OrchError("Qoder session or final message missing")
    try:
        result = json.loads(event["result"], object_pairs_hook=unique_object)
    except (ValueError, TypeError) as e:
        raise OrchError("Qoder final message must be exact JSON, without Markdown fences") from e
    if not isinstance(result, dict):
        raise OrchError("Qoder structured response must be an object")
    credits = event.get("total_credits")
    if credits is not None and (type(credits) not in (int, float) or not math.isfinite(credits) or credits < 0):
        raise OrchError("Invalid provider credit usage")
    return result, {"provider": "qoder-cn", "version": PINNED_VERSION, "session_id": event["session_id"],
                    "terminal": "success", "stop_reason": "end_turn", "tools_enabled": False,
                    "usage": {"credits": credits, "tokens": None, "amount_usd": None, "source": "provider_terminal"},
                    "billing_source": "qoder_cn_official_login", "billing_verified": False,
                    "limitations": ["Credits are provider-reported; zero token/currency fields do not prove zero consumption"]}


class QoderCNAdapter(CodexAdapter):
    def __init__(self, *, enabled=False, executable=None, **kwargs):
        path = executable or discover()
        super().__init__(enabled=enabled, executable=path or "qoder-cn-unavailable", **kwargs)
        self.executable = path

    async def probe(self, cwd, cancelled=None):
        cancelled = cancelled or self.cancelled
        report = {"provider": "qoder-cn", "documented": True, "installed": False, "authenticated": False,
                  "smoke_passed": False, "billing_verified": False, "production_ready": False,
                  "billing_source": "unknown", "docs": DOCS, "binary": self.executable, "version": None}
        if not self.executable:
            return report
        result = await self._run([self.executable, "--version"], cwd, "metadata.version", timeout=20, cancelled=cancelled)
        report.update(installed=result.exit_code == 0, version=result.stdout.strip())
        report["compatible"] = report["version"] == PINNED_VERSION
        status = await self._run([self.executable, "status", "-o", "json"], cwd, "metadata.auth", timeout=20, cancelled=cancelled, auth=True)
        try:
            body = json.loads(status.stdout, object_pairs_hook=unique_object)
        except (ValueError, TypeError) as e:
            raise OrchError("Invalid Qoder authentication status") from e
        report["authenticated"] = status.exit_code == 0 and isinstance(body, dict) and body.get("logged_in") is True
        if report["authenticated"]:
            report["billing_source"] = "qoder_cn_official_login"
        return report

    async def call(self, prompt, schema, workspace, timeout=120, cancelled=lambda: False):
        if not self.enabled:
            raise ProviderBlocked("Qoder CN disabled; enable explicitly for synthetic fixtures")
        self.last_metadata = None
        report = await self.probe(workspace, cancelled=cancelled)
        if not report.get("compatible") or not report["authenticated"]:
            raise ProviderBlocked("Pinned Qoder CN CLI with official login is required")
        argv = [self.executable, "--tools", "", "--setting-sources", "", "--strict-mcp-config",
                "--mcp-config", '{"mcpServers":{}}', "--permission-mode", "dont_ask",
                "--no-session-persistence", "--max-model-request-retries", "0",
                "--max-output-tokens", "8192", "-p", "-o", "json"]
        message = prompt + "\nReturn only a JSON object, no Markdown fences. Match this JSON Schema:\n" + json.dumps(schema)
        process = await self._run(argv, workspace, "exec", stdin=message, timeout=timeout, cancelled=cancelled, auth=True)
        if process.exit_code != 0 and (blocked_error(process.stderr) or (not process.stdout.lstrip().startswith(("{", "[")) and blocked_error(process.stdout))):
            raise ProviderBlocked("Qoder quota/permission/authentication error; no API fallback")
        if process.exit_code != 0 and not process.stdout.strip():
            raise OrchError("Qoder CLI failed: "+redact(process.stderr))
        result, self.last_metadata = parse_result(process.stdout, process.exit_code)
        return result
