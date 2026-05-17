# Windows build — run on Windows (PowerShell). Produces packaging\dist\SalesRetro\.
# Prereq: Python 3.10+ on the build machine (NOT on the end user's machine).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m venv .buildenv
.\.buildenv\Scripts\python -m pip install --upgrade pip
# Thin backend deps only (NO sounddevice/PortAudio — §6 step 1 decoupling).
.\.buildenv\Scripts\python -m pip install "..\src" pyinstaller
.\.buildenv\Scripts\pyinstaller sales_retro.spec --noconfirm

Write-Host "`nPyInstaller done: packaging\dist\SalesRetro\"

# Wrap into a single setup.exe if Inno Setup's ISCC is available.
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($iscc) {
    & $iscc installer.iss
    Write-Host "`nDone. Installer: packaging\Output\SalesRetro-Setup.exe"
} else {
    Write-Host "`nInno Setup (ISCC.exe) not found — skipped setup.exe."
    Write-Host "Install Inno Setup 6, then run: ISCC.exe installer.iss"
    Write-Host "Meanwhile you can ship the folder: packaging\dist\SalesRetro\"
}
