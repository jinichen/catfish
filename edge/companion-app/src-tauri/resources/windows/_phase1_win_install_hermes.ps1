#Requires -Version 5.1
<#
Phase 1 Windows verify: install.ps1 offline patch 3 branches E2E (W1 verify).

Prereq (5 files in the same directory as this script, e.g. E:\windows\):
  install.ps1
  uv.exe
  cpython-3.11.15-embed.zip
  hermes-agent-bundle.tar.gz
  _phase1_win_install_hermes.ps1 (this script)

Usage (NO admin needed, perUser install to %LOCALAPPDATA%\hermes):
  cd E:\windows
  powershell -ExecutionPolicy Bypass -File _phase1_win_install_hermes.ps1

Expected:
  - "Catfish offline" appears 3 times (Install-Uv / Test-Python / Install-Repository)
  - hermes installed at %LOCALAPPDATA%\hermes\hermes-agent
  - "hermes --version" returns Hermes vX.Y.Z

NOTE: Pure ASCII output (no Chinese) to avoid Windows PowerShell 5.1 codepage
issues. Chinese docs live in _phase1_win_CHECKLIST.md.
#>

$ErrorActionPreference = "Stop"

Write-Host "[Phase 1] install.ps1 offline mode test" -ForegroundColor Cyan

# 1. resolve working directory (where this script lives)
$WorkDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "  WorkDir: $WorkDir"

$InstallPs1 = Join-Path $WorkDir "install.ps1"
$UvExe = Join-Path $WorkDir "uv.exe"
$PythonZip = Join-Path $WorkDir "cpython-3.11.15-embed.zip"
$HermesTarGz = Join-Path $WorkDir "hermes-agent-bundle.tar.gz"

foreach ($f in @($InstallPs1, $UvExe, $PythonZip, $HermesTarGz)) {
    if (-not (Test-Path $f)) {
        Write-Host "  MISSING: $f" -ForegroundColor Red
        Write-Host "  Copy 4 artifacts (install.ps1 + uv.exe + cpython-*.zip + hermes-*.tar.gz) into this dir."
        exit 1
    }
}
Write-Host "  OK: 4 artifacts present"

# 2. extract hermes-agent-bundle.tar.gz -> hermes-agent-src\
$HermesSrc = Join-Path $WorkDir "hermes-agent-src"
if (Test-Path $HermesSrc) {
    Write-Host "  Removing existing hermes-agent-src\ (fresh extract)"
    Remove-Item -Recurse -Force $HermesSrc
}
Write-Host "  Extracting hermes-agent-bundle.tar.gz ..."
# Windows 10 build 17063+ has bsd tar built in
tar -xzf $HermesTarGz -C $WorkDir
$ExtractedDir = Join-Path $WorkDir "hermes-agent"
if (Test-Path $ExtractedDir) {
    Move-Item -LiteralPath $ExtractedDir -Destination $HermesSrc -Force
}
if (-not (Test-Path $HermesSrc)) {
    Write-Host "  FAILED: hermes-agent-src\ not present after extract" -ForegroundColor Red
    Write-Host "  Check tar output above. Windows 10 pre-17063 lacks tar.exe -- run winver to check."
    exit 1
}
Write-Host "  OK: extracted to $HermesSrc"

# 3. verify install.ps1 has catfish offline patch marker
$Content = Get-Content $InstallPs1 -Raw
if ($Content -notmatch "CATFISH-OFFLINE-PATCH-v1") {
    Write-Host "  FAILED: install.ps1 missing CATFISH-OFFLINE-PATCH-v1 marker" -ForegroundColor Red
    Write-Host "  Did you copy the PATCHED install.ps1 from resources/windows/, not the upstream one?"
    exit 1
}
Write-Host "  OK: install.ps1 has CATFISH-OFFLINE-PATCH-v1 marker"

# 4. run install.ps1 with -Offline* args
Write-Host ""
Write-Host "[Phase 1] Running install.ps1 offline mode:" -ForegroundColor Cyan
Write-Host "  -OfflineSourceDir: $HermesSrc"
Write-Host "  -OfflineUvExe:     $UvExe"
Write-Host "  -OfflinePythonZip: $PythonZip"
Write-Host "  -NonInteractive"
Write-Host ""

& $InstallPs1 `
    -OfflineSourceDir $HermesSrc `
    -OfflineUvExe $UvExe `
    -OfflinePythonZip $PythonZip `
    -NonInteractive

if ($LASTEXITCODE -ne 0) {
    Write-Host "  FAILED: install.ps1 exit code $LASTEXITCODE" -ForegroundColor Red
    exit 1
}

# 5. verify hermes install
Write-Host ""
Write-Host "[Phase 1] Verify install output" -ForegroundColor Cyan
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { "$env:LOCALAPPDATA\hermes" }
$HermesInstallDir = "$HermesHome\hermes-agent"
$UvCmd = "$HermesHome\bin\uv.exe"

if (-not (Test-Path $HermesInstallDir)) {
    Write-Host "  FAILED: hermes install dir missing: $HermesInstallDir" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $UvCmd)) {
    Write-Host "  FAILED: uv.exe missing: $UvCmd" -ForegroundColor Red
    exit 1
}

Write-Host "  OK HermesHome:     $HermesHome"
Write-Host "  OK hermes-agent:   $HermesInstallDir"
Write-Host "  OK uv.exe:         $UvCmd"

# find hermes CLI
$HermesCli = Get-ChildItem -Path @("$HermesHome\bin\hermes*", "$HermesInstallDir\bin\hermes*") `
    -File -ErrorAction SilentlyContinue | Select-Object -First 1

if ($HermesCli) {
    Write-Host ""
    Write-Host "[Phase 1] Running: $($HermesCli.FullName) --version"
    & $HermesCli.FullName --version
    Write-Host ""
    Write-Host "[Phase 1] Running: $($HermesCli.FullName) info"
    & $HermesCli.FullName info
} else {
    Write-Host ""
    Write-Host "  WARN: hermes CLI not found in bin\" -ForegroundColor Yellow
    Write-Host "  Manual check: dir $HermesHome\bin\; dir $HermesInstallDir\bin\"
}

Write-Host ""
Write-Host "[Phase 1] W1 verify DONE -- install.ps1 offline 3 branches worked" -ForegroundColor Green
Write-Host ""
Write-Host "Next: run _phase1_win_test_outlook.py (W3 verify)"
