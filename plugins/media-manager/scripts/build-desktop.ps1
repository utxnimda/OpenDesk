[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$SharedWorkspace = "",
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptRoot
. (Join-Path $scriptRoot "lib\workspace.ps1")
$projectRoot = Get-MediaManagerProjectRoot -RepositoryRoot $repoRoot
$venvRoot = Join-Path $projectRoot ".venvs\media-manager"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$requirements = Join-Path $repoRoot "packaging\requirements-lock.txt"
$backendProject = Join-Path $repoRoot "apps\framework\backend"
$spec = Join-Path $repoRoot "packaging\media-manager-desktop.spec"
$sharedWorkspaceRoot = Get-MediaManagerWorkspaceRoot -RepositoryRoot $repoRoot -WorkspaceRoot $SharedWorkspace
$distRoot = Join-Path $projectRoot "dist\plugins\media-manager\standalone"
$buildRoot = Join-Path $projectRoot ".cache\media-manager\standalone"

if (-not (Test-Path -LiteralPath $venvPython)) {
    & $Python -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to create desktop build environment: $venvRoot"
    }
}

if (-not $SkipDependencyInstall) {
    & $venvPython -m pip install --disable-pip-version-check -r $requirements
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to install desktop build dependencies."
    }
    & $venvPython -m pip install --disable-pip-version-check --no-deps $backendProject
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to install Media manager desktop dependencies."
    }
}

Push-Location $repoRoot
try {
    & $venvPython -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath $distRoot `
        --workpath $buildRoot `
        $spec
    if ($LASTEXITCODE -ne 0) {
        throw "Media manager desktop build failed."
    }
} finally {
    Pop-Location
}

$exe = Join-Path $distRoot "MediaManager\MediaManager.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "Desktop executable was not generated: $exe"
}
$packageRoot = Split-Path -Parent $exe
$bundledConfigRoot = Join-Path $packageRoot "config"
$bundledFeatureDataRoot = Join-Path $packageRoot "data\features"
if (Test-Path -LiteralPath $bundledConfigRoot) {
    throw "Desktop package unexpectedly contains an internal config directory: $bundledConfigRoot"
}
if (Test-Path -LiteralPath $bundledFeatureDataRoot) {
    throw "Desktop package unexpectedly contains an internal feature database: $bundledFeatureDataRoot"
}

$desktopConfig = Join-Path $packageRoot "media-manager-desktop.yaml"
$workspaceYaml = $sharedWorkspaceRoot.Replace("\", "/").Replace("'", "''")
$desktopConfigLines = @(
    "# Media manager desktop bootstrap configuration."
    "# The desktop package contains no application database or normal feature config."
    "version: 1"
    "paths:"
    "  workspace_root: '$workspaceYaml'"
)
$desktopConfigText = [string]::Join([Environment]::NewLine, $desktopConfigLines) + [Environment]::NewLine
[IO.File]::WriteAllText($desktopConfig, $desktopConfigText, [Text.UTF8Encoding]::new($false))

$archive = Join-Path $distRoot "MediaManager-Windows-x64.zip"
Compress-Archive -LiteralPath $packageRoot -DestinationPath $archive -Force
& $venvPython (Join-Path $scriptRoot "verify-desktop.py") `
    --package $packageRoot --archive $archive --workspace $sharedWorkspaceRoot
if ($LASTEXITCODE -ne 0) {
    throw "Desktop package verification failed; do not distribute this build."
}
Write-Host "Media manager desktop package: $exe"
Write-Host "Media manager desktop config: $desktopConfig"
Write-Host "Media manager shared workspace: $sharedWorkspaceRoot"
Write-Host "Media manager portable archive: $archive"
