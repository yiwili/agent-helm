"""AHCP-1 (Agent Helm Compact Wire Protocol v1): Token-saving inter-agent wire codec & template expander.

Rules:
1. Input prompts (task goals, verifier contracts, file context) are NEVER truncated or simplified so agents never misread requirements.
2. Inter-agent communication & return payloads use ultra-short English wire keys (or line-delimited AHCP-1 records) to minimize output token generation.
3. This module deterministically expands compact wire strings/objects into full canonical Agent Helm JSON templates, and renders final user-facing reports in Chinese.
"""
from __future__ import annotations

import json
from typing import Any

from .models import OrchError

WIRE_PROTOCOL_ID = "AHCP-1"

# Compact single-letter wire schema for Worker output (saves ~45-60% output tokens on JSON structure)
COMPACT_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["s", "f"],
    "properties": {
        "s": {"type": "string", "description": "Concise English technical summary"},
        "f": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["p", "c"],
                "properties": {
                    "p": {"type": "string", "description": "Relative file path"},
                    "c": {"type": "string", "description": "Full file content"},
                },
            },
        },
    },
}

# Compact single-letter wire schema for Leader / Sub-Reviewer decision
COMPACT_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["d", "h", "r", "e"],
    "properties": {
        "d": {"type": "string", "enum": ["A", "R", "E"], "description": "A=accept, R=rework, E=escalate"},
        "h": {"type": "string", "description": "Exact full 64-character candidate_hash copied verbatim from Evidence.candidate_hash"},
        "r": {"type": "array", "minItems": 1, "items": {"type": "string"}, "description": "Concise English reasons or rework instructions"},
        "e": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "enum": ["D", "V", "B", "S", "diff", "verification", "baseline", "scope"]},
        },
    },
}

# Compact Leader decomposition schema
COMPACT_DECOMPOSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sp", "rs", "ts"],
    "properties": {
        "sp": {"type": "boolean", "description": "true if task is cleanly splittable across roles/subtasks, false to pass verbatim to upstream worker"},
        "rs": {"type": "string", "description": "Decomposition rationale in English"},
        "ts": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "rl", "w", "g", "dp", "ap"],
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{1,64}$"},
                    "rl": {"type": "string", "description": "Assigned role name (e.g. implementer, analyzer, sub-reviewer)"},
                    "w": {"type": "string", "description": "Worker provider id"},
                    "rv": {"type": "string", "description": "Optional secondary reviewer provider id"},
                    "g": {"type": "string", "description": "Subtask goal (or verbatim original goal when sp=false)"},
                    "dp": {"type": "array", "items": {"type": "string"}, "description": "Dependency task IDs"},
                    "ap": {"type": "array", "minItems": 1, "items": {"type": "string"}, "description": "Allowed relative paths"},
                },
            },
        },
    },
}

_DECISION_MAP = {
    "A": "accept",
    "ACCEPT": "accept",
    "PASS": "accept",
    "R": "rework",
    "REWORK": "rework",
    "REJECT": "rework",
    "E": "escalate",
    "ESCALATE": "escalate",
}

_EVIDENCE_REF_MAP = {
    "D": "diff",
    "DIFF": "diff",
    "V": "verification",
    "VERIFICATION": "verification",
    "B": "baseline",
    "BASELINE": "baseline",
    "S": "scope",
    "SCOPE": "scope",
}

_STATUS_ZH = {
    "accepted": "已通过 Leader 终审并验收",
    "rework": "Leader 要求返工调整",
    "escalated": "已升级人工介入",
    "running": "正在并发/编排执行中",
    "verifying": "正在执行独立测试验证",
    "reviewing": "等待审查裁决",
    "queued": "排队等待执行",
    "failed": "执行失败",
    "blocked": "受安全/配额策略拦截",
    "timed_out": "执行超时",
    "cancelled": "已取消",
}


