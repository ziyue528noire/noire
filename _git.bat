@echo off
rem Find git and run it: call _git.bat args  (works even before PATH is refreshed)
where git >nul 2>&1
if not errorlevel 1 (
  git %*
  exit /b
)
if exist "%ProgramFiles%\Git\cmd\git.exe" (
  "%ProgramFiles%\Git\cmd\git.exe" %*
  exit /b
)
if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" (
  "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" %*
  exit /b
)
echo GIT_NOT_FOUND
exit /b 9009
