@echo off
rem REAL MONEY emergency stop: stop automatic trading + cancel open orders. Positions are NOT sold.
rem (To also sell everything, double-click liquidate_live.bat.)
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set NOIRE_PROFILE=live
if not exist live\logs mkdir live\logs
call _python.bat emergency.py stop
echo.
pause
