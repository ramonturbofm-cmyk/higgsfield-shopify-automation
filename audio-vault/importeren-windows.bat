@echo off
rem Muziek van de NAS importeren. Elke hoofdmap (bijv. Muziek, Jingles) wordt een collectie.
rem Opnieuw draaien slaat al geimporteerde bestanden over.
cd /d "%~dp0"
call compose-files.cmd
docker compose %FILES% exec -u node app node src/import.js /import --per-folder --collection Muziek
pause
