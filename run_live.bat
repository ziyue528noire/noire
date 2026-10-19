@echo off
rem Scheduled task "Noire Live Rotation": Mon-Fri 17:10, retry 17:40 / 18:10 (REAL MONEY account).
rem Places no orders unless live trading is turned on (config.py LIVE_ENABLED + enable_live.bat + LIVE_MAX_CAPITAL).
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set NOIRE_PROFILE=live
if not exist live\logs mkdir live\logs
if not exist live\orders mkdir live\orders
call _python.bat paper_job.py >> live\logs\paper_log.txt 2>&1
call _python.bat trade.py status > live\orders\status_after_run.txt 2>&1
echo. >> live\logs\paper_log.txt
