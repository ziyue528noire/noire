@echo off
rem One-time (2026-10): install the live (real money) mode SWITCHED OFF, plus small hardening.
rem Runs the safety tests, a PAPER dry run (no orders), the read-only PAPER monitor,
rem checks that real-money trading is OFF (no network), and makes a git commit. Writes logs\apply_live_log.txt
rem Does NOT touch the live account and does NOT install the live scheduled tasks.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "NOIRE_PROFILE="
if not exist logs mkdir logs
set "LOG=logs\apply_live_log.txt"
echo ===== apply_live %date% %time% ===== > "%LOG%"
echo ----- 1. safety tests ----- >> "%LOG%"
call _python.bat preflight.py >> "%LOG%" 2>&1
if errorlevel 1 goto testsfailed
echo ----- 2. paper dry run, no orders ----- >> "%LOG%"
call _python.bat paper_rotation.py >> "%LOG%" 2>&1
echo ----- 3. paper monitor, read only ----- >> "%LOG%"
call _python.bat monitor.py >> "%LOG%" 2>&1
echo ----- 4. real-money switch must be OFF (no network) ----- >> "%LOG%"
set NOIRE_PROFILE=live
call _python.bat -c "import broker, paths; print('live runtime:', paths.RUNTIME); print('live trading ready:', broker.live_trading_ready())" >> "%LOG%" 2>&1
set "NOIRE_PROFILE="
echo ----- 5. git commit ----- >> "%LOG%"
call _git.bat add -A >> "%LOG%" 2>&1
call _git.bat commit -q -F logs\commit_msg_live.txt >> "%LOG%" 2>&1
call _git.bat log --oneline -3 >> "%LOG%" 2>&1
echo ===== done ===== >> "%LOG%"
goto end
:testsfailed
echo ===== TESTS FAILED: nothing committed; daily trading will refuse to run until fixed ===== >> "%LOG%"
:end
type "%LOG%"
timeout /t 20
