# 在一台 Windows 机器上本地打出 msi

> 8/5。写这份是因为两条 CI 同时断了：GitHub Actions 扣款失败（job 起不来），
> CircleCI 额度用尽（8/13 才刷新）。这份是把 `.circleci/config.yml` 那 17 步
> 翻译成人手能跑的版本。
>
> **CI 上是"预装好的"那几样，本地必须自己装 —— 这才是真正会卡住的地方。**
> CircleCI 的 `Verify preinstalled tools` 那步只跑了 `node/npm/python/git --version`，
> 因为 MSVC、WiX 之类在镜像里本来就有。裸机上没有。

---

## 一、机器要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 1803+ / Windows 11，**x64** |
| 磁盘 | 空闲 **≥ 25 GB**（Rust target 目录 + chromium + node_modules 很吃） |
| 网络 | 要能上外网（装工具链、clone hermes、下 cpython/uv/chromium/WiX） |
| 权限 | 管理员（装 VS Build Tools 要） |

---

## 二、先装这些（按顺序）

### 1. Visual Studio Build Tools —— **最容易漏的一个**

Rust 的 MSVC toolchain 要靠它链接。没有它，`cargo build` 会在 link 阶段炸。

下载 <https://visualstudio.microsoft.com/downloads/> → **Build Tools for Visual Studio**

安装时勾选：

- **使用 C++ 的桌面开发**（Desktop development with C++）
- 组件里确认有 **MSVC v143 生成工具** 和 **Windows 11 SDK**

约 5–7 GB，装完**重启一次**（PATH 才生效）。

### 2. Rust

```powershell
winget install Rustlang.Rustup
# 或 https://win.rustup.rs 下 rustup-init.exe

rustup toolchain install stable-x86_64-pc-windows-msvc --profile minimal
rustup default stable-x86_64-pc-windows-msvc
rustup target add x86_64-pc-windows-msvc
```

⚠ **必须是 `-msvc` 不是 `-gnu`**。rustup 在某些机器上默认装 GNU host，
链接时会跟 MSVC 的 C 运行时对不上。CI 上专门为这个加过一条修复（W2.21）。

验：

```powershell
rustc --version --verbose    # host: 那行必须是 x86_64-pc-windows-msvc
```

需要 Rust ≥ 1.77（`Cargo.toml` 里 `rust-version = "1.77"`）。

### 3. Node.js 20

```powershell
winget install OpenJS.NodeJS.LTS
```

`package.json` 写的是 `engines: node >=20`，CI 上用的是 20。

### 4. Python 3

```powershell
winget install Python.Python.3.11
```

只用来跑 `patch_install_ps1_offline.py`，版本不敏感。

### 5. Git

```powershell
winget install Git.Git
```

### 6. WebView2 Runtime

Windows 11 自带。Windows 10 可能没有 → <https://developer.microsoft.com/microsoft-edge/webview2/>

### 7. tar

Windows 10 1803+ 自带 `tar.exe`（`where tar` 能找到就行）。找不到就装 Git for Windows 带的。

---

## 三、环境变量（**每个新开的 PowerShell 都要设**）

```powershell
$env:RUSTUP_TOOLCHAIN              = "stable-x86_64-pc-windows-msvc"
$env:CFLAGS_x86_64_pc_windows_msvc = "/MD"
$env:CXXFLAGS_x86_64_pc_windows_msvc = "/MD"
$env:PYTHONUTF8                    = "1"
$env:PYTHONIOENCODING              = "utf-8"
```

⚠ `/MD` 那两条不是可选的。少了会在链接期报

```
LNK2038: mismatch detected for 'RuntimeLibrary'
```

因为 `esaxx-rs` 默认 `/MT` 而 `ort-sys` 是 `/MD`，两边对不上。CI 上为这个专门修过（W2.14）。

---

## 四、WiX 3.14 + 换掉 light.exe —— **这步最不显然，跳过就打不出 msi**

```powershell
$wixDir = "$env:LOCALAPPDATA\tauri\WixTools314"
New-Item -ItemType Directory -Force -Path $wixDir | Out-Null
$wixZip = "$env:TEMP\wix314-binaries.zip"
Invoke-WebRequest -Uri "https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314-binaries.zip" -OutFile $wixZip
Expand-Archive -Path $wixZip -DestinationPath $wixDir -Force

# 把原 light.exe 挪开, 用我们自己编的 wrapper 顶上
Move-Item "$wixDir\light.exe" "$wixDir\light-real.exe" -Force
rustc "$PWD\edge\companion-app\scripts\light_wrapper.rs" -o "$wixDir\light.exe" --edition 2021 -C opt-level=0
```

**为什么要这么绕**（`light_wrapper.rs` 头部有完整说明）：

Tauri v2 生成的 `main.wxs` 给每个 File 加 `KeyPath="yes"`，那是 per-machine 安装的写法；
而鲶鱼装到 `%LOCALAPPDATA%`（per-user）。WiX 的 ICE38 检查要求 user profile 下的
Component 必须用 HKCU 注册表值做 KeyPath，不能用 File → **构建直接失败**。
Tauri v2 又不暴露 `additionalLightArgs`，没法从配置传抑制参数。
所以编一个 wrapper 顶掉 `light.exe`，它加上 ICE 抑制参数再转给 `light-real.exe`。

抑制的三条影响（可接受，达华是单用户企业机）：

