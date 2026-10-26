# Source launcher: no installation, downloads, or authentication changes.
$ErrorActionPreference = 'Stop'
$runtime = $env:AGENT_ORCH_PYTHON
if (-not $runtime) {
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundled) { $runtime = $bundled }
    else { $runtime = 'python' }
}
$previousPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $PSScriptRoot 'src'
    & $runtime -m agent_orch @args
    exit $LASTEXITCODE
}
finally { $env:PYTHONPATH = $previousPath }
