# Clone bootstrap helper for fresh Windows deployments.
[CmdletBinding()]
param(
    [switch]$InitWindowsConda,
    [switch]$InitFrontend,
    [switch]$BuildFrontend,
    [switch]$InitWslConda,
    [switch]$All,
    [switch]$SkipChecks
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location -LiteralPath $ProjectRoot

if ($All) {
    $InitWindowsConda = $true
    $InitFrontend = $true
    $BuildFrontend = $true
    $InitWslConda = $true
}

$EnvPath = Join-Path $ProjectRoot ".env"
$EnvExamplePath = Join-Path $ProjectRoot ".env.example"
$WindowsCondaSpec = Join-Path $ProjectRoot "environment.yml"
$WslCondaSpec = Join-Path $ProjectRoot "deploy\wsl\conda\insar_wsl_v1.environment.yml"
$FrontendDir = Join-Path $ProjectRoot "frontend"
$CheckRuntimeScript = Join-Path $ProjectRoot "scripts\check_runtime_config.py"

function Write-Section {
    param([string]$Title)

    Write-Host ""
    Write-Host "==> $Title" -ForegroundColor Cyan
}

function Write-Info {
    param([string]$Message)

    Write-Host "[INFO] $Message" -ForegroundColor DarkCyan
}

function Write-WarnMessage {
    param([string]$Message)

    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Get-FirstNonEmptyValue {
    param(
        [object[]]$Values,
        [string]$Fallback = ""
    )

    foreach ($value in $Values) {
        $trimmed = "$value".Trim().Trim('"').Trim("'")
        if ($trimmed) {
            return $trimmed
        }
    }

    return $Fallback
}

function Read-EnvFile {
    param([string]$Path)

    $map = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $map
    }

    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }

        $parts = $trimmed.Split("=", 2)
        $key = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        $map[$key] = $value
    }

    return $map
}

function Resolve-CommandPath {
    param([string[]]$Candidates)

    foreach ($candidate in $Candidates) {
        $trimmed = "$candidate".Trim().Trim('"').Trim("'")
        if (-not $trimmed) {
            continue
        }

        if (Test-Path -LiteralPath $trimmed) {
            $resolved = Resolve-Path -LiteralPath $trimmed -ErrorAction SilentlyContinue
            if ($resolved) {
                return $resolved.Path
            }
            return $trimmed
        }

        $command = Get-Command -Name $trimmed -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) {
            if ($command.Source) {
                return $command.Source
            }
            if ($command.Path) {
                return $command.Path
            }
            return $trimmed
        }
    }

    return $null
}

function Get-CondaEnvNameFromSpec {
    param(
        [string]$SpecPath,
        [string]$FallbackName
    )

    if (-not (Test-Path -LiteralPath $SpecPath)) {
        return $FallbackName
    }

    $match = Select-String -Path $SpecPath -Pattern '^\s*name:\s*([^\s#]+)\s*$' | Select-Object -First 1
    if ($match -and $match.Matches.Count -gt 0) {
        return $match.Matches[0].Groups[1].Value.Trim()
    }

    return $FallbackName
}

function Get-WindowsCondaEnvExists {
    param(
        [string]$CondaExe,
        [string]$EnvName
    )

    try {
        $envsJson = & "$CondaExe" env list --json 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $envsJson) {
            return $false
        }

        $envsInfo = $envsJson | ConvertFrom-Json
        foreach ($envPath in ($envsInfo.envs | Where-Object { $_ })) {
            if ((Split-Path -Leaf "$envPath") -eq $EnvName) {
                return $true
            }
        }
    } catch {
        return $false
    }

    return $false
}

function Resolve-CondaEnvPythonPath {
    param(
        [string]$CondaExe,
        [string]$EnvName
    )

    if (-not $CondaExe -or -not $EnvName) {
        return $null
    }

    try {
        $envsJson = & "$CondaExe" env list --json 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $envsJson) {
            return $null
        }

        $envsInfo = $envsJson | ConvertFrom-Json
        foreach ($envPath in ($envsInfo.envs | Where-Object { $_ })) {
            if ((Split-Path -Leaf "$envPath") -ne $EnvName) {
                continue
            }

            $pythonPath = Join-Path -Path "$envPath" -ChildPath "python.exe"
            if (Test-Path -LiteralPath $pythonPath) {
                $resolved = Resolve-Path -LiteralPath $pythonPath -ErrorAction SilentlyContinue
                if ($resolved) {
                    return $resolved.Path
                }
                return $pythonPath
            }
        }
    } catch {
        return $null
    }

    return $null
}

