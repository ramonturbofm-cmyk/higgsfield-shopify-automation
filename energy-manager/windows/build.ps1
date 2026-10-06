# Builds the Windows installer on a Windows 10/11 PC.
# Requirements: Python 3.12, Node.js 20+, Rust (rustup), Inno Setup 6, WebView2 (present on Windows 11).
# Usage (PowerShell, in energy-manager\):  .\windows\build.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "== Python: tests + EMS-server (PyInstaller)"
python -m venv .venv-build
.\.venv-build\Scripts\python -m pip install --upgrade pip
.\.venv-build\Scripts\python -m pip install ".[dev]" pyinstaller
.\.venv-build\Scripts\python -m pytest -q --timeout 300
.\.venv-build\Scripts\pyinstaller --noconfirm --clean --distpath dist --workpath build windows\EnergyManagerServer.spec
.\dist\EnergyManagerServer\EnergyManagerServer.exe --selftest
if ($LASTEXITCODE -ne 0) { throw "selftest van de server mislukt" }

Write-Host "== Windows-app (Tauri)"
Push-Location windows-app
npm install
npx tauri icon app-icon.png
npx tauri build --no-bundle
Pop-Location
New-Item -ItemType Directory -Force dist\app | Out-Null
Copy-Item "windows-app\src-tauri\target\release\energy-manager.exe" "dist\app\Energy Manager.exe" -Force

Write-Host "== Installer (Inno Setup)"
$version = (.\.venv-build\Scripts\python -c "import ems; print(ems.__version__)").Trim()
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" "/DAppVersion=$version" windows\installer.iss
Write-Host "Klaar: dist\EnergyManagerSetup-$version.exe"
