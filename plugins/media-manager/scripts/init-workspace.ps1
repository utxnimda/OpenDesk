[CmdletBinding()]
param([string]$WorkspaceRoot = "")

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptRoot
. (Join-Path $scriptRoot "lib\workspace.ps1")
if ([string]::IsNullOrWhiteSpace($WorkspaceRoot)) {
    $WorkspaceRoot = Join-Path (Get-MediaManagerProjectRoot -RepositoryRoot $repoRoot) "workspaces\media-manager"
}
Initialize-MediaManagerWorkspace -RepositoryRoot $repoRoot -WorkspaceRoot $WorkspaceRoot
