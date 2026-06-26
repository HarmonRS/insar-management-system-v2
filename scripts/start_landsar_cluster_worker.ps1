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

function Read-BoolDotEnvValue {
    param(
        [string]$Path,
        [string]$Name,
        [bool]$DefaultValue
    )
    $value = Read-DotEnvValue -Path $Path -Name $Name
    if (-not $value) {
        return $DefaultValue
    }
    return @("1", "true", "yes", "on") -contains $value.Trim().ToLowerInvariant()
}

function Read-IntDotEnvValue {
    param(
        [string]$Path,
        [string]$Name,
        [int]$DefaultValue
    )
    $value = Read-DotEnvValue -Path $Path -Name $Name
    if (-not $value) {
        return $DefaultValue
    }
    $parsed = 0
    if ([int]::TryParse($value.Trim(), [ref]$parsed)) {
        return $parsed
    }
    return $DefaultValue
}

function Test-TcpPort {
    param(
        [string]$HostName,
        [int]$Port
    )
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        $ok = $async.AsyncWaitHandle.WaitOne(1000, $false)
        if ($ok) {
            $client.EndConnect($async)
        }
        $client.Close()
        return $ok
    } catch {
        return $false
    }
}

function Get-LandSARConfigEndpoint {
    param(
        [string]$Path
    )
    $row = Read-DotEnvValue -Path $Path -Name "LANDSAR_CONFIG_ROW"
    if ($row) {
        $parts = $row.Split(",") | ForEach-Object { $_.Trim() }
        if ($parts.Count -ge 4) {
            $port = 6666
            [void][int]::TryParse($parts[3], [ref]$port)
            return [pscustomobject]@{
                Mode = $parts[0]
                Host = $(if ($parts[2]) { $parts[2] } else { "127.0.0.1" })
                Port = $port
            }
        }
    }
    return [pscustomobject]@{
        Mode = $(Read-DotEnvValue -Path $Path -Name "LANDSAR_LICENSE_MODE")
        Host = $(Read-DotEnvValue -Path $Path -Name "LANDSAR_LICENSE_HOST")
        Port = $(Read-IntDotEnvValue -Path $Path -Name "LANDSAR_LICENSE_PORT" -DefaultValue 6666)
    }
}

function Resolve-LandSARAuthServerPath {
    param(
        [string]$RepoRoot,
        [string]$EnvPath
    )
    $explicit = Read-DotEnvValue -Path $EnvPath -Name "LANDSAR_AUTH_SERVER_EXE"
    $candidates = @(
        $explicit,
        (Join-Path $RepoRoot "third_party\LandSAR\tools\_portable_release\LandSAR_auth_tools_win64\landsar_net_auth_server.exe"),
        (Join-Path $RepoRoot "third_party\LandSAR\landsar_net_auth_server.exe")
    ) | Where-Object { $_ -and $_.Trim() } | Select-Object -Unique
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $candidates | Select-Object -First 1
}

function Start-LandSARAuthServerIfNeeded {
    param(
        [string]$RepoRoot,
        [string]$EnvPath
    )
    $endpoint = Get-LandSARConfigEndpoint -Path $EnvPath
    $mode = $(if ($endpoint.Mode) { $endpoint.Mode } else { "netVersion" })
    if ($mode.Trim().ToLowerInvariant() -ne "netversion") {
        Write-Host "LandSAR auth: skipped for license mode $mode"
        return
    }

    $clientHost = $(if ($endpoint.Host) { $endpoint.Host } else { "127.0.0.1" })
    $clientPort = [int]$endpoint.Port
    if (Test-TcpPort -HostName $clientHost -Port $clientPort) {
        Write-Host "LandSAR auth: already listening on $clientHost`:$clientPort"
        return
    }

    $autoStart = Read-BoolDotEnvValue -Path $EnvPath -Name "LANDSAR_AUTH_SERVER_AUTO_START" -DefaultValue $true
    if (-not $autoStart) {
        throw "LandSAR auth server is not listening on $clientHost`:$clientPort and LANDSAR_AUTH_SERVER_AUTO_START=false."
    }

    $authExe = Resolve-LandSARAuthServerPath -RepoRoot $RepoRoot -EnvPath $EnvPath
    if (-not $authExe -or -not (Test-Path -LiteralPath $authExe)) {
        throw "LandSAR auth server executable not found: $authExe. Set LANDSAR_AUTH_SERVER_EXE in .env."
    }

    $serverDir = Split-Path -Parent $authExe
    $memoryBin = Join-Path $serverDir "dongle_0xa0.bin"
    if (-not (Test-Path -LiteralPath $memoryBin)) {
        $fallbackBin = Join-Path $RepoRoot "third_party\LandSAR\tools\dongle_0xa0.bin"
        if (Test-Path -LiteralPath $fallbackBin) {
            Copy-Item -LiteralPath $fallbackBin -Destination $memoryBin -Force
        } else {
            throw "LandSAR auth memory image missing: $memoryBin"
        }
    }

    $bindHost = Read-DotEnvValue -Path $EnvPath -Name "LANDSAR_AUTH_SERVER_HOST"
    if (-not $bindHost) {
        $bindHost = $clientHost
    }
    $bindPort = Read-IntDotEnvValue -Path $EnvPath -Name "LANDSAR_AUTH_SERVER_PORT" -DefaultValue $clientPort

    Write-Host "LandSAR auth: starting $authExe on $bindHost`:$bindPort"
    Start-Process `
        -FilePath $authExe `
        -ArgumentList @("--host", $bindHost, "--port", [string]$bindPort) `
        -WorkingDirectory $serverDir `
        -WindowStyle Hidden | Out-Null

    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -HostName $clientHost -Port $clientPort) {
            Write-Host "LandSAR auth: started and reachable on $clientHost`:$clientPort"
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw "LandSAR auth server was started but $clientHost`:$clientPort is not reachable."
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
$clusterToken = Read-DotEnvValue -Path $envPath -Name "CLUSTER_SHARED_TOKEN"
if (-not $clusterToken) {
    throw "CLUSTER_SHARED_TOKEN is required for LandSAR cluster input download and result upload."
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
$runtimeDir = Join-Path $RepoRoot "runtime\landsar_cluster_worker"
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$pidFile = Join-Path $runtimeDir "worker.pid"
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
Start-LandSARAuthServerIfNeeded -RepoRoot $RepoRoot -EnvPath $envPath

if ($Background) {
    if (Test-Path -LiteralPath $pidFile) {
        $existingPid = (Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
        if ($existingPid -and (Get-Process -Id ([int]$existingPid) -ErrorAction SilentlyContinue)) {
            throw "LandSAR cluster worker already appears to be running. PID=$existingPid. Use scripts\stop_landsar_cluster_worker.ps1 first."
        }
    }
    $process = Start-Process `
        -FilePath $python `
        -ArgumentList @($workerScript) `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ASCII
    Write-Host "Started background worker. PID=$($process.Id)"
    Write-Host "PID file: $pidFile"
    return
}

& $python $workerScript 2>&1 | Tee-Object -FilePath $stdoutLog -Append
exit $LASTEXITCODE
