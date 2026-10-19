@echo off
rem Remove the 2 Windows scheduled tasks of the REAL MONEY account (install_live_schedule.bat puts them back).
rem Positions, orders and records are not touched.
chcp 65001 >nul
schtasks /Delete /TN "Noire Live Rotation" /F
schtasks /Delete /TN "Noire Live Monitor" /F
timeout /t 10
