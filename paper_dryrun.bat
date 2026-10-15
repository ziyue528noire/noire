@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if "%PY%"=="py -3" (
  py -3 paper_rotation.py > paper_dryrun_log.txt 2>&1
) else (
  "%PY%" paper_rotation.py > paper_dryrun_log.txt 2>&1
)
type paper_dryrun_log.txt
echo.
pause
