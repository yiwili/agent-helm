import json
import uuid
from pathlib import Path

from .models import OrchError, digest
from .maintenance import maintenance_lock
from .verifier import verify
from .workspace import apply_files, copy_snapshot, diff, manifest


async def integrate(store, run):
    with maintenance_lock(store.root):
        return await _integrate(store, run)


async def _integrate(store, run):
    r = store.run(run)
    if r["state"] != "accepted":
        raise OrchError("Only accepted runs can produce integration candidates")
    if digest(manifest(r["source"])) != r["source_hash"]:
        raise OrchError("Integration baseline drift; replan required")
    ident = "integration-" + uuid.uuid4().hex
    target = store.root / "integrations" / ident
    copy_snapshot(r["source"], target)
    for task in store.accepted_tasks(run):
        row = store.db.execute("SELECT evidence FROM attempts WHERE run_id=? AND task_id=? AND number=?", (run, task["id"], task["attempt"])).fetchone()
        ev = json.loads(row[0])
        source = store.root / "runs" / run / task["id"] / str(task["attempt"]) / "candidate"
        if digest(manifest(source)) != ev["candidate_hash"]:
            raise OrchError("Accepted candidate drift")
        # Entire scoped file contents, checked again; no arbitrary git patch application.
        files = [{"path": name, "content": (source/name).read_text(encoding="utf-8")} for name in ev["changed_paths"]]
        apply_files(target, {"summary": "integrate", "files": files}, json.loads(task["spec"])["allowed_paths"])
    report = await verify(target)
    if not report["passed"] or digest(manifest(r["source"])) != r["source_hash"]:
        raise OrchError("Integration test failed or source drifted")
    _, patch = diff(r["source"], target)
    body = {"id": ident, "workspace": str(target), "candidate_hash": digest(manifest(target)), "verification": report, "diff": patch, "merged": False}
    with store.transaction():
        store.db.execute("INSERT INTO integrations VALUES(?,?,?)", (ident, run, json.dumps(body)))
        store.event(run, "", 0, "integration_candidate", {"id": ident, "candidate_hash": body["candidate_hash"]})
    return body