function Convert-WindowsPathToWsl {
    param([string]$WindowsPath)

    $resolved = Resolve-Path -LiteralPath $WindowsPath -ErrorAction SilentlyContinue
    $path = if ($resolved) { $resolved.Path } else { [System.IO.Path]::GetFullPath($WindowsPath) }

    if ($path -notmatch '^[A-Za-z]:\\') {
        throw "Path cannot be converted to a WSL mount path: $path"
    }

    $drive = $path.Substring(0, 1).ToLowerInvariant()
    $rest = $path.Substring(2).Replace('\', '/')
    return "/mnt/$drive$rest"
}

function Get-WslBootstrapPrefix {
    return @(
        "if [ -f ~/.bashrc ]; then source ~/.bashrc >/dev/null 2>&1; fi",
        "if ! command -v conda >/dev/null 2>&1 && [ -f ~/miniconda3/etc/profile.d/conda.sh ]; then . ~/miniconda3/etc/profile.d/conda.sh; fi",
        "if ! command -v conda >/dev/null 2>&1 && [ -f ~/anaconda3/etc/profile.d/conda.sh ]; then . ~/anaconda3/etc/profile.d/conda.sh; fi",
        "command -v conda >/dev/null 2>&1 || { echo 'conda not found inside WSL'; exit 127; }"
    ) -join "; "
}

function Invoke-WslCommand {
    param(
        [string]$Distro,
        [string]$Command,
        [switch]$AllowFailure
    )

    & "$WslExe" -d "$Distro" bash -lc "$Command"
    $exitCode = $LASTEXITCODE
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "WSL command failed for distro '$Distro' with exit code $exitCode."
    }
    return $exitCode
}

Write-Section "Clone bootstrap"
Write-Info "Project root: $ProjectRoot"

if (-not (Test-Path -LiteralPath $EnvPath)) {
    if (-not (Test-Path -LiteralPath $EnvExamplePath)) {
        throw "Missing .env.example at $EnvExamplePath"
    }

    Copy-Item -LiteralPath $EnvExamplePath -Destination $EnvPath
    Write-Info "Created .env from .env.example. Review it before starting the system."
} else {
    Write-Info ".env already exists. Keeping the current file."
}

$EnvMap = Read-EnvFile -Path $EnvPath
$CondaExe = Resolve-CommandPath -Candidates @($EnvMap["CONDA_EXE"], "conda", "conda.exe")
$NpmExe = Resolve-CommandPath -Candidates @("npm.cmd", "npm")
$PythonExe = Resolve-CommandPath -Candidates @($EnvMap["PYTHON_PATH"], "python", "py")
$WslExe = Resolve-CommandPath -Candidates @("wsl.exe", "wsl")

$WindowsCondaEnvName = Get-FirstNonEmptyValue -Values @(
    $EnvMap["CONDA_ENV_NAME"],
    (Get-CondaEnvNameFromSpec -SpecPath $WindowsCondaSpec -FallbackName "InSAR")
)

$WslDistro = Get-FirstNonEmptyValue -Values @(
    $EnvMap["WSL_DISTRO"],
    $EnvMap["TIMESERIES_WSL_DISTRO"],
    $EnvMap["ISCE2_WSL_DISTRO"],
    $EnvMap["PYINT_WSL_DISTRO"]
) -Fallback "Ubuntu-24.04"

$WslCondaEnvName = Get-FirstNonEmptyValue -Values @(
    $EnvMap["WSL_SHARED_CONDA_ENV"],
    $EnvMap["TIMESERIES_ENV_NAME"],
    (Get-CondaEnvNameFromSpec -SpecPath $WslCondaSpec -FallbackName "insar_wsl_v1")
) -Fallback "insar_wsl_v1"

if (-not $InitWindowsConda -and -not $InitFrontend -and -not $BuildFrontend -and -not $InitWslConda) {
    Write-Info "No optional install switches were requested. Use -All or individual switches to bootstrap runtimes."
}

if ($InitWindowsConda) {
    Write-Section "Windows Conda runtime"

    if (-not $CondaExe) {
        throw "Conda was not found. Set CONDA_EXE in .env or add conda to PATH."
    }
    if (-not (Test-Path -LiteralPath $WindowsCondaSpec)) {
        throw "Missing Windows conda spec: $WindowsCondaSpec"
    }

    if (Get-WindowsCondaEnvExists -CondaExe $CondaExe -EnvName $WindowsCondaEnvName) {
        Write-Info "Updating Windows conda env '$WindowsCondaEnvName' from environment.yml"
        & "$CondaExe" env update --name "$WindowsCondaEnvName" --file "$WindowsCondaSpec" --prune
    } else {
        Write-Info "Creating Windows conda env '$WindowsCondaEnvName' from environment.yml"
        & "$CondaExe" env create --name "$WindowsCondaEnvName" --file "$WindowsCondaSpec"
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Windows conda bootstrap failed."
    }

    $ResolvedWindowsPython = Resolve-CondaEnvPythonPath -CondaExe $CondaExe -EnvName $WindowsCondaEnvName
    if ($ResolvedWindowsPython) {
        $PythonExe = $ResolvedWindowsPython
        Write-Info "Suggested .env values:"
        Write-Host "  CONDA_EXE=$CondaExe"
        Write-Host "  CONDA_ENV_NAME=$WindowsCondaEnvName"
        Write-Host "  PYTHON_PATH=$ResolvedWindowsPython"
    } else {
        Write-WarnMessage "The conda environment was created or updated, but python.exe could not be resolved automatically."
    }
}

