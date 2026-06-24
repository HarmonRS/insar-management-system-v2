param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [switch]$All
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$runtimeDir = Join-Path $RepoRoot "runtime\landsar_cluster_worker"
$pidFile = Join-Path $runtimeDir "worker.pid"

$targetPids = New-Object System.Collections.Generic.List[int]
if (Test-Path -LiteralPath $pidFile) {
    $rawPid = (Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($rawPid) {
        [void]$targetPids.Add([int]$rawPid)
    }
}

if ($All -or $targetPids.Count -eq 0) {
    $workers = Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "python.exe" -and
            $_.CommandLine -like "*run_landsar_cluster_worker.py*"
        }
    foreach ($worker in $workers) {
        if ($targetPids -notcontains [int]$worker.ProcessId) {
            [void]$targetPids.Add([int]$worker.ProcessId)
        }
    }
}

if ($targetPids.Count -eq 0) {
    Write-Host "No LandSAR cluster worker process found."
    if (Test-Path -LiteralPath $pidFile) {
        Remove-Item -LiteralPath $pidFile -Force
    }
    return
}

foreach ($targetPid in $targetPids) {
    $process = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
    if (-not $process) {
        Write-Host "PID $targetPid is not running."
        continue
    }
    Stop-Process -Id $targetPid -Force
    Write-Host "Stopped LandSAR cluster worker PID=$targetPid"
}

if (Test-Path -LiteralPath $pidFile) {
    Remove-Item -LiteralPath $pidFile -Force
}
