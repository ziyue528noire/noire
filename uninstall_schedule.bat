@echo off
chcp 65001 >nul
schtasks /Delete /TN "Excaliber Paper Rotation" /F
schtasks /Delete /TN "Excaliber Monitor" /F
timeout /t 10
