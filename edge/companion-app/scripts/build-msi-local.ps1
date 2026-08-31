<#
.SYNOPSIS
    本地 Windows 一键打 catfish Companion msi (Step 2-11 全自动跑).

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
    [switch]$SkipNpmInstall,      # 若 node_modules 已在 · 跳过 npm install
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
foreach ($tool in @('rustc', 'cargo', 'node', 'npm', 'npx', 'git', 'python', 'tar')) {
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

Write-Host "`n[Step 1/11] git pull origin main..." -ForegroundColor Yellow
git pull origin main
if ($LASTEXITCODE -ne 0) { throw "git pull failed" }

# ─── Step 2 · clone hermes-agent ────────────────────────────

$hermesDir = "$env:TEMP\hermes-agent-src"
if ($SkipHermesClone -and (Test-Path $hermesDir)) {
    Write-Host "`n[Step 2/11] SKIP hermes-agent clone (已在 $hermesDir)" -ForegroundColor DarkYellow
} else {
    Write-Host "`n[Step 2/11] Clone hermes-agent pinned tag..." -ForegroundColor Yellow
    $HERMES_TAG = (Get-Content .\edge\companion-app\.hermes-git-tag -Raw).Trim()
    $HERMES_VER = (Get-Content .\edge\companion-app\.hermes-target-version -Raw).Trim()
    Write-Host "  hermes tag=$HERMES_TAG · expected pyproject version=$HERMES_VER"

    if (Test-Path $hermesDir) { Remove-Item -Recurse -Force $hermesDir }
    git clone --depth 1 --branch $HERMES_TAG https://github.com/NousResearch/hermes-agent.git $hermesDir
    if ($LASTEXITCODE -ne 0) { throw "hermes-agent clone failed" }

    $verLine = Select-String -Path "$hermesDir\pyproject.toml" -Pattern '^version'
    $actualVer = ($verLine.Line -replace '^version\s*=\s*"([^"]+)".*', '$1').Trim()
    if ($actualVer -ne $HERMES_VER) {
        throw ".hermes-git-tag ($HERMES_TAG) => pyproject $actualVer, expected $HERMES_VER"
    }
    Write-Host "  OK version match: $HERMES_VER" -ForegroundColor Green
}

# ─── Step 3 · Copy catfish plugins ──────────────────────────

Write-Host "`n[Step 3/11] Copy catfish plugins into hermes-agent-src..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path "$hermesDir\plugins\memory" | Out-Null
foreach ($plugin in @('catfish-memory', 'catfish-todo-sync')) {
    $src = "edge\hermes-plugins\$plugin"
    if (Test-Path $src) {
        Copy-Item -Recurse -Force $src "$hermesDir\plugins\memory\$plugin"
        Write-Host "  OK copied $plugin" -ForegroundColor Green
    } else {
        Write-Host "  WARN $src missing" -ForegroundColor DarkYellow
    }
}

# ─── Step 4 · Pre-install npm deps + npm pack ──────────────

if ($SkipNpmInstall -and (Test-Path "$hermesDir\node_modules")) {
    Write-Host "`n[Step 4/11] SKIP npm install (node_modules 已在)" -ForegroundColor DarkYellow
} else {
    Write-Host "`n[Step 4/11] Pre-install npm deps + npm pack global .tgz..." -ForegroundColor Yellow
    $env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = '1'

    # A. local npm install
    $pkgFiles = @(Get-ChildItem -Path $hermesDir -Recurse -Filter "package.json" -Depth 4 -ErrorAction SilentlyContinue |
                  Where-Object { $_.FullName -notmatch '\\node_modules\\' })
    if ($pkgFiles.Count -eq 0) {
        Write-Host "  WARN 无 package.json, skip local npm install" -ForegroundColor DarkYellow
    } else {
        foreach ($pkg in $pkgFiles) {
            $pkgDir = $pkg.DirectoryName
            Write-Host "  ===== npm install in $pkgDir =====" -ForegroundColor DarkGray
            Push-Location $pkgDir
            try {
                if (Test-Path "$pkgDir\package-lock.json") {
                    npm ci --no-audit --no-fund --loglevel=error
                } else {
                    npm install --no-audit --no-fund --loglevel=error
                }
                if ($LASTEXITCODE -ne 0) { throw "npm install failed in $pkgDir" }
            } finally { Pop-Location }
        }
        Write-Host "  OK $($pkgFiles.Count) package.json 都 install 完" -ForegroundColor Green
    }

    # B. npm pack agent-browser + camofox-browser 到 node-globals/
    $globalsDir = "$hermesDir\node-globals"
    New-Item -ItemType Directory -Force -Path $globalsDir | Out-Null
    Push-Location $globalsDir
    try {
        Write-Host "  ===== npm pack agent-browser + camofox-browser =====" -ForegroundColor DarkGray
        npm pack "agent-browser@^0.26.0" "@askjo/camofox-browser@^1.5.2" --loglevel=error
        if ($LASTEXITCODE -ne 0) { throw "npm pack failed" }
        $tgzList = Get-ChildItem -Filter "*.tgz"
        Write-Host "  OK $($tgzList.Count) global .tgz packed" -ForegroundColor Green
        $tgzList | ForEach-Object { Write-Host "    - $($_.Name) ($([math]::Round($_.Length/1KB,1)) KB)" -ForegroundColor DarkGray }
    } finally { Pop-Location }
}

# ─── Step 5 · Patch install.ps1 ─────────────────────────────

Write-Host "`n[Step 5/11] Patch install.ps1 offline mode..." -ForegroundColor Yellow
python edge\hermes-fork\patch_install_ps1_offline.py `
    --input "$hermesDir\scripts\install.ps1" `
    --output edge\companion-app\src-tauri\resources\windows\install.ps1
if ($LASTEXITCODE -ne 0) { throw "patch install.ps1 failed" }
Write-Host "  OK install.ps1 patched" -ForegroundColor Green

# ─── Step 6 · Download uv + cpython ────────────────────────

Write-Host "`n[Step 6/11] Download uv + repack cpython..." -ForegroundColor Yellow

# uv.exe
$UV_VERSION = '0.4.30'
$uvOut = "edge\companion-app\src-tauri\resources\windows\uv.exe"
if ((Test-Path $uvOut) -and (Get-Item $uvOut).Length -gt 1MB) {
    Write-Host "  SKIP uv.exe (已在 · $([math]::Round((Get-Item $uvOut).Length/1MB,1)) MB)" -ForegroundColor DarkYellow
} else {
    $uvUrl = "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-pc-windows-msvc.zip"
    $uvZip = "$env:TEMP\uv.zip"
    Invoke-WebRequest -Uri $uvUrl -OutFile $uvZip
    $uvExtract = "$env:TEMP\uv-extract"
    if (Test-Path $uvExtract) { Remove-Item -Recurse -Force $uvExtract }
    Expand-Archive -Path $uvZip -DestinationPath $uvExtract -Force
    Copy-Item "$uvExtract\uv.exe" $uvOut -Force
    Write-Host "  OK uv.exe" -ForegroundColor Green
}

# cpython 3.11.15
$PYTHON_VER = '3.11.15'
$pyOut = "edge\companion-app\src-tauri\resources\windows\cpython-${PYTHON_VER}-embed.zip"
if ((Test-Path $pyOut) -and (Get-Item $pyOut).Length -gt 10MB) {
    Write-Host "  SKIP cpython zip (已在 · $([math]::Round((Get-Item $pyOut).Length/1MB,1)) MB)" -ForegroundColor DarkYellow
} else {
    $BUILD_TAG = '20260623'
    $FNAME = "cpython-${PYTHON_VER}+${BUILD_TAG}-x86_64-pc-windows-msvc-install_only.tar.gz"
    $URL = "https://github.com/astral-sh/python-build-standalone/releases/download/${BUILD_TAG}/${FNAME}"
    $tarPath = "$env:TEMP\cpython.tar.gz"
    $extractDir = "$env:TEMP\cpython-extract"
    Invoke-WebRequest -Uri $URL -OutFile $tarPath
    if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }
    New-Item -ItemType Directory -Path $extractDir | Out-Null
    tar -xzf $tarPath -C $extractDir
    if ($LASTEXITCODE -ne 0) { throw "cpython tar extract failed" }
    $expectedName = "cpython-${PYTHON_VER}-windows-x86_64-none"
    Rename-Item -Path "$extractDir\python" -NewName $expectedName
    if (Test-Path $pyOut) { Remove-Item -Force $pyOut }
    Compress-Archive -Path "$extractDir\$expectedName" -DestinationPath $pyOut -Force
    Write-Host "  OK cpython embed zip" -ForegroundColor Green
}

