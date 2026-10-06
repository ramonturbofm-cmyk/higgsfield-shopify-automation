@echo off
rem Chooses the docker compose files based on what is filled in in .env.
set FILES=-f docker-compose.yml
findstr /b /c:"NAS_PASSWORD=" .env | findstr /r /c:"=.." >nul
if not errorlevel 1 set FILES=%FILES% -f docker-compose.nas.yml
findstr /b /c:"NAS_BACKUP_PASSWORD=" .env | findstr /r /c:"=.." >nul
if not errorlevel 1 set FILES=%FILES% -f docker-compose.nas-backup.yml
findstr /b /c:"NAS_ARCHIVE_PASSWORD=" .env | findstr /r /c:"=.." >nul
if not errorlevel 1 set FILES=%FILES% -f docker-compose.nas-archive.yml
findstr /b /c:"DOMAIN=" .env | findstr /r /c:"=.." >nul
if not errorlevel 1 set FILES=%FILES% -f docker-compose.https.yml
findstr /b /c:"DUCKDNS_TOKEN=" .env | findstr /r /c:"=.." >nul
if not errorlevel 1 set FILES=%FILES% -f docker-compose.duckdns.yml
