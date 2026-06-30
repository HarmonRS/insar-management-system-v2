# InSAR Management System - Startup Script (PowerShell)
# Features:
# - Process management (stop old instances, start new ones)
# - Database health check
# - Auto Nginx config update
# - Logs output

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 1. Set project root
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location -LiteralPath "$ProjectRoot"

Write-Host ">>> Starting InSAR Management System V2..." -ForegroundColor Cyan
Write-Host ">>> Project Root: $ProjectRoot"

# 2. Parse .env minimally (only for resolving Python/Conda before handing off to unified config)
$EnvPath = Join-Path $ProjectRoot ".env"
if (-not (Test-Path -LiteralPath "$EnvPath")) {
    Write-Error "File .env not found: $EnvPath"
    return
}

$PythonExe = "python"
$CondaExe = ""
$CondaEnvName = ""
$NginxExe = "C:/nginx-1.29.4/nginx.exe"
$ServerHost = ""
$ServerPort = 18000
$NginxAllowedClientIps = ""
$BackendReadyTimeoutSeconds = 120
$TileServerAutoStart = $false
$TileServerAutoStop = $true
$TileServerRoot = ""
$TileServerStartScript = "start-all.bat"
$TileServerStopScript = "stop-all.bat"
$TileServerUrl = ""

$envLines = Get-Content -LiteralPath "$EnvPath"
foreach ($line in $envLines) {
    $trimmed = $line.Trim()
    if ($trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) { continue }

    $parts = $trimmed.Split("=", 2)
    $key = $parts[0].Trim()
    $val = $parts[1].Trim().Trim('"').Trim("'")

    if ($key -eq "PYTHON_PATH") { if ($val) { $PythonExe = $val } }
    if ($key -eq "CONDA_EXE") { if ($val) { $CondaExe = $val } }
    if ($key -eq "CONDA_ENV_NAME") { if ($val) { $CondaEnvName = $val } }
    if ($key -eq "NGINX_PATH") { if ($val) { $NginxExe = $val } }
    if ($key -eq "NGINX_ALLOWED_CLIENT_IPS") { $NginxAllowedClientIps = $val }
    if ($key -eq "BACKEND_BIND_HOST") { if ($val) { $ServerHost = $val } }
    if ($key -eq "BACKEND_READY_TIMEOUT_SECONDS") {
        $parsed = 0
        if ([int]::TryParse($val, [ref]$parsed) -and $parsed -gt 0) { $BackendReadyTimeoutSeconds = $parsed }
    }
    if ($key -eq "TILE_SERVER_AUTO_START") { $TileServerAutoStart = $val -match '^(?i)(true|1|yes|on)$' }
    if ($key -eq "TILE_SERVER_AUTO_STOP") { $TileServerAutoStop = -not ($val -match '^(?i)(false|0|no|off)$') }
    if ($key -eq "TILE_SERVER_ROOT") { if ($val) { $TileServerRoot = $val } }
    if ($key -eq "TILE_SERVER_START_SCRIPT") { if ($val) { $TileServerStartScript = $val } }
    if ($key -eq "TILE_SERVER_STOP_SCRIPT") { if ($val) { $TileServerStopScript = $val } }
    if ($key -eq "VITE_TILE_SERVER_URL") { if ($val) { $TileServerUrl = $val.TrimEnd("/") } }
    if ($key -eq "PORT") {
        $parsed = 0
        if ([int]::TryParse($val, [ref]$parsed)) { $ServerPort = $parsed }
    }
}

$CheckDbScript = Join-Path $ProjectRoot "scripts\check_db_connection.py"
$CheckRuntimeScript = Join-Path $ProjectRoot "scripts\check_runtime_config.py"
$ExportLauncherConfigScript = Join-Path $ProjectRoot "scripts\export_launcher_config.py"
$InitDbScript = Join-Path $ProjectRoot "scripts\init_db.py"

