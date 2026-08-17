[CmdletBinding()]
param(
    [string]$OutputDirectory = "dist",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$deployDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $deployDirectory
$pyprojectPath = Join-Path $projectRoot "pyproject.toml"
$pyprojectText = Get-Content -LiteralPath $pyprojectPath -Raw -Encoding utf8
$versionMatch = [regex]::Match($pyprojectText, '(?m)^version\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) {
    throw "Cannot read version from pyproject.toml"
}
$version = $versionMatch.Groups[1].Value
$packageName = "agentd-primer-probe-server-$version"

if ([IO.Path]::IsPathRooted($OutputDirectory)) {
    $outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
} else {
    $outputRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
}
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$archivePath = Join-Path $outputRoot "$packageName.zip"
$checksumPath = "$archivePath.sha256"
foreach ($destination in @($archivePath, $checksumPath)) {
    if (Test-Path -LiteralPath $destination) {
        if (-not $Force) {
            throw "Output already exists: $destination. Use -Force to overwrite."
        }
        Remove-Item -LiteralPath $destination -Force
    }
}

$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ("agentd-package-" + [guid]::NewGuid().ToString("N"))
$stageRoot = Join-Path $temporaryRoot $packageName
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

try {
    $topLevelFiles = @(".dockerignore", ".gitattributes", ".gitignore", "pyproject.toml", "README.md")
    foreach ($relativePath in $topLevelFiles) {
        $sourcePath = Join-Path $projectRoot $relativePath
        if (Test-Path -LiteralPath $sourcePath) {
            Copy-Item -LiteralPath $sourcePath -Destination (Join-Path $stageRoot $relativePath)
        }
    }

    $sourceRoots = @("config", "deploy", "docs", "examples", "schemas", "skills", "src\agentd")
    $projectPrefix = $projectRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    foreach ($sourceRoot in $sourceRoots) {
        $absoluteSourceRoot = Join-Path $projectRoot $sourceRoot
        Get-ChildItem -LiteralPath $absoluteSourceRoot -File -Recurse -Force | ForEach-Object {
            $relativePath = $_.FullName.Substring($projectPrefix.Length)
            $normalized = $relativePath.Replace([char]92, [char]47)
            if ($normalized -eq "deploy/.env" -or
                $normalized.StartsWith("deploy/server-data/") -or
                $normalized.Contains("/__pycache__/") -or
                $normalized.EndsWith(".pyc")) {
                return
            }
            $destination = Join-Path $stageRoot $relativePath
            $destinationParent = Split-Path -Parent $destination
            New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $destination
        }
    }

    $stagePrefix = $stageRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $manifestLines = Get-ChildItem -LiteralPath $stageRoot -File -Recurse -Force |
        Sort-Object FullName |
        ForEach-Object {
            $relativePath = $_.FullName.Substring($stagePrefix.Length).Replace([char]92, [char]47)
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            "$hash  $relativePath"
        }
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllLines((Join-Path $stageRoot "PACKAGE-MANIFEST.sha256"), $manifestLines, $utf8WithoutBom)

    # Compress-Archive on Windows may retain backslashes in entry names.  The
    # package is unpacked on Linux servers, so create ZIP entries explicitly
    # with the portable forward-slash separator required by the ZIP format.
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::Open(
        $archivePath,
        [System.IO.Compression.ZipArchiveMode]::Create
    )
    try {
        Get-ChildItem -LiteralPath $stageRoot -File -Recurse -Force |
            Sort-Object FullName |
            ForEach-Object {
                $relativePath = $_.FullName.Substring($stagePrefix.Length).Replace([char]92, [char]47)
                $entryName = "$packageName/$relativePath"
                [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                    $archive,
                    $_.FullName,
                    $entryName,
                    [System.IO.Compression.CompressionLevel]::Optimal
                ) | Out-Null
            }
    } finally {
        $archive.Dispose()
    }
    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText(
        $checksumPath,
        "$archiveHash  $([IO.Path]::GetFileName($archivePath))`n",
        $utf8WithoutBom
    )
} finally {
    $resolvedTemporaryRoot = [IO.Path]::GetFullPath($temporaryRoot)
    $resolvedSystemTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if ($resolvedTemporaryRoot.StartsWith($resolvedSystemTemp) -and (Test-Path -LiteralPath $resolvedTemporaryRoot)) {
        Remove-Item -LiteralPath $resolvedTemporaryRoot -Recurse -Force
    }
}

Write-Output "PACKAGE=$archivePath"
Write-Output "SHA256_FILE=$checksumPath"
