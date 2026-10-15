@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist logs mkdir logs
set "LOG=logs\apply_retry_log.txt"
echo ===== apply retry %date% %time% ===== > "%LOG%"
call install_schedule.bat >> "%LOG%" 2>&1
echo ----- rotation task ----- >> "%LOG%"
schtasks /Query /TN "Noire Paper Rotation" /FO LIST /V | findstr /I /C:"Next Run" /C:"Status" /C:"Repeat" /C:"Task To Run" >> "%LOG%" 2>&1
echo ----- git ----- >> "%LOG%"
call _git.bat add -A >> "%LOG%" 2>&1
call _git.bat commit -q -m "Limited auto-retry for daily rebalance (17:00/17:30/18:00)" >> "%LOG%" 2>&1
call _git.bat log --oneline -3 >> "%LOG%" 2>&1
echo ----- preflight ----- >> "%LOG%"
call _python.bat preflight.py >> "%LOG%" 2>&1
echo ===== done ===== >> "%LOG%"
type "%LOG%"
timeout /t 15
