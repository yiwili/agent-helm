"""PyInstaller entry. No credentials, runtime state or user configuration bundled."""
from agent_orch.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
