param(
    [Parameter(Mandatory=$true)][string]$DistributionPath
)
$ErrorActionPreference = 'Stop'
function Assert-ReaderSafetyReport {
    param([string[]]$Doctor)
    $json = ($Doctor -join "`n").Trim()
    if (-not $json.StartsWith('{') -or -not $json.EndsWith('}')) {
        throw 'reader doctor expected one JSON object'
    }
    try {
        $report = $json | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw 'reader doctor returned invalid JSON (expected one safety report)'
    }
    $invalid = @()
    if ($report -is [array] -or $null -eq $report) { throw 'reader doctor returned no single report' }
    if (($report.protocol_version -isnot [int] -and $report.protocol_version -isnot [long]) -or
        $report.protocol_version -ne 1) { $invalid += 'protocol_version' }
    $expected = @{
        read_only = $true
        secure_key_store = $true
        ephemeral_plaintext_cache = $true
        modifies_wechat_app = $false
    }
    foreach ($field in $expected.Keys) {
        $value = $report.$field
        # Missing fields and strings such as "false" must fail closed.
        if ($value -isnot [bool] -or $value -ne $expected[$field]) { $invalid += $field }
        Write-Host "Reader safety field ${field}: $value"
    }
    if ($invalid.Count -gt 0) {
        throw "reader safety contract failed: $($invalid -join ', ')"
    }
}

$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { "$env:LOCALAPPDATA\hermes" }
$InstallDir = Join-Path $HermesHome 'hermes-agent'
$UvExe = Join-Path $HermesHome 'bin\uv.exe'
$PythonExe = Join-Path $InstallDir 'venv\Scripts\python.exe'
$ReaderExe = Join-Path $InstallDir 'venv\Scripts\catfish-wechat-reader.exe'
$Stage = Join-Path $env:TEMP ("catfish-wechat-reader-" + [guid]::NewGuid().ToString('N'))

foreach ($path in @($DistributionPath, $UvExe, $PythonExe)) {
    if (-not (Test-Path $path)) { throw "required file missing: $path" }
}
try {
    New-Item -ItemType Directory -Force -Path $Stage | Out-Null
    tar -xzf $DistributionPath -C $Stage
    if ($LASTEXITCODE -ne 0) { throw "reader distribution extract failed: $LASTEXITCODE" }
    $Wheels = @(Get-ChildItem -LiteralPath $Stage -Filter '*.whl' -File)
    if ($Wheels.Count -ne 1) { throw "expected one reader wheel, got $($Wheels.Count)" }
    # Same version does not imply the same wheel; replace stale installs without network.
    & $UvExe pip install --offline --reinstall --python $PythonExe --no-deps $Wheels[0].FullName
    if ($LASTEXITCODE -ne 0) { throw "uv pip install reader failed: $LASTEXITCODE" }
    if (-not (Test-Path $ReaderExe)) { throw "reader entry point missing: $ReaderExe" }
    $doctor = & $ReaderExe doctor --json
    if ($LASTEXITCODE -ne 0) { throw 'reader doctor failed' }
    Assert-ReaderSafetyReport -Doctor $doctor
    Write-Host "Catfish chat export reader installed: $ReaderExe"
} finally {
    if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
}
