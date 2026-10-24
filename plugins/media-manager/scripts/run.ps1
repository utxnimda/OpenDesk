[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$WorkspaceRoot = "",
    [string]$ListenHost = "127.0.0.1",
    [int]$Port = 8765,
    [switch]$StrictPort,
    [switch]$AllowRemote
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptRoot
. (Join-Path $scriptRoot "lib\workspace.ps1")
$Python = Get-MediaManagerPython -RepositoryRoot $repoRoot -Python $Python

$runArguments = @(
    "-m", "work_catalog_yaml",
    "jp-tv", "browse",
    "--host", $ListenHost,
    "--port", [string]$Port
)
if ($StrictPort) {
    $runArguments += "--strict-port"
}
if ($AllowRemote) {
    $runArguments += "--allow-remote"
}

Invoke-MediaManagerWorkspace -RepositoryRoot $repoRoot -WorkspaceRoot $WorkspaceRoot -Action {
    & $Python @runArguments
}
exit $LASTEXITCODE