function Resolve-ExecutablePath {
    param([string]$Candidate)

    $trimmed = "$Candidate".Trim().Trim('"').Trim("'")
    if (-not $trimmed) {
        return $null
    }

    if (Test-Path -LiteralPath "$trimmed") {
        $resolved = Resolve-Path -LiteralPath "$trimmed" -ErrorAction SilentlyContinue
        if ($resolved) {
            return $resolved.Path
        }
        return $trimmed
    }

    $cmd = Get-Command -Name "$trimmed" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd -and $cmd.Source) {
        return $cmd.Source
    }

    return $null
}

function Resolve-CondaEnvPythonPath {
    param(
        [string]$ResolvedCondaExe,
        [string]$EnvName
    )

    $trimmedEnvName = "$EnvName".Trim()
    if (-not $trimmedEnvName) {
        return $null
    }

    $anacondaRoot = Split-Path -Parent (Split-Path -Parent "$ResolvedCondaExe")
    $candidatePaths = @(
        (Join-Path -Path $anacondaRoot -ChildPath "envs\$trimmedEnvName\python.exe"),
        (Join-Path -Path $env:USERPROFILE -ChildPath ".conda\envs\$trimmedEnvName\python.exe")
    )

    foreach ($candidate in $candidatePaths) {
        if (Test-Path -LiteralPath "$candidate") {
            $resolved = Resolve-Path -LiteralPath "$candidate" -ErrorAction SilentlyContinue
            if ($resolved) {
                return $resolved.Path
            }
            return $candidate
        }
    }

    try {
        $envsJson = & "$ResolvedCondaExe" info --envs --json 2>$null
        if ($LASTEXITCODE -eq 0 -and $envsJson) {
            $envsInfo = $envsJson | ConvertFrom-Json
            foreach ($envPath in ($envsInfo.envs | Where-Object { $_ })) {
                if ((Split-Path -Leaf "$envPath") -ne $trimmedEnvName) {
                    continue
                }
                $pythonPath = Join-Path -Path "$envPath" -ChildPath "python.exe"
                if (Test-Path -LiteralPath "$pythonPath") {
                    $resolved = Resolve-Path -LiteralPath "$pythonPath" -ErrorAction SilentlyContinue
                    if ($resolved) {
                        return $resolved.Path
                    }
                    return $pythonPath
                }
            }
        }
    } catch {
        # Ignore and continue with fallback.
    }

    return $null
}

function Invoke-PythonScript {
    param([string]$ScriptPath)

    & "$PythonExe" "$ScriptPath"
}

function Invoke-PythonScriptJson {
    param([string]$ScriptPath)

    $output = & "$PythonExe" "$ScriptPath"
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    if (-not $output) {
        return $null
    }
    return ($output | ConvertFrom-Json)
}

# 3. Stop old instances
Write-Host ">>> Stopping existing processes..." -ForegroundColor Yellow

