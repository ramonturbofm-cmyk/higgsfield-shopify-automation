# Bouwt EnergyManager.exe en de installer op een Windows-pc.
# Vereist: Python 3.11+ (python.org) en Inno Setup 6 (https://jrsoftware.org).
# Gebruik (PowerShell, in de map energy-manager):  .\windows\build.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m venv .venv-build
.\.venv-build\Scripts\python -m pip install --upgrade pip
.\.venv-build\Scripts\python -m pip install . pyinstaller pytest pytest-asyncio

.\.venv-build\Scripts\python -m pytest -q
.\.venv-build\Scripts\pyinstaller --noconfirm --clean --distpath dist --workpath build windows\EnergyManager.spec

# Rooktest van de gebouwde exe
.\dist\EnergyManager.exe --selftest --data-dir "$env:TEMP\EnergyManagerSelfTest"
if ($LASTEXITCODE -ne 0) { throw "Selftest van EnergyManager.exe mislukt" }

$version = (.\.venv-build\Scripts\python -c "import ems; print(ems.__version__)").Trim()
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (Test-Path $iscc) {
    & $iscc "/DAppVersion=$version" windows\installer.iss
    Write-Host "Klaar: dist\EnergyManager-Setup-$version.exe en dist\EnergyManager.exe"
} else {
    Write-Host "Inno Setup niet gevonden; alleen dist\EnergyManager.exe gebouwd (draagbare versie)."
}
