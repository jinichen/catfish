param()
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$companion = Split-Path -Parent $scriptDir
$source = Join-Path (Split-Path -Parent $companion) 'wechat-reader'
$dest = Join-Path $companion 'src-tauri\resources\windows'
$stage = Join-Path $env:TEMP 'catfish-wechat-reader-dist'
$archive = Join-Path $dest 'catfish-wechat-reader-dist.tar.gz'

if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path $stage, $dest | Out-Null
python (Join-Path $source 'scripts\build_wheel.py') --output-dir $stage
if ($LASTEXITCODE -ne 0) { throw 'catfish-wechat-reader wheel build failed' }
$wheels = @(Get-ChildItem $stage -Filter '*.whl')
if ($wheels.Count -ne 1) { throw "expected one reader wheel, got $($wheels.Count)" }
if (Test-Path $archive) { Remove-Item -Force $archive }
tar -czf $archive -C $stage $wheels[0].Name
if ($LASTEXITCODE -ne 0) { throw 'reader distribution archive build failed' }
Write-Host "  OK catfish-wechat-reader-dist.tar.gz ($([math]::Round((Get-Item $archive).Length/1KB,1)) KB)" -ForegroundColor Green
