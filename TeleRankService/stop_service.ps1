<#
.SYNOPSIS
    Stops the TG Reaction Ranker service / background instance.
.DESCRIPTION
    - If TeleRankService is installed as a Windows service, stops it via SCM.
    - Otherwise sends a shutdown request to the local HTTP server
      (double-click / manual mode), using the port from service-info.json.
#>
$ErrorActionPreference = "Stop"

$svc = Get-Service -Name TeleRankService -ErrorAction SilentlyContinue
if ($svc) {
    if ($svc.Status -ne "Stopped") {
        Write-Host "Stopping service TeleRankService..."
        Stop-Service -Name TeleRankService -Force
    }
    Write-Host "Service TeleRankService is stopped."
    exit 0
}

# Manual (double-click) mode: find the running instance and shut it down.
$infoCandidates = @()
try {
    $reg = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\TeleRankService\Parameters" -ErrorAction SilentlyContinue
    if ($reg.DataDir) { $infoCandidates += (Join-Path $reg.DataDir "service-info.json") }
} catch {}
$infoCandidates += @(
    "C:\ProgramData\TelegramReactionRanker\service-info.json"
)

$port = $null
foreach ($candidate in ($infoCandidates | Select-Object -Unique)) {
    if (Test-Path -LiteralPath $candidate) {
        try {
            $info = Get-Content -LiteralPath $candidate -Encoding UTF8 | ConvertFrom-Json
            if ($info.state -eq "running" -and $info.port) { $port = [int]$info.port; break }
        } catch {}
    }
}

if (-not $port) {
    foreach ($p in 1717..1721) {
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$p/" -UseBasicParsing -TimeoutSec 1
            if ($resp.Headers["X-TG-Ranker-Version"]) { $port = $p; break }
        } catch {}
    }
}

if ($port) {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$port/__shutdown" -Method POST -UseBasicParsing -TimeoutSec 5 | Out-Null
        Write-Host "Shutdown request sent to port $port."
    } catch {
        Write-Host "Failed to stop the instance on port $port."
    }
} else {
    Write-Host "No running TG Reaction Ranker found (nothing to stop)."
}
