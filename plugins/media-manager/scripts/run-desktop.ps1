[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$WorkspaceRoot = "",
    [int]$Port = 8765,
    [switch]$DebugWebView
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptRoot
. (Join-Path $scriptRoot "lib\workspace.ps1")
$Python = Get-MediaManagerPython -RepositoryRoot $repoRoot -Python $Python

$arguments = @("-m", "work_catalog_yaml.desktop", "--workspace", $repoRoot, "--port", [string]$Port)
if ($DebugWebView) {
    $arguments += "--debug"
}

Invoke-MediaManagerWorkspace -RepositoryRoot $repoRoot -WorkspaceRoot $WorkspaceRoot -Action {
    & $Python @arguments
}
exit $LASTEXITCODE
