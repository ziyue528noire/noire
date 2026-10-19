@echo off
rem Scheduled task "Noire Live Monitor": Mon-Fri 9:47-15:47 hourly (REAL MONEY account, read-only)
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set NOIRE_PROFILE=live
if not exist live\logs mkdir live\logs
call _python.bat monitor.py > live\logs\monitor_last_run.txt 2>&1
