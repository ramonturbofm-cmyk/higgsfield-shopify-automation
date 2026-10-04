@echo off
rem Muziek van de NAS importeren. Elke hoofdmap (bijv. Muziek, Jingles) wordt een collectie.
rem Opnieuw draaien slaat al geimporteerde bestanden over.
cd /d "%~dp0"
findstr /b /c:"NAS_PASSWORD=" .env | findstr /r /c:"=.." >nul
if errorlevel 1 (
  docker compose exec -u node app node src/import.js /import --per-folder --collection Muziek
) else (
  docker compose -f docker-compose.yml -f docker-compose.nas.yml exec -u node app node src/import.js /import --per-folder --collection Muziek
)
pause
