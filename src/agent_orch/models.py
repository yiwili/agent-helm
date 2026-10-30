from __future__ import annotations

import hashlib
import json
import re
import base64
import binascii
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath


class OrchError(ValueError):
    pass


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def strict(obj, required, optional=()):
    if not isinstance(obj, dict) or set(obj) - set(required) - set(optional) or set(required) - set(obj):
        raise OrchError(f"Invalid object fields; required={sorted(required)}")


def relpath(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise OrchError("Expected portable relative path")
    p = PurePosixPath(value)
    if p.is_absolute() or PureWindowsPath(value).drive or any(x in ("", ".", "..") for x in value.split("/")):
        raise OrchError("Path escapes workspace")
    for part in p.parts:
        if part.rstrip(" .") != part or part.lower() in (".git", ".codex", ".env"):
            raise OrchError("Reserved path")
        if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", part):
            raise OrchError("Windows device path")
    return value


PROVIDERS = ("mock", "manual", "codex", "qoder-cn", "cursor", "claude", "deepseek", "antigravity")
WORKER_RESULT_PROTOCOL = "agent-helm.worker-result.v2"
WORKER_RESULT_STATUSES = ("completed", "partial", "blocked")
_RESULT_OPTIONAL_KEYS = (
    "schema_version",
    "protocol",
    "status",
    "provider",
    "attempt",
    "diagnostics",
    "thinking",
    "metrics",
    "artifacts",
    "extensions",
)


def validate_plan(plan):
    strict(plan, ("schema_version", "goal", "tasks", "max_rework_rounds", "api_budget"))
    if type(plan["schema_version"]) is not int or plan["schema_version"] != 1 or not isinstance(plan["goal"], str) or not plan["goal"].strip():
        raise OrchError("Unsupported schema or empty goal")
    if type(plan["max_rework_rounds"]) is not int or not 0 <= plan["max_rework_rounds"] <= 2:
        raise OrchError("Rework limit must be 0..2")
    if type(plan["api_budget"]) not in (int, float) or plan["api_budget"] != 0:
        raise OrchError("API billing paths are disabled in this release")
    if not isinstance(plan["tasks"], list) or not 1 <= len(plan["tasks"]) <= 100:
        raise OrchError("Plan requires 1..100 tasks")
    ids = set()
    for t in plan["tasks"]:
        strict(t, ("id", "goal", "provider", "dependencies", "allowed_paths", "acceptance", "verifier_profile", "wall_seconds"), ("mock_behavior", "milestone_review"))
        if "milestone_review" in t and type(t["milestone_review"]) is not bool:
            raise OrchError("milestone_review must be boolean")
        if not isinstance(t["id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", t["id"]) or t["id"] in ids:
            raise OrchError("Duplicate or invalid task ID")
        ids.add(t["id"])
        if t["provider"] not in PROVIDERS:
            raise OrchError("Unknown or unimplemented provider")
        if not isinstance(t["goal"], str) or not t["goal"].strip():
            raise OrchError("Task goal required")
        for key in ("dependencies", "allowed_paths", "acceptance"):
            if not isinstance(t[key], list) or any(not isinstance(x, str) for x in t[key]):
                raise OrchError(f"{key} must be string array")
        if not t["allowed_paths"] or not t["acceptance"] or any(not x.strip() for x in t["acceptance"]) or len(set(t["dependencies"])) != len(t["dependencies"]):
            raise OrchError("Empty scope/acceptance or repeated dependency")
        for p in t["allowed_paths"]:
            relpath(p)
        if len({p.casefold() for p in t["allowed_paths"]}) != len(t["allowed_paths"]):
            raise OrchError("Case-colliding paths")
        from .verifier_registry import profile_id
        profile_id(t["verifier_profile"])
        if type(t["wall_seconds"]) not in (int, float) or not 0.05 <= t["wall_seconds"] <= 600:
            raise OrchError("Task timeout outside 0.05..600 seconds")
        if t.get("mock_behavior", "good") not in ("good", "bad-first", "bad", "timeout", "quota", "permission"):
            raise OrchError("Unknown mock behavior")
    pending = {t["id"]: set(t["dependencies"]) for t in plan["tasks"]}
    if any(not deps <= ids for deps in pending.values()):
        raise OrchError("Unknown dependency")
    visited = set()
    while pending:
        ready = [i for i, deps in pending.items() if deps <= visited]
        if not ready:
            raise OrchError("DAG contains cycle")
        for i in ready:
            visited.add(i)
            del pending[i]
    return plan


def _validate_result_extensions(obj):
    if "protocol" in obj and (not isinstance(obj["protocol"], str) or not obj["protocol"].strip()):
        raise OrchError("Invalid worker result protocol")
    if "status" in obj and obj["status"] not in WORKER_RESULT_STATUSES:
        raise OrchError("Invalid worker result status")
    if "provider" in obj and (not isinstance(obj["provider"], str) or obj["provider"] not in PROVIDERS):
        raise OrchError("Invalid worker result provider")
    if "attempt" in obj and (type(obj["attempt"]) is not int or obj["attempt"] < 1):
        raise OrchError("Invalid worker result attempt")
    for dict_key in ("diagnostics", "thinking", "metrics", "extensions"):
        if dict_key in obj and not isinstance(obj[dict_key], dict):
            raise OrchError(f"Invalid worker result {dict_key}")
    if "artifacts" in obj and not isinstance(obj["artifacts"], list):
        raise OrchError("Invalid worker result artifacts")


def validate_result(obj, allowed):
    if isinstance(obj, dict) and "operations" in obj:
        strict(obj, ("schema_version", "summary", "operations"), ("files", *_RESULT_OPTIONAL_KEYS))
        if type(obj["schema_version"]) is not int or obj["schema_version"] != 2 or not isinstance(obj["summary"], str) or not isinstance(obj["operations"], list) or len(obj["operations"]) > 50:
            raise OrchError("Invalid operations result")
        _validate_result_extensions(obj)
        seen = set()
        for op in obj["operations"]:
            if not isinstance(op, dict):
                raise OrchError("Invalid file operation")
            action = op.get("action")
            if action == "write":
                strict(op, ("action", "path", "content"), ("encoding", "executable"))
                if not isinstance(op["content"], str) or op.get("encoding", "utf-8") not in ("utf-8", "base64"):
                    raise OrchError("Invalid file encoding")
                try:
                    data = base64.b64decode(op["content"], validate=True) if op.get("encoding") == "base64" else op["content"].encode("utf-8")
                except (ValueError, binascii.Error) as e:
                    raise OrchError("Invalid base64 candidate") from e
                if len(data) > 262144 or ("executable" in op and type(op["executable"]) is not bool):
                    raise OrchError("Candidate file size or mode invalid")
            elif action == "delete":
                strict(op, ("action", "path"))
            elif action == "rename":
                strict(op, ("action", "path", "target"))
            else:
                raise OrchError("Unsupported file operation")
            for p in [op["path"], *([op["target"]] if action == "rename" else [])]:
                relpath(p)
                if p not in allowed or p.casefold() in seen:
                    raise OrchError("Operation scope exceeded or paths overlap")
                seen.add(p.casefold())
        return obj
    strict(obj, ("summary", "files"), _RESULT_OPTIONAL_KEYS)
    if "schema_version" in obj and (type(obj["schema_version"]) is not int or obj["schema_version"] not in (1, 2)):
        raise OrchError("Invalid result schema_version")
    _validate_result_extensions(obj)
    if not isinstance(obj["summary"], str) or not isinstance(obj["files"], list):
        raise OrchError("Malformed worker result")
    seen = set()
    if len(obj["files"]) > 50:
        raise OrchError("Too many returned files")
    for f in obj["files"]:
        strict(f, ("path", "content"))
        p = relpath(f["path"])
        if p not in allowed or p.casefold() in seen or not isinstance(f["content"], str) or len(f["content"].encode()) > 262144:
            raise OrchError("Worker exceeded approved file scope")
        seen.add(p.casefold())
    return obj


def normalize_worker_result(obj, allowed, *, provider="mock", attempt=1, metadata=None, thinking=None):
    validated = validate_result(obj, allowed)
    if "operations" in validated:
        operations = [dict(op) for op in validated["operations"]]
        files = [
            {"path": op["path"], "content": op["content"]}
            for op in operations
            if op.get("action") == "write" and op.get("encoding", "utf-8") == "utf-8"
        ]
    else:
        files = [{"path": f["path"], "content": f["content"]} for f in validated["files"]]
        operations = [
            {"action": "write", "path": f["path"], "content": f["content"], "encoding": "utf-8", "executable": False}
            for f in files
        ]
    raw_diag = validated.get("diagnostics") if isinstance(validated.get("diagnostics"), dict) else {}
    diagnostics = {
        "root_cause": raw_diag.get("root_cause") if isinstance(raw_diag.get("root_cause"), str) else None,
        "checks": [str(x) for x in raw_diag.get("checks", []) if isinstance(x, (str, int, float))],
        "risks": [str(x) for x in raw_diag.get("risks", []) if isinstance(x, (str, int, float))],
        "warnings": [str(x) for x in raw_diag.get("warnings", []) if isinstance(x, (str, int, float))],
    }
    meta = dict(metadata) if isinstance(metadata, dict) else {}
    raw_metrics = validated.get("metrics") if isinstance(validated.get("metrics"), dict) else {}
    metrics = {
        "credits": meta.get("credits", raw_metrics.get("credits", 0.0)),
        "tokens": meta.get("tokens", raw_metrics.get("tokens", 0)),
        "amount_usd": meta.get("amount_usd", raw_metrics.get("amount_usd", 0.0)),
        "source": meta.get("source", raw_metrics.get("source", "none")),
    }
    raw_thinking = validated.get("thinking") if isinstance(validated.get("thinking"), dict) else {}
    cfg_thinking = dict(thinking) if isinstance(thinking, dict) else {}
    thinking_envelope = {
        "model": cfg_thinking.get("model") or meta.get("model") or raw_thinking.get("model") or None,
        "reasoning_effort": cfg_thinking.get("reasoning_effort") or meta.get("reasoning_effort") or raw_thinking.get("reasoning_effort") or None,
        "thinking_mode": cfg_thinking.get("thinking") or meta.get("thinking") or raw_thinking.get("thinking_mode") or raw_thinking.get("mode") or None,
        "context_window": cfg_thinking.get("context_window") or meta.get("context_window") or raw_thinking.get("context_window") or None,
    }
    artifacts = list(validated["artifacts"]) if isinstance(validated.get("artifacts"), list) else []
    extensions = dict(validated["extensions"]) if isinstance(validated.get("extensions"), dict) else {}
    return {
        "schema_version": 2,
        "protocol": validated.get("protocol") or WORKER_RESULT_PROTOCOL,
        "status": validated.get("status") or "completed",
        "provider": validated.get("provider") or provider,
        "attempt": int(validated.get("attempt") or attempt),
        "summary": validated["summary"],
        "files": files,
        "operations": operations,
        "diagnostics": diagnostics,
        "thinking": thinking_envelope,
        "metrics": metrics,
        "artifacts": artifacts,
        "extensions": extensions,
    }


def validate_review(obj, evidence):
    strict(obj, ("decision", "candidate_hash", "reasons", "evidence_refs"))
    if obj["decision"] not in ("accept", "rework", "escalate") or obj["candidate_hash"] != evidence["candidate_hash"]:
        raise OrchError("Invalid review or candidate mismatch")
    for field in ("reasons", "evidence_refs"):
        if not isinstance(obj[field], list) or not obj[field] or any(not isinstance(x, str) or not x for x in obj[field]):
            raise OrchError("Review requires reasons and evidence references")
    if not set(obj["evidence_refs"]) <= {"diff", "verification", "baseline", "scope"}:
        raise OrchError("Unknown evidence reference")
    if obj["decision"] == "accept" and (not evidence["verification"]["passed"] or not evidence["scope_ok"]):
        raise OrchError("Acceptance gate failed")
    return obj


def validate_diagnosis(obj, task):
    strict(obj, ("summary", "proposed_paths", "checks", "risks", "needs_scope_change"))
    if not isinstance(obj["summary"], str) or not obj["summary"].strip() or type(obj["needs_scope_change"]) is not bool:
        raise OrchError("Invalid diagnosis")
    for key in ("proposed_paths", "checks", "risks"):
        if not isinstance(obj[key], list) or any(not isinstance(x, str) for x in obj[key]):
            raise OrchError("Malformed diagnosis fields")
    if not obj["proposed_paths"] or not obj["checks"]:
        raise OrchError("Diagnosis requires scoped paths and concrete checks")
    for path in obj["proposed_paths"]:
        relpath(path)
    return obj


def validate_milestone_review(obj, bundle):
    strict(obj, ("decision", "milestone_hash", "reasons"))
    if obj["decision"] not in ("continue", "rework", "escalate") or obj["milestone_hash"] != digest(bundle):
        raise OrchError("Milestone decision or hash mismatch")
    if not isinstance(obj["reasons"], list) or not obj["reasons"] or any(not isinstance(x, str) or not x for x in obj["reasons"]):
        raise OrchError("Milestone review requires concrete reasons")
    if obj["decision"] == "continue" and (bundle["diagnosis"]["needs_scope_change"] or not set(bundle["diagnosis"]["proposed_paths"]) <= set(bundle["task"]["allowed_paths"])):
        raise OrchError("Milestone cannot broaden approved scope")
    return obj


TERMINAL = {"accepted", "failed", "cancelled", "timed_out", "escalated"}
TRANSITIONS = {
    "queued": {"running", "cancel_requested", "blocked", "failed"},
    "running": {"verifying", "blocked", "failed", "timed_out", "cancel_requested", "needs_reconciliation"},
    "verifying": {"reviewing", "failed", "cancel_requested", "needs_reconciliation"},
    "reviewing": {"accepted", "rework", "escalated", "failed", "cancel_requested", "needs_reconciliation"},
    "rework": {"queued", "escalated", "cancel_requested"},
    "blocked": {"queued", "failed", "cancel_requested"},
    "cancel_requested": {"cancelled", "needs_reconciliation"},
    "needs_reconciliation": {"failed", "cancel_requested"},
    **{s: set() for s in TERMINAL},
}
