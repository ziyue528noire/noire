@echo off
rem Scheduled task: Mon-Fri 9:45-15:45 hourly monitor (read-only)
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "NOIRE_PROFILE="
if not exist logs mkdir logs
call _python.bat monitor.py > logs\monitor_last_run.txt 2>&1
