@echo off
rem Create the 2 Windows scheduled tasks for the REAL MONEY account (they place no orders until live trading is turned on).
chcp 65001 >nul
cd /d "%~dp0"
if not exist live mkdir live
echo Creating 2 Windows scheduled tasks for the LIVE (real money) account:
echo  1. Noire Live Rotation : Mon-Fri 17:10, retry 17:40 and 18:10 if needed
echo  2. Noire Live Monitor  : Mon-Fri 09:47-15:47 every hour (read-only)
echo.
schtasks /Create /TN "Noire Live Rotation" /TR "wscript.exe //B \"%~dp0run_hidden.vbs\" \"%~dp0run_live.bat\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 17:10 /RI 30 /DU 01:01 /F
schtasks /Create /TN "Noire Live Monitor" /TR "wscript.exe //B \"%~dp0run_hidden.vbs\" \"%~dp0run_live_monitor.bat\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 09:47 /RI 60 /DU 06:01 /F
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0task_settings.ps1" -Live
echo.
(
  schtasks /Query /TN "Noire Live Rotation" /V /FO LIST
  schtasks /Query /TN "Noire Live Monitor" /V /FO LIST
) > live\schedule_log.txt 2>&1
echo Done. Details in live\schedule_log.txt
timeout /t 60
