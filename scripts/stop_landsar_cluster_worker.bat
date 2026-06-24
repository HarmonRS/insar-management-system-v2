@echo off
setlocal
cd /d "%~dp0\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_landsar_cluster_worker.ps1" -All
pause