# ─── Step 7 · Playwright Chromium ──────────────────────────

$chromiumOut = "$PWD\edge\companion-app\src-tauri\resources\windows\chromium-embed.tar.gz"
$pwCacheDir = "$env:LOCALAPPDATA\ms-playwright"

if ($SkipChromium -and (Test-Path $chromiumOut) -and (Get-Item $chromiumOut).Length -gt 50MB) {
    Write-Host "`n[Step 7/11] SKIP Playwright chromium (chromium-embed.tar.gz 已在 · $([math]::Round((Get-Item $chromiumOut).Length/1MB,1)) MB)" -ForegroundColor DarkYellow
} else {
    Write-Host "`n[Step 7/11] Install Playwright Chromium + pack..." -ForegroundColor Yellow
    Push-Location $hermesDir
    try {
        Write-Host "  ===== 1/3 playwright dry-run: 解析下载地址和安装位置 =====" -ForegroundColor DarkGray
        $dry = @(npx --yes playwright install chromium --dry-run)
        if ($LASTEXITCODE -ne 0) { throw "playwright dry-run failed (exit=$LASTEXITCODE)" }
        $dry | ForEach-Object { Write-Host "    $_" }

        $targets = @()
        $loc = $null
        foreach ($line in $dry) {
            if ($line -match '^\s*Install location:\s*(.+?)\s*$') { $loc = $Matches[1]; continue }
            if ($line -match '^\s*Download url:\s*(\S+)\s*$' -and $loc) {
                $targets += [pscustomobject]@{ Dir = $loc; Url = $Matches[1] }
                $loc = $null
            }
        }
        $targets = @($targets | Where-Object { (Split-Path $_.Dir -Leaf) -like 'chromium*' })
        if ($targets.Count -eq 0) { throw "dry-run 没有解析出 chromium* 下载目标" }
    } finally { Pop-Location }

    $pwRoot = "$env:LOCALAPPDATA\ms-playwright"
    foreach ($t in $targets) {
        if (-not $t.Dir.StartsWith($pwRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Playwright 安装位置不在 $pwRoot 下: $($t.Dir)"
        }
    }

    Write-Host "  ===== 2/3 下载 + 解压 $($targets.Count) 个 Chromium 包 =====" -ForegroundColor DarkGray
    foreach ($t in $targets) {
        $name = Split-Path $t.Dir -Leaf
        $marker = Join-Path $t.Dir 'INSTALLATION_COMPLETE'
        if (Test-Path $marker) { Write-Host "    [$name] 已存在, 跳过"; continue }

        $zip = Join-Path $env:TEMP "$name.zip"
        if (Test-Path $zip) { Remove-Item -Force $zip }
        Write-Host "    [$name] 下载中..."
        curl.exe -L --fail --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 1200 -o $zip $t.Url
        if ($LASTEXITCODE -ne 0) { throw "[$name] download failed (curl exit=$LASTEXITCODE)" }

        if (Test-Path $t.Dir) { Remove-Item -Recurse -Force $t.Dir }
        New-Item -ItemType Directory -Force -Path $t.Dir | Out-Null
        $unpacked = $false
        try {
            tar.exe -xf $zip -C $t.Dir
            $unpacked = ($LASTEXITCODE -eq 0)
        } catch { $unpacked = $false }
        if (-not $unpacked) { Expand-Archive -Path $zip -DestinationPath $t.Dir -Force }

        $files = @(Get-ChildItem -Recurse -File $t.Dir)
        $exes = @($files | Where-Object { $_.Extension -eq '.exe' })
        if ($exes.Count -eq 0) { throw "[$name] 解压后没有 exe, zip 或目录层级异常" }
        New-Item -ItemType File -Force -Path $marker | Out-Null
        Remove-Item -Force $zip
        Write-Host "    [$name] 完成 · $($exes.Count) 个 exe" -ForegroundColor Green
    }
    Write-Host "  ===== 3/3 打包 Chromium 资源 =====" -ForegroundColor DarkGray

    if (-not (Test-Path $pwCacheDir)) { throw "Playwright cache 目录不存在: $pwCacheDir" }
    $chromiumDirs = Get-ChildItem $pwCacheDir -Directory -Filter "chromium*"
    Write-Host "  OK Playwright chromium: $($chromiumDirs.FullName -join ', ')" -ForegroundColor Green

    Push-Location $pwCacheDir
    try {
        $chromiumNames = @(Get-ChildItem -Directory -Filter "chromium*" | ForEach-Object { $_.Name })
        if ($chromiumNames.Count -eq 0) { throw "无 chromium* 目录" }
        tar czf $chromiumOut @chromiumNames
        if ($LASTEXITCODE -ne 0) { throw "tar chromium failed" }
    } finally { Pop-Location }
    Write-Host "  OK chromium-embed.tar.gz: $([math]::Round((Get-Item $chromiumOut).Length/1MB,1)) MB" -ForegroundColor Green
}

# ─── Step 8 · Pack hermes-agent bundle ──────────────────────

Write-Host "`n[Step 8/11] Pack hermes-agent bundle..." -ForegroundColor Yellow
$hermesTarOut = "$PWD\edge\companion-app\src-tauri\resources\windows\hermes-agent-bundle.tar.gz"
Push-Location $env:TEMP
try {
    # Companion 只需要 Hermes 的 Python gateway/tool/plugin 运行链路。
    # 裁剪只作用于 tar 输入, 不修改 $hermesDir, 避免污染下次构建缓存。
    # agent-browser 是运行时依赖, 只保留 Windows x64 原生二进制。
    $bundleExcludes = @(
        'hermes-agent-src/.git',
        'hermes-agent-src/venv',
        'hermes-agent-src/venv.bak',
        'hermes-agent-src/.venv',
        'hermes-agent-src/target',
        'hermes-agent-src/apps/desktop',
        'hermes-agent-src/node_modules/electron',
        'hermes-agent-src/node_modules/node-pty',
        'hermes-agent-src/node_modules/emojibase-data',
        'hermes-agent-src/node_modules/hermes',
        'hermes-agent-src/node_modules/mermaid',
        'hermes-agent-src/node_modules/@mermaid-js',
        'hermes-agent-src/node_modules/@tabler',
        'hermes-agent-src/node_modules/@icons-pack',
        'hermes-agent-src/node_modules/lucide-react',
        'hermes-agent-src/node_modules/three',
        'hermes-agent-src/node_modules/three-stdlib',
        'hermes-agent-src/node_modules/electron-winstaller',
        'hermes-agent-src/node_modules/typescript',
        'hermes-agent-src/node_modules/@rolldown',
        'hermes-agent-src/node_modules/@tauri-apps',
        'hermes-agent-src/node_modules/react-native-*',
        'hermes-agent-src/node_modules/@types',
        'hermes-agent-src/node_modules/agent-browser/bin/agent-browser-darwin-arm64',
        'hermes-agent-src/node_modules/agent-browser/bin/agent-browser-darwin-x64',
        'hermes-agent-src/node_modules/agent-browser/bin/agent-browser-linux-arm64',
        'hermes-agent-src/node_modules/agent-browser/bin/agent-browser-linux-musl-arm64',
        'hermes-agent-src/node_modules/agent-browser/bin/agent-browser-linux-musl-x64',
        'hermes-agent-src/node_modules/agent-browser/bin/agent-browser-linux-x64',
        'hermes-agent-src/website',
        'hermes-agent-src/tests',
        'hermes-agent-src/tests-js',
        'hermes-agent-src/.github'
    )
    $tarArgs = @('czhf', $hermesTarOut)
    $tarArgs += @($bundleExcludes | ForEach-Object { "--exclude=$($_)" })
    $tarArgs += 'hermes-agent-src'
    & tar.exe @tarArgs
    if ($LASTEXITCODE -ne 0) { throw "hermes-agent tar failed" }
} finally { Pop-Location }
Write-Host "  OK hermes-agent-bundle.tar.gz: $([math]::Round((Get-Item $hermesTarOut).Length/1MB,1)) MB" -ForegroundColor Green

# 归档校验：防止 exclude 写错后悄悄把开发/跨平台文件重新打进去。
$archiveList = @(tar.exe tzf $hermesTarOut)
if ($LASTEXITCODE -ne 0) { throw "无法读取 hermes-agent-bundle.tar.gz" }
$forbiddenArchivePaths = @(
    'hermes-agent-src/.git/',
    'hermes-agent-src/venv/',
    'hermes-agent-src/target/',
    'hermes-agent-src/apps/desktop/',
    'hermes-agent-src/node_modules/electron/',
    'hermes-agent-src/node_modules/node-pty/',
    'hermes-agent-src/node_modules/emojibase-data/',
    'hermes-agent-src/node_modules/hermes/',
    'hermes-agent-src/node_modules/mermaid/',
    'hermes-agent-src/node_modules/@mermaid-js/',
    'hermes-agent-src/node_modules/@tabler/',
    'hermes-agent-src/node_modules/@icons-pack/',
    'hermes-agent-src/node_modules/lucide-react/',
    'hermes-agent-src/node_modules/three/',
    'hermes-agent-src/node_modules/three-stdlib/',
    'hermes-agent-src/node_modules/electron-winstaller/',
    'hermes-agent-src/node_modules/typescript/',
    'hermes-agent-src/node_modules/@rolldown/',
    'hermes-agent-src/node_modules/@tauri-apps/',
    'hermes-agent-src/node_modules/@types/',
    'hermes-agent-src/website/',
    'hermes-agent-src/tests/',
    'hermes-agent-src/tests-js/',
    'hermes-agent-src/.github/'
)
foreach ($forbidden in $forbiddenArchivePaths) {
    if ($archiveList | Where-Object { $_ -like "$forbidden*" }) {
        throw "Hermes 归档包含被排除路径: $forbidden"
    }
}
$requiredArchivePaths = @(
    'hermes-agent-src/node_modules/agent-browser/bin/agent-browser.js',
    'hermes-agent-src/node_modules/agent-browser/bin/agent-browser-win32-x64.exe'
)
foreach ($required in $requiredArchivePaths) {
    if (-not ($archiveList | Where-Object { $_ -eq $required })) {
        throw "Hermes 归档缺少运行时文件: $required"
    }
}
$foreignAgentBins = @(
    'agent-browser-darwin-arm64',
    'agent-browser-darwin-x64',
    'agent-browser-linux-arm64',
    'agent-browser-linux-musl-arm64',
    'agent-browser-linux-musl-x64',
    'agent-browser-linux-x64'
)
foreach ($foreign in $foreignAgentBins) {
    $foreignPath = "hermes-agent-src/node_modules/agent-browser/bin/$foreign"
    if ($archiveList | Where-Object { $_ -eq $foreignPath }) {
        throw "Hermes 归档包含非 Windows agent-browser 二进制: $foreign"
    }
}
Write-Host "  OK Hermes runtime 裁剪校验通过 · $($archiveList.Count) 个归档条目" -ForegroundColor Green

# Build the safe chat export reader wheel that the MSI post-install action installs offline.
& "$scriptDir\build-wechat-reader-resource.ps1"
if ($LASTEXITCODE -ne 0) { throw "catfish-wechat-reader resource build failed" }

# ─── Step 9 · Mac placeholder ──────────────────────────────

Write-Host "`n[Step 9/11] Create mac resources placeholders (tauri validate)..." -ForegroundColor Yellow
$macDir = "edge\companion-app\src-tauri\resources\mac"
New-Item -ItemType Directory -Force -Path $macDir | Out-Null
foreach ($f in @('install.sh', 'uv', 'cpython-3.11.15-embed.tar.gz', 'hermes-agent-bundle.tar.gz')) {
    $p = "$macDir\$f"
    if (-not (Test-Path $p)) { Set-Content -Path $p -Value '' }
}

# ─── Step 10 · Frontend npm install ─────────────────────────

Set-Location $companionApp
if ($SkipFrontendInstall -and (Test-Path "node_modules")) {
    Write-Host "`n[Step 10/11] SKIP 前端 npm install (node_modules 已在)" -ForegroundColor DarkYellow
} else {
    Write-Host "`n[Step 10/11] 前端 npm install..." -ForegroundColor Yellow
    npm install --no-audit --no-fund --loglevel=error
    if ($LASTEXITCODE -ne 0) { throw "前端 npm install failed" }
}

# ─── Step 11 · tauri build msi ──────────────────────────────

Write-Host "`n[Step 11/11] npx tauri build msi (15-20 min)..." -ForegroundColor Yellow
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
