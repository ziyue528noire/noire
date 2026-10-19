@echo off
rem LIVE account dry run: shows what it WOULD buy or sell with real money. Places NO orders.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set NOIRE_PROFILE=live
if not exist live\logs mkdir live\logs
call _python.bat paper_rotation.py > live\logs\live_dryrun_log.txt 2>&1
type live\logs\live_dryrun_log.txt
echo.
timeout /t 60
