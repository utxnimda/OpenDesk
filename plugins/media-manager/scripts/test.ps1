[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$SkipJavaScript
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptRoot
. (Join-Path $scriptRoot "lib\workspace.ps1")
$Python = Get-MediaManagerPython -RepositoryRoot $repoRoot -Python $Python

$testRoots = @(
    (Join-Path $repoRoot "apps\framework\tests"),
    (Join-Path $repoRoot "apps\features")
) | Where-Object { Test-Path -LiteralPath $_ -PathType Container }
$testFiles = @($testRoots | ForEach-Object {
    Get-ChildItem -LiteralPath $_ -Recurse -File -Filter "test_*.py"
}) | Sort-Object FullName

$temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$testWorkspace = Join-Path $temporaryRoot ("media-manager-tests-" + [Guid]::NewGuid().ToString("N"))
try {
    Initialize-MediaManagerWorkspace -RepositoryRoot $repoRoot -WorkspaceRoot $testWorkspace | Out-Null
    Invoke-MediaManagerWorkspace -RepositoryRoot $repoRoot -WorkspaceRoot $testWorkspace -Action {
    foreach ($testFile in $testFiles) {
        & $Python $testFile.FullName -v
        if ($LASTEXITCODE -ne 0) {
            throw "Python tests failed: $($testFile.FullName)"
        }
    }

    if (-not $SkipJavaScript) {
        $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
        if ($null -eq $nodeCommand) {
            Write-Warning "Node.js is unavailable; JavaScript syntax checks were skipped."
        } else {
            $javascriptFiles = Get-ChildItem -LiteralPath (Join-Path $repoRoot "apps") `
                -Recurse -File -Filter "*.js" |
                Where-Object { $_.FullName -notlike "*\vendor\*" }
            foreach ($javascriptFile in $javascriptFiles) {
                if ($javascriptFile.FullName -match '[\\/]frontend[\\/]') {
                    # Browser frontend files are loaded as ES modules by main.js/App.js.
                    # Check stdin in module mode; package.json remains CommonJS for tests.
                    $previousOutputEncoding = $OutputEncoding
                    try {
                        $OutputEncoding = [Text.UTF8Encoding]::new($false)
                        Get-Content -LiteralPath $javascriptFile.FullName -Raw -Encoding UTF8 |
                            & $nodeCommand.Source --input-type=module --check
                    } finally {
                        $OutputEncoding = $previousOutputEncoding
                    }
                } else {
                    & $nodeCommand.Source --check $javascriptFile.FullName
                }
                if ($LASTEXITCODE -ne 0) {
                    throw "JavaScript syntax check failed: $($javascriptFile.FullName)"
                }
            }
            Write-Host "JavaScript syntax checks passed: $($javascriptFiles.Count) files"

            $javascriptTestFiles = Get-ChildItem -LiteralPath (Join-Path $repoRoot "apps") `
                -Recurse -File -Filter "test_*.js" |
                Sort-Object FullName
            foreach ($javascriptTestFile in $javascriptTestFiles) {
                & $nodeCommand.Source --test $javascriptTestFile.FullName
                if ($LASTEXITCODE -ne 0) {
                    throw "JavaScript tests failed: $($javascriptTestFile.FullName)"
                }
            }
            Write-Host "JavaScript test files passed: $($javascriptTestFiles.Count) files"
        }
    }
    }
} finally {
    $resolvedTestWorkspace = [IO.Path]::GetFullPath($testWorkspace)
    $temporaryBoundary = $temporaryRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (-not $resolvedTestWorkspace.StartsWith($temporaryBoundary, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($resolvedTestWorkspace) -notmatch '^media-manager-tests-[a-f0-9]{32}$') {
        throw "Refusing to clean a test workspace outside the temporary directory."
    }
    if (Test-Path -LiteralPath $resolvedTestWorkspace) {
        Remove-Item -LiteralPath $resolvedTestWorkspace -Recurse -Force -ErrorAction Stop
    }
}
