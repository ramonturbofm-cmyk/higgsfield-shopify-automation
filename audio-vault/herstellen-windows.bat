@echo off
rem Een backup terugzetten: database + ontbrekende audiobestanden.
cd /d "%~dp0"
call compose-files.cmd
echo Beschikbare backups:
echo.
docker compose %FILES% run --rm --entrypoint sh backup -c "ls -1 /backup/database"
echo.
set /p DUMP=Typ de naam van de backup die je wilt terugzetten: 
if "%DUMP%"=="" exit /b 1
echo.
echo LET OP: de huidige database wordt vervangen door %DUMP%.
set /p OK=Typ JA om door te gaan: 
if /i not "%OK%"=="JA" exit /b 1
docker compose %FILES% stop app
docker compose %FILES% run --rm --entrypoint sh backup -c "pg_restore -h db -U onair -d onair --clean --if-exists --no-owner /backup/database/%DUMP%"
docker compose %FILES% run --rm --entrypoint sh -v "%cd%\data\audio:/restore" backup -c "mkdir -p /restore/files && cp -n /backup/audio/* /restore/files/"
docker compose %FILES% start app
echo.
echo Klaar. De backup %DUMP% is teruggezet.
pause
