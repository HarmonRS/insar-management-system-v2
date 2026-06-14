@echo off
:: =====================================================================
:: InSAR Management System - Windows One-Click Stopper
:: =====================================================================
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo [*] Stopping system services (PowerShell)...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\stop_app.ps1"

if %errorlevel% neq 0 (
    echo.
    echo [!] Stop failed.
    pause
)
