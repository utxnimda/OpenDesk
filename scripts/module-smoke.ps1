param(
    [string]$Executable,
    [string]$Fixtures
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $Executable) { $Executable = Join-Path $projectRoot 'dist\OpenDesk\OpenDesk.exe' }
if (-not $Fixtures) { $Fixtures = Join-Path $projectRoot 'dist\module-fixtures' }
$Executable = (Resolve-Path -LiteralPath $Executable).Path
$Fixtures = (Resolve-Path -LiteralPath $Fixtures).Path
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { throw 'A module-aware host executable is required.' }
if (-not (Test-Path -LiteralPath $Fixtures -PathType Container)) { throw 'A fixture directory is required.' }

$indexPath = Join-Path $Fixtures 'fixtures.json'
$indexFile = Get-Item -LiteralPath $indexPath
if ($indexFile.PSIsContainer -or ($indexFile.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'The fixture index must be a regular file inside the fixture directory.'
}
$fixtureIndex = Get-Content -LiteralPath $indexPath -Raw | ConvertFrom-Json
if ($fixtureIndex.schemaVersion -ne 1 -or $fixtureIndex.testFixtures -ne $true -or $fixtureIndex.packages -isnot [array]) {
    throw 'fixtures.json must declare schemaVersion 1, testFixtures true and a packages array.'
}
$requiredFixtures = [ordered]@{
    'baseline' = @{ id = 'test-lab'; version = $null }
    'compatible-update' = @{ id = 'test-lab'; version = '1.1.0' }
    'incompatible' = @{ id = 'test-lab'; version = '2.0.0' }
    'new-module' = @{ id = 'test-extra'; version = '1.0.0' }
    'ui-failure' = @{ id = 'test-lab'; version = '1.2.0' }
}
$baselineFixture = $null
foreach ($purpose in $requiredFixtures.Keys) {
    $purposeEntries = @($fixtureIndex.packages | Where-Object { $_.purpose -ceq $purpose })
    if ($purposeEntries.Count -ne 1) { throw "Exactly one '$purpose' fixture is required. Run npm run build:module-fixtures first." }
    $entry = $purposeEntries[0]
    $expected = $requiredFixtures[$purpose]
    if ($entry.id -cne $expected.id -or $entry.version -cnotmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' -or ($expected.version -and $entry.version -cne $expected.version)) {
        throw "Invalid '$purpose' fixture identity."
    }
    if ($entry.file -cnotmatch '^[a-z0-9][a-z0-9._-]*\.ndpkg$' -or $entry.sha256 -cnotmatch '^[a-f0-9]{64}$') {
        throw "Invalid '$purpose' fixture filename or SHA-256."
    }
    $packagePath = Join-Path $Fixtures $entry.file
    $packageFile = Get-Item -LiteralPath $packagePath
    if ($packageFile.PSIsContainer -or ($packageFile.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Fixture '$purpose' must be a regular package file, not a linked path."
    }
    if ((Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.sha256) {
        throw "SHA-256 mismatch for '$purpose' fixture."
    }
    if ($purpose -ceq 'baseline') { $baselineFixture = $entry }
}

$runRoot = Join-Path $projectRoot ('.local\module-smoke\' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss-fff') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $runRoot | Out-Null
$runRoot = [IO.Path]::GetFullPath($runRoot)
$rootPrefix = $runRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$environmentNames = @('OPENDESK_DATA_DIR', 'OPENDESK_MODULE_FIXTURES', 'OPENDESK_PYTHON', 'OPENDESK_WORKER_SOURCE', 'PYTHONHOME', 'PYTHONPATH')
$previousEnvironment = @{}
foreach ($name in $environmentNames) { $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$ownedProcess = $null

try {
    $env:OPENDESK_DATA_DIR = $runRoot
    $env:OPENDESK_MODULE_FIXTURES = $Fixtures
    foreach ($name in @('OPENDESK_PYTHON', 'OPENDESK_WORKER_SOURCE', 'PYTHONHOME', 'PYTHONPATH')) {
        Remove-Item -LiteralPath ('Env:\' + $name) -ErrorAction SilentlyContinue
    }
    # Retain the exact owned process object. Never stop another OpenDesk instance.
    $ownedProcess = Start-Process -FilePath $Executable -ArgumentList '--module-smoke' -WorkingDirectory $env:TEMP -WindowStyle Hidden -PassThru
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $exited = $false
    while ($timer.ElapsedMilliseconds -lt 140000) {
        $remaining = [int][Math]::Min(1000, 140000 - $timer.ElapsedMilliseconds)
        if ($remaining -gt 0 -and $ownedProcess.WaitForExit($remaining)) { $exited = $true; break }
    }
    if (-not $exited) {
        $ownedProcess.Refresh()
        if (-not $ownedProcess.HasExited) { $ownedProcess.Kill(); $null = $ownedProcess.WaitForExit(5000) }
        throw 'Module smoke exceeded 140 seconds; only its owned test host was stopped.'
    }
    $ownedProcess.Refresh()
    $reportPath = Join-Path $runRoot 'module-smoke-result.json'
    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        throw "Module smoke produced no report. Exit=$($ownedProcess.ExitCode). An existing normal instance was not stopped. Evidence root: $runRoot"
    }
    $reportText = Get-Content -LiteralPath $reportPath -Raw
    $report = $reportText | ConvertFrom-Json
    $leftovers = @(Get-CimInstance Win32_Process -Filter "Name='module-worker.exe'" | Where-Object {
        if (-not $_.ExecutablePath) { return $false }
        try { return [IO.Path]::GetFullPath($_.ExecutablePath).StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase) }
        catch { return $false }
    })
    if ($leftovers.Count -gt 0) {
        throw "Module smoke left $($leftovers.Count) worker process(es) inside this test root; they were not killed by name. Evidence: $reportPath"
    }
    if ($ownedProcess.ExitCode -ne 0 -or $report.status -ne 'passed') {
        Write-Output $reportText
        throw "Module smoke failed. Exit=$($ownedProcess.ExitCode). Evidence: $reportPath"
    }
    if ($report.initialBundleOnlyTower -ne $true -or $report.baselineImportedFromFixture -ne $true -or $report.baselineFixtureSha256 -cne $baselineFixture.sha256 -or $report.baseline.version -cne $baselineFixture.version) {
        throw "Module smoke did not verify the production bundle and explicit baseline import. Evidence: $reportPath"
    }
    Write-Output $reportText
    Write-Output "Module smoke passed. Evidence: $reportPath"
} finally {
    foreach ($name in $environmentNames) {
        if ($null -eq $previousEnvironment[$name]) {
            Remove-Item -LiteralPath ('Env:\' + $name) -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
        }
    }
    if ($null -ne $ownedProcess) { $ownedProcess.Dispose() }
}
