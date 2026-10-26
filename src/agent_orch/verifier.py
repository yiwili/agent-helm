from __future__ import annotations

import hashlib
import sys
import ast
import json

from .models import digest, utcnow, OrchError
from .supervisor import run_process
from .workspace import manifest


def fixture_source_allowed(workspace):
    """The v1 fixture profile admits a pure sum function, never arbitrary Python."""
    try:
        tree = ast.parse((workspace / "stats.py").read_text(encoding="utf-8"))
    except (SyntaxError, OSError, UnicodeError):
        return False
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        return False
    func = tree.body[0]
    if func.name != "total" or func.decorator_list or func.returns or func.type_comment or getattr(func, "type_params", []):
        return False
    args = func.args
    if len(args.args) != 1 or args.args[0].arg != "values" or args.args[0].annotation or args.defaults or args.kw_defaults or args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg:
        return False
    allowed = (ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.If, ast.IfExp,
               ast.UnaryOp, ast.Not, ast.Call, ast.Name, ast.Load, ast.Constant, ast.Expr)
    nodes = list(ast.walk(tree))
    if len(nodes) > 500 or any(not isinstance(n, allowed) for n in nodes):
        return False
    for node in nodes:
        if isinstance(node, ast.Name) and node.id not in ("sum", "values"):
            return False
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id == "sum" and len(node.args) == 1 and isinstance(node.args[0], ast.Name) and node.args[0].id == "values" and not node.keywords):
            return False
    return True

# Trusted, external to every worker workspace. No worker-supplied command strings.
STATS_CHECK = '''import json,runpy,sys
m=runpy.run_path(sys.argv[1]+"/stats.py")
cases=[([],0),([1,2,3],6),([-2,1],-1),([0],0),([1.5,2.5],4.0)]
results=[]
for values, expected in cases:
    actual=m["total"](values)
    results.append({"input":values,"expected":expected,"actual":actual,"passed":actual==expected})
print(json.dumps(results))
raise SystemExit(0 if all(x["passed"] for x in results) else 1)
'''


async def verify(workspace, timeout=30, cancelled=lambda: False, profile=None):
    if profile and profile.get("kind") == "registered":
        from .verifier_registry import check_profile
        check_profile(profile)
        start = utcnow()
        before = digest(manifest(workspace))
        argv = [str(workspace) if a == "{workspace}" else a for a in profile["argv"]]
        p = await run_process(argv, workspace, timeout=profile["timeout"], cancelled=cancelled)
        check_profile(profile)
        after = digest(manifest(workspace))
        report = {"identity": "registered:"+profile["id"], "profile_hash": digest(profile), "argv": argv,
                  "workspace": str(workspace), "started": start, "finished": utcnow(), "exit_code": p.exit_code,
                  "stdout": p.stdout, "stderr": p.stderr, "candidate_hash": before, "unchanged": before == after,
                  "passed": p.exit_code == 0 and before == after}
        report["report_hash"] = digest(report)
        return report
    if profile:
        from .verifier_registry import check_profile
        check_profile(profile)
    before = digest(manifest(workspace))
    start = utcnow()
    argv = [sys.executable, "-I", "-B", "-c", STATS_CHECK, str(workspace)]
    if fixture_source_allowed(workspace):
        p = await run_process(argv, workspace, timeout=timeout, cancelled=cancelled)
        try:
            checks = json.loads(p.stdout)
            valid_report = isinstance(checks, list) and len(checks) == 5 and all(isinstance(c, dict) and c.get("passed") is True for c in checks)
        except (ValueError, TypeError):
            valid_report = False
    else:
        from .supervisor import ProcessResult
        p = ProcessResult(-1, "", "fixture-stats rejects code outside the registered pure total(values) grammar")
        valid_report = False
    after = digest(manifest(workspace))
    report = {"identity": "trusted-fixture-stats-v1", "argv": argv, "workspace": str(workspace),
              "started": start, "finished": utcnow(), "exit_code": p.exit_code,
              "stdout": p.stdout, "stderr": p.stderr, "candidate_hash": before,
              "unchanged": before == after, "passed": p.exit_code == 0 and before == after and valid_report}
    report["report_hash"] = digest(report)
    return report
