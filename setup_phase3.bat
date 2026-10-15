@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist logs mkdir logs
set "LOG=logs\setup_phase3_log.txt"
echo ===== setup_phase3 %date% %time% ===== > "%LOG%"
echo ----- scheduled tasks: wake to run ----- >> "%LOG%"
call install_schedule.bat >> "%LOG%" 2>&1
echo ----- move obsolete fix_schedule files to Recycle Bin ----- >> "%LOG%"
powershell -NoProfile -Command "Add-Type -AssemblyName Microsoft.VisualBasic; foreach($f in @('fix_schedule.bat','fix_schedule.ps1')){ $p = Join-Path '%~dp0' $f; if (Test-Path -LiteralPath $p) { [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p, 'OnlyErrorDialogs', 'SendToRecycleBin'); 'recycled ' + $f } else { 'not found ' + $f } }" >> "%LOG%" 2>&1
echo ----- research data download ----- >> "%LOG%"
call _python.bat research\fetch_data.py >> "%LOG%" 2>&1
echo ----- git ----- >> "%LOG%"
call _git.bat add -A >> "%LOG%" 2>&1
call _git.bat commit -q -m "Wake-to-run task settings, CLAUDE.md, research data fetcher" >> "%LOG%" 2>&1
call _git.bat log --oneline -2 >> "%LOG%" 2>&1
echo ----- preflight ----- >> "%LOG%"
call _python.bat preflight.py >> "%LOG%" 2>&1
echo ===== done ===== >> "%LOG%"
type "%LOG%"
timeout /t 15
