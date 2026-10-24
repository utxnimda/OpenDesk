# Shared source/workspace setup for launch, benchmark and test entrypoints.
# Dot-source this file; no environment or working-directory mutation occurs here.

function Get-MediaManagerBackendRoots {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$RepositoryRoot)

    $resolvedRoot = (Resolve-Path -LiteralPath $RepositoryRoot -ErrorAction Stop).ProviderPath
    $frameworkRoot = Join-Path $resolvedRoot "apps\framework\backend"
    if (-not (Test-Path -LiteralPath $frameworkRoot -PathType Container)) {
        throw "Media Manager framework backend is missing: $frameworkRoot"
    }
    $frameworkRoot
    $compositionRoot = Join-Path $resolvedRoot "apps\composition\backend"
    if (Test-Path -LiteralPath $compositionRoot -PathType Container) { $compositionRoot }
    $featuresRoot = Join-Path $resolvedRoot "apps\features"
    if (Test-Path -LiteralPath $featuresRoot -PathType Container) {
        Get-ChildItem -LiteralPath $featuresRoot -Directory |
            ForEach-Object { Join-Path $_.FullName "backend" } |
            Where-Object { Test-Path -LiteralPath $_ -PathType Container } |
            Sort-Object
    }
}

function Get-MediaManagerProjectRoot {
    param([Parameter(Mandatory = $true)][string]$RepositoryRoot)
    $sourceRoot = (Resolve-Path -LiteralPath $RepositoryRoot -ErrorAction Stop).ProviderPath
    [IO.Path]::GetFullPath((Join-Path $sourceRoot "..\.."))
}

function Get-MediaManagerWorkspaceRoot {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [string]$WorkspaceRoot = ""
    )
    $candidate = if ([string]::IsNullOrWhiteSpace($WorkspaceRoot)) {
        Join-Path (Get-MediaManagerProjectRoot -RepositoryRoot $RepositoryRoot) "workspaces\media-manager"
    } else {
        $WorkspaceRoot
    }
    if ($candidate -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+(?:\\|$))') {
        throw "WorkspaceRoot must be an absolute path: $candidate"
    }
    $resolvedWorkspace = (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).ProviderPath
    foreach ($name in @("config", "data")) {
        if (-not (Test-Path -LiteralPath (Join-Path $resolvedWorkspace $name) -PathType Container)) {
            throw "Workspace is missing '$name': $resolvedWorkspace. Use init-workspace.ps1 for a new empty workspace."
        }
    }
    $resolvedWorkspace
}

function Get-MediaManagerPython {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [string]$Python = ""
    )
    if (-not [string]::IsNullOrWhiteSpace($Python)) { return $Python }
    $candidate = Join-Path (Get-MediaManagerProjectRoot -RepositoryRoot $RepositoryRoot) ".venvs\media-manager\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "Build Python is missing: $candidate. Create the environment described in plugins/media-manager/README.md or pass -Python explicitly."
    }
    $candidate
}

function Initialize-MediaManagerWorkspace {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$WorkspaceRoot
    )
    if ($WorkspaceRoot -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+(?:\\|$))') { throw "WorkspaceRoot must be an absolute path." }
    $destination = [IO.Path]::GetFullPath($WorkspaceRoot)
    if (Test-Path -LiteralPath $destination) { throw "Workspace already exists; no files were changed: $destination" }
    $templates = Join-Path $RepositoryRoot "defaults\config"
    if (-not (Test-Path -LiteralPath $templates -PathType Container)) { throw "Public workspace templates are missing." }
    New-Item -ItemType Directory -Path $destination -ErrorAction Stop | Out-Null
    Copy-Item -LiteralPath $templates -Destination (Join-Path $destination "config") -Recurse -ErrorAction Stop
    foreach ($relative in @("data\features\collection-detail\db", "data\features\collection-info\db", "data\source", "data\finished")) {
        New-Item -ItemType Directory -Path (Join-Path $destination $relative) -Force -ErrorAction Stop | Out-Null
    }
    $destination
}

function Invoke-MediaManagerWorkspace {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [string]$WorkspaceRoot = "",
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [switch]$Utf8PythonIO
    )

    $resolvedRoot = (Resolve-Path -LiteralPath $RepositoryRoot -ErrorAction Stop).ProviderPath
    $backendRoots = @(Get-MediaManagerBackendRoots -RepositoryRoot $resolvedRoot)
    $resolvedWorkspace = Get-MediaManagerWorkspaceRoot -RepositoryRoot $resolvedRoot -WorkspaceRoot $WorkspaceRoot
    $workspaceEnvironment = @{
        PYTHONDONTWRITEBYTECODE = "1"
        PYTHONPATH = [string]::Join([IO.Path]::PathSeparator, $backendRoots)
        MEDIA_MANAGER_APPLICATION_ROOT = $resolvedRoot
        MEDIA_MANAGER_WORKSPACE_ROOT = $resolvedWorkspace
    }
    if ($Utf8PythonIO) { $workspaceEnvironment.PYTHONIOENCODING = "utf-8" }
    $previousEnvironment = @{}
    foreach ($name in $workspaceEnvironment.Keys) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    }

    Push-Location -LiteralPath $resolvedWorkspace
    try {
        foreach ($name in $workspaceEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $workspaceEnvironment[$name], "Process")
        }
        & $Action
    } finally {
        foreach ($name in $previousEnvironment.Keys) {
            if ($null -eq $previousEnvironment[$name]) {
                Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue
            } else {
                [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
            }
        }
        Pop-Location
    }
}
