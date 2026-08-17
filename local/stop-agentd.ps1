[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $projectRoot
$pidFile = Join-Path $workspaceRoot "agentd-runtime\local-agentd.pid"

if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
    Write-Host "No local Agent D PID file was found."
    return
}

$processId = [int](Get-Content -LiteralPath $pidFile -Raw)
$process = Get-Process -Id $processId -ErrorAction SilentlyContinue
if ($null -ne $process) {
    Stop-Process -Id $processId -Force
    Write-Host "Agent D local service stopped. PID=$processId"
} else {
    Write-Host "Agent D was already stopped. Removing stale PID file."
}
Remove-Item -LiteralPath $pidFile -Force
