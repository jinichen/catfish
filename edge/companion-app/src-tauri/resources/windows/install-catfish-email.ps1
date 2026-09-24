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
    # 9/24: 以前是 --reinstall (所有 wheel 都强制重装)。pywin32 同版本 312 也会被
    # 卸了重装, 而 Hermes / 邮件扫描器正在跑时 Python 已经加载了
    # pywin32_system32\pythoncom311.dll —— Windows 不让删已加载的 DLL:
    #   failed to remove file ...pythoncom311.dll: 拒绝访问 (os error 5)
    # 装到一半失败, pywin32 被拆坏 (下次报 missing RECORD), 连带 Hermes 都起不来。
    # 现在只强制重装 catfish-email 本身; pywin32 只在它真的坏了 (导入失败) 时才重装。
    $InstallArgs = @(
        'pip', 'install', '--python', $PythonExe,
        '--no-index', '--find-links', $Stage,
        '--reinstall-package', 'catfish-email'
    )
    # 探针失败时 Python 往 stderr 打 traceback; Windows PowerShell 5.1 在
    # ErrorActionPreference=Stop 下会把 native 命令的 stderr 当成终止错误,
    # 脚本当场退出 —— 恰好在最需要修复的时候。探针期间临时放宽。
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $PythonExe -c "import win32api" *> $null
    $Pywin32Probe = $LASTEXITCODE
    $ErrorActionPreference = $PreviousPreference
    if ($Pywin32Probe -ne 0) {
        Write-Host "pywin32 导入失败, 一并修复" -ForegroundColor Yellow
        $InstallArgs += @('--reinstall-package', 'pywin32')
    }
    $InstallArgs += @($Wheels.FullName)
    & $UvExe @InstallArgs
    if ($LASTEXITCODE -ne 0) {
        throw "uv 安装 catfish-email 失败: $LASTEXITCODE"
    }

    $EmailExe = Join-Path $InstallDir 'venv\Scripts\catfish-email.exe'
    if (-not (Test-Path -LiteralPath $EmailExe -PathType Leaf)) {
        throw "邮件 CLI 安装后不存在: $EmailExe"
    }
    # An old CLI also passes --help. Validate discovery without touching mail/COM.
    & $EmailExe discover --help
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
