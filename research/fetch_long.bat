@echo off
rem Research only: download the SP500 index history since 1927 into research\data (no trading)
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
if not exist logs mkdir logs
set "LOG=logs\research_fetch_long_log.txt"
echo ===== research_fetch_long %date% %time% ===== > "%LOG%"
call _python.bat research\fetch_data.py long >> "%LOG%" 2>&1
echo ===== done ===== >> "%LOG%"
type "%LOG%"
timeout /t 15
