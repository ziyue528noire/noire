@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist logs mkdir logs
set "LOG=logs\rename_log.txt"
echo ===== rename to Noire %date% %time% ===== > "%LOG%"
call install_schedule.bat >> "%LOG%" 2>&1
schtasks /Query /TN "Noire Paper Rotation" >nul 2>&1
if errorlevel 1 goto newmissing
schtasks /Query /TN "Noire Monitor" >nul 2>&1
if errorlevel 1 goto newmissing
schtasks /Delete /TN "Excaliber Paper Rotation" /F >> "%LOG%" 2>&1
schtasks /Delete /TN "Excaliber Monitor" /F >> "%LOG%" 2>&1
echo OLD_TASKS_REMOVED >> "%LOG%"
goto check
:newmissing
echo NEW_TASKS_MISSING_OLD_TASKS_KEPT >> "%LOG%"
:check
echo ----- tasks now ----- >> "%LOG%"
schtasks /Query /FO LIST | findstr /I /C:"Noire" /C:"Excaliber" >> "%LOG%" 2>&1
schtasks /Query /TN "Noire Paper Rotation" /FO LIST /V | findstr /I /C:"Next Run" /C:"Status" >> "%LOG%" 2>&1
schtasks /Query /TN "Noire Monitor" /FO LIST /V | findstr /I /C:"Next Run" /C:"Status" >> "%LOG%" 2>&1
echo ----- git ----- >> "%LOG%"
call _git.bat config user.name "Noire Bot" >> "%LOG%" 2>&1
call _git.bat config user.email "noire-bot@localhost" >> "%LOG%" 2>&1
call _git.bat add -A >> "%LOG%" 2>&1
call _git.bat commit -q -m "Rename Excaliber to Noire" >> "%LOG%" 2>&1
call _git.bat log --oneline -3 >> "%LOG%" 2>&1
echo ----- preflight ----- >> "%LOG%"
call _python.bat preflight.py >> "%LOG%" 2>&1
echo ===== done ===== >> "%LOG%"
type "%LOG%"
timeout /t 15