if ($InitFrontend) {
    Write-Section "Frontend dependencies"

    if (-not $NpmExe) {
        throw "npm was not found. Install Node.js and make sure npm is in PATH."
    }
    if (-not (Test-Path -LiteralPath $FrontendDir)) {
        throw "Missing frontend directory: $FrontendDir"
    }

    Push-Location $FrontendDir
    try {
        Write-Info "Running npm ci in frontend/"
        & "$NpmExe" ci
        if ($LASTEXITCODE -ne 0) {
            throw "npm ci failed."
        }
    } finally {
        Pop-Location
    }
}

if ($BuildFrontend) {
    Write-Section "Frontend build"

    if (-not $NpmExe) {
        throw "npm was not found. Install Node.js and make sure npm is in PATH."
    }
    if (-not (Test-Path -LiteralPath $FrontendDir)) {
        throw "Missing frontend directory: $FrontendDir"
    }

    Push-Location $FrontendDir
    try {
        Write-Info "Running npm run build in frontend/"
        & "$NpmExe" run build
        if ($LASTEXITCODE -ne 0) {
            throw "npm run build failed."
        }
    } finally {
        Pop-Location
    }
}

if ($InitWslConda) {
    Write-Section "WSL shared runtime"

    if (-not $WslExe) {
        throw "wsl.exe was not found."
    }
    if (-not (Test-Path -LiteralPath $WslCondaSpec)) {
        throw "Missing WSL conda spec: $WslCondaSpec"
    }

    $WslSpecPath = Convert-WindowsPathToWsl -WindowsPath $WslCondaSpec
    $Prefix = Get-WslBootstrapPrefix
    $ExistsCommand = "$Prefix; conda env list | grep -E ""^$WslCondaEnvName[[:space:]]"" >/dev/null"
    $ExistsExitCode = Invoke-WslCommand -Distro $WslDistro -Command $ExistsCommand -AllowFailure

    if ($ExistsExitCode -eq 0) {
        Write-Info "Updating WSL conda env '$WslCondaEnvName' in distro '$WslDistro'"
        $BootstrapCommand = "$Prefix; conda env update -n ""$WslCondaEnvName"" -f ""$WslSpecPath"" --prune"
    } else {
        Write-Info "Creating WSL conda env '$WslCondaEnvName' in distro '$WslDistro'"
        $BootstrapCommand = "$Prefix; conda env create -n ""$WslCondaEnvName"" -f ""$WslSpecPath"""
    }

    [void](Invoke-WslCommand -Distro $WslDistro -Command $BootstrapCommand)

    Write-Info "Suggested .env values:"
    Write-Host "  WSL_DISTRO=$WslDistro"
    Write-Host "  WSL_SHARED_CONDA_ENV=$WslCondaEnvName"
    Write-Host "  TIMESERIES_ENV_NAME=$WslCondaEnvName"
    Write-WarnMessage "Verify WSL_SHARED_PYTHON, ISCE2_PYTHON, TIMESERIES_PYTHON, and PYINT_WSL_PYTHON against the real conda install root inside WSL."
}

if (-not $SkipChecks) {
    Write-Section "Deployment validation"

    if (-not $PythonExe -and $CondaExe -and $WindowsCondaEnvName) {
        $PythonExe = Resolve-CondaEnvPythonPath -CondaExe $CondaExe -EnvName $WindowsCondaEnvName
    }

    if (-not $PythonExe) {
        Write-WarnMessage "No Windows Python interpreter was resolved. Skipping scripts/check_runtime_config.py."
    } elseif (-not (Test-Path -LiteralPath $CheckRuntimeScript)) {
        Write-WarnMessage "Missing validation script: $CheckRuntimeScript"
    } else {
        Write-Info "Using Python for validation: $PythonExe"
        & "$PythonExe" "$CheckRuntimeScript"
        if ($LASTEXITCODE -ne 0) {
            throw "Deployment configuration validation failed. Fix .env and rerun the bootstrap."
        }
    }
}

Write-Section "Next steps"
Write-Host "1. Review .env and align local paths, database settings, and WSL python paths."
Write-Host "2. Run start_system.bat when scripts/check_runtime_config.py passes."
Write-Host "3. Use GET /api/health after startup to confirm database, catalog, product_packages, and wsl_runtime are healthy."
