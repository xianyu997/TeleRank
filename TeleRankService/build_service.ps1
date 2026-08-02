# Builds the TG Reaction Ranker Windows service EXE (TeleRankService).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = "C:\Users\27889\AppData\Local\Programs\Python\Python312\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$Venv = Join-Path $Root ".venv-build"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating build virtualenv at $Venv ..."
    & $Python -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create virtualenv" }
}

Write-Host "Installing build dependencies..."
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install "pyinstaller>=6" "pywin32>=306" "telethon>=1.44,<2" "cryptg>=0.5"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "Building TeleRankService.exe ..."
& $VenvPython -m PyInstaller --noconfirm --clean TeleRankService.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$Exe = Join-Path $Root "dist\TeleRankService\TeleRankService.exe"
if (Test-Path $Exe) {
    # Sync the fresh build to the service folder root so the shipped layout
    # (TeleRankService.exe + _internal next to the scripts) stays self-contained.
    if (-not $Root.EndsWith("TeleRankService")) {
        throw "Unexpected build root: $Root"
    }
    Copy-Item -LiteralPath $Exe -Destination (Join-Path $Root "TeleRankService.exe") -Force
    $rootInternal = Join-Path $Root "_internal"
    if (Test-Path -LiteralPath $rootInternal) {
        Remove-Item -LiteralPath $rootInternal -Recurse -Force
    }
    Copy-Item -LiteralPath (Join-Path $Root "dist\TeleRankService\_internal") -Destination $rootInternal -Recurse
    Write-Host "Synced build to $rootInternal"
    $Exe = Join-Path $Root "TeleRankService.exe"
}
Write-Host ""
Write-Host "Build complete: $Exe"
Write-Host "Next step: run install_service.ps1 from an elevated PowerShell (it will ask via UAC)."
