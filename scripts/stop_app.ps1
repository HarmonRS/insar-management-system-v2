# InSAR Management System - Stop Script (PowerShell)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location -LiteralPath "$ProjectRoot"

$EnvPath = Join-Path $ProjectRoot ".env"
$NginxExe = "C:/nginx-1.29.4/nginx.exe"
$TileServerAutoStop = $true
$TileServerRoot = ""
$TileServerStopScript = "stop-all.bat"

if (Test-Path -LiteralPath "$EnvPath") {
    foreach ($line in (Get-Content -LiteralPath "$EnvPath")) {
        $trimmed = $line.Trim()
        if ($trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) { continue }
        $parts = $trimmed.Split("=", 2)
        $key = $parts[0].Trim()
        $val = $parts[1].Trim().Trim('"').Trim("'")

        if ($key -eq "NGINX_PATH" -and $val) { $NginxExe = $val }
        if ($key -eq "TILE_SERVER_AUTO_STOP") { $TileServerAutoStop = -not ($val -match '^(?i)(false|0|no|off)$') }
        if ($key -eq "TILE_SERVER_ROOT" -and $val) { $TileServerRoot = $val }
        if ($key -eq "TILE_SERVER_STOP_SCRIPT" -and $val) { $TileServerStopScript = $val }
    }
}

function Stop-Backend-By-Cmdline {
    param([string]$MatchText)
    $candidates = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue
    foreach ($proc in $candidates) {
        if ($proc.CommandLine -and $proc.CommandLine -like "*$MatchText*") {
            Write-Host "Stopping python PID $($proc.ProcessId): $MatchText"
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Stop-Process-By-Name {
    param([string]$Name, [string]$ExeName)
    $projectMatch = Join-Path -Path $ProjectRoot -ChildPath "nginx"
    $matched = @()
    $candidates = Get-CimInstance Win32_Process -Filter "Name='$Name.exe'" -ErrorAction SilentlyContinue
    foreach ($proc in ($candidates | Where-Object { $_ })) {
        if ($proc.CommandLine -and $proc.CommandLine -like "*$projectMatch*") {
            $matched += $proc
        }
    }
    foreach ($proc in $matched) {
        Write-Host "Stopping $Name PID $($proc.ProcessId)"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Stop-TileServer {
    if (-not $TileServerAutoStop -or -not $TileServerRoot) {
        return
    }
    $script = if ([System.IO.Path]::IsPathRooted($TileServerStopScript)) {
        $TileServerStopScript
    } else {
        Join-Path -Path $TileServerRoot -ChildPath $TileServerStopScript
    }
    if (-not (Test-Path -LiteralPath "$script")) {
        Write-Warning "tile-server stop script not found: $script"
        return
    }
    Write-Host "Stopping tile-server..."
    $previousNoPause = $env:NO_PAUSE
    try {
        $env:NO_PAUSE = "1"
        & "$script"
    } finally {
        if ($null -eq $previousNoPause) {
            Remove-Item Env:\NO_PAUSE -ErrorAction SilentlyContinue
        } else {
            $env:NO_PAUSE = $previousNoPause
        }
    }
}

Write-Host ">>> Stopping InSAR Management System V2..." -ForegroundColor Yellow
Stop-Backend-By-Cmdline -MatchText "run_backend.py"
Stop-Backend-By-Cmdline -MatchText "run_worker.py"

$NginxProcName = Split-Path -Leaf $NginxExe
$NginxProcName = $NginxProcName -replace '\.exe$', ''
Stop-Process-By-Name -Name $NginxProcName -ExeName $NginxExe

Stop-TileServer
Write-Host "Done." -ForegroundColor Green
