"""Official Qoder CN CLI, pinned protocol, tools disabled. No token extraction."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
from pathlib import Path

from .base import ProviderBlocked
from .codex_cli import CodexAdapter, unique_object, blocked_error, redact
from ..models import OrchError

PINNED_VERSION = "1.1.80"
DOCS = "https://docs.qoder.cn/cli/cli-reference"
REASONING_EFFORTS = ("low", "high", "max")
THINKING_MODES = ("auto", "adaptive", "enabled", "disabled")


def normalize_context_window(value):
    if value is None or value == "":
        return None
    if isinstance(value, int) and value > 0:
        return str(value)
    text = str(value).strip().lower().replace("_", "").replace(",", "")
    if not text:
        return None
    if text.isdigit() and int(text) > 0:
        return str(int(text))
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(k|m)", text)
    if match:
        amount = float(match.group(1))
        factor = 1_000_000 if match.group(2) == "m" else 1_000
        tokens = int(round(amount * factor))
        if tokens > 0:
            return str(tokens)
    raise OrchError("Invalid context_window; use positive integer or shorthand like 1m / 200k")


def resolve_executable_path(candidate):
    if not candidate:
        return None
    cleaned = str(candidate).strip().strip('"').strip("'")
    if not cleaned:
        return None
    p = Path(cleaned)
    if p.is_dir():
        for rel in (
            Path("qoderclicn.exe"),
            Path("qodercn.exe"),
            Path("bin") / "qoderclicn" / "qoderclicn.exe",
            Path("bin") / "qoderclicn.exe",
            Path("qoder-cli") / "qoderclicn.exe",
        ):
            sub = p / rel
            if sub.is_file():
                return str(sub)
        return cleaned
    return cleaned


def discover():
    env_bin = resolve_executable_path(os.environ.get("AGENT_ORCH_QODER_CN"))
    if env_bin:
        return env_bin
    for name in ("qoderclicn", "qodercn", "qodercli"):
        path = shutil.which(name)
        if path and not path.lower().endswith((".cmd", ".bat", ".ps1")):
            return path
    home = Path.home()
    local_app = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
    candidates = [
        home / ".qoder-cn" / "bin" / "qoderclicn" / "qoderclicn.exe",
        home / ".qoder-cn" / "bin" / "qoderclicn.exe",
        home / ".qoder-cn" / "qoderclicn.exe",
        home / ".local" / "bin" / "qoderclicn.exe",
        local_app / "Programs" / "Qoder CN" / "resources" / "app" / "bin" / "qoderclicn.exe",
        local_app / "Programs" / "Qoder CN" / "qoderclicn.exe",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


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
    def __init__(self, *, enabled=False, executable=None, reasoning_effort=None, thinking=None, context_window=None, **kwargs):
        path = resolve_executable_path(executable) or discover()
        super().__init__(enabled=enabled, executable=path or "qoder-cn-unavailable", **kwargs)
        self.executable = path
        self.reasoning_effort = reasoning_effort.strip().lower() if isinstance(reasoning_effort, str) and reasoning_effort.strip() else None
        if self.reasoning_effort and self.reasoning_effort not in REASONING_EFFORTS:
            raise OrchError(f"Unsupported reasoning_effort: {self.reasoning_effort}")
        self.thinking = thinking.strip().lower() if isinstance(thinking, str) and thinking.strip() else None
        if self.thinking and self.thinking not in THINKING_MODES:
            raise OrchError(f"Unsupported thinking mode: {self.thinking}")
        self.context_window_raw = str(context_window).strip() if context_window else None
        self.context_window = normalize_context_window(context_window)

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
        if self.model:
            argv += ["--model", self.model]
        if self.context_window:
            argv += ["--context-window", self.context_window]
        if self.reasoning_effort:
            argv += ["--reasoning-effort", self.reasoning_effort]
        if self.thinking:
            argv += ["--thinking", self.thinking]
        message = prompt + "\nReturn only a JSON object, no Markdown fences. Match this JSON Schema:\n" + json.dumps(schema)
        process = await self._run(argv, workspace, "exec", stdin=message, timeout=timeout, cancelled=cancelled, auth=True)
        if process.exit_code != 0 and (blocked_error(process.stderr) or (not process.stdout.lstrip().startswith(("{", "[")) and blocked_error(process.stdout))):
            raise ProviderBlocked("Qoder quota/permission/authentication error; no API fallback")
        if process.exit_code != 0 and not process.stdout.strip():
            raise OrchError("Qoder CLI failed: "+redact(process.stderr))
        result, self.last_metadata = parse_result(process.stdout, process.exit_code)
        self.last_metadata["requested_model"] = self.model
        self.last_metadata["reasoning_effort"] = self.reasoning_effort
        self.last_metadata["thinking"] = self.thinking
        self.last_metadata["context_window"] = self.context_window_raw or self.context_window
        return result
