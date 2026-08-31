param(
    [Parameter(Mandatory=$true)][string]$DistributionPath
)
$ErrorActionPreference = 'Stop'
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
    & $UvExe pip install --python $PythonExe --no-deps $Wheels[0].FullName
    if ($LASTEXITCODE -ne 0) { throw "uv pip install reader failed: $LASTEXITCODE" }
    if (-not (Test-Path $ReaderExe)) { throw "reader entry point missing: $ReaderExe" }
    $doctor = & $ReaderExe doctor --json
    if ($LASTEXITCODE -ne 0) { throw 'reader doctor failed' }
    $report = $doctor | ConvertFrom-Json
    if (-not $report.read_only -or $report.modifies_wechat_app) {
        throw 'reader safety contract failed'
    }
    Write-Host "Catfish chat export reader installed: $ReaderExe"
} finally {
    if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
}
