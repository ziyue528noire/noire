@echo off
rem Research only: download historical ETF data into research\data (no trading)
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist logs mkdir logs
set "LOG=logs\research_fetch_log.txt"
echo ===== research_fetch %date% %time% ===== > "%LOG%"
call _python.bat research\fetch_data.py >> "%LOG%" 2>&1
echo ===== done ===== >> "%LOG%"
type "%LOG%"
timeout /t 15
