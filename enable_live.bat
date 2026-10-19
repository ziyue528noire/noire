@echo off
rem Turn ON automatic REAL-MONEY trading. Only the user can do this (must type YES).
chcp 65001 >nul
cd /d "%~dp0"
if not exist live mkdir live
echo ================================================================
echo   This turns ON automatic REAL-MONEY trading (Alpaca live account).
echo   Limits: LIVE_MAX_CAPITAL and LIVE_MAX_LEVERAGE in config.py
echo   (config.py must also say LIVE_ENABLED = True).
echo   To turn it off again: double-click disable_live.bat
echo ================================================================
echo.
set "OK="
set /p "OK=Type YES (capital letters) and press Enter to turn it on: "
if not "%OK%"=="YES" goto notdone
> "live\LIVE_ENABLED" echo enabled by the user on %date% %time%
echo.
echo Real-money trading switch is ON.
goto end
:notdone
echo.
echo Not turned on (nothing changed).
:end
echo.
pause