- **ICE38** — 多用户机上第二个用户装会无效
- **ICE64** — 卸载可能残留 `%LOCALAPPDATA%\Catfish Companion\` 子目录
- **ICE91** — 仅警告

---

## 五、出包步骤

```powershell
git clone https://github.com/jinichen/catfish.git
cd catfish
```

### 1. clone 上游 hermes（版本号从仓库里读，别手填）

```powershell
$HERMES_TAG = (Get-Content edge\companion-app\.hermes-git-tag -Raw).Trim()
$hermesDir  = "$env:TEMP\hermes-agent-src"
if (Test-Path $hermesDir) { Remove-Item -Recurse -Force $hermesDir }
git clone --depth 1 --branch $HERMES_TAG https://github.com/NousResearch/hermes-agent.git $hermesDir
```

### 2. 把 catfish 的 plugin 拷进去

```powershell
Copy-Item -Recurse -Force edge\hermes-plugins\* "$hermesDir\plugins\"
```

> 具体路径以 `.circleci/config.yml` 第 7 步 `Copy catfish plugins into hermes-agent-src` 为准，
> 那份是事实源。

### 3. 预装 node 依赖（达华无公网，必须打进包）

对 `$hermesDir` 下所有 `package.json`（Depth 4 内，排 `node_modules` 内嵌的）跑 `npm install`，
再 `npm pack` 两个全局包到 `$hermesDir\node-globals\`：

```powershell
npm pack "agent-browser@^0.26.0" "@askjo/camofox-browser@^1.5.2"
```

### 4. patch install.ps1 + **验语法**

```powershell
python edge\hermes-fork\patch_install_ps1_offline.py `
  --input "$hermesDir\scripts\install.ps1" `
  --output edge\companion-app\src-tauri\resources\windows\install.ps1

# 只查 marker 不够 —— 7/17 就是这么栽的, 装不上装了两周多没人发现
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path "edge\companion-app\src-tauri\resources\windows\install.ps1"),
    [ref]$null, [ref]$errors) | Out-Null
if ($errors) { $errors | ForEach-Object { Write-Host $_.Message }; throw "语法错" }
```

### 5. 下 uv / cpython / chromium

照 `.circleci/config.yml` 第 11/12/13 步。产物都落到
`edge\companion-app\src-tauri\resources\windows\`。

### 6. 打 hermes-agent bundle

```powershell
Push-Location $env:TEMP
tar czhf "<repo>\edge\companion-app\src-tauri\resources\windows\hermes-agent-bundle.tar.gz" `
  --exclude=hermes-agent-src/.git `
  --exclude=hermes-agent-src/venv `
  --exclude=hermes-agent-src/venv.bak `
  --exclude=hermes-agent-src/.venv `
  --exclude=hermes-agent-src/target `
  hermes-agent-src
Pop-Location
```

打完**必验**（exclude 写错不会报错，只会安静地什么都不排）：

```powershell
tar tzf $bundle | Select-String '^hermes-agent-src/(\.git|venv|\.venv|target)/'   # 要为空
tar tzf $bundle | Select-String '^hermes-agent-src/node_modules/' -Quiet          # 要为 True
```

### 7. mac 占位文件

```powershell
$macDir = "edge\companion-app\src-tauri\resources\mac"
New-Item -ItemType Directory -Force -Path $macDir | Out-Null
'install.sh','uv','cpython-3.11.15-embed.tar.gz','hermes-agent-bundle.tar.gz' | ForEach-Object {
  if (-not (Test-Path "$macDir\$_")) { Set-Content "$macDir\$_" '' }
}
```

> 这步现在其实是**死代码**（7/29 资源目录按架构拆开后，base conf 里已经没有
> `resources/mac/*` 了）。留着无害，先别删，免得多一个变量。

### 8. 前端依赖 + 构建 msi

```powershell
cd edge\companion-app
npm ci --no-audit --no-fund
npx tauri build --target x86_64-pc-windows-msvc --bundles msi --verbose
```

**注意这里不传 `--config`** —— Windows 用的就是 `src-tauri\tauri.conf.json`。

### 9. 产物

```
edge\companion-app\src-tauri\target\x86_64-pc-windows-msvc\release\bundle\msi\*.msi
```

---

## 六、装上之后要看的

这一版加了启动诊断。如果还是白屏，**页面上会画出一块面板**（不是空白），
上面有：

```
invoke("xxx") 失败, 载荷 NNN 字节
RangeError: Invalid string length
...
── 此前的超大 IPC 载荷 (>8MB) ──
invoke("yyy") 载荷 NNN.N MB
```

**第二段是关键** —— 真凶未必是抛错的那个，可能是它前面某个把内存撑爆的。
拍照带回来。

> 前端的 `console.*` **不进日志文件**（没装 plugin-log，release 版 devtools 是关的），
> 所以日志文件里找不到这些，只能看屏幕。

---

## 七、最容易卡住的四个地方

1. **没装 VS Build Tools** → 链接期报找不到 `link.exe`
2. **rustup 装成了 `-gnu` host** → CRT 不匹配
3. **忘了设 `/MD`** → `LNK2038: mismatch detected for 'RuntimeLibrary'`
4. **没换 light.exe** → WiX 报 ICE38，msi 打不出来

前三个在链接期炸，第四个在打包期炸，报错都还算直白。
真正难查的是**不报错**的那类 —— 所以第 4、6 步的验证不要跳。

---

## 事实源

这份是从 `.circleci/config.yml`（17 步）翻译来的。两边不一致时，
**以 `.circleci/config.yml` 为准**，并回来改这份文档。
