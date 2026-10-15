@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "LOG=%~dp0setup_log.txt"
echo [setup] start %date% %time% > "%LOG%"

set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY (
  echo Installing Python 3.12 with winget, please wait...
  echo [setup] installing python >> "%LOG%"
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements >> "%LOG%" 2>&1
)
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY (
  echo [setup] FAILED: python not found after install >> "%LOG%"
  echo Python install failed. See setup_log.txt
  pause
  exit /b 1
)

echo Using Python: %PY%
echo Installing libraries...
if "%PY%"=="py -3" (
  py -3 --version >> "%LOG%" 2>&1
  py -3 -m pip install -r requirements.txt >> "%LOG%" 2>&1
  echo Running backtest...
  py -3 backtest.py >> "%LOG%" 2>&1
) else (
  "%PY%" --version >> "%LOG%" 2>&1
  "%PY%" -m pip install -r requirements.txt >> "%LOG%" 2>&1
  echo Running backtest...
  "%PY%" backtest.py >> "%LOG%" 2>&1
)
echo [setup] done %date% %time% >> "%LOG%"
echo.
type "%LOG%"
echo.
echo ===== DONE =====
pause
