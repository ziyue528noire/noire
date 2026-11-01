@echo off
rem Show the LIVE account (equity, positions, orders). Read-only.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set NOIRE_PROFILE=live
if not exist live\orders mkdir live\orders
rem trade.py writes live\orders\status.txt itself; the console log goes to another file
rem (redirecting into status.txt too made Windows raise PermissionError at the end)
call _python.bat trade.py status > live\orders\last_run_log.txt 2>&1
type live\orders\last_run_log.txt
echo.
timeout /t 60
