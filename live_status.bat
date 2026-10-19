@echo off
rem Show the LIVE account (equity, positions, orders). Read-only.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set NOIRE_PROFILE=live
if not exist live\orders mkdir live\orders
call _python.bat trade.py status > live\orders\status.txt 2>&1
type live\orders\status.txt
echo.
timeout /t 60
