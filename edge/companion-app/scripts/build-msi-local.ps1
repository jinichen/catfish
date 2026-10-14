<#
.SYNOPSIS
    本地 Windows 一键打 catfish Companion msi (Step 2-6 全自动跑).

.DESCRIPTION
    复现 CircleCI build-msi job 全部步骤到本地 Windows.
    前置: 已装 Rust MSVC + Node.js 22 + WiX 3.14 + Git + Python (见 build-windows-msi-local.md).

    用法:
        cd E:\catfish\edge\companion-app
        powershell -ExecutionPolicy Bypass -File scripts\build-msi-local.ps1

    首次 15-20 min · 后续增量 3-5 min.

.NOTES
    v0.20.0 (8/18)
    BL-WIN-LOCAL-BUILD (7/17 免 CircleCI 依赖).
#>

param(
    [switch]$SkipHermesClone,     # 若 %TEMP%\hermes-agent-src 已在 · 跳过 clone (加速)
    [switch]$SkipNpmInstall,      # 10/9 起无作用 (不再装 hermes 的 npm 依赖), 保留只为兼容
    [switch]$SkipChromium,        # 若 %LOCALAPPDATA%\ms-playwright\chromium* 已在 · 跳过重装
    [switch]$SkipFrontendInstall  # 若 companion-app/node_modules 已在 · 跳过前端 npm install
)

$ErrorActionPreference = 'Stop'
$startTime = Get-Date

Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  catfish Companion · 本地 Windows msi build  " -ForegroundColor Cyan
Write-Host "  v0.20.0 · $(Get-Date -Format 'yyyy-MM-dd HH:mm')" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan

# ─── 前置检查 ───────────────────────────────────────────────

