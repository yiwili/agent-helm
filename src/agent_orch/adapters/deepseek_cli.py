"""DeepSeek Harness CLI worker adapter, disabled by default. No token extraction."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .base import ProviderBlocked
from .codex_cli import CodexAdapter, unique_object, blocked_error, redact
from .qoder_cn_cli import normalize_context_window, resolve_executable_path
from ..models import OrchError

DOCS = "https://api-docs.deepseek.com/"


def discover():
    env_bin = resolve_executable_path(os.environ.get("AGENT_ORCH_DEEPSEEK"))
    if env_bin:
        return env_bin
    for name in ("deepseek-harness", "deepseek-cli", "deepseek"):
        path = shutil.which(name)
        if path and not path.lower().endswith((".cmd", ".bat", ".ps1")):
            return path
    home = Path.home()
    candidates = [
        home / ".deepseek" / "bin" / "deepseek-harness.exe",
        home / ".deepseek" / "bin" / "deepseek.exe",
        home / ".local" / "bin" / "deepseek-harness.exe",
        home / ".local" / "bin" / "deepseek.exe",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def parse_result(text, exit_code):
    if len(text.encode("utf-8")) > 2_000_000:
        raise OrchError("DeepSeek Harness output limit exceeded")
    if exit_code != 0:
        if blocked_error(text):
            raise ProviderBlocked("DeepSeek Harness quota/permission/authentication error; no API fallback")
        raise OrchError("DeepSeek Harness exited with non-zero status")
    raw = text.strip()
    try:
        parsed = json.loads(raw, object_pairs_hook=unique_object)
    except (ValueError, TypeError) as e:
        raise OrchError("Malformed DeepSeek Harness JSON output") from e
    if not isinstance(parsed, dict):
        raise OrchError("DeepSeek Harness output must be a JSON object")
    if isinstance(parsed.get("result"), str):
        try:
            inner = json.loads(parsed["result"], object_pairs_hook=unique_object)
        except (ValueError, TypeError) as e:
            raise OrchError("Malformed DeepSeek Harness inner result JSON") from e
        if not isinstance(inner, dict):
            raise OrchError("DeepSeek Harness inner result must be an object")
        result = inner
        session_id = parsed.get("session_id", "deepseek-session")
    elif isinstance(parsed.get("structured_output"), dict):
        result = parsed["structured_output"]
        session_id = parsed.get("session_id", "deepseek-session")
    else:
        result = parsed
        session_id = "deepseek-direct"
    from ..wire import expand_wire_object
    result = expand_wire_object(result)
    return result, {
        "provider": "deepseek",
        "session_id": session_id,
        "terminal": "success",
        "tools_enabled": False,
        "usage": {"credits": None, "tokens": None, "amount_usd": None, "source": "provider_terminal"},
        "billing_source": "deepseek_local_cli",
        "billing_verified": False,
    }


class DeepSeekAdapter(CodexAdapter):
    def __init__(self, *, enabled=False, executable=None, reasoning_effort=None, thinking=None, context_window=None, **kwargs):
        path = resolve_executable_path(executable) or discover()
        super().__init__(enabled=enabled, executable=path or "deepseek-unavailable", **kwargs)
        self.executable = path
        self.reasoning_effort = reasoning_effort.strip().lower() if isinstance(reasoning_effort, str) and reasoning_effort.strip() else None
        self.thinking = thinking.strip().lower() if isinstance(thinking, str) and thinking.strip() else None
        self.context_window_raw = str(context_window).strip() if context_window else None
        self.context_window = normalize_context_window(context_window) if context_window else None

    async def probe(self, cwd, cancelled=None):
        cancelled = cancelled or self.cancelled
        report = {
            "provider": "deepseek",
            "documented": True,
            "installed": False,
            "authenticated": False,
            "smoke_passed": False,
            "billing_verified": False,
            "production_ready": False,
            "billing_source": "unknown",
            "docs": DOCS,
            "binary": self.executable,
            "version": None,
        }
        if not self.executable or not Path(self.executable).is_file():
            return report
        result = await self._run([self.executable, "--version"], cwd, "metadata.version", timeout=20, cancelled=cancelled)
        version = (result.stdout or result.stderr).strip()
        report.update(installed=result.exit_code == 0, version=version or None, compatible=result.exit_code == 0)
        status = await self._run([self.executable, "status", "--json"], cwd, "metadata.auth", timeout=20, cancelled=cancelled, auth=True)
        if status.exit_code == 0:
            report["authenticated"] = True
            report["billing_source"] = "deepseek_local_cli"
        return report

    async def call(self, prompt, schema, workspace, timeout=120, cancelled=lambda: False):
        if not self.enabled:
            raise ProviderBlocked("DeepSeek Harness disabled; enable explicitly for synthetic fixtures")
        if not self.executable or not Path(self.executable).is_file():
            raise ProviderBlocked("DeepSeek Harness CLI binary not found")
        self.last_metadata = None
        argv = [self.executable, "exec", "--output-format", "json", "--no-tools"]
        if self.model:
            argv += ["--model", self.model]
        if self.context_window:
            argv += ["--context-window", self.context_window]
        if self.reasoning_effort:
            argv += ["--reasoning-effort", self.reasoning_effort]
        if self.thinking:
            argv += ["--thinking", self.thinking]
        from ..schemas import RESULT_SCHEMA, REVIEW_SCHEMA
        from ..wire import COMPACT_RESULT_SCHEMA, COMPACT_REVIEW_SCHEMA
        wire_schema = COMPACT_RESULT_SCHEMA if schema == RESULT_SCHEMA else (COMPACT_REVIEW_SCHEMA if schema == REVIEW_SCHEMA else schema)
        message = prompt + "\n[AHCP-1 Wire Protocol: Use compact English keys to save output tokens] Return only a JSON object, no Markdown fences. Match this JSON Schema:\n" + json.dumps(wire_schema, separators=(",", ":"))
        process = await self._run(argv, workspace, "exec", stdin=message, timeout=timeout, cancelled=cancelled, auth=True)
        if process.exit_code != 0 and blocked_error(process.stderr + "\n" + process.stdout):
            raise ProviderBlocked("DeepSeek Harness quota/permission/authentication error; no API fallback")
        if process.exit_code != 0 and not process.stdout.strip():
            raise OrchError("DeepSeek Harness CLI failed: " + redact(process.stderr))
        result, self.last_metadata = parse_result(process.stdout, process.exit_code)
        self.last_metadata["requested_model"] = self.model
        self.last_metadata["reasoning_effort"] = self.reasoning_effort
        self.last_metadata["thinking"] = self.thinking
        self.last_metadata["context_window"] = self.context_window_raw or self.context_window
        return result
