@echo off
rem Save the Alpaca LIVE (real money) keys to .env.live and test the connection (read-only, no orders).
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
echo ================================================================
echo   Alpaca LIVE (REAL MONEY) key setup
echo   Use the keys from the LIVE account page - NOT the paper keys.
echo   Keys are saved ONLY to this folder (.env.live). Never share them.
echo   Right-click in this window to paste.
echo ================================================================
echo.
set /p "AK=Paste your LIVE API Key, then press Enter: "
set /p "AS=Paste your LIVE API Secret, then press Enter: "
> ".env.live" echo ALPACA_LIVE_KEY=%AK%
>> ".env.live" echo ALPACA_LIVE_SECRET=%AS%
set "AK="
set "AS="
cls
echo Keys saved to .env.live  -  testing connection (read-only, no orders)...
echo.
if not exist live mkdir live
set NOIRE_PROFILE=live
call _python.bat test_connection.py > live\connection_log.txt 2>&1
type live\connection_log.txt
echo.
pause
