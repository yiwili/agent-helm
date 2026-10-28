RESULT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "files"],
    "properties": {"summary": {"type": "string"}, "files": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["path", "content"],
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}}}}

REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["decision", "candidate_hash", "reasons", "evidence_refs"],
    "properties": {"decision": {"type": "string", "enum": ["accept", "rework", "escalate"]},
                   "candidate_hash": {"type": "string"},
                   "reasons": {"type": "array", "items": {"type": "string"}},
                   "evidence_refs": {"type": "array", "minItems": 1,
                                     "items": {"type": "string", "enum": ["diff", "verification", "baseline", "scope"]}}}}

TASK_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["id", "goal", "provider", "dependencies", "allowed_paths", "acceptance", "verifier_profile", "wall_seconds", "milestone_review"],
    "properties": {"id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{1,64}$"}, "goal": {"type": "string"},
                   "provider": {"enum": ["mock", "manual", "codex", "qoder-cn", "cursor", "claude"]},
                   "milestone_review": {"type": "boolean"},
                   "dependencies": {"type": "array", "items": {"type": "string"}},
                   "allowed_paths": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                   "acceptance": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                   "verifier_profile": {"const": "fixture-stats"}, "wall_seconds": {"type": "number", "minimum": 0.05, "maximum": 600}}}

PLAN_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["schema_version", "goal", "tasks", "max_rework_rounds", "api_budget"],
    "properties": {"schema_version": {"const": 1}, "goal": {"type": "string"},
                   "tasks": {"type": "array", "minItems": 1, "items": TASK_SCHEMA},
                   "max_rework_rounds": {"type": "integer", "minimum": 0, "maximum": 2}, "api_budget": {"const": 0}}}

DIAGNOSIS_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["summary", "proposed_paths", "checks", "risks", "needs_scope_change"],
    "properties": {"summary": {"type": "string"}, "proposed_paths": {"type": "array", "items": {"type": "string"}},
                   "checks": {"type": "array", "items": {"type": "string"}}, "risks": {"type": "array", "items": {"type": "string"}},
                   "needs_scope_change": {"type": "boolean"}}}

MILESTONE_REVIEW_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["decision", "milestone_hash", "reasons"],
    "properties": {"decision": {"enum": ["continue", "rework", "escalate"]}, "milestone_hash": {"type": "string"},
                   "reasons": {"type": "array", "items": {"type": "string"}}}}
