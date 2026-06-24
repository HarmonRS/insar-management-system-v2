param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$PostgresDataDir = "D:\PostgreSQLData",
    [string]$PostgresServiceName = "postgresql-x64-17",
    [string]$FirewallRuleName = "InSAR PostgreSQL 5432 LandSAR Cluster",
    [int]$PostgresPort = 5432
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

function Normalize-WorkerAddress {
    param([string]$Raw)
    $value = $Raw.Trim()
    if (-not $value) {
        return $null
    }
    if ($value -match "/") {
        $parts = $value -split "/", 2
        $ip = $parts[0]
        $prefix = [int]$parts[1]
        if ($prefix -lt 0 -or $prefix -gt 32) {
            throw "Invalid CIDR prefix: $value"
        }
    } else {
        $ip = $value
        $prefix = 32
    }
    $parsed = $null
    if (-not [System.Net.IPAddress]::TryParse($ip, [ref]$parsed)) {
        throw "Invalid IP address: $value"
    }
    if ($parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
        throw "Only IPv4 addresses are supported for LandSAR cluster workers: $value"
    }
    return "$ip/$prefix"
}

$envPath = Join-Path $RepoRoot ".env"
$allowedRaw = Read-DotEnvValue -Path $envPath -Name "LANDSAR_CLUSTER_ALLOWED_WORKER_IPS"
if (-not $allowedRaw) {
    throw "LANDSAR_CLUSTER_ALLOWED_WORKER_IPS is empty. Set it in .env, for example: LANDSAR_CLUSTER_ALLOWED_WORKER_IPS=192.168.1.6"
}

$allowed = @()
foreach ($part in ($allowedRaw -split "[,;]")) {
    $normalized = Normalize-WorkerAddress $part
    if ($normalized -and ($allowed -notcontains $normalized)) {
        $allowed += $normalized
    }
}
if (-not $allowed) {
    throw "No valid LandSAR cluster worker IPs found in LANDSAR_CLUSTER_ALLOWED_WORKER_IPS."
}

$hbaPath = Join-Path $PostgresDataDir "pg_hba.conf"
if (-not (Test-Path -LiteralPath $hbaPath)) {
    throw "pg_hba.conf not found: $hbaPath"
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
Copy-Item -LiteralPath $hbaPath -Destination "$hbaPath.bak_$timestamp"

$begin = "# BEGIN InSAR LandSAR cluster workers"
$end = "# END InSAR LandSAR cluster workers"
$content = Get-Content -LiteralPath $hbaPath
$newContent = New-Object System.Collections.Generic.List[string]
$inside = $false
foreach ($line in $content) {
    if ($line -eq $begin) {
        $inside = $true
        continue
    }
    if ($line -eq $end) {
        $inside = $false
        continue
    }
    if (-not $inside) {
        $newContent.Add($line)
    }
}

$newContent.Add("")
$newContent.Add($begin)
foreach ($address in $allowed) {
    $newContent.Add(("host    insar_management    all             {0,-20} scram-sha-256" -f $address))
}
$newContent.Add($end)
Set-Content -LiteralPath $hbaPath -Value $newContent -Encoding ASCII

$pgCtl = "C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe"
if (Test-Path -LiteralPath $pgCtl) {
    & $pgCtl reload -D $PostgresDataDir | Out-Host
} else {
    Restart-Service -Name $PostgresServiceName
}

$remoteAddresses = $allowed | ForEach-Object { ($_ -split "/", 2)[0] }
$rule = Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue
if (-not $rule) {
    New-NetFirewallRule `
        -DisplayName $FirewallRuleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $PostgresPort `
        -RemoteAddress $remoteAddresses `
        -Profile Any | Out-Null
} else {
    $rule | Set-NetFirewallRule -Enabled True -Direction Inbound -Action Allow -Profile Any
    $rule | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress $remoteAddresses
    $rule | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -Protocol TCP -LocalPort $PostgresPort
}

Write-Host "LandSAR cluster network access synced."
Write-Host ("Allowed workers: " + ($allowed -join ", "))
Write-Host "PostgreSQL hba: $hbaPath"
Write-Host "Firewall rule: $FirewallRuleName"
