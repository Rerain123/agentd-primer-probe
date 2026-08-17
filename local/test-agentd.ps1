[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$baseUrl = "http://127.0.0.1:$Port"
$routes = @{
    "WF-01" = "wf01/design"
    "WF-02" = "wf02/multiplex"
    "WF-03" = "wf03/internal-control"
    "WF-04" = "wf04/validate"
    "WF-05" = "wf05/variation"
    "WF-06" = "wf06/complete-panel"
    "WF-07" = "wf07/diagnose"
}

$health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 5
if ($health.service -ne "agentd-primer-probe") {
    throw "The service on port $Port is not Agent D."
}

$summary = foreach ($index in 1..7) {
    $workflow = "WF-{0:D2}" -f $index
    $requestPath = Join-Path $projectRoot ("examples\wf{0:D2}-request.json" -f $index)
    $body = Get-Content -LiteralPath $requestPath -Encoding utf8 -Raw
    $result = Invoke-RestMethod `
        -Uri "$baseUrl/v1/$($routes[$workflow])" `
        -Method Post `
        -ContentType "application/json; charset=utf-8" `
        -Body $body
    $saved = Invoke-RestMethod -Uri "$baseUrl/v1/jobs/$($result.job_id)"
    [pscustomobject]@{
        Workflow = $result.workflow
        Http = 200
        Status = $result.status
        Evidence = $result.evidence_level
        Candidates = @($result.candidates).Count
        Errors = @($result.errors).Count
        Persisted = $saved.job_id -eq $result.job_id
    }
}

$summary | Format-Table -AutoSize
if (@($summary | Where-Object { $_.Errors -gt 0 -or -not $_.Persisted }).Count -gt 0) {
    throw "At least one local workflow failed the smoke test."
}
Write-Host "All seven local workflows passed. PARTIAL means external scientific evidence is incomplete."
