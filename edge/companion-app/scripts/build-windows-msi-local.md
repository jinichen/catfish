# 本地 Windows msi build SOP

**目的**: 免 CircleCI 依赖 · 在你自己 Windows 机器上打 catfish Companion msi. 后续每次 15-20 min.

**版本**: v0.18.0 (7/17)
**测试环境**: Windows 11 (Win10 21H2+ 应该也 OK) · x86_64

---

## 一次性 · 装 prerequisites (~30 min)

以下 5 个工具 · **管理员** 打开 PowerShell 跑, 或用**普通 PowerShell + winget** 装:

### 1. Rust (MSVC toolchain · x86_64-pc-windows-msvc)

```powershell
# 装 rustup + Rust 1.90 (跟 CircleCI config.yml 里 pin 的一致)
# 若已装 rustup · 跳
Invoke-WebRequest -Uri "https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe" -OutFile "$env:TEMP\rustup-init.exe"
& "$env:TEMP\rustup-init.exe" -y --default-toolchain 1.90.0 --profile minimal --default-host x86_64-pc-windows-msvc
# 加 PATH · 已装 rustup 会自动加
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"

# 验证
rustc --version    # 期望 rustc 1.90.0
cargo --version
```

### 2. Node.js 22 (含 npm + npx)

```powershell
# winget 装最简 (下载 8-10 min · winget 自动加 PATH)
winget install OpenJS.NodeJS.LTS --version 22.14.0

# 验证 (可能需要新开 PowerShell 让 PATH 生效)
node --version    # 期望 v22.14
npm --version
```

### 3. WiX 3.14 (msi 打包)

```powershell
# 下载 WiX 3.14 binary zip (Tauri v2 要求 3.11+, 我们用 3.14)
$wixZip = "$env:TEMP\wix314.zip"
$wixDir = "$env:USERPROFILE\.wix314"
Invoke-WebRequest -Uri "https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314-binaries.zip" -OutFile $wixZip
Expand-Archive -Path $wixZip -DestinationPath $wixDir -Force
# 加 PATH · 让 candle.exe / light.exe 全局可调
[Environment]::SetEnvironmentVariable("PATH", "$wixDir;$env:PATH", "User")
$env:PATH = "$wixDir;$env:PATH"

# 验证
candle.exe -?     # 期望看到 WiX help 输出
light.exe -?
```

### 4. Git (你已装 · 若无:)

```powershell
winget install Git.Git
```

### 5. Python 3 (patch 脚本用)

```powershell
# Python 3.11+ · Tauri 项目里的 patch_install_ps1_offline.py 需要
winget install Python.Python.3.12
# 验证
python --version
```

---

## 每次 build msi (15-20 min)

### Step 1 · 拉 catfish 最新代码

```powershell
cd E:\catfish  # 或你 clone 的地方
git pull origin main
```

### Step 2 · 拉 hermes-agent pinned tag (复现 CircleCI clone step)

```powershell
$ErrorActionPreference = 'Stop'
$HERMES_TAG = (Get-Content .\edge\companion-app\.hermes-git-tag -Raw).Trim()
$HERMES_VER = (Get-Content .\edge\companion-app\.hermes-target-version -Raw).Trim()
Write-Host "Cloning hermes-agent tag=$HERMES_TAG (pyproject version=$HERMES_VER)"

$hermesDir = "$env:TEMP\hermes-agent-src"
if (Test-Path $hermesDir) { Remove-Item -Recurse -Force $hermesDir }
git clone --depth 1 --branch $HERMES_TAG https://github.com/NousResearch/hermes-agent.git $hermesDir

# verify 版本
$verLine = Select-String -Path "$hermesDir\pyproject.toml" -Pattern '^version'
$actualVer = ($verLine.Line -replace '^version\s*=\s*"([^"]+)".*', '$1').Trim()
if ($actualVer -ne $HERMES_VER) {
    throw ".hermes-git-tag ($HERMES_TAG) => pyproject $actualVer, expected $HERMES_VER"
}
Write-Host "OK version match: $HERMES_VER"
```

### Step 3 · Copy catfish plugins 进 hermes-agent-src

```powershell
$hermesDir = "$env:TEMP\hermes-agent-src"
New-Item -ItemType Directory -Force -Path "$hermesDir\plugins\memory" | Out-Null
foreach ($plugin in @('catfish-memory', 'catfish-todo-sync')) {
    $src = "edge\hermes-plugins\$plugin"
    if (Test-Path $src) {
        Copy-Item -Recurse -Force $src "$hermesDir\plugins\memory\$plugin"
        Write-Host "OK copied $plugin"
    } else {
        Write-Host "WARN: $src missing"
    }
}
```

