@echo off
cd /d "%~dp0"
echo Pushing Noire to GitHub (origin master)...
"C:\Program Files\Git\cmd\git.exe" push -u origin master
if errorlevel 1 (echo. & echo PUSH FAILED - copy the lines above and send them to Claude.) else (echo. & echo PUSH OK)
pause
