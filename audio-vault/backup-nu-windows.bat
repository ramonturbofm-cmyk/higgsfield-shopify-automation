@echo off
rem Direct een backup maken (database + nieuwe audiobestanden).
cd /d "%~dp0"
call compose-files.cmd
docker compose %FILES% exec backup sh /backup.sh now
pause
