@echo off
rem Audio OnAir Turbo starten op Windows (vereist Docker Desktop).
cd /d "%~dp0"
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop draait niet. Start Docker Desktop en probeer het opnieuw.
  pause
  exit /b 1
)
if not exist .env (
  copy docker.env.example .env >nul
  echo Er is een bestand .env aangemaakt. Vul daarin de wachtwoorden en NAS-gegevens in,
  echo sla op en start dit bestand daarna opnieuw.
  notepad .env
  exit /b 0
)
findstr /b /c:"NAS_PASSWORD=" .env | findstr /r /c:"=.." >nul
if errorlevel 1 (
  docker compose up -d --build
) else (
  docker compose -f docker-compose.yml -f docker-compose.nas.yml up -d --build
)
if errorlevel 1 ( pause & exit /b 1 )
echo.
echo Audio OnAir Turbo draait. De browser opent nu.
timeout /t 5 >nul
start "" http://localhost:3000
