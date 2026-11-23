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
$projectRoot = Get-MediaManagerProjectRoot -RepositoryRoot $repoRoot
$integrationRoot = Join-Path $repoRoot "integrations\opendesk"

$testRoots = @(
    (Join-Path $repoRoot "apps\framework\tests"),
    (Join-Path $repoRoot "apps\features"),
    (Join-Path $integrationRoot "tests")
) | Where-Object { Test-Path -LiteralPath $_ -PathType Container }
$testFiles = @($testRoots | ForEach-Object {
    Get-ChildItem -LiteralPath $_ -Recurse -File -Filter "test_*.py"
}) | Sort-Object FullName

# Use the same canonical root for fixtures and production paths on Windows.
$temporaryRootJson = & $Python -c 'import json, tempfile; from pathlib import Path; print(json.dumps(dict(root=str(Path(tempfile.gettempdir()).resolve(strict=True)))))'
if ($LASTEXITCODE -ne 0 -or -not $temporaryRootJson) {
    throw "Could not resolve the temporary root using the test Python."
}
$temporaryRoot = (ConvertFrom-Json -InputObject ($temporaryRootJson -join "`n")).root
if (-not $temporaryRoot -or -not (Test-Path -LiteralPath $temporaryRoot -PathType Container)) {
    throw "The resolved temporary root is not a directory."
}
$testWorkspace = Join-Path $temporaryRoot ("media-manager-tests-" + [Guid]::NewGuid().ToString("N"))
$testEnvironment = @{
    MEDIA_MANAGER_TEST_APPLICATION_ROOT = $repoRoot
    OPENDESK_TEST_ROOT = $projectRoot
    TEMP = $temporaryRoot
    TMP = $temporaryRoot
    TMPDIR = $temporaryRoot
}
$previousTestEnvironment = @{}
try {
    foreach ($name in $testEnvironment.Keys) {
        $previousTestEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, $testEnvironment[$name], "Process")
    }
    Initialize-MediaManagerWorkspace -RepositoryRoot $repoRoot -WorkspaceRoot $testWorkspace | Out-Null
    Invoke-MediaManagerWorkspace -RepositoryRoot $repoRoot -WorkspaceRoot $testWorkspace -Action {
        foreach ($testFile in $testFiles) {
            & $Python $testFile.FullName -v
            if ($LASTEXITCODE -ne 0) {
                throw "Python tests failed: $($testFile.FullName)"
            }
        }
        Write-Host "Python test files passed: $($testFiles.Count) files"

        if (-not $SkipJavaScript) {
            $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
            if ($null -eq $nodeCommand) {
                throw "Node.js is required for the complete media manager test suite."
            }
            $javascriptFiles = @(
                Get-ChildItem -LiteralPath (Join-Path $repoRoot "apps") -Recurse -File |
                    Where-Object {
                        $_.Extension -in @(".js", ".cjs", ".mjs") -and
                        $_.FullName -notlike "*\vendor\*"
                    }
                Get-ChildItem -LiteralPath $integrationRoot -Recurse -File |
                    Where-Object { $_.Extension -in @(".js", ".cjs", ".mjs") }
            ) | Sort-Object FullName -Unique
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

            $javascriptTestFiles = @(
                Get-ChildItem -LiteralPath (Join-Path $repoRoot "apps") -Recurse -File -Filter "test_*.js"
                Get-ChildItem -LiteralPath (Join-Path $integrationRoot "tests") -File -Filter "*.test.cjs"
            ) | Sort-Object FullName -Unique
            foreach ($javascriptTestFile in $javascriptTestFiles) {
                & $nodeCommand.Source --test $javascriptTestFile.FullName
                if ($LASTEXITCODE -ne 0) {
                    throw "JavaScript tests failed: $($javascriptTestFile.FullName)"
                }
            }
            Write-Host "JavaScript test files passed: $($javascriptTestFiles.Count) files"
        }
    }
} finally {
    foreach ($name in $previousTestEnvironment.Keys) {
        if ($null -eq $previousTestEnvironment[$name]) {
            Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($name, $previousTestEnvironment[$name], "Process")
        }
    }
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
