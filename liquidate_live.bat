@echo off
rem REAL MONEY: stop automatic trading, cancel open orders and SELL ALL positions at market
rem (next open if the market is closed). The user must type SELL. resume_live.bat turns trading back on.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set NOIRE_PROFILE=live
echo ================================================================
echo   LIVE (REAL MONEY) account:
echo   - stop automatic trading (STOP file + disable Noire Live Rotation)
echo   - cancel all open orders
echo   - SELL ALL positions at market (next open if the market is closed)
echo   Automatic trading stays OFF until you double-click resume_live.bat
echo ================================================================
set "OK="
set /p "OK=Type SELL (capital letters) and press Enter to continue: "
if not "%OK%"=="SELL" goto notdone
if not exist live\logs mkdir live\logs
call _python.bat emergency.py liquidate
goto end
:notdone
echo Nothing was sold.
:end
echo.
pause
