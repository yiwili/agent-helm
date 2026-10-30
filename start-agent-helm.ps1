# Agent Helm One-Click Launcher (GUI Studio / MCP Server / CLI)
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Position = 0)]
    [ValidateSet("gui", "tauri", "mcp", "cli")]
    [string]$Mode = "gui",
    [int]$Port = 1420,
    [switch]$NoBrowser,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
$DesktopDir = Join-Path $RepoRoot "desktop"

# 1. Resolve Python 3.11+ runtime
$PythonExe = $env:AGENT_ORCH_PYTHON
if (-not $PythonExe) {
    $CodexPy = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path -LiteralPath $CodexPy) {
        $PythonExe = $CodexPy
    } else {
        $PythonExe = "python"
    }
}

# 2. Resolve Node.js runtime
$NodeExe = $env:AGENT_ORCH_NODE
if (-not $NodeExe) {
    $CodexNode = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    if (Test-Path -LiteralPath $CodexNode) {
        $NodeExe = $CodexNode
    } else {
        $NodeCmd = Get-Command node -ErrorAction SilentlyContinue
        if ($NodeCmd) { $NodeExe = $NodeCmd.Source }
    }
}

# 3. Ensure Qoder CN CLI is in session PATH if installed
$QoderDir = Join-Path $env:USERPROFILE ".qoder-cn\bin\qoderclicn"
if ((Test-Path -LiteralPath $QoderDir) -and ($env:PATH -notlike "*$QoderDir*")) {
    $env:PATH = "$QoderDir;$env:PATH"
}

$StateRoot = if ($env:AGENT_ORCH_STATE) { $env:AGENT_ORCH_STATE } else { Join-Path $env:LOCALAPPDATA "agent-helm" }
$WebDistDir = Join-Path $StateRoot "web-dist"

$PrevPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $RepoRoot "src"

    if ($Mode -eq "cli") {
        & $PythonExe -m agent_orch --state-root $StateRoot @ExtraArgs
        exit $LASTEXITCODE
    }

    if ($Mode -eq "mcp") {
        Write-Host "[Agent Helm] Starting MCP server (Qoder CN enabled: deepseek-flash, 1m, high)..." -ForegroundColor Cyan
        & $PythonExe -m agent_orch --state-root $StateRoot mcp `
            --enable-qoder-cn `
            --qoder-cn-model deepseek-flash `
            --qoder-cn-context-window 1m `
            --qoder-cn-reasoning-effort high `
            @ExtraArgs
        exit $LASTEXITCODE
    }

    if ($Mode -eq "tauri") {
        if (-not $NodeExe) { throw "Node.js runtime not found." }
        $env:AGENT_HELM_PYTHON = $PythonExe
        & $NodeExe (Join-Path $DesktopDir "scripts\desktop.mjs") dev
        exit $LASTEXITCODE
    }

    # Default Mode: "gui" (Builds Apple-style frontend to external state root and starts Studio server + browser)
    if ($NodeExe -and (Test-Path -LiteralPath (Join-Path $DesktopDir "node_modules\vite\bin\vite.js"))) {
        Write-Host "[Agent Helm] Building Apple-style UI assets to $WebDistDir ..." -ForegroundColor Cyan
        $env:AGENT_HELM_WEB_DIST = $WebDistDir
        Push-Location $DesktopDir
        try {
            & $NodeExe "node_modules\vite\bin\vite.js" build --logLevel warn
        } finally {
            Pop-Location
        }
    } elseif (-not (Test-Path -LiteralPath (Join-Path $WebDistDir "index.html"))) {
        throw "Frontend assets not found and Node.js is unavailable to build them."
    }

    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  Agent Helm Studio (Apple Design Edition)" -ForegroundColor Green
    Write-Host "  URL        : http://127.0.0.1:$Port" -ForegroundColor Green
    Write-Host "  State Root : $StateRoot" -ForegroundColor Green
    Write-Host "  Press Ctrl+C to stop the service." -ForegroundColor Gray
    Write-Host "============================================================" -ForegroundColor Green

    $OpenArgs = if ($NoBrowser) { @() } else { @("--open") }
    & $PythonExe -m agent_orch --state-root $StateRoot desktop --http --web-dir $WebDistDir --port $Port @OpenArgs @ExtraArgs
    exit $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $PrevPythonPath
}
