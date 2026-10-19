@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "NOIRE_PROFILE="
echo ================================================
echo   Alpaca paper-trading key setup
echo   Keys are saved ONLY to this folder (.env).
echo   Right-click in this window to paste.
echo ================================================
echo.
set /p "AK=Paste your API Key, then press Enter: "
set /p "AS=Paste your API Secret, then press Enter: "
> ".env" echo ALPACA_KEY=%AK%
>> ".env" echo ALPACA_SECRET=%AS%
set "AK="
set "AS="
cls
echo Keys saved to .env  -  testing connection (read-only, no orders)...
echo.

set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if "%PY%"=="py -3" (
  py -3 test_connection.py > connection_log.txt 2>&1
) else (
  "%PY%" test_connection.py > connection_log.txt 2>&1
)
type connection_log.txt
echo.
pause
