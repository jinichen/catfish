param([Parameter(Mandatory=$true)][string]$DistributionPath)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ne 5) {
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $PSCommandPath -DistributionPath $DistributionPath
    if ($LASTEXITCODE -ne 0) { throw 'Windows PowerShell 5.1 reader regression failed' }
    return
}
$companion = Split-Path -Parent $PSScriptRoot
$installer = Join-Path $companion 'src-tauri\resources\windows\install-wechat-reader.ps1'
# Load just the production validator, not the installer side effects.
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw 'reader installer has PowerShell syntax errors' }
$validator = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Assert-ReaderSafetyReport'
}, $true)
if ($null -eq $validator) { throw 'reader validator not found' }
. ([scriptblock]::Create($validator.Extent.Text))
$valid = '{"protocol_version":1,"read_only":true,"secure_key_store":true,"ephemeral_plaintext_cache":true,"modifies_wechat_app":false}'
Assert-ReaderSafetyReport -Doctor $valid
foreach ($invalid in @(
    '{}', 'null', '[]', 'not json', ('[' + $valid + ']'),
    $valid.Replace('"read_only":true', '"read_only":false'),
    $valid.Replace('"read_only":true', '"read_only":"true"'),
    $valid.Replace('"modifies_wechat_app":false', '"modifies_wechat_app":"false"'),
    $valid.Replace('"modifies_wechat_app":false', '"modifies_wechat_app":true'),
    $valid.Replace('"protocol_version":1', '"protocol_version":2'),
    $valid.Replace('"secure_key_store":true,', ''),
    $valid.Replace('"ephemeral_plaintext_cache":true,', '')
)) {
    $rejected = $false
    try { Assert-ReaderSafetyReport -Doctor $invalid } catch { $rejected = $true }
    if (-not $rejected) { throw "unsafe reader report accepted: $invalid" }
}
$uv = Join-Path $companion 'src-tauri\resources\windows\uv.exe'
if (-not (Test-Path -LiteralPath $uv)) { throw "missing bundled uv: $uv" }
$testRoot = Join-Path $env:TEMP ('catfish reader test ' + [guid]::NewGuid().ToString('N'))
$runtime = Join-Path $testRoot 'hermes'
$venv = Join-Path $runtime 'hermes-agent\venv'
New-Item -ItemType Directory -Force -Path (Join-Path $runtime 'bin') | Out-Null
Copy-Item -LiteralPath $uv -Destination (Join-Path $runtime 'bin\uv.exe')
$python = (& python -c 'import sys; print(sys.executable)').Trim()
& $uv venv --python $python $venv
if ($LASTEXITCODE -ne 0) { throw 'test venv creation failed' }
$priorHermesHome = $env:HERMES_HOME
try {
    $env:HERMES_HOME = $runtime
    # Use Windows PowerShell 5.1, not the CI runner's PowerShell 7. Redirect both
    # streams like Companion does, so the real installed launcher is exercised.
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        $stdout = Join-Path $testRoot "stdout-$attempt.log"
        $stderr = Join-Path $testRoot "stderr-$attempt.log"
        $wrapper = Join-Path $testRoot "run-reader-installer-$attempt.ps1"
        $statusFile = Join-Path $testRoot "status-$attempt.txt"
        # Start-Process in Windows PowerShell 5.1 can expose a null ExitCode
        # even after HasExited is true. Let the child persist its own result.
        Set-Content -LiteralPath $wrapper -Encoding UTF8 -Value @'
$ErrorActionPreference = 'Stop'
try {
    & $env:CATFISH_TEST_INSTALLER -DistributionPath $env:CATFISH_TEST_DISTRIBUTION
    $code = if ($LASTEXITCODE -is [int]) { $LASTEXITCODE } else { 0 }
    Set-Content -LiteralPath $env:CATFISH_TEST_STATUS -Encoding ASCII -Value $code
    exit $code
} catch {
    Write-Error $_
    Set-Content -LiteralPath $env:CATFISH_TEST_STATUS -Encoding ASCII -Value 1
    exit 1
}
'@
        $env:CATFISH_TEST_INSTALLER = $installer
        $env:CATFISH_TEST_DISTRIBUTION = $DistributionPath
        $env:CATFISH_TEST_STATUS = $statusFile
        $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $wrapper
        $process = Start-Process powershell.exe -ArgumentList $arguments -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $deadline = (Get-Date).AddSeconds(120)
        while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 100
        }
        if (-not $process.HasExited) {
            & taskkill.exe /PID $process.Id /T /F
            throw 'reader installer exceeded two minutes in offline test'
        }
        # Polling HasExited keeps the timeout independent from the child result.
        $process.WaitForExit()
        Get-Content -LiteralPath $stdout, $stderr
        if (-not (Test-Path -LiteralPath $statusFile)) {
            throw 'reader installer exited without writing a status code'
        }
        $statusText = (Get-Content -LiteralPath $statusFile -Raw).Trim()
        $exitCode = 0
        if (-not [int]::TryParse($statusText, [ref]$exitCode)) {
            throw "reader installer wrote an invalid status code: $statusText"
        }
        if ($exitCode -ne 0) { throw "reader installer failed: $exitCode" }
        $reader = Join-Path $venv 'Scripts\catfish-wechat-reader.exe'
        $report = (& $reader doctor --json) | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or $report.protocol_version -ne 1 -or
            $report.read_only -ne $true -or $report.modifies_wechat_app -ne $false) {
            throw 'installed reader contract mismatch'
        }
        if ($attempt -eq 1) {
            # Simulate an old/damaged package with the SAME version. The second
            # install must actually replace it rather than uv skipping version 0.1.0.
            $module = Join-Path $venv 'Lib\site-packages\catfish_wechat_reader\__main__.py'
            Set-Content -LiteralPath $module -Encoding ASCII -Value 'raise RuntimeError("stale reader fixture")'
        }
    }
    Write-Host 'PASS: Windows 5.1 offline reader install and same-version repair'
} finally {
    $env:HERMES_HOME = $priorHermesHome
    # Disposable fixtures are retained for CI failure inspection, never delete user runtimes.
    Write-Host "Reader test artifacts: $testRoot"
}
