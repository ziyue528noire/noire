@echo off
chcp 65001 >nul
schtasks /Delete /TN "Noire Paper Rotation" /F
schtasks /Delete /TN "Noire Monitor" /F
timeout /t 10
