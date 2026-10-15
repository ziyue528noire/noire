@echo off
rem One-off: let Excaliber scheduled tasks wake the PC and run on battery. Safe to run again.
chcp 65001 >nul
cd /d "%~dp0"
echo ===== fix_schedule %date% %time% ===== > fix_schedule_log.txt
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix_schedule.ps1" >> fix_schedule_log.txt 2>&1
echo ----- power settings (read only, not changed) ----- >> fix_schedule_log.txt
powercfg /q SCHEME_CURRENT SUB_SLEEP STANDBYIDLE >> fix_schedule_log.txt 2>&1
powercfg /q SCHEME_CURRENT SUB_SLEEP RTCWAKE >> fix_schedule_log.txt 2>&1
type fix_schedule_log.txt
echo.
echo Done. Results saved in fix_schedule_log.txt
pause
