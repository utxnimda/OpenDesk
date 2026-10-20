param([string]$Executable)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $Executable) { $Executable = Join-Path $projectRoot 'dist\NimdaDesk\NimdaDesk.exe' }
$Executable = (Resolve-Path -LiteralPath $Executable).Path
$runRoot = Join-Path $projectRoot ('.local\smoke\' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
$previousRoot = $env:NIMDADESK_DATA_DIR
$previousPython = $env:NIMDADESK_PYTHON
$previousSource = $env:NIMDADESK_WORKER_SOURCE
try {
    $env:NIMDADESK_DATA_DIR = $runRoot
    $env:NIMDADESK_PYTHON = $null
    $env:NIMDADESK_WORKER_SOURCE = $null
    # Start from another directory, with no source Python configuration.
    $process = Start-Process -FilePath $Executable -ArgumentList '--smoke-test' -WorkingDirectory $env:TEMP -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(55000)) {
        # Stop only this owned, still-live process via its retained Process object.
        $process.Kill()
        throw 'Desktop smoke timed out; owned test host was stopped.'
    }
    $process.Refresh()
    $reportPath = Join-Path $runRoot 'smoke-result.json'
    if (-not (Test-Path -LiteralPath $reportPath)) { throw "Desktop smoke did not produce its report. Exit=$($process.ExitCode)" }
    $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    if ($process.ExitCode -ne 0 -or $report.status -ne 'passed') {
        Get-Content -LiteralPath $reportPath
        throw "Desktop smoke failed. Exit=$($process.ExitCode)"
    }
    $workerPath = Join-Path ([IO.Path]::GetDirectoryName($Executable)) 'nimdadesk-worker.exe'
    $remaining = @(Get-CimInstance Win32_Process -Filter "Name='nimdadesk-worker.exe'" | Where-Object { $_.ExecutablePath -eq $workerPath })
    if ($remaining.Count -gt 0) { throw 'Desktop smoke left a packaged worker running.' }
    Get-Content -LiteralPath $reportPath
    Write-Output "Desktop smoke passed. Evidence: $reportPath"
} finally {
    $env:NIMDADESK_DATA_DIR = $previousRoot
    $env:NIMDADESK_PYTHON = $previousPython
    $env:NIMDADESK_WORKER_SOURCE = $previousSource
}
