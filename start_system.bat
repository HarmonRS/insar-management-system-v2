@echo off
:: =====================================================================
:: InSAR Management System - Windows One-Click Starter
:: =====================================================================
chcp 65001 >nul
setlocal

:: Always switch to the script directory
cd /d "%~dp0"

echo [*] Starting system launcher (PowerShell)...

:: Use PowerShell for the core startup logic
:: -NoExit keeps the window open so you can read logs
powershell -NoProfile -ExecutionPolicy Bypass -NoExit -File "scripts\start_app.ps1"

if %errorlevel% neq 0 (
    echo.
    echo [!] Startup failed.
    pause
)
