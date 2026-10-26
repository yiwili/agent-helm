from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path

from .maintenance import maintenance_lock
from .models import OrchError, canonical, digest, strict, utcnow
from .workspace import checked


def inventory(root):
    root = Path(root)
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        raise OrchError("Backup root cannot be a symlink or junction")
    result = {}
    size = 0
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(parent)/name
            relative = path.relative_to(root).as_posix()
            checked(root, relative)
            if path.is_dir():
                continue
            if not path.is_file():
                raise OrchError("Special file in backup")
            size += path.stat().st_size
            if len(result) >= 10000 or size > 200_000_000:
                raise OrchError("Backup exceeds local release limits")
            result[relative] = {"size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return result


def validate_database(path):
    conn = sqlite3.connect(path.as_uri()+"?mode=ro&immutable=1", uri=True)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise OrchError("Backup database integrity check failed")
        if conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] != 1:
            raise OrchError("Unsupported backup database schema")
    except sqlite3.DatabaseError as e:
        raise OrchError("Invalid backup database") from e
    finally:
        conn.close()


def backup_bundle(store, target):
    target = Path(target).resolve()
    if target.exists() or target.is_relative_to(store.root) or store.root.is_relative_to(target):
        raise OrchError("Backup target must be new and disjoint from state root")
    with maintenance_lock(store.root), store.transaction():
        active = store.db.execute("SELECT COUNT(*) FROM tasks WHERE state IN ('running','verifying','reviewing','cancel_requested','needs_reconciliation')").fetchone()[0]
        if active:
            raise OrchError("Stop and reconcile all in-flight/uncertain tasks before backup")
        target.mkdir(parents=True)
        # Separate reader sees a committed snapshot while this connection prevents new claims.
        conn = sqlite3.connect(store.root/"state.db")
        dest = sqlite3.connect(target/"state.db")
        try:
            conn.backup(dest)
        finally:
            conn.close()
            dest.close()
        for child in ("runs", "integrations", "fixtures"):
            if (store.root/child).exists():
                inventory(store.root/child)  # reject links before copytree follows anything
                shutil.copytree(store.root/child, target/child)
                if inventory(store.root/child) != inventory(target/child):
                    raise OrchError("Artifacts changed while backing up")
        index = inventory(target)
        meta = {"format_version": 1, "schema_version": 1, "platform": os.name, "created": utcnow(),
                "original_state_root": str(store.root), "requires_same_absolute_path": False,
                "files": index, "manifest_hash": digest(index)}
        (target/"backup.json").write_text(canonical(meta), encoding="utf-8")
        validate_database(target/"state.db")
    verify_backup(target)
    return {"backup": str(target), "schema_version": 1, "manifest_hash": meta["manifest_hash"], "verified": True}


def verify_backup(source):
    source = Path(source).resolve()
    meta_path = checked(source, "backup.json")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise OrchError("Missing or invalid backup manifest") from e
    strict(meta, ("format_version", "schema_version", "platform", "created", "original_state_root", "requires_same_absolute_path", "files", "manifest_hash"))
    if meta["format_version"] != 1 or meta["schema_version"] != 1 or meta["platform"] != os.name:
        raise OrchError("Unsupported backup format, schema, or platform")
    if not isinstance(meta["files"], dict) or "state.db" not in meta["files"] or meta["manifest_hash"] != digest(meta["files"]):
        raise OrchError("Backup manifest hash mismatch")
    for relative in meta["files"]:
        checked(source, relative)
    actual = inventory(source)
    actual.pop("backup.json", None)
    if actual != meta["files"]:
        raise OrchError("Backup contains missing, modified, or unexpected files")
    validate_database(source/"state.db")
    return meta


def restore_bundle(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists() or target.is_relative_to(source) or source.is_relative_to(target):
        raise OrchError("Restore target must be new and disjoint; existing state is never overwritten")
    meta = verify_backup(source)
    target.mkdir(parents=True)
    for name in meta["files"]:
        out = checked(target, name)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(checked(source, name), out)
    if inventory(target) != meta["files"]:
        raise OrchError("Restore copy integrity mismatch")
    old = Path(meta["original_state_root"])

    def rebase(value):
        if not isinstance(value, str):
            return value
        p = Path(value)
        if p.is_absolute() and p.is_relative_to(old):
            return str(target/p.relative_to(old))
        return value

    def report(value):
        value["workspace"] = rebase(value["workspace"])
        value["argv"] = [rebase(x) for x in value["argv"]]
        value.pop("report_hash", None)
        value["report_hash"] = digest(value)

    conn = sqlite3.connect(target/"state.db")
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            for row in conn.execute("SELECT * FROM approvals").fetchall():
                conn.execute("UPDATE approvals SET source=? WHERE id=?", (rebase(row["source"]), row["id"]))
            for row in conn.execute("SELECT * FROM runs").fetchall():
                new_source = rebase(row["source"])
                body_hash = digest({"plan": json.loads(row["plan"]), "source": new_source, "approval": row["approval"]})
                conn.execute("UPDATE runs SET source=?,body_hash=? WHERE id=?", (new_source, body_hash, row["id"]))
            for row in conn.execute("SELECT * FROM attempts WHERE evidence IS NOT NULL").fetchall():
                ev = json.loads(row["evidence"])
                report(ev["baseline"])
                report(ev["verification"])
                conn.execute("UPDATE attempts SET evidence=? WHERE run_id=? AND task_id=? AND number=?", (canonical(ev), row["run_id"], row["task_id"], row["number"]))
                path = checked(target, f"runs/{row['run_id']}/{row['task_id']}/{row['number']}/evidence.json")
                if path.exists():
                    path.write_text(canonical(ev), encoding="utf-8")
            for row in conn.execute("SELECT * FROM integrations").fetchall():
                body = json.loads(row["body"])
                body["workspace"] = rebase(body["workspace"])
                report(body["verification"])
                conn.execute("UPDATE integrations SET body=? WHERE id=?", (canonical(body), row["id"]))
    finally:
        conn.close()
    validate_database(target/"state.db")
    # Recheck original backup, which must remain unchanged during restore.
    verify_backup(source)
    result = {"restored": str(target), "schema_version": 1, "source_manifest_hash": meta["manifest_hash"],
              "replayed_tasks": 0, "external_source_paths": "preserved; still require original source directory"}
    (target/"restore.json").write_text(canonical(result), encoding="utf-8")
    return result