Write-Host "`n[Pre-check] 验证工具就位..." -ForegroundColor Yellow
foreach ($tool in @('rustc', 'cargo', 'node', 'npm', 'npx', 'git', 'python', 'tar', 'bash')) {
    $found = Get-Command $tool -ErrorAction SilentlyContinue
    if (-not $found) {
        Write-Host "  X $tool NOT FOUND" -ForegroundColor Red
        Write-Host "    → 装完 prerequisites 再跑 (见 build-windows-msi-local.md 一次性 setup 段)" -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK $tool" -ForegroundColor Green
}

# WiX candle 单独查 · 通常需要手动加 PATH
$candlePath = Get-Command candle.exe -ErrorAction SilentlyContinue
if (-not $candlePath) {
    $wixDir = "$env:USERPROFILE\.wix314"
    if (Test-Path "$wixDir\candle.exe") {
        $env:PATH = "$wixDir;$env:PATH"
        Write-Host "  OK candle.exe (从 $wixDir 加 PATH)" -ForegroundColor Green
    } else {
        Write-Host "  X candle.exe NOT FOUND (WiX 3.14)" -ForegroundColor Red
        Write-Host "    → 装 WiX 3.14 (见 build-windows-msi-local.md)" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "  OK candle.exe" -ForegroundColor Green
}

# 找 catfish repo root (脚本在 edge/companion-app/scripts/ 里)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$companionApp = Split-Path -Parent $scriptDir
$catfishRoot = Split-Path -Parent (Split-Path -Parent $companionApp)
Set-Location $catfishRoot
Write-Host "  OK catfish root: $catfishRoot" -ForegroundColor Green

# ─── Step 1 · git pull ───────────────────────────────────────

Write-Host "`n[Step 1/6] git pull origin main..." -ForegroundColor Yellow
git pull origin main
if ($LASTEXITCODE -ne 0) { throw "git pull failed" }

# ─── Step 2 · Hermes 运行时资源 (10/14, hermes v0.22.0 起) ───────────
# 原来 Step 2-8 是: clone 上游 → 拷插件 → 给 install.ps1 打离线补丁 → 下 uv + cpython 3.11 → npx 下
# Chromium → 打 hermes 归档。v0.22 上游安装器整个换成 hermes pm (只认 Python 3.14、联网装), 补丁锚点
# 全部对不上; Companion 改成自己装 (hermes_install_native.rs)。资源跟 CI 同一个脚本出 (需要 Git Bash):
#   uv.exe / cpython-3.14.7-embed.tar.gz / hermes-agent-bundle.tar.gz (含 wheelhouse + 安装戳) / chromium-embed.tar.gz

Write-Host "`n[Step 2/6] Build Windows Hermes runtime resources (bash)..." -ForegroundColor Yellow
bash edge/companion-app/scripts/build-windows-hermes-resources.sh
if ($LASTEXITCODE -ne 0) { throw "build-windows-hermes-resources.sh failed" }

# ─── Step 3 · Offline email + edge runtime resources ───────

Write-Host "`n[Step 3/6] Build offline email + edge runtime resources..." -ForegroundColor Yellow
$emailSource = Join-Path $catfishRoot 'edge\email-agent'
$resourceDir = Join-Path $catfishRoot 'edge\companion-app\src-tauri\resources\windows'
$emailStage = Join-Path $env:TEMP 'catfish-email-dist'
foreach ($dir in @($emailStage)) {
    if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

python -m pip wheel --no-deps --wheel-dir $emailStage $emailSource
if ($LASTEXITCODE -ne 0) { throw 'catfish-email wheel build failed' }
python (Join-Path $PSScriptRoot 'verify-email-wheel.py') $emailStage $emailSource
if ($LASTEXITCODE -ne 0) { throw 'email wheel source verification failed' }
# Outlook COM 适配器依赖 pywin32；把 Windows x64 / CPython 3.14 wheel 一并放进
# 同一个离线归档，安装器用 uv --no-index 一次装完，现场不会偷偷访问 PyPI。
# (10/14: 邮件环境用 hermes venv 的解释器建, hermes v0.22 起是 3.14)
python -m pip download --only-binary=:all: --dest $emailStage `
    --python-version 3.14 --platform win_amd64 --implementation cp --abi cp314 pywin32
if ($LASTEXITCODE -ne 0) { throw 'pywin32 wheel download failed' }
$emailWheels = @(Get-ChildItem $emailStage -Filter '*.whl')
if (-not ($emailWheels | Where-Object { $_.Name -like 'catfish_email-*.whl' })) {
    throw 'catfish-email archive missing catfish_email wheel'
}
if (-not ($emailWheels | Where-Object { $_.Name -like 'pywin32-*.whl' })) {
    throw 'catfish-email archive missing pywin32 wheel'
}
$skillDest = Join-Path $emailStage 'hermes-skill'
New-Item -ItemType Directory -Force -Path $skillDest | Out-Null
Copy-Item -Recurse "$emailSource\hermes-skill\*" $skillDest
$emailTar = Join-Path $resourceDir 'catfish-email-dist.tar.gz'
if (Test-Path $emailTar) { Remove-Item -Force $emailTar }
tar -czf $emailTar -C $emailStage .
if ($LASTEXITCODE -ne 0) { throw 'catfish-email-dist.tar.gz build failed' }

# 10/14: hermes 额外依赖 (hermes-deps-dist) 已并进 hermes 归档的 wheelhouse (Step 2)。
# 9/23: tool-bridge / local-search 源码 (9/28 加上传文件解析脚本) —— 之前两个平台的包都没带, 客户机器上
# Companion 找不到它们 (见 src-tauri/src/services/edge_runtime.rs)。跟 mac 同一个脚本。
$edgeTar = Join-Path $resourceDir 'catfish-edge-runtime.tar.gz'
python (Join-Path $PSScriptRoot 'build_edge_runtime.py') $edgeTar
if ($LASTEXITCODE -ne 0) { throw 'catfish-edge-runtime.tar.gz build failed' }
foreach ($archive in @($emailTar, $edgeTar)) {
    $file = Get-Item $archive
    if ($file.Length -le 1KB) { throw "resource archive too small: $archive" }
    Write-Host "  OK $($file.Name) ($([math]::Round($file.Length/1KB,1)) KB)" -ForegroundColor Green
}

# ─── Step 4 · Mac placeholder ──────────────────────────────

Write-Host "`n[Step 4/6] Create mac resources placeholders (tauri validate)..." -ForegroundColor Yellow
$macDir = "edge\companion-app\src-tauri\resources\mac"
New-Item -ItemType Directory -Force -Path $macDir | Out-Null
foreach ($f in @('uv', 'cpython-3.14.7-embed.tar.gz', 'hermes-agent-bundle.tar.gz')) {
    $p = "$macDir\$f"
    if (-not (Test-Path $p)) { New-Item -ItemType File -Force -Path $p | Out-Null }
}

# ─── Step 5 · Frontend npm install ─────────────────────────

Set-Location $companionApp
if ($SkipFrontendInstall -and (Test-Path "node_modules")) {
    Write-Host "`n[Step 5/6] SKIP 前端 npm install (node_modules 已在)" -ForegroundColor DarkYellow
} else {
    Write-Host "`n[Step 5/6] 前端 npm install..." -ForegroundColor Yellow
    npm install --no-audit --no-fund --loglevel=error
    if ($LASTEXITCODE -ne 0) { throw "前端 npm install failed" }
}

# ─── Step 6 · tauri build msi ──────────────────────────────

Write-Host "`n[Step 6/6] npx tauri build msi (15-20 min)..." -ForegroundColor Yellow
# 9/28: 每次改版本号, 上一版的编译产物 (可执行文件 / 增量缓存) 就成了孤儿, cargo 不清;
# 跟 mac 打包同一个脚本, 见 scripts/prune-rust-target.mjs 头部。失败只提示, 不拦打包。
node (Join-Path $PSScriptRoot 'prune-rust-target.mjs') release x86_64-pc-windows-msvc
Write-Host "  Rust MSVC compile · WiX msi pack · 增量 build 2-3 min" -ForegroundColor DarkGray
npx tauri build --target x86_64-pc-windows-msvc --bundles msi --verbose
if ($LASTEXITCODE -ne 0) { throw "tauri build msi failed" }

# ─── DONE ──────────────────────────────────────────────────

$msiDir = "src-tauri\target\x86_64-pc-windows-msvc\release\bundle\msi"
$msiFiles = Get-ChildItem $msiDir -Filter "*.msi"
$duration = (Get-Date) - $startTime

Write-Host "`n===============================================" -ForegroundColor Green
Write-Host "  DONE · 用时 $([math]::Round($duration.TotalMinutes,1)) min" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Green
foreach ($msi in $msiFiles) {
    Write-Host "  MSI: $($msi.FullName)" -ForegroundColor Cyan
    Write-Host "  大小: $([math]::Round($msi.Length/1MB,1)) MB" -ForegroundColor Cyan
}

Write-Host "`n装 msi:" -ForegroundColor Yellow
Write-Host "  Start-Process msiexec.exe -ArgumentList '/i','`"$($msiFiles[0].FullName)`"','/l*v','`"`$env:USERPROFILE\Downloads\catfish-msi-verbose.log`"' -Wait" -ForegroundColor DarkGray

Write-Host "`n若 msi 装挂 · verbose log:" -ForegroundColor Yellow
Write-Host "  Get-Content `$env:USERPROFILE\Downloads\catfish-msi-verbose.log | Select-String 'CatfishRunInstall|Return value|Fatal' | Select-Object -Last 40" -ForegroundColor DarkGray
