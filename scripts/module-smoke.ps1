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

$requiredPackages = @(
    'test-lab-1.1.0.ndpkg',
    'test-lab-2.0.0-incompatible.ndpkg',
    'test-extra-1.0.0.ndpkg',
    'test-lab-1.2.0-ui-failure.ndpkg'
)
foreach ($package in $requiredPackages) {
    if (-not (Test-Path -LiteralPath (Join-Path $Fixtures $package) -PathType Leaf)) {
        throw "Missing fixture: $package. Run npm run build:module-fixtures first."
    }
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
