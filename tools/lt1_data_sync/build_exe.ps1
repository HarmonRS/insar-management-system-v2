param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$ToolDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ToolDir

try {
    & $Python --version
} catch {
    throw "Python was not found. Install Python 3.10+ or pass -Python with a full python.exe path."
}

$PythonExe = (Get-Command $Python).Source
$EnvRoot = Split-Path -Parent $PythonExe
$CondaBin = Join-Path $EnvRoot "Library\bin"
$ExtraArgs = @()
foreach ($DllName in @("tcl86t.dll", "tk86t.dll", "libcrypto-3-x64.dll", "liblzma.dll", "libbz2.dll")) {
    $DllPath = Join-Path $CondaBin $DllName
    if (Test-Path $DllPath) {
        $ExtraArgs += "--add-binary"
        $ExtraArgs += "$DllPath;."
    }
}

& $Python -m pip install --upgrade pyinstaller
& $Python -m PyInstaller `
    --noconfirm `
    --onefile `
    --windowed `
    --name LT1DataSync `
    @ExtraArgs `
    lt1_data_sync_gui.py

Write-Host ""
Write-Host "EXE built at: $ToolDir\dist\LT1DataSync.exe"
