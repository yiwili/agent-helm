from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import stat
import base64
from pathlib import Path

from .models import OrchError, digest, relpath, validate_result

EXCLUDED = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache"}


def checked(root, relative, *, allow_git_metadata=False):
    # Only private backup/restore uses this flag. Worker paths can never address .git.
    if allow_git_metadata and isinstance(relative, str):
        relpath("/".join("__git_metadata__" if p.lower() == ".git" else p for p in relative.split("/")))
    else:
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
    from .git_workspace import selected_files
    selected = selected_files(root)
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in (EXCLUDED if selected is None else {".git"}))
        if selected is not None:
            prefix = Path(parent).relative_to(root).as_posix()
            prefix = "" if prefix == "." else prefix+"/"
            dirs[:] = [d for d in dirs if any(p.startswith(prefix+d+"/") for p in selected)]
        for name in dirs + sorted(files):
            p = Path(parent) / name
            relative = p.relative_to(root).as_posix()
            if not p.is_dir() and selected is not None and relative not in selected:
                continue
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
    if "operations" in result:
        for op in result["operations"]:
            path = checked(root, op["path"])
            if op["action"] == "delete":
                if not path.is_file():
                    raise OrchError("Delete requires an existing regular file")
                path.unlink()
            elif op["action"] == "rename":
                target = checked(root, op["target"])
                if not path.is_file() or target.exists():
                    raise OrchError("Rename source missing or target already exists")
                target.parent.mkdir(parents=True, exist_ok=True)
                path.rename(target)
            else:
                current = bool(path.stat().st_mode & stat.S_IXUSR) if path.exists() else False
                desired = op.get("executable", current)
                if os.name == "nt" and current != desired:
                    raise OrchError("Executable mode changes require POSIX")
                path.parent.mkdir(parents=True, exist_ok=True)
                data = base64.b64decode(op["content"], validate=True) if op.get("encoding") == "base64" else op["content"].encode("utf-8")
                path.write_bytes(data)
                if os.name != "nt" and "executable" in op:
                    mode = path.stat().st_mode
                    path.chmod(mode | 0o111 if desired else mode & ~0o111)
        return
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
            if any(b"\0" in checked(root, p).read_bytes() for root, tree in ((base, a), (candidate, b)) if p in tree):
                raise UnicodeError("Binary NUL")
            old = checked(base, p).read_text(encoding="utf-8").splitlines(keepends=True) if p in a else []
            new = checked(candidate, p).read_text(encoding="utf-8").splitlines(keepends=True) if p in b else []
            chunks.extend(difflib.unified_diff(old, new, fromfile="a/"+p, tofile="b/"+p))
        except UnicodeError:
            chunks.append(f"Binary file changed: {p}\n")
        if p in a and p in b and a[p]["executable"] != b[p]["executable"]:
            chunks.append(f"Executable mode changed: {p}\n")
    return changed, "".join(chunks)


def change_details(base, candidate):
    a, b = manifest(base), manifest(candidate)
    changed = sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p))
    removed = {p for p in changed if p not in b}
    added = {p for p in changed if p not in a}
    result = []
    for old in sorted(removed):
        matches = sorted(p for p in added if b[p]["sha256"] == a[old]["sha256"] and b[p]["executable"] == a[old]["executable"])
        if matches:
            new = matches[0]
            added.remove(new)
            removed.remove(old)
            result.append({"action": "rename", "path": old, "target": new, "detection": "exact-content", "before": a[old], "after": b[new]})
    renamed = {x["path"] for x in result} | {x["target"] for x in result}
    for p in changed:
        if p in renamed:
            continue
        action = "delete" if p not in b else "add" if p not in a else "modify"
        source = checked(candidate if p in b else base, p)
        data = source.read_bytes()
        try:
            data.decode("utf-8")
            binary = b"\0" in data
        except UnicodeError:
            binary = True
        result.append({"action": action, "path": p, "binary": binary, "before": a.get(p), "after": b.get(p)})
    return result


def replay_changes(base, candidate, target, allowed):
    changed, _ = diff(base, candidate)
    if not set(changed) <= set(allowed):
        raise OrchError("Integration exceeds approved scope")
    # All paths are checked before any file is touched; candidate/source snapshots stay separate.
    for name in changed:
        checked(candidate, name)
        checked(target, name)
    for name in changed:
        source, dest = checked(candidate, name), checked(target, name)
        if source.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        elif dest.is_file():
            dest.unlink()


def make_fixture(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    (root / "stats.py").write_text("def total(values):\n    return sum(values) if values else None\n", encoding="utf-8")
    (root / "README.txt").write_text("Synthetic non-sensitive fixture. total([]) must be 0.\n", encoding="utf-8")
    return root
