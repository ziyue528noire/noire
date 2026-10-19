@echo off
rem REAL MONEY: undo emergency_stop_live.bat (remove live\STOP, re-enable the Noire Live Rotation task).
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set NOIRE_PROFILE=live
call _python.bat emergency.py resume
echo.
pause
