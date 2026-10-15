@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist logs mkdir logs
if not exist orders mkdir orders
(
  echo ===== setup %date% %time% =====
  call _python.bat versions.py save install-risk-monitor-emergency
  call install_schedule.bat
  echo ----- schedule check -----
  type schedule_log.txt
  echo ----- first monitor run -----
  call _python.bat monitor.py
  echo ===== setup done =====
) > setup_phase1_log.txt 2>&1
type setup_phase1_log.txt
timeout /t 20
