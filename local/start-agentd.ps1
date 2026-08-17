[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $projectRoot
$agentd = Join-Path $projectRoot ".venv\Scripts\agentd.exe"
$runtimeRoot = Join-Path $workspaceRoot "agentd-runtime"
$stateDirectory = Join-Path $runtimeRoot "local-jobs"
$pidFile = Join-Path $runtimeRoot "local-agentd.pid"
$baseUrl = "http://127.0.0.1:$Port"

if (-not (Test-Path -LiteralPath $agentd -PathType Leaf)) {
    throw "Local environment not found: $agentd. Install project dependencies first."
}

$health = $null
try {
    $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 2
} catch [System.Net.WebException] {
    $health = $null
}
if ($null -ne $health) {
    if ($health.service -eq "agentd-primer-probe") {
        Write-Host "Agent D is already running: $baseUrl"
        if (-not $NoBrowser) {
            Start-Process "$baseUrl/"
        }
        return
    }
    throw "Port $Port is occupied by another service."
}

New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
$arguments = @(
    "--state-dir", "`"$stateDirectory`"",
    "serve",
    "--host", "127.0.0.1",
    "--port", "$Port"
)
$process = Start-Process `
    -FilePath $agentd `
    -ArgumentList $arguments `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -PassThru
[IO.File]::WriteAllText($pidFile, "$($process.Id)", [Text.Encoding]::ASCII)

$ready = $false
foreach ($attempt in 1..30) {
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) {
        throw "Agent D exited during startup with code $($process.ExitCode)."
    }
    try {
        $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 2
        if ($health.service -eq "agentd-primer-probe") {
            $ready = $true
            break
        }
    } catch {
        # Continue waiting until the bounded startup timeout is reached.
    }
}

if (-not $ready) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    throw "Agent D did not become ready within 15 seconds."
}

Write-Host "Agent D local service started: $baseUrl"
Write-Host "Local UI: $baseUrl/"
Write-Host "API docs: $baseUrl/docs"
Write-Host "Local job directory: $stateDirectory"
if (-not $NoBrowser) {
    Start-Process "$baseUrl/"
}
