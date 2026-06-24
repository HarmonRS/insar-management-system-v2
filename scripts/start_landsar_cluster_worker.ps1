param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$PythonPath = "",
    [string]$WorkerId = "",
    [switch]$Background
)

$ErrorActionPreference = "Stop"

function Read-DotEnvValue {
    param(
        [string]$Path,
        [string]$Name
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return ""
    }
    $line = Get-Content -LiteralPath $Path |
        Where-Object { $_ -match "^\s*$([regex]::Escape($Name))\s*=" } |
        Select-Object -Last 1
    if (-not $line) {
        return ""
    }
    $value = ($line -split "=", 2)[1].Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    return $value
}

function Resolve-PythonPath {
    param(
        [string]$ExplicitPath,
        [string]$EnvPath
    )
    $candidates = @(
        $ExplicitPath,
        $EnvPath,
        "C:\ProgramData\anaconda3\envs\InSAR\python.exe",
        "python.exe"
    ) | Where-Object { $_ -and $_.Trim() } | Select-Object -Unique

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }
    }
    throw "Python interpreter not found. Set PYTHON_PATH in .env or pass -PythonPath."
}

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$envPath = Join-Path $RepoRoot ".env"
$templatePath = Join-Path $RepoRoot "config\landsar_cluster_worker.env.example"
$workerScript = Join-Path $RepoRoot "run_landsar_cluster_worker.py"

if (-not (Test-Path -LiteralPath $workerScript)) {
    throw "Worker script not found: $workerScript"
}

if (-not (Test-Path -LiteralPath $envPath)) {
    if (Test-Path -LiteralPath $templatePath) {
        Copy-Item -LiteralPath $templatePath -Destination $envPath
        throw ".env was created from config\landsar_cluster_worker.env.example. Review paths and credentials, then run this launcher again."
    }
    throw ".env not found: $envPath"
}

$dotenvPythonPath = Read-DotEnvValue -Path $envPath -Name "PYTHON_PATH"
$python = Resolve-PythonPath -ExplicitPath $PythonPath -EnvPath $dotenvPythonPath

$allowedTypes = Read-DotEnvValue -Path $envPath -Name "JOB_WORKER_ALLOWED_TYPES"
if (-not $allowedTypes) {
    $env:JOB_WORKER_ALLOWED_TYPES = "LANDSAR_CLUSTER_ITEM"
}
$concurrency = Read-DotEnvValue -Path $envPath -Name "JOB_WORKER_CONCURRENCY"
if (-not $concurrency) {
    $env:JOB_WORKER_CONCURRENCY = "1"
}
if ($WorkerId) {
    $env:LANDSAR_CLUSTER_WORKER_ID = $WorkerId
}

$logDir = Join-Path $RepoRoot "logs\landsar_cluster_worker"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stdoutLog = Join-Path $logDir "worker_$timestamp.log"
$stderrLog = Join-Path $logDir "worker_$timestamp.err.log"

Write-Host "LandSAR cluster worker launcher"
Write-Host "RepoRoot: $RepoRoot"
Write-Host "Python:   $python"
Write-Host "Env:      $envPath"
Write-Host "Log:      $stdoutLog"
Write-Host "Mode:     $(if ($Background) { 'background' } else { 'foreground' })"

Set-Location $RepoRoot

if ($Background) {
    $process = Start-Process `
        -FilePath $python `
        -ArgumentList @($workerScript) `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -WindowStyle Hidden `
        -PassThru
    Write-Host "Started background worker. PID=$($process.Id)"
    return
}

& $python $workerScript 2>&1 | Tee-Object -FilePath $stdoutLog -Append
exit $LASTEXITCODE
