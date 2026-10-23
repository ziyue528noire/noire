@echo off
rem One-time (2026-10-22): commit dashboard_export.py + README section 18.
rem Runs the safety tests and one export first; commits only these 3 files. No orders, no account access. Writes logs\apply_dashboard_log.txt
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "NOIRE_PROFILE="
if not exist logs mkdir logs
set "LOG=logs\apply_dashboard_log.txt"
echo ===== apply_dashboard %date% %time% ===== > "%LOG%"
echo ----- 1. safety tests ----- >> "%LOG%"
call _python.bat preflight.py >> "%LOG%" 2>&1
if errorlevel 1 goto testsfailed
echo ----- 2. dashboard export, read only ----- >> "%LOG%"
call _python.bat dashboard_export.py >> "%LOG%" 2>&1
if errorlevel 1 goto testsfailed
echo ----- 3. git commit ----- >> "%LOG%"
call _git.bat add dashboard_export.py README.md apply_dashboard.bat >> "%LOG%" 2>&1
call _git.bat commit -q -F logs\commit_msg_dashboard.txt >> "%LOG%" 2>&1
call _git.bat log --oneline -3 >> "%LOG%" 2>&1
call _git.bat status --short >> "%LOG%" 2>&1
echo ===== done ===== >> "%LOG%"
goto end
:testsfailed
echo ===== TESTS OR EXPORT FAILED: nothing committed ===== >> "%LOG%"
:end
type "%LOG%"
timeout /t 20
