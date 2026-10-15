@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Creating 2 Windows scheduled tasks (paper account only):
echo  1. Noire Paper Rotation : Mon-Fri 17:00
echo  2. Noire Monitor        : Mon-Fri 09:45-15:45 every hour
echo.
schtasks /Create /TN "Noire Paper Rotation" /TR "wscript.exe //B \"%~dp0run_hidden.vbs\" \"%~dp0run_paper.bat\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 17:00 /F
schtasks /Create /TN "Noire Monitor" /TR "wscript.exe //B \"%~dp0run_hidden.vbs\" \"%~dp0run_monitor.bat\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 09:45 /RI 60 /DU 06:01 /F
echo.
(
  schtasks /Query /TN "Noire Paper Rotation" /V /FO LIST
  schtasks /Query /TN "Noire Monitor" /V /FO LIST
) > schedule_log.txt 2>&1
echo Done. Details in schedule_log.txt
