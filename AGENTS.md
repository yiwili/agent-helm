# Project rules

- Test only this project and newly generated, non-sensitive synthetic fixtures. Never use company business repositories, business source code, backups, or credentials as experimental data.
- Keep runtime databases, logs, raw provider output, evidence bundles, and temporary workspaces outside this repository. Never commit personal absolute paths, account secrets, or private run evidence.
- Runtime and default tests use the Python standard library. Run `PYTHONPATH=src python -m unittest discover -s tests -v` (set PYTHONPATH appropriately in PowerShell).
- Default tests must not invoke real providers, consume model quota, change authentication, or operate desktop apps. `scripts/verify_codex_lifecycle.py` is an explicit opt-in live smoke on generated fixtures.
- `scripts/verify_git_workspace.py` only creates new synthetic Git repositories; real Codex requires `--real-codex`. Never substitute a company repository for those fixtures. Registered acceptance programs must be outside both the source workspace and mutable state root and must stay hash-pinned.
- `scripts/verify_cross_provider.py` is an explicit opt-in live Codex/Qoder CN brain with Codex/Qoder CN/Cursor workers on newly generated fixtures. Its optional first fault is injected by the test harness, never evidence of model failure. Output and dedicated Cursor config must be outside this repository; do not add paid API calls to default tests or automatic fallback.
- Keep real providers disabled by default. Do not add API fallback, broaden permissions, merge, push, or publish without user authorization.
- Preserve existing user changes. Unknown process effects or lost leases require reconciliation, never automatic retry. Keep public documentation clear about local evidence versus real provider behavior.
