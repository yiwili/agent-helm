"""Trusted local user registration; no model-supplied command execution."""
import hashlib
import json
import re
from pathlib import Path

from .models import OrchError, canonical, digest, strict, relpath


def profile_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        raise OrchError("Invalid verifier profile ID")
    relpath(value+".json")
    return value


def file_hash(path):
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise OrchError("Verifier requires an existing absolute regular file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def builtin_profile():
    source = Path(__file__).with_name("verifier.py")
    return {"schema_version": 1, "id": "fixture-stats", "kind": "builtin", "code_hash": file_hash(source.resolve())}


def register_profile(state_root, ident, definition):
    ident = profile_id(ident)
    if ident == "fixture-stats":
        raise OrchError("Cannot replace builtin verifier")
    strict(definition, ("schema_version", "argv", "pinned_files", "timeout"))
    if type(definition["schema_version"]) is not int or definition["schema_version"] != 1 or type(definition["timeout"]) not in (int, float) or not 0.1 <= definition["timeout"] <= 120:
        raise OrchError("Verifier timeout must be 0.1..120 seconds")
    argv = definition["argv"]
    if not isinstance(argv, list) or not 2 <= len(argv) <= 30 or any(not isinstance(a, str) or "\0" in a for a in argv) or argv.count("{workspace}") != 1:
        raise OrchError("Verifier requires argv and exactly one workspace argument")
    executable = Path(argv[0])
    if not executable.is_absolute() or executable.suffix.lower() in (".cmd", ".bat"):
        raise OrchError("Verifier executable must be absolute; shell shims are rejected")
    pins = definition["pinned_files"]
    if not isinstance(pins, list) or not pins or any(not isinstance(p, str) for p in pins):
        raise OrchError("External verifier scripts must be explicitly pinned")
    executable = executable.resolve()
    argv = [str(executable), *argv[1:]]
    frozen = {str(Path(p).resolve()): file_hash(p) for p in [str(executable), *pins]}
    if any(Path(p).is_relative_to(Path(state_root).resolve()) for p in frozen):
        raise OrchError("Trusted verifier programs must live outside the mutable state root")
    record = {**definition, "argv": argv, "id": ident, "kind": "registered", "pinned_files": frozen}
    target = Path(state_root)/"verifiers"/(ident+".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise OrchError("Profile is immutable; register a new ID for a changed verifier")
    target.write_text(canonical(record), encoding="utf-8")
    return {"profile": ident, "profile_hash": digest(record)}


def load_profile(state_root, ident):
    profile_id(ident)
    if ident == "fixture-stats":
        return builtin_profile()
    try:
        record = json.loads((Path(state_root)/"verifiers"/(ident+".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise OrchError("Verifier profile is not registered") from e
    if record.get("id") != ident or record.get("kind") != "registered":
        raise OrchError("Malformed registered profile")
    check_profile(record)
    return record


def check_profile(record):
    if record.get("kind") == "builtin":
        if record != builtin_profile():
            raise OrchError("Builtin verifier changed after approval; reapprove")
        return
    if record.get("kind") != "registered" or not isinstance(record.get("pinned_files"), dict):
        raise OrchError("Invalid verifier record")
    strict(record, ("schema_version", "id", "kind", "argv", "pinned_files", "timeout"))
    profile_id(record["id"])
    argv = record["argv"]
    if record["schema_version"] != 1 or not record["pinned_files"] or not isinstance(argv, list) or not 2 <= len(argv) <= 30 or any(not isinstance(a, str) or "\0" in a for a in argv) or argv.count("{workspace}") != 1:
        raise OrchError("Malformed frozen verifier")
    if argv[0] not in record["pinned_files"] or type(record["timeout"]) not in (int, float) or not 0.1 <= record["timeout"] <= 120:
        raise OrchError("Unpinned executable or invalid timeout")
    for path, expected in record["pinned_files"].items():
        if not isinstance(path, str) or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise OrchError("Invalid verifier fingerprint")
        if file_hash(path) != expected:
            raise OrchError("Trusted verifier file changed after registration/approval")
