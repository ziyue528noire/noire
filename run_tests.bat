@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist logs mkdir logs
call _python.bat preflight.py > logs\run_tests_log.txt 2>&1
type logs\run_tests_log.txt
timeout /t 15
