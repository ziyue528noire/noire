@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
(
  echo ===== reinstall + test %date% %time% =====
  call install_schedule.bat
  type schedule_log.txt | findstr /C:"TaskName" /C:"Next Run Time" /C:"Scheduled Task State" /C:"Repeat: Every"
  echo ----- trigger monitor task -----
  schtasks /Run /TN "Noire Monitor"
  echo ----- test notification -----
  call _python.bat monitor.py test
) > test_tasks_log.txt 2>&1
timeout /t 5
