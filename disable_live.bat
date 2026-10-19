@echo off
rem Turn OFF automatic real-money trading (renames live\LIVE_ENABLED; nothing is deleted).
chcp 65001 >nul
cd /d "%~dp0"
if not exist "live\LIVE_ENABLED" goto already
move /Y "live\LIVE_ENABLED" "live\LIVE_ENABLED.off" >nul
echo Real-money trading switch is OFF. Open orders (if any) are not touched;
echo to also cancel them, double-click emergency_stop_live.bat
goto end
:already
echo Real-money trading switch was already off.
:end
echo.
pause
