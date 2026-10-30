$ErrorActionPreference = 'Stop'
$runtime = $env:AGENT_ORCH_PYTHON
if (-not $runtime) {
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundled) { $runtime = $bundled } else { $runtime = 'python' }
}
& $runtime (Join-Path $PSScriptRoot 'scripts\build.py') @args
exit $LASTEXITCODE
