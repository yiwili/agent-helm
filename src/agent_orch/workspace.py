from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

from .models import OrchError, digest, relpath, validate_result

EXCLUDED = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache"}


def checked(root, relative):
    relpath(relative)
    root = Path(root).resolve()
    candidate = root / relative
    for part in [candidate, *candidate.parents]:
        if part == root:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise OrchError("Symlink/junction rejected")
    if not candidate.resolve().is_relative_to(root):
        raise OrchError("Resolved path outside workspace")
    return candidate


def manifest(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise OrchError("Source directory missing")
    entries = {}
    total = 0
    folded = set()
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
        for name in dirs + sorted(files):
            p = Path(parent) / name
            relative = p.relative_to(root).as_posix()
            checked(root, relative)
            if p.is_dir():
                continue
            if not p.is_file():
                raise OrchError("Special file rejected")
            if relative.casefold() in folded:
                raise OrchError("Case-colliding workspace paths")
            folded.add(relative.casefold())
            size = p.stat().st_size
            total += size
            if size > 2_000_000 or total > 20_000_000 or len(entries) >= 1000:
                raise OrchError("Workspace exceeds first-release snapshot limits")
            entries[relative] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "size": size,
                                 "executable": bool(p.stat().st_mode & stat.S_IXUSR)}
    return entries


def copy_snapshot(source, dest):
    source, dest = Path(source).resolve(), Path(dest).resolve()
    if dest == source or dest.is_relative_to(source) or source.is_relative_to(dest):
        raise OrchError("Snapshot and source must be disjoint")
    before = manifest(source)
    dest.mkdir(parents=True, exist_ok=False)
    for name in before:
        out = checked(dest, name)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(checked(source, name), out)
    if manifest(source) != before or manifest(dest) != before:
        raise OrchError("Source drift while taking snapshot")
    return before


def apply_files(root, result, allowed):
    validate_result(result, allowed)
    for file in result["files"]:
        p = checked(root, file["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(file["content"].encode("utf-8"))


def diff(base, candidate):
    a, b = manifest(base), manifest(candidate)
    changed = sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p))
    chunks = []
    for p in changed:
        try:
            old = checked(base, p).read_text(encoding="utf-8").splitlines(keepends=True) if p in a else []
            new = checked(candidate, p).read_text(encoding="utf-8").splitlines(keepends=True) if p in b else []
            chunks.extend(difflib.unified_diff(old, new, fromfile="a/"+p, tofile="b/"+p))
        except UnicodeError:
            chunks.append(f"Binary file changed: {p}\n")
        if p in a and p in b and a[p]["executable"] != b[p]["executable"]:
            chunks.append(f"Executable mode changed: {p}\n")
    return changed, "".join(chunks)


def make_fixture(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    (root / "stats.py").write_text("def total(values):\n    return sum(values) if values else None\n", encoding="utf-8")
    (root / "README.txt").write_text("Synthetic non-sensitive fixture. total([]) must be 0.\n", encoding="utf-8")
    return root