### Step 4 · Pre-install npm deps + npm pack 全局包

```powershell
$hermesDir = "$env:TEMP\hermes-agent-src"

# A. 本地 npm install · 装到 node_modules/
$env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = '1'
$pkgFiles = Get-ChildItem -Path $hermesDir -Recurse -Filter "package.json" -Depth 4 -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch '\\node_modules\\' }
foreach ($pkg in $pkgFiles) {
    $pkgDir = $pkg.DirectoryName
    Write-Host "===== npm install in $pkgDir ====="
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

# B. npm pack agent-browser + camofox 到 node-globals/
$globalsDir = "$hermesDir\node-globals"
New-Item -ItemType Directory -Force -Path $globalsDir | Out-Null
Push-Location $globalsDir
try {
    npm pack "agent-browser@^0.26.0" "@askjo/camofox-browser@^1.5.2" --loglevel=error
    if ($LASTEXITCODE -ne 0) { throw "npm pack failed" }
    $tgzList = Get-ChildItem -Filter "*.tgz"
    Write-Host "OK $($tgzList.Count) global .tgz packed"
} finally { Pop-Location }
```

### Step 5 · Patch install.ps1 · offline 模式

```powershell
python edge\hermes-fork\patch_install_ps1_offline.py `
    --input "$env:TEMP\hermes-agent-src\scripts\install.ps1" `
    --output edge\companion-app\src-tauri\resources\windows\install.ps1
if ($LASTEXITCODE -ne 0) { throw "patch failed" }
```

### Step 6 · 下载 uv + repack cpython

```powershell
# uv.exe
$UV_VERSION = '0.4.30'
$uvUrl = "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-pc-windows-msvc.zip"
$uvZip = "$env:TEMP\uv.zip"
Invoke-WebRequest -Uri $uvUrl -OutFile $uvZip
$uvExtract = "$env:TEMP\uv-extract"
if (Test-Path $uvExtract) { Remove-Item -Recurse -Force $uvExtract }
Expand-Archive -Path $uvZip -DestinationPath $uvExtract -Force
Copy-Item "$uvExtract\uv.exe" edge\companion-app\src-tauri\resources\windows\uv.exe -Force

# cpython 3.11.15
$PYTHON_VER = '3.11.15'
$BUILD_TAG = '20260623'
$FNAME = "cpython-${PYTHON_VER}+${BUILD_TAG}-x86_64-pc-windows-msvc-install_only.tar.gz"
$URL = "https://github.com/astral-sh/python-build-standalone/releases/download/${BUILD_TAG}/${FNAME}"
$tarPath = "$env:TEMP\cpython.tar.gz"
$extractDir = "$env:TEMP\cpython-extract"
Invoke-WebRequest -Uri $URL -OutFile $tarPath
if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }
New-Item -ItemType Directory -Path $extractDir | Out-Null
tar -xzf $tarPath -C $extractDir
$expectedName = "cpython-${PYTHON_VER}-windows-x86_64-none"
Rename-Item -Path "$extractDir\python" -NewName $expectedName
$outZip = "$PWD\edge\companion-app\src-tauri\resources\windows\cpython-${PYTHON_VER}-embed.zip"
if (Test-Path $outZip) { Remove-Item -Force $outZip }
Compress-Archive -Path "$extractDir\$expectedName" -DestinationPath $outZip -Force
```

### Step 7 · Install Playwright Chromium + 打包

```powershell
$hermesDir = "$env:TEMP\hermes-agent-src"
Push-Location $hermesDir
try {
    Write-Host "===== npx playwright install chromium (下载 ~170MB compressed / 解压 ~350MB) ====="
    npx --yes playwright install chromium --loglevel=error
    if ($LASTEXITCODE -ne 0) { throw "npx playwright install failed" }
} finally { Pop-Location }

$pwCacheDir = "$env:LOCALAPPDATA\ms-playwright"
if (-not (Test-Path $pwCacheDir)) { throw "Playwright cache 目录不存在" }
$chromiumDirs = Get-ChildItem $pwCacheDir -Directory -Filter "chromium*"
Write-Host "OK Playwright chromium: $($chromiumDirs.FullName -join ', ')"

$outTar = "$PWD\edge\companion-app\src-tauri\resources\windows\chromium-embed.tar.gz"
Push-Location $pwCacheDir
try {
    $chromiumNames = @(Get-ChildItem -Directory -Filter "chromium*" | ForEach-Object { $_.Name })
    if ($chromiumNames.Count -eq 0) { throw "无 chromium* 目录" }
    tar czf $outTar @chromiumNames
    if ($LASTEXITCODE -ne 0) { throw "tar chromium failed" }
} finally { Pop-Location }
Write-Host "OK chromium-embed.tar.gz: $([math]::Round((Get-Item $outTar).Length/1MB,1)) MB"
```

