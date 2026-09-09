param(
    [Parameter(Mandatory = $true)][string]$DistributionPath
)

$ErrorActionPreference = 'Stop'

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
        throw "邮件组件安装所需文件不存在: $path"
    }
}

try {
    New-Item -ItemType Directory -Force -Path $Stage | Out-Null
    tar.exe -xzf $DistributionPath -C $Stage
    if ($LASTEXITCODE -ne 0) {
        throw "解压 catfish-email 分发包失败: $LASTEXITCODE"
    }

    $Wheels = @(Get-ChildItem -LiteralPath $Stage -Filter '*.whl' -File)
    $EmailWheel = @($Wheels | Where-Object { $_.Name -like 'catfish_email-*.whl' })
    if ($EmailWheel.Count -ne 1) {
        throw "邮件分发包必须正好包含一个 catfish-email wheel，实际 $($EmailWheel.Count)"
    }

    # Windows 包可同时携带 pywin32；--no-index 确保现场不会偷偷访问公网。
    $InstallArgs = @(
        'pip', 'install', '--python', $PythonExe,
        '--no-index', '--reinstall', '--find-links', $Stage
    ) + @($Wheels.FullName)
    & $UvExe @InstallArgs
    if ($LASTEXITCODE -ne 0) {
        throw "uv 安装 catfish-email 失败: $LASTEXITCODE"
    }

    $EmailExe = Join-Path $InstallDir 'venv\Scripts\catfish-email.exe'
    if (-not (Test-Path -LiteralPath $EmailExe -PathType Leaf)) {
        throw "邮件 CLI 安装后不存在: $EmailExe"
    }
    & $EmailExe --help *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "邮件 CLI 自检失败: $EmailExe"
    }

    $SkillSrc = Join-Path $Stage 'hermes-skill\catfish-email'
    $SkillFile = Join-Path $SkillSrc 'SKILL.md'
    if (-not (Test-Path -LiteralPath $SkillFile -PathType Leaf)) {
        throw "邮件分发包缺少 Hermes skill: $SkillFile"
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
