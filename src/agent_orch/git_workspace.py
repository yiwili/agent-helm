"""Read-only Git baselines. Never clone, stash, reset, checkout, or share .git."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from .models import OrchError, digest, relpath
from .supervisor import safe_env


def git(root, *args, allow_failure=False):
    executable = shutil.which("git")
    if not executable:
        raise OrchError("Git executable is required for Git workspaces")
    env = safe_env()
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    argv = [executable, "--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.hooksPath="+os.devnull,
            "-c", "diff.external=", "-C", str(root), *args]
    p = subprocess.run(argv, env=env, capture_output=True, timeout=20, shell=False)
    if len(p.stdout) > 4_000_000:
        raise OrchError("Git metadata exceeds snapshot limits")
    if p.returncode and not allow_failure:
        raise OrchError("Git metadata command failed: "+args[0])
    return p


def git_root(root):
    root = Path(root).resolve()
    meta = root/".git"
    if not meta.exists() and not meta.is_symlink():
        return False
    if not meta.is_dir() or meta.is_symlink() or (hasattr(meta, "is_junction") and meta.is_junction()):
        raise OrchError("Linked worktrees and symlinked Git metadata are unsupported")
    for name in ("config", "index", "HEAD", "objects", "refs"):
        p = meta/name
        if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
            raise OrchError("Symlinked Git control file")
    if (meta/"commondir").exists() or (meta/"objects"/"info"/"alternates").exists():
        raise OrchError("Shared Git metadata/object stores are unsupported")
    # Reject local config that could import host configuration or execute filters.
    unsafe = git(root, "config", "--local", "--no-includes", "--get-regexp",
                 r"^(filter\.|include\.|includeif\.|core\.worktree|extensions\.worktreeconfig)", allow_failure=True)
    if unsafe.returncode not in (0, 1) or unsafe.stdout:
        raise OrchError("Git filters, config includes, and external worktree configuration are unsupported")
    top = git(root, "rev-parse", "--show-toplevel").stdout.decode("utf-8").strip()
    if Path(top).resolve() != root:
        raise OrchError("Source must be the exact Git repository root")
    return True


def split_paths(data):
    try:
        paths = [p.decode("utf-8") for p in data.split(b"\0") if p]
    except UnicodeError as e:
        raise OrchError("Git filenames must be UTF-8") from e
    for path in paths:
        relpath(path)
    return paths


def selected_files(root):
    if not git_root(root):
        return None
    # Ignored files are not read or copied. Tracked files remain included even if ignored.
    paths = split_paths(git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").stdout)
    if len(paths) > 1000:
        raise OrchError("Git workspace exceeds 1000 selected paths")
    return set(paths)


def metadata(root):
    root = Path(root).resolve()
    if not git_root(root):
        return {"kind": "directory"}
    head = git(root, "rev-parse", "--verify", "HEAD", allow_failure=True)
    if head.returncode:
        raise OrchError("Git source requires an existing HEAD commit")
    index = []
    for record in git(root, "ls-files", "--stage", "-z").stdout.split(b"\0"):
        if not record:
            continue
        try:
            fields, raw_path = record.split(b"\t", 1)
            mode, oid, stage = fields.decode("ascii").split()
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeError) as e:
            raise OrchError("Malformed Git index metadata") from e
        relpath(path)
        if stage != "0" or mode not in ("100644", "100755"):
            raise OrchError("Unmerged index, symlink, or submodule is unsupported")
        index.append({"path": path, "mode": mode, "object_id": oid})
    raw = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout.split(b"\0")
    status = []
    while raw and raw[0]:
        record = raw.pop(0).decode("utf-8")
        code, path = record[:2], relpath(record[3:])
        item = {"status": code, "path": path}
        if "R" in code or "C" in code:
            if not raw or not raw[0]:
                raise OrchError("Incomplete Git rename metadata")
            item["old_path"] = relpath(raw.pop(0).decode("utf-8"))
        status.append(item)
    branch = git(root, "symbolic-ref", "--short", "HEAD", allow_failure=True)
    index_file = root/".git"/"index"
    return {"kind": "git", "head_commit": head.stdout.decode("ascii").strip(),
            "branch": branch.stdout.decode("utf-8").strip() if branch.returncode == 0 else None,
            "index": index, "index_hash": digest(index),
            "index_file_hash": hashlib.sha256(index_file.read_bytes()).hexdigest() if index_file.exists() else None,
            "status": status}


def source_snapshot(root):
    from .workspace import manifest
    before = metadata(root)
    files = manifest(root)
    if metadata(root) != before:
        raise OrchError("Git baseline changed during capture")
    return {**before, "manifest": files, "tree_hash": digest(files)}


def make_git_fixture(root):
    from .workspace import make_fixture
    root = make_fixture(root)
    git(root, "init", "--template=", "--initial-branch=main", ".")
    git(root, "config", "core.autocrlf", "false")
    git(root, "add", "--", "stats.py", "README.txt")
    git(root, "-c", "user.name=Synthetic Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-m", "test: seed synthetic stats fixture")
    return root
