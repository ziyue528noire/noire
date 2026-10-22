@echo off
rem One-time (2026-10-22): commit the look-ahead trap tests + README section 17.
rem Runs the safety tests first; commits only these 3 files. No orders, no account access. Writes logs\apply_lookahead_log.txt
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "NOIRE_PROFILE="
if not exist logs mkdir logs
set "LOG=logs\apply_lookahead_log.txt"
echo ===== apply_lookahead %date% %time% ===== > "%LOG%"
echo ----- 1. safety tests ----- >> "%LOG%"
call _python.bat preflight.py >> "%LOG%" 2>&1
if errorlevel 1 goto testsfailed
echo ----- 2. git commit ----- >> "%LOG%"
call _git.bat add tests/test_lookahead.py README.md apply_lookahead.bat >> "%LOG%" 2>&1
call _git.bat commit -q -F logs\commit_msg_lookahead.txt >> "%LOG%" 2>&1
call _git.bat log --oneline -3 >> "%LOG%" 2>&1
call _git.bat status --short >> "%LOG%" 2>&1
echo ===== done ===== >> "%LOG%"
goto end
:testsfailed
echo ===== TESTS FAILED: nothing committed ===== >> "%LOG%"
:end
type "%LOG%"
timeout /t 20
