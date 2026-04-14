@echo off
setlocal
cd /d "%~dp0"

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%~dp0license_issuer_gui.pyw"
    exit /b 0
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0license_issuer_gui.pyw"
    exit /b 0
)

python "%~dp0license_issuer_gui.pyw"