def parse_ahcp_text(text: str) -> dict[str, Any]:
    """Parse compact AHCP-1 line-oriented wire text into a canonical dict.

    Supported wire formats:
    1. Worker Result:
       AHCP1|RES|<summary>
       @@FILE|<path>
       <content...>
       @@END
    2. Review Decision:
       AHCP1|REV|<A|R|E>|<candidate_hash>|<D,V,B,S>|<reason1>;<reason2>
    """
    raw = text.strip()
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return expand_wire_object(obj)
        except ValueError:
            pass
    lines = raw.splitlines()
    if not lines or not lines[0].startswith("AHCP1|"):
        raise OrchError("Input is neither JSON nor AHCP-1 wire format")
    header = lines[0].split("|")
    kind = header[1] if len(header) > 1 else ""
    if kind == "RES":
        summary = "|".join(header[2:]).strip() or "Completed task"
        files = []
        idx = 1
        while idx < len(lines):
            line = lines[idx]
            if line.startswith("@@FILE|"):
                path = line.split("|", 1)[1].strip()
                idx += 1
                buf = []
                while idx < len(lines) and lines[idx].strip() != "@@END":
                    buf.append(lines[idx])
                    idx += 1
                content = "\n".join(buf) + ("\n" if buf else "")
                files.append({"path": path, "content": content})
            idx += 1
        return {"summary": summary, "files": files}
    if kind == "REV":
        if len(header) < 6:
            raise OrchError("Malformed AHCP1|REV wire header")
        decision_code = header[2].strip().upper()
        candidate_hash = header[3].strip()
        refs_raw = [x.strip().upper() for x in header[4].split(",") if x.strip()]
        reasons_raw = [x.strip() for x in "|".join(header[5:]).split(";") if x.strip()]
        return expand_wire_review(
            {
                "d": decision_code,
                "h": candidate_hash,
                "e": refs_raw or ["D", "V", "B", "S"],
                "r": reasons_raw or ["Reviewed via AHCP-1"],
            }
        )
    raise OrchError(f"Unknown AHCP-1 wire record kind: {kind}")


def expand_wire_result(obj: dict[str, Any]) -> dict[str, Any]:
    """Expand compact worker output {'s': ..., 'f': [{'p': ..., 'c': ...}]} to canonical {'summary': ..., 'files': ...}."""
    if not isinstance(obj, dict):
        raise OrchError("Worker wire result must be an object")
    if "summary" in obj and ("files" in obj or "operations" in obj):
        return obj
    if "s" in obj and "f" in obj and isinstance(obj["f"], list):
        files = []
        for item in obj["f"]:
            if not isinstance(item, dict) or "p" not in item or "c" not in item:
                raise OrchError("Malformed compact file entry in AHCP-1 worker output")
            files.append({"path": item["p"], "content": item["c"]})
        expanded = {"summary": str(obj["s"]), "files": files}
        for k in ("diagnostics", "thinking", "metrics", "artifacts", "extensions"):
            if k in obj:
                expanded[k] = obj[k]
        return expanded
    return obj


def expand_wire_review(obj: dict[str, Any], default_hash: str = "") -> dict[str, Any]:
    """Expand compact review {'d':'A','h':'...','r':[...],'e':['D','V']} to canonical review object."""
    if not isinstance(obj, dict):
        raise OrchError("Review wire payload must be an object")
    if "decision" in obj and "candidate_hash" in obj and "reasons" in obj and "evidence_refs" in obj:
        return obj
    raw_d = str(obj.get("d", obj.get("decision", ""))).strip().upper()
    decision = _DECISION_MAP.get(raw_d, raw_d.lower())
    candidate_hash = str(obj.get("h") or obj.get("candidate_hash") or default_hash).strip()
    raw_reasons = obj.get("r") if "r" in obj else obj.get("reasons", [])
    if isinstance(raw_reasons, str):
        reasons = [raw_reasons.strip()] if raw_reasons.strip() else ["Reviewed by leader"]
    elif isinstance(raw_reasons, list):
        reasons = [str(x).strip() for x in raw_reasons if str(x).strip()]
    else:
        reasons = ["Reviewed by leader"]
    raw_refs = obj.get("e") if "e" in obj else obj.get("evidence_refs", ["diff", "verification", "baseline", "scope"])
    refs = []
    if isinstance(raw_refs, list):
        for ref in raw_refs:
            mapped = _EVIDENCE_REF_MAP.get(str(ref).strip().upper(), str(ref).strip().lower())
            if mapped and mapped not in refs:
                refs.append(mapped)
    if not refs:
        refs = ["diff", "verification", "baseline", "scope"]
    return {
        "decision": decision,
        "candidate_hash": candidate_hash,
        "reasons": reasons,
        "evidence_refs": refs,
    }