### Step 8 · Pack hermes-agent bundle

```powershell
$outTar = "$PWD\edge\companion-app\src-tauri\resources\windows\hermes-agent-bundle.tar.gz"
Push-Location $env:TEMP
try {
    tar czhf $outTar `
        --exclude=hermes-agent-src/.git `
        --exclude=hermes-agent-src/venv `
        --exclude=hermes-agent-src/venv.bak `
        --exclude=hermes-agent-src/.venv `
        --exclude=hermes-agent-src/target `
        hermes-agent-src
    if ($LASTEXITCODE -ne 0) { throw "tar hermes failed" }
} finally { Pop-Location }
Write-Host "OK hermes-agent-bundle.tar.gz: $([math]::Round((Get-Item $outTar).Length/1MB,1)) MB"
```

### Step 9 · 建 mac resources 空 placeholder (tauri validate 需要)

```powershell
$macDir = "edge\companion-app\src-tauri\resources\mac"
New-Item -ItemType Directory -Force -Path $macDir | Out-Null
foreach ($f in @('install.sh', 'uv', 'cpython-3.11.15-embed.tar.gz', 'hermes-agent-bundle.tar.gz')) {
    $p = "$macDir\$f"
    if (-not (Test-Path $p)) { Set-Content -Path $p -Value '' }
}
```

### Step 10 · npm install 前端 · tauri build msi

```powershell
cd edge\companion-app

# 前端 deps (只第一次)
npm install --loglevel=error

# tauri build msi
$env:PATH = "$env:USERPROFILE\.wix314;$env:USERPROFILE\.cargo\bin;$env:PATH"
npx tauri build --target x86_64-pc-windows-msvc --bundles msi --verbose
```

### Step 11 · 拿到 msi

```powershell
$msiDir = "src-tauri\target\x86_64-pc-windows-msvc\release\bundle\msi"
Get-ChildItem $msiDir -Filter "*.msi" | Select-Object Name, @{N='MB';E={[math]::Round($_.Length/1MB,1)}}
# 期望: Catfish Companion_0.18.0_x64_zh-CN.msi · ~1100 MB
```

装完直接:
```powershell
Start-Process msiexec.exe -ArgumentList "/i","`"$PWD\$msiDir\Catfish Companion_0.18.0_x64_zh-CN.msi`"","/l*v","`"$env:USERPROFILE\Downloads\catfish-msi-local-verbose.log`"" -Wait
```

---

## 常见坑

### A. Rust `stack overflow` (link.exe 挂)

Rust 装完后 · target 得加:
```powershell
rustup target add x86_64-pc-windows-msvc
```

### B. Node.js 版本不对 · npm ci 挂

用 nvm-windows 装多版本:
```powershell
winget install CoreyButler.NVMforWindows
nvm install 22.14.0
nvm use 22.14.0
```

### C. WiX candle.exe 找不到

**必须**加 WiX 到 PATH · 且用 3.14 (Tauri v2 支持 3.11+, 我们 pin 3.14):
```powershell
$env:PATH = "$env:USERPROFILE\.wix314;$env:PATH"
```

### D. tauri build 挂 `resource not found`

检查 `resources/windows/` 里 4 个真文件是否都在 (uv.exe / cpython.zip / hermes-bundle.tar.gz / chromium-embed.tar.gz).

### E. 磁盘空间 · 需要至少 5 GB free

chromium 350MB + hermes-agent 200MB + cpython 47MB + tar 中间产物 + Rust build cache 2-3GB.

---

## 一键脚本 (全自动跑 Step 2-11)

若你嫌手动跑麻烦, 可以另存下面为 `build-msi-local.ps1` 一键跑:

```powershell
# 见文件 edge/companion-app/scripts/build-msi-local.ps1 (待写)
```

---

## 加速 tips

- **加 `--no-audit --no-fund`** 到 npm install · 省 30 秒
- **Rust build cache** 首次 15 min · 后续 2-3 min (增量)
- **hermes-agent clone** cache 到 `%TEMP%\hermes-agent-src` · 若 Step 2 已跑 · 后续 Step 3-8 可复用 (不用重 clone)
- **Playwright chromium** 只需装 1 次 · 后续 `$env:LOCALAPPDATA\ms-playwright\` 会缓存

---

## 支持

- 装完 msi 若 chat 401 · 那是 gateway URL / OIDC 配置问题, 不是 msi 问题
- 装 msi 若 CustomAction 挂 · 看 verbose log:
  `Get-Content $env:USERPROFILE\Downloads\catfish-msi-local-verbose.log | Select-String "CatfishRunInstall|Return value"`
