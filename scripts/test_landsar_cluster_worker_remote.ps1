param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
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

function Add-Check {
    param(
        [System.Collections.Generic.List[object]]$Rows,
        [string]$Name,
        [bool]$Ok,
        [string]$Detail
    )
    $Rows.Add([pscustomobject]@{
        Check = $Name
        Ok = $Ok
        Detail = $Detail
    }) | Out-Null
}

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$envPath = Join-Path $RepoRoot ".env"
$rows = [System.Collections.Generic.List[object]]::new()

Add-Check $rows "repo_root" (Test-Path -LiteralPath $RepoRoot) $RepoRoot
Add-Check $rows "env_file" (Test-Path -LiteralPath $envPath) $envPath
Add-Check $rows "worker_script" (Test-Path -LiteralPath (Join-Path $RepoRoot "run_landsar_cluster_worker.py")) (Join-Path $RepoRoot "run_landsar_cluster_worker.py")
Add-Check $rows "start_launcher" (Test-Path -LiteralPath (Join-Path $RepoRoot "scripts\start_landsar_cluster_worker.ps1")) (Join-Path $RepoRoot "scripts\start_landsar_cluster_worker.ps1")
Add-Check $rows "stop_launcher" (Test-Path -LiteralPath (Join-Path $RepoRoot "scripts\stop_landsar_cluster_worker.ps1")) (Join-Path $RepoRoot "scripts\stop_landsar_cluster_worker.ps1")

$databaseUrl = Read-DotEnvValue -Path $envPath -Name "DATABASE_URL"
$dbHost = ""
$dbPort = 5432
if ($databaseUrl -match "@(?<host>[^:/]+)(:(?<port>\d+))?/") {
    $dbHost = $Matches.host
    if ($Matches.port) {
        $dbPort = [int]$Matches.port
    }
}
Add-Check $rows "database_url" ([bool]$databaseUrl) $databaseUrl
if ($dbHost) {
    $tcpOk = $false
    try {
        $tcpOk = [bool](Test-NetConnection -ComputerName $dbHost -Port $dbPort -InformationLevel Quiet -WarningAction SilentlyContinue)
    } catch {
        $tcpOk = $false
    }
    Add-Check $rows "database_tcp" $tcpOk "$dbHost`:$dbPort"
} else {
    Add-Check $rows "database_tcp" $false "DATABASE_URL host could not be parsed"
}

$pythonPath = Read-DotEnvValue -Path $envPath -Name "PYTHON_PATH"
if (-not $pythonPath) {
    $pythonPath = "C:\ProgramData\anaconda3\envs\InSAR\python.exe"
}
Add-Check $rows "python_path" (Test-Path -LiteralPath $pythonPath) $pythonPath

$landsarConsole = Read-DotEnvValue -Path $envPath -Name "LANDSAR_CONSOLE_EXE"
if (-not $landsarConsole) {
    $landsarConsole = "D:\LandSAR\InSAR_Console.exe"
}
Add-Check $rows "landsar_console" (Test-Path -LiteralPath $landsarConsole) $landsarConsole

$landsarHome = Read-DotEnvValue -Path $envPath -Name "LANDSAR_HOME"
if (-not $landsarHome) {
    $landsarHome = Split-Path -Parent $landsarConsole
}
Add-Check $rows "landsar_home" (Test-Path -LiteralPath $landsarHome) $landsarHome

$landsarExtraHome = Read-DotEnvValue -Path $envPath -Name "LANDSAR_EXTRA_HOME"
if (-not $landsarExtraHome) {
    $landsarExtraHome = Join-Path $RepoRoot "third_party\LandSAR"
}
Add-Check $rows "landsar_extra_home" (Test-Path -LiteralPath $landsarExtraHome) $landsarExtraHome

$authExe = Read-DotEnvValue -Path $envPath -Name "LANDSAR_AUTH_SERVER_EXE"
if (-not $authExe) {
    $authExe = Join-Path $RepoRoot "third_party\LandSAR\tools\_portable_release\LandSAR_auth_tools_win64\landsar_net_auth_server.exe"
}
Add-Check $rows "landsar_auth_exe" (Test-Path -LiteralPath $authExe) $authExe

$authMemory = Join-Path (Split-Path -Parent $authExe) "dongle_0xa0.bin"
$fallbackMemory = Join-Path $RepoRoot "third_party\LandSAR\tools\dongle_0xa0.bin"
Add-Check $rows "landsar_auth_memory" ((Test-Path -LiteralPath $authMemory) -or (Test-Path -LiteralPath $fallbackMemory)) "$authMemory or $fallbackMemory"

$authHost = Read-DotEnvValue -Path $envPath -Name "LANDSAR_AUTH_SERVER_HOST"
if (-not $authHost) {
    $authHost = "127.0.0.1"
}
$authPortText = Read-DotEnvValue -Path $envPath -Name "LANDSAR_AUTH_SERVER_PORT"
$authPort = 6666
if ($authPortText) {
    [void][int]::TryParse($authPortText, [ref]$authPort)
}
Add-Check $rows "landsar_auth_endpoint" ($authPort -gt 0) "$authHost`:$authPort"

$demPath = Read-DotEnvValue -Path $envPath -Name "LANDSAR_DEM_PATH"
if (-not $demPath) {
    $demPath = "D:\DEM\SRTMDEM_RSP_SARscape_global_int16.tif"
}
Add-Check $rows "landsar_dem" (Test-Path -LiteralPath $demPath) $demPath

$workRoot = Read-DotEnvValue -Path $envPath -Name "LANDSAR_WORK_ROOT"
if (-not $workRoot) {
    $workRoot = "D:\LandSAR_Work"
}
try {
    New-Item -ItemType Directory -Force -Path $workRoot | Out-Null
    $workRootOk = Test-Path -LiteralPath $workRoot
} catch {
    $workRootOk = $false
}
Add-Check $rows "landsar_work_root" $workRootOk $workRoot

$allowedTypes = Read-DotEnvValue -Path $envPath -Name "JOB_WORKER_ALLOWED_TYPES"
Add-Check $rows "allowed_job_types" ($allowedTypes -eq "LANDSAR_CLUSTER_ITEM") $allowedTypes

$clusterMainUrl = Read-DotEnvValue -Path $envPath -Name "CLUSTER_MAIN_SERVER_URL"
Add-Check $rows "cluster_main_server_url" ([bool]$clusterMainUrl) $clusterMainUrl

$clusterToken = Read-DotEnvValue -Path $envPath -Name "CLUSTER_SHARED_TOKEN"
Add-Check $rows "cluster_shared_token" ([bool]$clusterToken) $(if ($clusterToken) { "configured" } else { "missing" })

$rows | Format-Table -AutoSize

$failed = @($rows | Where-Object { -not $_.Ok })
if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host "FAILED CHECKS:" -ForegroundColor Red
    $failed | Format-Table -AutoSize
    exit 1
}

Write-Host ""
Write-Host "All LandSAR cluster worker checks passed." -ForegroundColor Green
