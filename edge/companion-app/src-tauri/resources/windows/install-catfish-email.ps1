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
$EmailVenv = $null
$Published = $false

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

    # Never replace pywin32 DLLs in the live Hermes environment. Build a fresh
    # generation, validate it, then publish a pointer. Old generations stay usable
    # by in-flight CLI processes; no user Python/Outlook process is terminated.
    $RuntimeRoot = Join-Path $HermesHome 'email-runtime'
    $Generation = 'env-' + [guid]::NewGuid().ToString('N')
    $EmailVenv = Join-Path $RuntimeRoot $Generation
    New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
    & $UvExe venv --python $PythonExe --no-python-downloads $EmailVenv
    if ($LASTEXITCODE -ne 0) { throw "email venv creation failed: $LASTEXITCODE" }
    $EmailPython = Join-Path $EmailVenv 'Scripts\python.exe'
    & $UvExe pip install --python $EmailPython --no-index --find-links $Stage @($Wheels.FullName)
    if ($LASTEXITCODE -ne 0) { throw "isolated email install failed: $LASTEXITCODE" }
    $EmailExe = Join-Path $EmailVenv 'Scripts\catfish-email.exe'
    & $EmailPython -c "import catfish_email.discovery, win32api, pythoncom"
    if ($LASTEXITCODE -ne 0) { throw "isolated email imports failed: $LASTEXITCODE" }
    & $EmailExe discover --help
    if ($LASTEXITCODE -ne 0) { throw "catfish-email self-check failed: $LASTEXITCODE" }

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

    # Give terminal-based skill users the same executable as Companion/tools.
    $InstalledSkill = Join-Path $SkillDst 'SKILL.md'
    $RuntimeNote = "`r`n## Windows runtime`r`nUse this installed executable instead of a legacy PATH entry: ``$EmailExe``. In PowerShell use the call operator: & '$EmailExe' accounts.`r`n"
    [IO.File]::AppendAllText($InstalledSkill, $RuntimeNote, (New-Object Text.UTF8Encoding $false))
    $Pointer = Join-Path $RuntimeRoot 'current.txt'
    $Pending = Join-Path $RuntimeRoot ($Generation + '.txt')
    [IO.File]::WriteAllText($Pending, $Generation, (New-Object Text.UTF8Encoding $false))
    if (Test-Path -LiteralPath $Pointer) {
        [IO.File]::Replace($Pending, $Pointer, $null)
    } else {
        [IO.File]::Move($Pending, $Pointer)
    }
    $Published = $true

    Write-Host "Catfish email installed: $EmailExe" -ForegroundColor Green
} finally {
    # Only clean the fresh, unpublished generation from this attempt. Never touch
    # the active pointer target or an older environment held by a running CLI.
    if (-not $Published -and $EmailVenv -and (Test-Path -LiteralPath $EmailVenv)) {
        Remove-Item -LiteralPath $EmailVenv -Recurse -Force -ErrorAction Continue
    }
    if (Test-Path -LiteralPath $Stage) {
        Remove-Item -LiteralPath $Stage -Recurse -Force
    }
}
