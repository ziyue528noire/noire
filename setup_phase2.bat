@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist logs mkdir logs
(
  echo ===== setup_phase2 %date% %time% =====
  call _git.bat --version
  if errorlevel 1 (
    echo GIT_NOT_FOUND
  ) else (
    if not exist .git call _git.bat init
    call _git.bat config user.name "Noire Bot"
    call _git.bat config user.email "noire-bot@localhost"
    call _git.bat config core.autocrlf false
    call _git.bat add -A
    call _git.bat commit -m "Noire: risk ledger, reconciliation, safety tests, scoreboard"
    echo ----- tracked files -----
    call _git.bat ls-files
    echo ----- ignored check -----
    call _git.bat check-ignore -v .env data logs
    echo ----- log -----
    call _git.bat log --oneline -5
  )
  echo ----- preflight -----
  call _python.bat preflight.py
  echo ----- dry run -----
  call _python.bat paper_rotation.py
  echo ----- monitor -----
  call _python.bat monitor.py
  echo ----- scoreboard -----
  call _python.bat scoreboard.py
  dir /b D:\scoreboard
  echo ===== done =====
) > logs\setup_phase2_log.txt 2>&1
type logs\setup_phase2_log.txt
timeout /t 20