function Stop-Process-By-Name {
    param([string]$Name, [string]$ExeName)
    # Prefer precise cmdline matching; fall back to executable path when CIM command line is unavailable.
    $NginxConfMatch = Join-Path -Path $ProjectRoot -ChildPath "nginx"
    $matched = @()
    $resolvedExe = Resolve-ExecutablePath -Candidate $ExeName

    $candidates = Get-CimInstance Win32_Process -Filter "Name='$Name.exe'" -ErrorAction SilentlyContinue
    if ($candidates) {
        foreach ($proc in $candidates) {
            if ($proc.CommandLine -and $proc.CommandLine -like "*$NginxConfMatch*") {
                $matched += $proc
            }
        }
    }

    if ($matched.Count -eq 0 -and $resolvedExe) {
        $processesByPath = Get-Process -Name $Name -ErrorAction SilentlyContinue
        foreach ($proc in $processesByPath) {
            $procPath = $null
            try {
                $procPath = $proc.Path
            } catch {
                $procPath = $null
            }

            if ($procPath -and ([string]::Equals($procPath, $resolvedExe, [System.StringComparison]::OrdinalIgnoreCase))) {
                $matched += $proc
            }
        }
    }

    if ($matched.Count -gt 0) {
        $targetPids = @($matched | ForEach-Object {
            if ($_.PSObject.Properties['ProcessId']) { $_.ProcessId }
            elseif ($_.PSObject.Properties['Id']) { $_.Id }
        } | Where-Object { $_ } | Sort-Object -Unique)

        Write-Host "    Stopping $Name (PID: $($targetPids -join ', '))..." -NoNewline
        foreach ($p in $matched) {
            $processId = $null
            if ($p.PSObject.Properties['ProcessId']) { $processId = $p.ProcessId }
            elseif ($p.PSObject.Properties['Id']) { $processId = $p.Id }
            if ($processId) {
                Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
            }
        }
        Start-Sleep -Seconds 1
        $stillAlive = $false
        foreach ($targetProcessId in $targetPids) {
            if (Get-Process -Id $targetProcessId -ErrorAction SilentlyContinue) {
                $stillAlive = $true
            }
        }
        if (-not $stillAlive) {
            Write-Host " [Done]" -ForegroundColor Green
        } else {
            Write-Host " [Failed]" -ForegroundColor Red
        }
    }
}

function Stop-Backend-By-Cmdline {
    param([string]$MatchText)
    $candidates = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue
    foreach ($proc in $candidates) {
        if ($proc.CommandLine -and $proc.CommandLine -like "*$MatchText*") {
            try {
                Write-Host "    Stopping python (PID $($proc.ProcessId)) with cmdline match: $MatchText" -NoNewline
                Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 1
                $check = Get-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue
                if (-not $check) {
                    Write-Host " [Done]" -ForegroundColor Green
                } else {
                    Write-Host " [Failed]" -ForegroundColor Red
                }
            } catch {
                Write-Host " [Failed]" -ForegroundColor Red
            }
        }
    }
}

function Get-ListeningPortOwner {
    param([int]$Port)

    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        $procName = "Unknown"
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        if ($proc) {
            $procName = $proc.ProcessName
        }

        return [PSCustomObject]@{
            Port = $Port
            PID = $conn.OwningProcess
            ProcessName = $procName
            LocalAddress = $conn.LocalAddress
        }
    }

    $netstatLines = netstat -ano -p tcp 2>$null
    if ($netstatLines) {
        foreach ($line in $netstatLines) {
            $trimmed = "$line".Trim()
            if (-not $trimmed.StartsWith("TCP")) {
                continue
            }
            if ($trimmed -notmatch "\s+LISTENING\s+") {
                continue
            }
            $parts = $trimmed -split "\s+"
            if ($parts.Count -lt 5) {
                continue
            }
            $localEndpoint = $parts[1]
            $pidText = $parts[4]
            $localPort = -1
            if ($localEndpoint -match ":(\d+)$") {
                $localPort = [int]$matches[1]
            }
            if ($localPort -ne $Port) {
                continue
            }

            $ownerPid = 0
            [void][int]::TryParse("$pidText", [ref]$ownerPid)
            $procName = "Unknown"
            if ($ownerPid -gt 0) {
                $proc = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
                if ($proc) {
                    $procName = $proc.ProcessName
                }
            }

            return [PSCustomObject]@{
                Port = $Port
                PID = $ownerPid
                ProcessName = $procName
                LocalAddress = $localEndpoint
            }
        }
    }

    return $null
}

function Test-PortAvailable {
    param([int]$Port)

    try {
        $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $probe.Start()
        $probe.Stop()
        return $true
    } catch {
        return $false
    }
}

