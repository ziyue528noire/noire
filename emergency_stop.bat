@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "NOIRE_PROFILE="
color 4F
echo ==================================================
echo   EMERGENCY STOP (paper account)
echo   - block all trading (STOP file)
echo   - disable the 17:00 auto-rebalance task
echo   - cancel all open orders
echo   - SELL ALL positions at market
echo   Close this window within 10 seconds to cancel.
echo ==================================================
timeout /t 10
if not exist logs mkdir logs
call _python.bat emergency.py stop
echo.
timeout /t 30