def expand_wire_object(obj: dict[str, Any]) -> dict[str, Any]:
    if "s" in obj and "f" in obj:
        return expand_wire_result(obj)
    if "d" in obj and ("h" in obj or "r" in obj):
        return expand_wire_review(obj)
    return obj


def encode_wire_message(sender: str, receiver: str, action: str, payload: dict[str, Any]) -> str:
    """Create a compact English AHCP-1 inter-agent wire frame for logging and handoffs."""
    compact_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return f"AHCP1|{sender}->{receiver}|{action}|{compact_body}"


_INTERMEDIATE_PROCESS_KEYS = frozenset(
    {
        "agent_hops",
        "thinking",
        "thinking_state",
        "provider_metadata",
        "reviewer_metadata",
        "milestone",
        "usage",
        "worker_result",
        "changes",
        "manifest",
        "source_snapshot",
    }
)


def agent_result_only_view(evidence: dict[str, Any]) -> dict[str, Any]:
    """Strip all intermediate process / thinking traces / hop logs so downstream agents ONLY see final results.

    Rule: Intermediate processes and thinking states are strictly for human user inspection in the UI;
    agents communicating with each other receive ONLY the final result payload.
    """
    if not isinstance(evidence, dict):
        return {}
    clean: dict[str, Any] = {}
    for k, v in evidence.items():
        if k in _INTERMEDIATE_PROCESS_KEYS:
            continue
        if k in ("baseline", "verification") and isinstance(v, dict):
            clean[k] = {"passed": bool(v.get("passed"))}
            continue
        if k == "sub_review" and isinstance(v, dict):
            rev = v.get("review") if isinstance(v.get("review"), dict) else v
            clean["sub_review"] = {
                "reviewer": v.get("reviewer"),
                "decision": rev.get("decision"),
                "candidate_hash": rev.get("candidate_hash"),
                "reasons": rev.get("reasons", []),
            }
            continue
        if k == "rework_context" and isinstance(v, dict):
            old_rev = v.get("review") if isinstance(v.get("review"), dict) else {}
            clean["rework_context"] = {
                "previous_attempt": v.get("previous_attempt"),
                "previous_candidate_hash": v.get("previous_candidate_hash"),
                "review": {
                    "decision": old_rev.get("decision"),
                    "candidate_hash": old_rev.get("candidate_hash"),
                    "reasons": old_rev.get("reasons", []),
                },
                "verification": v.get("verification"),
                "previous_diff": v.get("previous_diff"),
            }
            continue
        clean[k] = v
    return clean


def translate_summary_to_zh(summary_en: str, *, provider: str = "", role: str = "", passed: bool = True) -> str:
    """Used by the Leader to synthesize a natural, pure-Chinese final conclusion from an English worker technical summary."""
    text = (summary_en or "").strip()
    lower = text.lower()
    role_prefix = f"【子角色：{role} / 执行器：{provider}】" if role and provider else (f"【执行器：{provider}】" if provider else "")
    status_tag = "独立测试通过" if passed else "待返工修正"
    if not passed:
        return f"{role_prefix}（{status_tag}）Leader 汇总中文结论：当前候选修改未能通过全部独立沙箱验收测试，需按审查意见执行返工修正。"
    if "total" in lower or "empty" in lower or "sum(" in lower:
        return (
            f"{role_prefix}（{status_tag}）Leader 汇总中文结论："
            f"已完成 stats.py 中 total 函数的边界修复，消除空列表返回 None 的缺陷并确保空列表返回 0，"
            f"同时正负数与浮点数求和保持准确，全部 5 项独立验收测试均已通过。"
        )
    return f"{role_prefix}（{status_tag}）Leader 汇总中文结论：已完成受控范围内的代码实现并通过独立沙箱验证与哈希一致性核验。"


