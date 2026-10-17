@echo off
rem One-time (2026-10-16): second leg (RSI2 dip buy) + hardening + tracking tools.
rem Runs the safety tests, a dry run (no orders; writes reports\paper_status_dryrun.txt),
rem the read-only monitor, and a git commit. Writes logs\apply_dip_log.txt
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist logs mkdir logs
set "LOG=logs\apply_dip_log.txt"
echo ===== apply_dip %date% %time% ===== > "%LOG%"
echo ----- 1. safety tests ----- >> "%LOG%"
call _python.bat preflight.py >> "%LOG%" 2>&1
if errorlevel 1 goto testsfailed
echo ----- 2. dry run, no orders ----- >> "%LOG%"
call _python.bat paper_rotation.py >> "%LOG%" 2>&1
echo ----- 3. monitor, read only ----- >> "%LOG%"
call _python.bat monitor.py >> "%LOG%" 2>&1
echo ----- 4. git commit ----- >> "%LOG%"
call _git.bat add -A >> "%LOG%" 2>&1
call _git.bat commit -q -F logs\commit_msg_dip.txt >> "%LOG%" 2>&1
call _git.bat log --oneline -3 >> "%LOG%" 2>&1
echo ===== done ===== >> "%LOG%"
goto end
:testsfailed
echo ===== TESTS FAILED: nothing committed; daily trading will refuse to run until fixed ===== >> "%LOG%"
:end
type "%LOG%"
timeout /t 20
