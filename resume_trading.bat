@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "NOIRE_PROFILE="
if not exist logs mkdir logs
call _python.bat emergency.py resume
echo.
timeout /t 15
