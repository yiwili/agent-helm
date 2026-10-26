import json
import uuid
from pathlib import Path

from .models import OrchError, digest
from .maintenance import maintenance_lock
from .verifier import verify
from .workspace import copy_snapshot, diff, manifest, replay_changes, change_details


async def integrate(store, run):
    with maintenance_lock(store.root):
        return await _integrate(store, run)


async def _integrate(store, run):
    r = store.run(run)
    if r["state"] != "accepted":
        raise OrchError("Only accepted runs can produce integration candidates")
    if digest(manifest(r["source"])) != r["source_hash"]:
        raise OrchError("Integration baseline drift; replan required")
    from .git_workspace import source_snapshot
    expected = store.submission(run).get("source_snapshot")
    if expected and source_snapshot(r["source"]) != expected:
        raise OrchError("Integration Git HEAD, index, or status drift; replan required")
    ident = "integration-" + uuid.uuid4().hex
    target = store.root / "integrations" / ident
    copy_snapshot(r["source"], target)
    for task in store.accepted_tasks(run):
        row = store.db.execute("SELECT evidence FROM attempts WHERE run_id=? AND task_id=? AND number=?", (run, task["id"], task["attempt"])).fetchone()
        ev = json.loads(row[0])
        source = store.root / "runs" / run / task["id"] / str(task["attempt"]) / "candidate"
        if digest(manifest(source)) != ev["candidate_hash"]:
            raise OrchError("Accepted candidate drift")
        if digest(manifest(target)) != ev["baseline_hash"]:
            raise OrchError("Integration candidate does not match task baseline")
        base = source.parent/"baseline"
        if digest(manifest(base)) != ev["baseline_hash"]:
            raise OrchError("Task baseline snapshot drift")
        replay_changes(base, source, target, json.loads(task["spec"])["allowed_paths"])
        if digest(manifest(target)) != ev["candidate_hash"]:
            raise OrchError("Integration replay hash mismatch")
    verifications = []
    for profile_id in dict.fromkeys(json.loads(t["spec"])["verifier_profile"] for t in store.accepted_tasks(run)):
        verifications.append(await verify(target, profile=store.verifier_profile(run, profile_id)))
    report = verifications[-1]
    if not all(v["passed"] for v in verifications) or digest(manifest(r["source"])) != r["source_hash"] or (expected and source_snapshot(r["source"]) != expected):
        raise OrchError("Integration test failed or source drifted")
    _, patch = diff(r["source"], target)
    body = {"id": ident, "workspace": str(target), "candidate_hash": digest(manifest(target)), "verification": report,
            "verifications": verifications, "changes": change_details(r["source"], target), "source_snapshot": expected, "diff": patch, "merged": False}
    with store.transaction():
        store.db.execute("INSERT INTO integrations VALUES(?,?,?)", (ident, run, json.dumps(body)))
        store.event(run, "", 0, "integration_candidate", {"id": ident, "candidate_hash": body["candidate_hash"]})
    return body
