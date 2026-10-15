@echo off
rem Find Python and run: call _python.bat script.py args
py -3 --version >nul 2>&1
if not errorlevel 1 (
  py -3 %*
  exit /b
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" %*
  exit /b
)
python %*
