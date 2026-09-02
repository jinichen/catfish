# Windows x64 MSI 本地构建

本文只描述当前可用的 MSI 构建流程。它不是 Burn Bootstrapper 文档；Burn 完成后会另有独立构建入口。

## 产物边界

| 产物 | 作用 |
|---|---|
| raw `.exe` | 只验证 Windows target 编译，不是安装包 |
| Companion `.msi` | Tauri/WiX 主程序包，当前 Windows 测试安装包 |
| Burn `Setup.exe` | 计划中的员工一键安装器，尚未由本脚本生成 |

当前脚本：`edge/companion-app/scripts/build-msi-local.ps1`。

## 前置条件

在 Windows x64 主机安装：

| 工具 | 要求 |
|---|---|
| Rust | MSVC toolchain，包含 `x86_64-pc-windows-msvc` target |
| Node.js | 22.x，包含 npm/npx |
| WiX | 3.14，`candle.exe` 和 `light.exe` 在 PATH，或位于 `%USERPROFILE%\.wix314` |
| Git | 能访问 Catfish 和 Hermes 仓库 |
| Python | Python 3，可运行 Hermes patch 脚本 |
| tar | Windows 自带版本或 Git/其他发行版提供的可用版本 |

脚本启动时会再次检查 `rustc`、`cargo`、`node`、`npm`、`npx`、`git`、`python`、`tar` 和 WiX `candle.exe`。

建议至少准备 5GB 可用磁盘空间：Rust 编译缓存、Hermes Node 依赖、Chromium 解压目录和 MSI 中间文件会同时存在。

## 构建命令

```powershell
cd E:\catfish\edge\companion-app
powershell -ExecutionPolicy Bypass -File scripts\build-msi-local.ps1
```

脚本会自动定位仓库根目录，并依次完成：

1. `git pull origin main`；
2. 按 `.hermes-git-tag` 拉取 Hermes，并校验 `.hermes-target-version`；
3. 注入 `edge/hermes-plugins/` 中的 Catfish plugins；
4. 安装 Hermes 目录下需要的 npm 依赖，并打包全局 npm 包；
5. 生成带离线参数的 `install.ps1`；
6. 下载 uv 和 CPython embed zip；
7. 用 Playwright dry-run 解析 Chromium 地址，再用 `curl.exe` 下载、解压和校验 `.exe`，最后打包 `chromium-embed.tar.gz`；
8. 打包 `hermes-agent-bundle.tar.gz`；
9. 准备 macOS 资源占位文件（Windows 配置不会再参与 macOS 构建）；
10. 安装 Companion 前端依赖并运行 Tauri/WiX MSI 构建。

Chromium 步骤不应手动再运行 `npx playwright install chromium`。当前脚本已经把下载、解压和校验拆开并打印阶段日志，避免下载进度到 100% 后长时间静默。

MSI 只负责安装 Companion 和离线资源，不再在安装事务中运行 Hermes
`install.ps1`、邮件或微信读取器脚本。用户第一次启动 Companion 后，应用会在后台
以隐藏子进程完成准备，界面可继续使用并显示阶段进度；详细输出写入：

```text
%LOCALAPPDATA%\hermes\logs\catfish-companion-bootstrap.log
```

## 可选跳过参数

只有在对应缓存已经通过上一次构建验证后，才使用这些参数：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-msi-local.ps1 `
  -SkipHermesClone `
  -SkipNpmInstall `
  -SkipChromium `
  -SkipFrontendInstall
```

不要只因为文件存在就跳过资源校验。特别是 Chromium 目录和 Hermes `node_modules` 被杀毒软件或中断构建破坏时，应删除缓存后重新准备。

## Windows 资源

资源目录：

```text
edge/companion-app/src-tauri/resources/windows/
```

最终构建至少需要以下非空文件：

```text
install.ps1
uv.exe
cpython-3.11.15-embed.zip
hermes-agent-bundle.tar.gz
chromium-embed.tar.gz
```

这些大型资源由 Windows 构建脚本生成，默认不提交到 Git。Windows 资源只由
`tauri.windows.conf.json` 在 Windows 构建时加载；macOS 构建不会读取这些文件。
安装阶段不执行这些脚本，首次启动时由 Companion 后台任务按需执行。

## 输出位置

```text
edge/companion-app/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/msi/
```

检查产物：

```powershell
$msiDir = "src-tauri\target\x86_64-pc-windows-msvc\release\bundle\msi"
Get-ChildItem $msiDir -Filter "*.msi" |
  Select-Object Name, @{N='MB';E={[math]::Round($_.Length / 1MB, 1)}}
```

## 安装测试和日志

```powershell
$msi = Get-ChildItem "src-tauri\target\x86_64-pc-windows-msvc\release\bundle\msi\*.msi" |
  Select-Object -First 1
$log = "$env:USERPROFILE\Downloads\catfish-msi-local-verbose.log"
Start-Process msiexec.exe -ArgumentList @(
  "/i", $msi.FullName,
  "/l*v", $log
) -Wait
```

当前 MSI 仍包含较大的离线 Runtime，但不再执行 Hermes 或附加组件的长耗时
post-install。若首次启动后准备失败，查看前述 bootstrap 日志；MSI 安装日志
`$log` 只用于排查安装器本身的问题。

## 构建前检查

```powershell
Get-ChildItem "edge\companion-app\src-tauri\resources\windows" |
  Where-Object { $_.Name -in @(
    'install.ps1', 'uv.exe', 'cpython-3.11.15-embed.zip',
    'hermes-agent-bundle.tar.gz', 'chromium-embed.tar.gz'
  ) } |
  Select-Object Name, Length
```

任何资源为 0 字节都不能继续生成正式 MSI。

## 与 macOS 的关系

本脚本只在 Windows 上生成 Windows MSI。macOS 发布使用 Companion `package.json` 中的 `tauri:build:arm64` 或 `tauri:build:x64`，不要用本脚本替代 macOS 构建。
