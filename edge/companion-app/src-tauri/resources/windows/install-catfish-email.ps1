param(
    [Parameter(Mandatory = $true)][string]$DistributionPath
)

$ErrorActionPreference = 'Stop'

# KEEP THIS FILE ASCII-ONLY. Windows PowerShell 5.1 reads a .ps1 without BOM in the
# system ANSI code page (GBK on Chinese Windows); UTF-8 Chinese text can then eat a
# closing quote and the whole script fails to parse (1.0.14). Guarded by a cargo test.

# Write our own output as UTF-8 so the bootstrap log (UTF-8 from Companion and uv)
# is not mixed with GBK. May fail without a console; that only affects the log.
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }

$HermesHome = if ($env:HERMES_HOME) {
    $env:HERMES_HOME
} else {
    Join-Path $env:LOCALAPPDATA 'hermes'
}
$InstallDir = Join-Path $HermesHome 'hermes-agent'
$PythonExe = Join-Path $InstallDir 'venv\Scripts\python.exe'
$UvExe = Join-Path $HermesHome 'bin\uv.exe'
$Stage = Join-Path $env:TEMP ("catfish-email-" + [guid]::NewGuid().ToString('N'))
$SkillsDir = Join-Path $HermesHome 'skills\productivity'
$SkillDst = Join-Path $SkillsDir 'catfish-email'

foreach ($path in @($DistributionPath, $PythonExe, $UvExe)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "catfish-email install input missing: $path"
    }
}

try {
    New-Item -ItemType Directory -Force -Path $Stage | Out-Null
    tar.exe -xzf $DistributionPath -C $Stage
    if ($LASTEXITCODE -ne 0) {
        throw "extract catfish-email distribution failed: $LASTEXITCODE"
    }

    $Wheels = @(Get-ChildItem -LiteralPath $Stage -Filter '*.whl' -File)
    $EmailWheel = @($Wheels | Where-Object { $_.Name -like 'catfish_email-*.whl' })
    if ($EmailWheel.Count -ne 1) {
        throw "distribution must contain exactly one catfish-email wheel, found $($EmailWheel.Count)"
    }

    # The Windows package may also carry pywin32; --no-index keeps uv off the network.
    # 9/24: this used to be --reinstall (force every wheel). Reinstalling the same
    # pywin32 312 means deleting pywin32_system32\pythoncom311.dll / pywintypes311.dll,
    # which Windows refuses while any running Python has them loaded (os error 5).
    # The install then stops half way and leaves pywin32 broken (missing RECORD).
    # Now only catfish-email is force-reinstalled; pywin32 only when it fails to import.
    $InstallArgs = @(
        'pip', 'install', '--python', $PythonExe,
        '--no-index', '--find-links', $Stage,
        '--reinstall-package', 'catfish-email'
    )
    # When the probe fails Python prints a traceback to stderr. Windows PowerShell 5.1
    # with ErrorActionPreference=Stop turns native stderr into a terminating error and
    # the script would exit exactly when a repair is needed, so relax it for the probe.
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $PythonExe -c "import win32api" *> $null
    $Pywin32Probe = $LASTEXITCODE
    $ErrorActionPreference = $PreviousPreference
    if ($Pywin32Probe -ne 0) {
        Write-Host "pywin32 import failed; reinstalling it too" -ForegroundColor Yellow
        $InstallArgs += @('--reinstall-package', 'pywin32')
    }
    $InstallArgs += @($Wheels.FullName)
    & $UvExe @InstallArgs
    if ($LASTEXITCODE -ne 0) {
        throw "uv install catfish-email failed: $LASTEXITCODE"
    }

    $EmailExe = Join-Path $InstallDir 'venv\Scripts\catfish-email.exe'
    if (-not (Test-Path -LiteralPath $EmailExe -PathType Leaf)) {
        throw "catfish-email CLI missing after install: $EmailExe"
    }
    # An old CLI also passes --help. Validate discovery without touching mail/COM.
    & $EmailExe discover --help
    if ($LASTEXITCODE -ne 0) {
        throw "catfish-email CLI self-check failed: $EmailExe"
    }

    $SkillSrc = Join-Path $Stage 'hermes-skill\catfish-email'
    $SkillFile = Join-Path $SkillSrc 'SKILL.md'
    if (-not (Test-Path -LiteralPath $SkillFile -PathType Leaf)) {
        throw "distribution is missing the Hermes skill: $SkillFile"
    }
    New-Item -ItemType Directory -Force -Path $SkillsDir | Out-Null
    if (Test-Path -LiteralPath $SkillDst) {
        Remove-Item -LiteralPath $SkillDst -Recurse -Force
    }
    Copy-Item -LiteralPath $SkillSrc -Destination $SkillDst -Recurse -Force

    Write-Host "Catfish email installed: $EmailExe" -ForegroundColor Green
} finally {
    if (Test-Path -LiteralPath $Stage) {
        Remove-Item -LiteralPath $Stage -Recurse -Force
    }
}