function Write-NginxClientAllowFile {
    param([string]$Path)

    $entries = @()
    $raw = "$NginxAllowedClientIps".Trim()
    if ($raw) {
        $entries = @($raw -split '[;,\s]+' | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
    }

    $lines = @(
        "# Generated by scripts/start_app.ps1.",
        "# Configure NGINX_ALLOWED_CLIENT_IPS in .env. Empty means allow all clients."
    )

    if ($entries.Count -gt 0) {
        $allowed = @("127.0.0.1", "::1") + $entries
        $allowed = @($allowed | Sort-Object -Unique)
        foreach ($item in $allowed) {
            $lines += "        allow $item;"
        }
        $lines += "        deny all;"
    }

    $dir = Split-Path -Parent "$Path"
    if (-not (Test-Path -LiteralPath "$dir")) {
        New-Item -ItemType Directory -Path "$dir" -Force | Out-Null
    }
    $Utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText("$Path", (($lines -join [Environment]::NewLine) + [Environment]::NewLine), $Utf8NoBom)

    if ($entries.Count -gt 0) {
        Write-Host ">>> Nginx client IP whitelist enabled: $($entries -join ', ')" -ForegroundColor Yellow
    } else {
        Write-Host ">>> Nginx client IP whitelist disabled." -ForegroundColor DarkGray
    }
}

function Test-TileServerReady {
    if (-not $TileServerUrl) {
        return $false
    }
    try {
        $response = Invoke-WebRequest -Uri "$TileServerUrl/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Get-TileServerScriptPath {
    param([string]$ScriptName)

    $root = "$TileServerRoot".Trim().Trim('"').Trim("'")
    if (-not $root) {
        return $null
    }
    $script = "$ScriptName".Trim().Trim('"').Trim("'")
    if (-not $script) {
        return $null
    }
    if ([System.IO.Path]::IsPathRooted($script)) {
        return $script
    }
    return (Join-Path -Path $root -ChildPath $script)
}

function Stop-TileServer {
    if (-not $TileServerAutoStop) {
        return
    }
    $stopScript = Get-TileServerScriptPath -ScriptName "$TileServerStopScript"
    if (-not $stopScript -or -not (Test-Path -LiteralPath "$stopScript")) {
        return
    }
    Write-Host ">>> Stopping tile-server..." -ForegroundColor Yellow
    $previousNoPause = $env:NO_PAUSE
    try {
        $env:NO_PAUSE = "1"
        & "$stopScript"
    } catch {
        Write-Warning "tile-server stop failed: $($_.Exception.Message)"
    } finally {
        if ($null -eq $previousNoPause) {
            Remove-Item Env:\NO_PAUSE -ErrorAction SilentlyContinue
        } else {
            $env:NO_PAUSE = $previousNoPause
        }
    }
}

function Start-TileServer {
    if (-not $TileServerAutoStart) {
        return
    }
    if (Test-TileServerReady) {
        Write-Host ">>> tile-server already responding: $TileServerUrl" -ForegroundColor Green
        return
    }
    $startScript = Get-TileServerScriptPath -ScriptName "$TileServerStartScript"
    if (-not $startScript -or -not (Test-Path -LiteralPath "$startScript")) {
        Write-Error "tile-server start script not found. TILE_SERVER_ROOT=$TileServerRoot TILE_SERVER_START_SCRIPT=$TileServerStartScript"
        $global:LASTEXITCODE = 1
        return
    }
    Write-Host ">>> Launching tile-server..." -ForegroundColor Green
    $previousNoPause = $env:NO_PAUSE
    try {
        $env:NO_PAUSE = "1"
        & "$startScript"
    } finally {
        if ($null -eq $previousNoPause) {
            Remove-Item Env:\NO_PAUSE -ErrorAction SilentlyContinue
        } else {
            $env:NO_PAUSE = $previousNoPause
        }
    }
    for ($i = 0; $i -lt 20; $i++) {
        if (Test-TileServerReady) {
            Write-Host ">>> tile-server ready: $TileServerUrl" -ForegroundColor Green
            return
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Warning "tile-server was started but did not pass health check: $TileServerUrl/health"
}

Stop-Backend-By-Cmdline -MatchText "run_backend.py"
Stop-Backend-By-Cmdline -MatchText "run_worker.py"
$NginxProcName = Split-Path -Leaf $NginxExe
$NginxProcName = $NginxProcName -replace '\.exe$', ''
Stop-Process-By-Name -Name $NginxProcName -ExeName $NginxExe
if ($TileServerAutoStart -and $TileServerAutoStop) {
    Stop-TileServer
}

$PortAvailable = Test-PortAvailable -Port $ServerPort
if (-not $PortAvailable) {
    $PortOwner = Get-ListeningPortOwner -Port $ServerPort
    if ($PortOwner) {
        Write-Error (
            "Backend port $ServerPort is already in use by PID $($PortOwner.PID) " +
            "($($PortOwner.ProcessName)) on $($PortOwner.LocalAddress). " +
            "Please stop that process or change PORT in .env."
        )
    } else {
        Write-Error (
            "Backend port $ServerPort is not available (bind failed). " +
            "Please stop the process using this port or change PORT in .env."
        )
    }
    $global:LASTEXITCODE = 1
    return
}

# 4. Runtime config + database pre-flight check
Write-Host ">>> Checking deployment configuration..." -ForegroundColor Yellow

$UseCondaRun = -not [string]::IsNullOrWhiteSpace("$CondaEnvName".Trim())
$ResolvedCondaExe = $null
$CondaEnvPythonExe = $null

if ($UseCondaRun) {
    if ([string]::IsNullOrWhiteSpace("$CondaExe".Trim())) {
        $CondaExe = "conda"
    }
    $ResolvedCondaExe = Resolve-ExecutablePath -Candidate $CondaExe
    if (-not $ResolvedCondaExe) {
        Write-Error "Conda executable not found. CONDA_EXE=$CondaExe"
        $global:LASTEXITCODE = 1
        return
    }
    $CondaEnvPythonExe = Resolve-CondaEnvPythonPath -ResolvedCondaExe "$ResolvedCondaExe" -EnvName "$CondaEnvName"
    if (-not $CondaEnvPythonExe) {
        Write-Error "Conda environment python not found. CONDA_ENV_NAME=$CondaEnvName"
        $global:LASTEXITCODE = 1
        return
    }
    $PythonExe = $CondaEnvPythonExe
} else {
    $ResolvedPythonExe = Resolve-ExecutablePath -Candidate $PythonExe
    if (-not $ResolvedPythonExe) {
        Write-Error "Python executable not found. PYTHON_PATH=$PythonExe"
        $global:LASTEXITCODE = 1
        return
    }
    $PythonExe = $ResolvedPythonExe
}

$LauncherConfig = Invoke-PythonScriptJson -ScriptPath "$ExportLauncherConfigScript"
if (-not $LauncherConfig) {
    Write-Error "Failed to load launcher runtime configuration from Python settings layer."
    $global:LASTEXITCODE = 1
    return
}

if ($LauncherConfig.nginx_path) {
    $NginxExe = [string]$LauncherConfig.nginx_path
}
if ($LauncherConfig.backend_bind_host) {
    $ServerHost = [string]$LauncherConfig.backend_bind_host
}
if ($LauncherConfig.port) {
    $ServerPort = [int]$LauncherConfig.port
}

function Write-StatusLine {
    param(
        [string]$Message,
        [ConsoleColor]$ForegroundColor = [ConsoleColor]::White
    )

    Write-Host $Message -ForegroundColor $ForegroundColor
}

function Get-BackgroundProcessLogs {
    param([string]$ScriptPath)

    $LogDir = Join-Path $ProjectRoot "logs"
    if (-not (Test-Path -LiteralPath "$LogDir")) {
        New-Item -ItemType Directory -Path "$LogDir" -Force | Out-Null
    }

    $ScriptBase = [System.IO.Path]::GetFileNameWithoutExtension("$ScriptPath")
    return [PSCustomObject]@{
        StdOut = Join-Path $LogDir "$ScriptBase.stdout.log"
        StdErr = Join-Path $LogDir "$ScriptBase.stderr.log"
    }
}

function Start-PythonBackground {
    param([string]$ScriptPath)

    $LogTargets = Get-BackgroundProcessLogs -ScriptPath "$ScriptPath"
    Write-Host "    stdout -> $($LogTargets.StdOut)"
    Write-Host "    stderr -> $($LogTargets.StdErr)"

    return Start-Process `
        -FilePath "$PythonExe" `
        -ArgumentList @("$ScriptPath") `
        -WorkingDirectory "$ProjectRoot" `
        -PassThru `
        -NoNewWindow `
        -RedirectStandardOutput "$($LogTargets.StdOut)" `
        -RedirectStandardError "$($LogTargets.StdErr)"
}

function Assert-ProcessAlive {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$DisplayName
    )

    if (-not $Process) {
        Write-Error "$DisplayName failed to start: process handle is null."
        $global:LASTEXITCODE = 1
        return $false
    }

    Start-Sleep -Milliseconds 800
    $procCheck = Get-Process -Id $Process.Id -ErrorAction SilentlyContinue
    if (-not $procCheck) {
        Write-Error "$DisplayName exited immediately after startup. Please check logs for details."
        $global:LASTEXITCODE = 1
        return $false
    }

    return $true
}

function Wait-BackendReady {
    param(
        [int]$Port,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSeconds))
    # Use the lightweight root route for readiness. /api/health runs a full
    # operational self-check and can legitimately take longer during startup.
    $url = "http://127.0.0.1:$Port/"
    $lastError = $null

    Write-Host ">>> Waiting for backend readiness: $url" -ForegroundColor Yellow
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri "$url" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                Write-Host ">>> Backend ready (status=$($response.StatusCode))." -ForegroundColor Green
                return $true
            }
            $lastError = "HTTP $($response.StatusCode)"
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 800
    }

    Write-Error "Backend did not become ready within $TimeoutSeconds seconds. Last error: $lastError"
    $global:LASTEXITCODE = 1
    return $false
}

Invoke-PythonScript -ScriptPath "$CheckRuntimeScript"
if ($LastExitCode -ne 0) {
    Write-Host "`n[ERROR] Deployment configuration check failed." -ForegroundColor Red
    Write-Host "Please review the error messages above and fix the configuration." -ForegroundColor Red
    $global:LASTEXITCODE = 1
    return
}

Write-Host ">>> Checking database connection..." -ForegroundColor Yellow
Invoke-PythonScript -ScriptPath "$CheckDbScript"
if ($LastExitCode -ne 0) {
    Write-Host "`n[ERROR] Database connection check failed." -ForegroundColor Red
    Write-Host "Please review the error messages above and fix the configuration." -ForegroundColor Red
    $global:LASTEXITCODE = 1
    return
}

# 4.5 Schema sync
Write-Host ">>> Checking database schema..." -ForegroundColor Yellow
Invoke-PythonScript -ScriptPath "$InitDbScript"
if ($LastExitCode -ne 0) {
    Write-Host "`n[ERROR] Database schema check failed." -ForegroundColor Red
    Write-Host "Please review the error messages above and fix the configuration." -ForegroundColor Red
    $global:LASTEXITCODE = 1
    return
}

# 5. Update Nginx config (absolute path fix)
$NginxConfPath = Join-Path -Path $ProjectRoot -ChildPath "nginx\nginx.conf"
if (Test-Path -LiteralPath "$NginxConfPath") {
    Write-Host ">>> Configuring Nginx paths..." -ForegroundColor Yellow

    $FrontendDistPath = Join-Path -Path $ProjectRoot -ChildPath "frontend\dist"
    $FrontendIndexPath = Join-Path -Path $FrontendDistPath -ChildPath "index.html"
    if (-not (Test-Path -LiteralPath "$FrontendIndexPath")) {
        Write-Host "`n[ERROR] Frontend build output not found: $FrontendIndexPath" -ForegroundColor Red
        Write-Host "Run scripts\bootstrap_clone.ps1 -InitFrontend -BuildFrontend or build frontend/dist manually before using start_system.bat." -ForegroundColor Red
        $global:LASTEXITCODE = 1
        return
    }

    $NginxBase = Split-Path -Parent "$NginxExe"
    $SrcMime = Join-Path -Path $NginxBase -ChildPath "conf\mime.types"
    $DestMime = Join-Path -Path $ProjectRoot -ChildPath "nginx\mime.types"
    if (-not (Test-Path -LiteralPath "$DestMime") -and (Test-Path -LiteralPath "$SrcMime")) {
        Copy-Item -Path "$SrcMime" -Destination "$DestMime" -Force
    }

    $ForwardRoot = $ProjectRoot.Replace([char]92, [char]47)
    $FrontendDist = "$ForwardRoot/frontend/dist"
    $ImageCache = "$ForwardRoot/backend/image_cache"
    $NginxClientAllowFile = Join-Path -Path $ProjectRoot -ChildPath "nginx\client_allow.conf"

    if (-not (Test-Path -LiteralPath "$ProjectRoot/backend/image_cache")) {
        New-Item -ItemType Directory -Path "$ProjectRoot/backend/image_cache" -Force | Out-Null
    }
    Write-NginxClientAllowFile -Path "$NginxClientAllowFile"

    $ConfContent = Get-Content -LiteralPath "$NginxConfPath" -Raw
    $NewConfContent = $ConfContent -replace 'root\s+[^;]+;', "root   `"$FrontendDist`";"
    $NewConfContent = $NewConfContent -replace 'alias\s+[^;]+;', "alias  `"$ImageCache/`";"
    $ClientAllowForwardPath = "$ForwardRoot/nginx/client_allow.conf"
    $NewConfContent = $NewConfContent -replace 'include\s+"[^"]*client_allow\.conf";', "include      `"$ClientAllowForwardPath`";"
    $BackendProxy = "http://127.0.0.1:$ServerPort"
    $NewConfContent = [regex]::Replace(
        $NewConfContent,
        '(location\s+/api/\s*\{[\s\S]*?proxy_pass\s+)http://(127\.0\.0\.1|localhost):\d+(;)',
        "`${1}$BackendProxy`${3}"
    )
    $NewConfContent = [regex]::Replace(
        $NewConfContent,
        '(location\s+/api/tasks/active/stream\s*\{[\s\S]*?proxy_pass\s+)http://(127\.0\.0\.1|localhost):\d+(;)',
        "`${1}$BackendProxy`${3}"
    )
    $NewConfContent = [regex]::Replace(
        $NewConfContent,
        '(location\s+/api/tasks/runtime-summary/stream\s*\{[\s\S]*?proxy_pass\s+)http://(127\.0\.0\.1|localhost):\d+(;)',
        "`${1}$BackendProxy`${3}"
    )
    $NewConfContent = [regex]::Replace(
        $NewConfContent,
        '(location\s+/api/cluster/\s*\{[\s\S]*?proxy_pass\s+)http://(127\.0\.0\.1|localhost):\d+(;)',
        "`${1}$BackendProxy`${3}"
    )
    # 使用 UTF8 无 BOM 编码写入
    $Utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText("$NginxConfPath", $NewConfContent, $Utf8NoBom)
}

# 6. Start backend
Write-Host ">>> Launching backend (FastAPI)..." -ForegroundColor Green
if ($UseCondaRun) {
    Write-Host ">>> Using Conda env python: $PythonExe (env=$CondaEnvName)"
} else {
    Write-Host ">>> Using Python: $PythonExe"
}

$BackendProc = Start-PythonBackground -ScriptPath "run_backend.py"
if (-not (Assert-ProcessAlive -Process $BackendProc -DisplayName "Backend")) {
    return
}
if (-not (Wait-BackendReady -Port $ServerPort -TimeoutSeconds $BackendReadyTimeoutSeconds)) {
    return
}

# 6.5 Start job worker
Write-Host ">>> Launching job worker..." -ForegroundColor Green
$WorkerProc = Start-PythonBackground -ScriptPath "run_worker.py"
if (-not (Assert-ProcessAlive -Process $WorkerProc -DisplayName "Worker")) {
    return
}

# 6.6 Start tile-server
Start-TileServer
if ($global:LASTEXITCODE -eq 1) {
    return
}

# 7. Start Nginx
if (Test-Path -LiteralPath "$NginxExe") {
    Write-Host ">>> Launching Nginx..." -ForegroundColor Green
    $NginxDir = Split-Path -Parent "$NginxExe"
    $NginxName = Split-Path -Leaf $NginxExe

    $SafeConfPath = $NginxConfPath.Replace([char]92, [char]47)
    $NginxArgs = @("-c", "$SafeConfPath")
    $NginxLogs = Get-BackgroundProcessLogs -ScriptPath "$NginxName"
    Write-Host "    stdout -> $($NginxLogs.StdOut)"
    Write-Host "    stderr -> $($NginxLogs.StdErr)"
    $NginxProc = Start-Process `
        -FilePath "$NginxExe" `
        -ArgumentList $NginxArgs `
        -WorkingDirectory "$NginxDir" `
        -PassThru `
        -NoNewWindow `
        -RedirectStandardOutput "$($NginxLogs.StdOut)" `
        -RedirectStandardError "$($NginxLogs.StdErr)"

    Start-Sleep -Seconds 2

    $NginxProcName = $NginxName -replace '\.exe$', ''
    $NginxRunning = Get-Process -Name $NginxProcName -ErrorAction SilentlyContinue

    if ($NginxRunning) {
        $DisplayHost = "localhost"
        if ($ServerHost) { $DisplayHost = $ServerHost }
        Write-Host ""
        Write-StatusLine "============================================================" Green
        Write-StatusLine "SUCCESS: InSAR Management System is running!" Green
        Write-StatusLine "Frontend (via Nginx): http://$DisplayHost"
        Write-StatusLine "Backend (internal):  http://127.0.0.1`:$ServerPort"
        Write-StatusLine "API Docs (internal): http://127.0.0.1`:$ServerPort/docs"
        if ($TileServerAutoStart -and $TileServerUrl) {
            Write-StatusLine "Tile Server:         $TileServerUrl"
        }
        Write-StatusLine "============================================================" Green
        Write-Host ""
        Write-StatusLine "System is running. Press Ctrl+C to stop all services." Yellow
    } else {
        Write-Warning "Nginx process not found. Check logs/nginx_error.log for details."
    }
} else {
    Write-Warning "Nginx executable not found at: $NginxExe"
}

# 8. Wait for exit
try {
    Write-Host ""
    Write-StatusLine "Waiting for processes (Backend PID: $($BackendProc.Id), Worker PID: $($WorkerProc.Id))..." Cyan

    $BackgroundJob = Register-ObjectEvent -InputObject $BackendProc -EventName "Exited" -Action { Write-Host "`nBackend process exited." -ForegroundColor Red } -ErrorAction SilentlyContinue

    Wait-Process -Id $BackendProc.Id -ErrorAction SilentlyContinue

} finally {
    Write-Host "`nShutting down..." -ForegroundColor Yellow
    Stop-Backend-By-Cmdline -MatchText "run_backend.py"
    Stop-Backend-By-Cmdline -MatchText "run_worker.py"
    Stop-Process-By-Name -Name $NginxProcName -ExeName $NginxExe
    Stop-TileServer
    Write-Host "Done." -ForegroundColor Green
}