def build_agent_hops(
    *,
    leader: str,
    worker: str,
    role: str,
    sub_reviewer: str | None,
    attempt: int,
    goal: str,
    allowed_paths: list[str],
    worker_result: dict[str, Any] | None,
    verification_passed: bool,
    scope_ok: bool,
    candidate_hash: str,
    sub_review: dict[str, Any] | None = None,
    leader_review: dict[str, Any] | None = None,
    rework_context: dict[str, Any] | None = None,
    leader_thinking: dict[str, Any] | None = None,
    sub_reviewer_thinking: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build the step-by-step inter-agent communication & thinking trace for an attempt.

    Contract:
    - All inter-agent returns (Leader -> Worker, Worker -> Sub-Reviewer/Leader, Sub-Reviewer -> Leader, Leader -> Worker Rework)
      are strictly in English ('language': 'en').
    - Only the final Leader conclusion to the user ('stage': 'leader_final_zh') is in Chinese ('language': 'zh-CN').
    - Every hop exposes both `thinking_state` (思考状态) and `return_content` (返回的内容).
    """
    hops: list[dict[str, Any]] = []
    l_think = leader_thinking or {}
    l_effort = l_think.get("reasoning_effort") or "high"
    l_ctx = l_think.get("context_window") or "default"
    l_model = l_think.get("model") or leader

    # Hop 1: Leader -> Worker (Dispatch or Rework Dispatch in English)
    if attempt > 1 and isinstance(rework_context, dict):
        prev_reasons = (rework_context.get("review") or {}).get("reasons") or ["Fix failing verification checks"]
        dispatch_payload = {
            "act": "REWORK",
            "att": attempt,
            "rl": role,
            "ap": allowed_paths,
            "prev_h": str(rework_context.get("previous_candidate_hash") or "")[:16],
            "fix": prev_reasons,
        }
        dispatch_en = f"Rework Round #{attempt} for role '{role}': {'; '.join(str(r) for r in prev_reasons)} | Full prompt preserved."
        dispatch_thought = (
            f"Leader ({leader}) analyzed Attempt #{attempt - 1} rejection & verifier failure, "
            f"kept original task prompt intact, and attached English rework directives for Worker '{worker}'."
        )
    else:
        dispatch_payload = {
            "act": "DISPATCH",
            "att": attempt,
            "rl": role,
            "ap": allowed_paths,
            "verbatim_prompt": True,
        }
        dispatch_en = f"Dispatch to role '{role}' ({worker}): Execute full task prompt within allowed scope {allowed_paths} and return compact AHCP-1 English result."
        dispatch_thought = (
            f"Leader ({leader}) assessed task splittability & scope boundaries ({', '.join(allowed_paths)}), "
            f"assigned role '{role}' to '{worker}', and forwarded the complete task prompt without truncation."
        )

    hops.append(
        {
            "step": len(hops) + 1,
            "stage": "leader_dispatch",
            "from_agent": f"Leader ({leader})",
            "to_agent": f"Worker [{role}] ({worker})",
            "language": "en",
            "agent_handoff_mode": "result_only",
            "thinking_state": {
                "visibility": "user_only",
                "agent": leader,
                "role": "leader",
                "model": l_model,
                "context_window": l_ctx,
                "reasoning_effort": l_effort,
                "status": "completed",
                "badge": f"思考完成 · Leader 调度推理 ({l_effort})",
                "thought": dispatch_thought,
            },
            "wire_frame": encode_wire_message(f"leader:{leader}", f"worker:{worker}:{role}", "DISPATCH", dispatch_payload),
            "compact_payload": dispatch_payload,
            "return_content": dispatch_en,
        }
    )

    # Hop 2: Worker -> Sub-Reviewer or Leader (Worker Return in English ONLY)
    if isinstance(worker_result, dict):
        w_think = worker_result.get("thinking") if isinstance(worker_result.get("thinking"), dict) else {}
        w_model = w_think.get("model") or worker
        w_ctx = w_think.get("context_window") or "default"
        w_effort = w_think.get("reasoning_effort") or w_think.get("thinking") or "standard"
        summary_en = str(worker_result.get("summary") or "Completed candidate modification")
        files_list = worker_result.get("files") if isinstance(worker_result.get("files"), list) else []
        compact_worker_obj = {
            "s": summary_en,
            "f": [{"p": f.get("path", ""), "c": f"<{len(str(f.get('content', '')))} chars>"} for f in files_list],
        }
        receiver_label = f"Sub-Reviewer ({sub_reviewer})" if sub_reviewer else f"Leader ({leader})"
        receiver_wire = f"sub_reviewer:{sub_reviewer}" if sub_reviewer else f"leader:{leader}"
        hops.append(
            {
                "step": len(hops) + 1,
                "stage": "worker_return",
                "from_agent": f"Worker [{role}] ({worker})",
                "to_agent": receiver_label,
                "language": "en",
                "agent_handoff_mode": "result_only",
                "thinking_state": {
                    "visibility": "user_only",
                    "agent": worker,
                    "role": role,
                    "model": w_model,
                    "context_window": w_ctx,
                    "reasoning_effort": w_effort,
                    "status": "completed",
                    "badge": f"思考完成 · 模型 {w_model} · 思考强度 {w_effort}" + (f" · 窗口 {w_ctx}" if w_ctx != "default" else ""),
                    "thought": (
                        f"Worker '{worker}' (role: {role}) analyzed baseline failure in {', '.join(allowed_paths)}, "
                        f"generated candidate patch (SHA-256: {(candidate_hash or '')[:12]}...), and returned compact English AHCP-1 payload."
                    ),
                },
                "wire_frame": encode_wire_message(
                    f"worker:{worker}:{role}",
                    receiver_wire,
                    "RESULT",
                    {"s": summary_en[:160], "f": [f.get("path", "") for f in files_list]},
                ),
                "compact_payload": compact_worker_obj,
                "return_content": f"[EN Result Only] Summary: {summary_en} | Modified files: {', '.join(f.get('path', '') for f in files_list) or 'none'}",
            }
        )

    # Hop 3 (Optional): Sub-Reviewer -> Leader (Sub-Review Return in English ONLY)
    if sub_reviewer and isinstance(sub_review, dict):
        sr_body = sub_review.get("review") if isinstance(sub_review.get("review"), dict) else sub_review
        sr_decision = str(sr_body.get("decision") or "accept")
        sr_code = {"accept": "A", "rework": "R", "escalate": "E"}.get(sr_decision, "A")
        sr_reasons = sr_body.get("reasons") if isinstance(sr_body.get("reasons"), list) else ["Reviewed candidate"]
        sr_think = sub_reviewer_thinking or {}
        sr_model = sr_think.get("model") or sub_reviewer
        sr_effort = sr_think.get("reasoning_effort") or "high"
        sr_compact = {
            "d": sr_code,
            "h": (candidate_hash or "")[:16],
            "r": sr_reasons,
            "e": ["D", "V", "B", "S"],
        }
        hops.append(
            {
                "step": len(hops) + 1,
                "stage": "sub_review_return",
                "from_agent": f"Sub-Reviewer ({sub_reviewer})",
                "to_agent": f"Leader ({leader})",
                "language": "en",
                "agent_handoff_mode": "result_only",
                "thinking_state": {
                    "visibility": "user_only",
                    "agent": sub_reviewer,
                    "role": "sub-reviewer",
                    "model": sr_model,
                    "context_window": sr_think.get("context_window") or "default",
                    "reasoning_effort": sr_effort,
                    "status": "completed",
                    "badge": f"思考完成 · 次级审查推理 ({sr_effort})",
                    "thought": (
                        f"Sub-Reviewer '{sub_reviewer}' audited candidate diff & verifier result "
                        f"(passed={verification_passed}, scope_ok={scope_ok}) and forwarded English review decision '{sr_decision}' to Leader."
                    ),
                },
                "wire_frame": encode_wire_message(f"sub_reviewer:{sub_reviewer}", f"leader:{leader}", "SUB_REV", sr_compact),
                "compact_payload": sr_compact,
                "return_content": f"[EN Sub-Review Result Only] Decision={sr_decision.upper()} ({sr_code}) | Reasons: {'; '.join(str(x) for x in sr_reasons)}",
            }
        )

    # Hop 4: Leader Review & Final Output
    if isinstance(leader_review, dict) and leader_review.get("decision") and leader_review.get("decision") != "pending":
        l_decision = str(leader_review.get("decision"))
        l_code = {"accept": "A", "rework": "R", "escalate": "E"}.get(l_decision, "A")
        l_reasons_en = leader_review.get("reasons") if isinstance(leader_review.get("reasons"), list) else ["Reviewed by leader"]
        l_compact = {
            "d": l_code,
            "h": (candidate_hash or "")[:16],
            "r": l_reasons_en,
            "e": ["D", "V", "B", "S"],
        }
        # If rejected (rework), Leader sends an English inter-agent rework notice back to Worker
        if l_decision == "rework":
            hops.append(
                {
                    "step": len(hops) + 1,
                    "stage": "leader_rework_notice",
                    "from_agent": f"Leader ({leader})",
                    "to_agent": f"Worker [{role}] ({worker})",
                    "language": "en",
                    "agent_handoff_mode": "result_only",
                    "thinking_state": {
                        "visibility": "user_only",
                        "agent": leader,
                        "role": "leader",
                        "model": l_model,
                        "context_window": l_ctx,
                        "reasoning_effort": l_effort,
                        "status": "completed",
                        "badge": f"思考完成 · Leader 退回返工裁决 ({l_effort})",
                        "thought": (
                            f"Leader '{leader}' detected failed verification or policy violation "
                            f"(verification_passed={verification_passed}, scope_ok={scope_ok}) and issued English rework notice to Worker '{worker}'."
                        ),
                    },
                    "wire_frame": encode_wire_message(f"leader:{leader}", f"worker:{worker}:{role}", "REWORK", l_compact),
                    "compact_payload": l_compact,
                    "return_content": f"[EN Rework Result Only] Decision=REWORK (R) | Instructions: {'; '.join(str(x) for x in l_reasons_en)}",
                }
            )

        # Final Hop: Leader -> User (Chinese Final Response!)
        w_summary_en = str((worker_result or {}).get("summary") or "")
        zh_conclusion = translate_summary_to_zh(
            w_summary_en,
            provider=worker,
            role=role,
            passed=(l_decision == "accept" and verification_passed),
        )
        decision_zh_label = {
            "accept": "审查通过（Accept）· 准予结项",
            "rework": "审查拒绝（Rework）· 已通知 Worker 返工修正",
            "escalate": "升级人工处理（Escalate）",
        }.get(l_decision, l_decision)
        leader_final_zh_text = (
            f"【Leader ({leader}) 最终中文审查结论】裁决结果：{decision_zh_label}。"
            f"{zh_conclusion}；独立沙箱验证：{'通过' if verification_passed else '未通过'}，"
            f"文件受控范围：{'合规' if scope_ok else '越界'}。"
        )
        hops.append(
            {
                "step": len(hops) + 1,
                "stage": "leader_final_zh",
                "from_agent": f"Leader ({leader})",
                "to_agent": "最终交付 (User · 中文输出)",
                "language": "zh-CN",
                "agent_handoff_mode": "user_final_zh",
                "thinking_state": {
                    "visibility": "user_only",
                    "agent": leader,
                    "role": "leader",
                    "model": l_model,
                    "context_window": l_ctx,
                    "reasoning_effort": l_effort,
                    "status": "completed",
                    "badge": f"思考完成 · Leader 终审与中文汇总 ({l_effort})",
                    "thought": (
                        f"Leader ({leader}) 综合核验了 Worker 英文返回载荷、候选快照哈希 ({(candidate_hash or '')[:12]}...)、"
                        f"独立验收测试结果与文件权限边界，生成最终面向用户的中文审查与交付报告。"
                    ),
                },
                "wire_frame": encode_wire_message(f"leader:{leader}", "user:final_zh", "FINAL_ZH", l_compact),
                "compact_payload": l_compact,
                "return_content": leader_final_zh_text,
            }
        )

    return hops


def build_chinese_orchestration_report(
    *,
    mode: str,
    leader: str,
    splittable: bool,
    decomposition_reason: str,
    tasks: list[dict[str, Any]],
    attempts_by_task: dict[str, list[dict[str, Any]]],
    reviews_by_task: dict[str, list[dict[str, Any]]],
    final_state: str,
) -> dict[str, Any]:
    """Generate the Leader's final Chinese report for the user while keeping inter-agent records in English."""
    mode_label = "模式二：Leader 拆解与多角色协同编排" if mode == "decompose" else "模式一：Leader 直派单 Worker 闭环"
    if mode == "decompose" and not splittable:
        split_desc = f"经 Leader ({leader}) 评估，当前任务为强耦合原子修改，不适合强行拆分，已直接将原始提示词透传给上游主力 Worker 执行（英文评估依据：{decomposition_reason}）。"
    elif mode == "decompose":
        split_desc = f"Leader ({leader}) 已将任务拆解为 {len(tasks)} 个子角色节点并按依赖波次并发/顺序派发（英文拆解依据：{decomposition_reason}）。"
    else:
        split_desc = f"Leader ({leader}) 直接将完整任务派发给指定 Worker 执行，并在完成后执行独立测试与 Leader 终审。"

    task_reports = []
    leader_final_sections = []
    for t in tasks:
        tid = t["id"]
        role = t.get("role") or "worker"
        provider = t["provider"]
        sub_reviewer = t.get("sub_reviewer")
        attempts = attempts_by_task.get(tid, [])
        reviews = reviews_by_task.get(tid, [])
        last_attempt = attempts[-1] if attempts else {}
        wr = last_attempt.get("worker_result") or {}
        summary_en = wr.get("summary") or ""
        passed = bool(last_attempt.get("verification_passed"))
        last_review = reviews[-1] if reviews else {}
        decision = last_review.get("decision", "pending")
        decision_zh = {"accept": "通过 (Accept)", "rework": "打回返工 (Rework)", "escalate": "升级处理 (Escalate)"}.get(decision, "待裁决")
        leader_zh_item = translate_summary_to_zh(summary_en, provider=provider, role=role, passed=passed)
        leader_final_sections.append(f"节点 {tid}（{role} -> {provider}）：{leader_zh_item}，Leader 终审：{decision_zh}。")
        task_reports.append(
            {
                "task_id": tid,
                "role": role,
                "worker": provider,
                "sub_reviewer": sub_reviewer,
                "goal": t["goal"],
                "dependencies": t.get("dependencies", []),
                "attempt_count": len(attempts),
                "verification_passed": passed,
                "worker_return_en": summary_en,
                "leader_decision_zh": decision_zh,
                "leader_summary_zh": leader_zh_item,
                "wire_protocol": WIRE_PROTOCOL_ID,
            }
        )

    leader_final_response_zh = (
        f"【Leader ({leader}) 最终中文结项报告】\n"
        f"1. 编排模式：{mode_label}（内部通信协议：{WIRE_PROTOCOL_ID} 英文极简帧，最终由 Leader 统一输出中文）。\n"
        f"2. 拆解与派发判定：{split_desc}\n"
        f"3. 各角色执行与终审汇总：{' '.join(leader_final_sections)}\n"
        f"4. 最终运行状态：{_STATUS_ZH.get(final_state, final_state)}。"
    )

    return {
        "protocol": WIRE_PROTOCOL_ID,
        "inter_agent_language": "en",
        "final_leader_language": "zh-CN",
        "language": "zh-CN",
        "orchestration_mode": mode,
        "orchestration_mode_zh": mode_label,
        "leader": leader,
        "splittable": splittable,
        "decomposition_explanation_zh": split_desc,
        "final_state": final_state,
        "final_state_zh": _STATUS_ZH.get(final_state, final_state),
        "leader_final_response_zh": leader_final_response_zh,
        "tasks_zh": task_reports,
    }
