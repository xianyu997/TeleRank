<#
.SYNOPSIS
    Installs and starts TG Reaction Ranker as a Windows background service.
.DESCRIPTION
    Must run as Administrator. If the current shell is not elevated, the script
    relaunches itself with a UAC prompt.
.PARAMETER Port
    TCP port the service listens on (LAN + localhost). Default 1717.
.PARAMETER DataDir
    Folder for preferences.json / telegram-sync.json / session / logs.
    Default C:\ProgramData\TelegramReactionRanker.
.PARAMETER ArchiveRoot
    Telegram import archive root. Default D:\TelegramReactionRanker\Imports.
#>
param(
    [int]$Port = 1717,
    [string]$DataDir = "C:\ProgramData\TelegramReactionRanker",
    [string]$ArchiveRoot = "D:\TelegramReactionRanker\Imports",
    [string]$TrashRoot = "D:\TelegramReactionRanker\DeletedImports"
)
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Exe = Join-Path $Root "TeleRankService.exe"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Requesting administrator rights (UAC)..."
    $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Port $Port -DataDir `"$DataDir`" -ArchiveRoot `"$ArchiveRoot`" -TrashRoot `"$TrashRoot`""
    Start-Process powershell.exe -Verb RunAs -ArgumentList $argList
    exit 0
}

if (-not (Test-Path $Exe)) {
    throw "EXE not found: $Exe`nRun build_service.ps1 first."
}

$svc = Get-Service -Name TeleRankService -ErrorAction SilentlyContinue
if ($svc) {
    Write-Host "Service TeleRankService already exists - reinstalling."
    if ($svc.Status -ne "Stopped") {
        Stop-Service -Name TeleRankService -Force
    }
    sc.exe delete TeleRankService | Out-Null
    Start-Sleep -Milliseconds 800
}

Write-Host "Creating service $Exe ..."
New-Service -Name TeleRankService `
    -BinaryPathName ('"' + $Exe + '"') `
    -DisplayName "TG Reaction Ranker Service" `
    -Description "Background web + Telegram import service for TG Reaction Ranker." `
    -StartupType Automatic | Out-Null

Write-Host "Writing service parameters..."
$regPath = "HKLM:\SYSTEM\CurrentControlSet\Services\TeleRankService\Parameters"
New-Item -Path $regPath -Force | Out-Null
New-ItemProperty -Path $regPath -Name Port -Value $Port -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $regPath -Name DataDir -Value $DataDir -PropertyType String -Force | Out-Null
New-ItemProperty -Path $regPath -Name ArchiveRoot -Value $ArchiveRoot -PropertyType String -Force | Out-Null
New-ItemProperty -Path $regPath -Name TrashRoot -Value $TrashRoot -PropertyType String -Force | Out-Null

# Best-effort firewall rule so LAN devices can reach the service
try {
    netsh advfirewall firewall delete rule name="TG Reaction Ranker" | Out-Null
    netsh advfirewall firewall add rule name="TG Reaction Ranker" dir=in action=allow protocol=TCP localport=$Port | Out-Null
    Write-Host "Firewall rule added for TCP $Port."
} catch {
    Write-Warning "Could not configure firewall rule: $_"
}

Write-Host "Starting service..."
Start-Service -Name TeleRankService
Start-Sleep -Seconds 3
sc.exe query TeleRankService

$info = Join-Path $DataDir "service-info.json"
if (Test-Path $info) {
    Write-Host ""
    Get-Content $info -Encoding UTF8
}

Write-Host ""
Write-Host "Done. Open http://127.0.0.1:$Port/ in your browser (LAN: use the lan_url above)."
