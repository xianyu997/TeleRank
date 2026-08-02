<#
.SYNOPSIS
    Stops and removes the TG Reaction Ranker Windows service.
.DESCRIPTION
    Must run as Administrator; relaunches itself with a UAC prompt if needed.
    Keeps C:\ProgramData\TelegramReactionRanker data (configs, sessions, logs)
    so a later reinstall keeps your Telegram login.
#>
$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Requesting administrator rights (UAC)..."
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit 0
}

$svc = Get-Service -Name TeleRankService -ErrorAction SilentlyContinue
if ($svc) {
    if ($svc.Status -ne "Stopped") {
        Write-Host "Stopping service..."
        Stop-Service -Name TeleRankService -Force
    }
    sc.exe delete TeleRankService | Out-Null
    Write-Host "Service TeleRankService removed."
} else {
    Write-Host "Service TeleRankService is not installed."
}

try {
    netsh advfirewall firewall delete rule name="TG Reaction Ranker" | Out-Null
} catch {
    Write-Warning "Could not remove firewall rule: $_"
}

Write-Host "Done. Data under C:\ProgramData\TelegramReactionRanker was kept."
