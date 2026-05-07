@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "APP=%~dp0license_issuer_gui.pyw"
set "PYTHONDONTWRITEBYTECODE=1"
if not defined PYTHON_PATH set "PYTHON_PATH=C:\ProgramData\anaconda3\envs\InSAR\python.exe"
for %%I in ("%PYTHON_PATH%") do set "PYTHONW_PATH=%%~dpIpythonw.exe"

if exist "%PYTHONW_PATH%" (
    start "" "%PYTHONW_PATH%" "%APP%"
    exit /b 0
)

if exist "%PYTHON_PATH%" (
    "%PYTHON_PATH%" "%APP%"
    if errorlevel 1 pause
    exit /b %errorlevel%
)

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%APP%"
    exit /b 0
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%APP%"
    exit /b 0
)

echo [!] Python not found.
echo     Expected: %PYTHON_PATH%
echo     Or install Python Launcher / pythonw and add it to PATH.
pause
exit /b 1
