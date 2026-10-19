@echo off
rem Scheduled task: Mon-Fri 17:00 / 17:30 / 18:00 (paper account only).
rem paper_job.py skips if today's run already succeeded; only the 3rd failure raises an alert.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "NOIRE_PROFILE="
if not exist logs mkdir logs
call _python.bat versions.py save auto-snapshot-before-daily-run >> logs\paper_log.txt 2>&1
call _git.bat --version >nul 2>&1
if not errorlevel 1 (
  call _git.bat add -A >nul 2>&1
  call _git.bat commit -q -m "daily auto snapshot before run" >nul 2>&1
)
call _python.bat paper_job.py >> logs\paper_log.txt 2>&1
call _python.bat trade.py status > orders\status_after_run.txt 2>&1
echo. >> logs\paper_log.txt
