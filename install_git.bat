@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs
(
  echo ===== install_git %date% %time% =====
  if not exist "D:\scoreboard" mkdir "D:\scoreboard"
  if exist "D:\scoreboard" (echo SCOREBOARD_DIR_OK) else (echo SCOREBOARD_DIR_FAILED)
  call _git.bat --version
  if errorlevel 1 (
    echo ----- installing Git for Windows via winget -----
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements --silent
  )
  echo ----- after install -----
  call _git.bat --version
) > logs\install_git_log.txt 2>&1
type logs\install_git_log.txt
call setup_phase2.bat
