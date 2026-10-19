@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "NOIRE_PROFILE="
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if "%PY%"=="py -3" (
  py -3 trade.py status > orders\last_run_log.txt 2>&1
) else (
  "%PY%" trade.py status > orders\last_run_log.txt 2>&1
)
type orders\last_run_log.txt
timeout /t 10
