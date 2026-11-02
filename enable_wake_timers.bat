@echo off
rem Let Windows wake this PC from sleep for Noire's scheduled trades (paper 17:00, live 17:10).
rem The tasks are already set to "wake the computer to run this task", but Windows only does that
rem when "Allow wake timers" is enabled in the power plan. The 2026-11-01 check found it disabled.
rem This turns it on when PLUGGED IN only. On battery it stays off, so a laptop in a bag never wakes up.
rem Undo: powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 0  then  powercfg /setactive SCHEME_CURRENT
cd /d "%~dp0"
if not exist logs mkdir logs
echo Turning on "Allow wake timers" (plugged in only) in the current power plan...
powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
if errorlevel 1 goto failed
powercfg /setactive SCHEME_CURRENT
if errorlevel 1 goto failed
powercfg /q SCHEME_CURRENT SUB_SLEEP RTCWAKE > logs\wake_timers_log.txt 2>&1
type logs\wake_timers_log.txt
echo.
echo OK. The AC (plugged in) setting above should now be 0x00000001.
pause
exit /b 0
:failed
echo.
echo FAILED. Right-click enable_wake_timers.bat and choose "Run as administrator",
echo or send the lines above to Claude.
pause
exit /b 1
