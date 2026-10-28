"""Trusted explanatory context; does not change the pinned acceptance program."""


def verifier_context(profile_id):
    if profile_id != "fixture-stats":
        return {"profile_id": profile_id, "contract_version": 1,
                "constraints": ["Use the task acceptance and the independently registered verifier; do not infer fixture-stats rules.",
                                "Workers and reviewers cannot change verifier commands, hashes or approval scope."]}
    return {"profile_id": "fixture-stats", "contract_version": 1,
            "checks": [{"input": [], "expected": 0}, {"input": [1, 2, 3], "expected": 6},
                       {"input": [-2, 1], "expected": -1}, {"input": [0], "expected": 0},
                       {"input": [1.5, 2.5], "expected": 4.0}],
            "constraints": [
                "Implement only the undecorated pure function total(values) in stats.py, with no annotations or extra arguments.",
                "The only allowed identifiers are sum and values; the only allowed call is sum(values) without keyword arguments.",
                "Return, if/conditional expressions, truthiness, not and constants are permitted by the fixture AST guard.",
                "Comparisons (including is/is not None), attributes, imports, assignments, arithmetic and helper functions are outside this bounded fixture contract.",
                "The registered verifier runs exactly the five listed checks. Additional None-input or negative-only checks are not required evidence; do not invent extra acceptance requirements.",
                "The trusted verifier decides success. Do not relax its grammar or choose new test commands to make a candidate pass."]}
