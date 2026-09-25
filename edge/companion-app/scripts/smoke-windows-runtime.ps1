param([Parameter(Mandatory = $true)][string]$Resources)
$ErrorActionPreference = 'Stop'
$Resources = (Resolve-Path -LiteralPath $Resources).Path
$Work = Join-Path $env:RUNNER_TEMP ('catfish-runtime-smoke-' + [guid]::NewGuid().ToString('N'))
$PreviousHermesHome = $env:HERMES_HOME
$PreviousPythonUtf8 = $env:PYTHONUTF8
$PreviousPythonEncoding = $env:PYTHONIOENCODING
$PreviousProjectEnv = $env:UV_PROJECT_ENVIRONMENT
$PreviousUvPython = $env:UV_PYTHON
$DllHolder = $null
try {
    New-Item -ItemType Directory -Force -Path $Work | Out-Null
    $env:HERMES_HOME = Join-Path $Work 'hermes home'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    New-Item -ItemType Directory -Force -Path $env:HERMES_HOME | Out-Null
    $Uv = Join-Path $Resources 'uv.exe'
    New-Item -ItemType Directory -Force -Path (Join-Path $env:HERMES_HOME 'bin') | Out-Null
    Copy-Item -LiteralPath $Uv -Destination (Join-Path $env:HERMES_HOME 'bin\uv.exe')
    Expand-Archive -LiteralPath (Join-Path $Resources 'cpython-3.11.15-embed.zip') -DestinationPath (Join-Path $Work 'python')
    $BasePython = Get-ChildItem (Join-Path $Work 'python') -Recurse -Filter python.exe |
        Where-Object { $_.FullName -notmatch '\\Scripts\\' } | Select-Object -First 1
    if (-not $BasePython) { throw 'Bundled Python missing' }
    tar -xzf (Join-Path $Resources 'hermes-agent-bundle.tar.gz') -C $env:HERMES_HOME
    if ($LASTEXITCODE -ne 0) { throw 'Hermes archive extraction failed' }
    Rename-Item (Join-Path $env:HERMES_HOME 'hermes-agent-src') 'hermes-agent'
    $Agent = Join-Path $env:HERMES_HOME 'hermes-agent'
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $Agent 'venv'
    $env:UV_PYTHON = $BasePython.FullName
    & $Uv sync --project $Agent --extra all --locked
    if ($LASTEXITCODE -ne 0) { throw 'Bundled Hermes dependencies failed' }
    $Python = Join-Path $Agent 'venv\Scripts\python.exe'
    $Edge = Join-Path $Work 'edge'
    New-Item -ItemType Directory -Force -Path $Edge | Out-Null
    tar -xzf (Join-Path $Resources 'catfish-edge-runtime.tar.gz') -C $Edge
    if ($LASTEXITCODE -ne 0) { throw 'Edge archive extraction failed' }
    & $Python (Join-Path $PSScriptRoot 'smoke_tool_bridge.py') --python $Python `
        --source (Join-Path $Edge 'tool-bridge\src') --hermes $Agent
    if ($LASTEXITCODE -ne 0) { throw 'Packaged Tool Bridge failed its real startup / IPC smoke' }

    $Installer = Join-Path $Resources 'install-catfish-email.ps1'
    $Distribution = Join-Path $Resources 'catfish-email-dist.tar.gz'
    # Use Windows PowerShell 5.1, the exact shell used by the desktop installer.
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Installer -DistributionPath $Distribution
    if ($LASTEXITCODE -ne 0) { throw 'Isolated email installation failed' }
    $Runtime = Join-Path $env:HERMES_HOME 'email-runtime'
    $Old = (Get-Content -LiteralPath (Join-Path $Runtime 'current.txt') -Raw).Trim()
    $OldPython = Join-Path $Runtime "$Old\Scripts\python.exe"
    $Ready = Join-Path $Work 'dll-loaded.txt'
    $HoldScript = Join-Path $Work 'hold_dll.py'
    [IO.File]::WriteAllText($HoldScript, "import sys, time, pathlib, win32api, pythoncom`npathlib.Path(sys.argv[1]).write_text('loaded')`ntime.sleep(180)")
    $DllHolder = Start-Process -FilePath $OldPython -ArgumentList @("`"$HoldScript`"", "`"$Ready`"") -PassThru -WindowStyle Hidden
    $Deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $Ready)) {
        if ($DllHolder.HasExited -or [DateTime]::UtcNow -gt $Deadline) { throw 'COM DLL holder did not become ready' }
        Start-Sleep -Milliseconds 100
    }
    $SharedDlls = @(Get-ChildItem (Join-Path $Agent 'venv\Lib\site-packages\pywin32_system32') -Filter '*.dll' -ErrorAction SilentlyContinue)
    $Before = @($SharedDlls | Get-FileHash | Select-Object -ExpandProperty Hash) -join ','
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Installer -DistributionPath $Distribution
    if ($LASTEXITCODE -ne 0) { throw 'Email update while COM DLL loaded failed' }
    $New = (Get-Content -LiteralPath (Join-Path $Runtime 'current.txt') -Raw).Trim()
    if ($New -eq $Old -or $DllHolder.HasExited) { throw 'Update did not preserve the active old runtime' }
    $After = @($SharedDlls | Get-FileHash | Select-Object -ExpandProperty Hash) -join ','
    if ($Before -ne $After) { throw 'Email update modified Hermes shared DLLs' }
    $NewPython = Join-Path $Runtime "$New\Scripts\python.exe"
    & $NewPython -c 'import catfish_email.discovery, win32api, pythoncom'
    if ($LASTEXITCODE -ne 0) { throw 'New isolated runtime failed imports' }
    # A failure after venv installation but before publication must preserve the
    # last working pointer and clean only the new, unpublished generation.
    $Fixture = Join-Path $Work 'broken-email-fixture'
    New-Item -ItemType Directory -Force -Path $Fixture | Out-Null
    tar -xzf $Distribution -C $Fixture
    if ($LASTEXITCODE -ne 0) { throw 'Email fixture extraction failed' }
    $BadDistribution = Join-Path $Work 'email-without-skill.tar.gz'
    tar -czf $BadDistribution --exclude=hermes-skill -C $Fixture .
    if ($LASTEXITCODE -ne 0) { throw 'Email failure fixture creation failed' }
    $BeforeDirs = @(Get-ChildItem $Runtime -Directory -Filter 'env-*').Count
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Installer -DistributionPath $BadDistribution
    if ($LASTEXITCODE -eq 0) { throw 'Incomplete email distribution unexpectedly succeeded' }
    if ((Get-Content -LiteralPath (Join-Path $Runtime 'current.txt') -Raw).Trim() -ne $New) {
        throw 'Failed upgrade changed the active email pointer'
    }
    if (@(Get-ChildItem $Runtime -Directory -Filter 'env-*').Count -ne $BeforeDirs) {
        throw 'Failed upgrade left an unpublished email environment'
    }
    Write-Host 'Windows packaged runtime smoke passed'
} finally {
    if ($DllHolder -and -not $DllHolder.HasExited) { Stop-Process -Id $DllHolder.Id -Force }
    $env:HERMES_HOME = $PreviousHermesHome
    $env:PYTHONUTF8 = $PreviousPythonUtf8
    $env:PYTHONIOENCODING = $PreviousPythonEncoding
    $env:UV_PROJECT_ENVIRONMENT = $PreviousProjectEnv
    $env:UV_PYTHON = $PreviousUvPython
    # Keep isolated smoke files for debugging. The hosted runner disposes of them.
    Write-Host "Smoke evidence: $Work"
}
# The deliberate failed-upgrade test leaves LASTEXITCODE nonzero; report the
# outcome of the assertions, not that expected child-process failure.
exit 0
