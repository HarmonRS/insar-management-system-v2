param(
    [Parameter(Mandatory = $true)]
    [string]$Root,

    [Parameter(Mandatory = $true)]
    [string]$SourcePool,

    [Parameter(Mandatory = $true)]
    [string[]]$Scenes
)

$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
    throw $Message
}

$rootPath = [System.IO.Path]::GetFullPath($Root)
$sourcePoolPath = [System.IO.Path]::GetFullPath($SourcePool)

if (-not (Test-Path -LiteralPath $sourcePoolPath -PathType Container)) {
    Fail "Source pool not found: $sourcePoolPath"
}

if (Test-Path -LiteralPath $rootPath) {
    Fail "Experiment root already exists: $rootPath"
}

$downloadDir = Join-Path $rootPath "pyint_stage\DOWNLOAD"
$inputDir = Join-Path $rootPath "input"
$templatesDir = Join-Path $rootPath "templates"
$logsDir = Join-Path $rootPath "logs"
$demStoreDir = Join-Path $rootPath "dem_store"

New-Item -ItemType Directory -Path $downloadDir -Force | Out-Null
New-Item -ItemType Directory -Path $inputDir -Force | Out-Null
New-Item -ItemType Directory -Path $templatesDir -Force | Out-Null
New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
New-Item -ItemType Directory -Path $demStoreDir -Force | Out-Null

$manifestLines = @(
    "Experiment Root: $rootPath"
    "Created: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    "Source Pool: $sourcePoolPath"
    "Scenes:"
)

$copiedScenes = @()

foreach ($scene in $Scenes) {
    $sceneSourceDir = Join-Path $sourcePoolPath $scene
    if (-not (Test-Path -LiteralPath $sceneSourceDir -PathType Container)) {
        Fail "Scene not found: $sceneSourceDir"
    }

    $sceneInputDir = Join-Path $inputDir $scene
    New-Item -ItemType Directory -Path $sceneInputDir -Force | Out-Null

    $files = Get-ChildItem -LiteralPath $sceneSourceDir -File
    if ($files.Count -eq 0) {
        Fail "No files found in scene: $sceneSourceDir"
    }

    foreach ($file in $files) {
        Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $sceneInputDir $file.Name)
        Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $downloadDir $file.Name)
    }

    $manifestLines += "$scene | files=$($files.Count)"
    $copiedScenes += [pscustomobject]@{
        scene = $scene
        file_count = $files.Count
    }
}

$manifestPath = Join-Path $rootPath "MANIFEST.txt"
Set-Content -LiteralPath $manifestPath -Value $manifestLines -Encoding UTF8

$result = [pscustomobject]@{
    root = $rootPath
    download_dir = $downloadDir
    input_dir = $inputDir
    manifest = $manifestPath
    scenes = $copiedScenes
}

$result | ConvertTo-Json -Depth 4
