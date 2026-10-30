"""Source and frozen launch commands, without arbitrary Python flags in bundles."""
import sys


def frozen():
    return bool(getattr(sys, "frozen", False))


def entry_command():
    return [sys.executable] if frozen() else [sys.executable, "-m", "agent_orch"]
