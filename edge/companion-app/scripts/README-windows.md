# Windows 构建入口

Windows 构建有两种用途，不能混用：

| 入口 | 运行环境 | 产物 | 用途 |
|---|---|---|---|
| `build-windows.sh` | macOS/Linux | raw `x86_64-pc-windows-gnu` `.exe` | 验证 Rust/前端能否编译到 Windows |
| `build-msi-local.ps1` | Windows x64 | WiX/Tauri `.msi` | 正式 MSI 本地构建和离线资源打包 |
| Burn 构建脚本 | Windows x64 | `Catfish-Companion-Setup.exe` | 面向员工的一键安装入口，正在实施 |

## 1. raw `.exe` 跨编译

在 macOS 或 Linux 上执行：

```bash
cd edge/companion-app
bash scripts/build-windows.sh
```

该流程只能验证代码和 Rust target，不能完成以下工作：

- 生成 MSI 或 Burn Bootstrapper；
- Authenticode 签名；
- 准备离线 Hermes、Python、uv、Chromium 运行时；
- 验证 Windows 真机安装和升级。

## 2. 正式 MSI 本地构建

必须在 Windows x64 主机执行。完整步骤见 [`build-windows-msi-local.md`](build-windows-msi-local.md)：

```powershell
cd E:\catfish\edge\companion-app
powershell -ExecutionPolicy Bypass -File scripts\build-msi-local.ps1
```

脚本会自动：

- 拉取 `.hermes-git-tag` 指定的 Hermes 版本并校验 `.hermes-target-version`；
- 注入 Catfish plugins；
- 准备 Python、uv 和 Chromium 离线资源；
- 打包 Hermes 及其已安装的 Node 依赖；
- 运行 Tauri/WiX 生成 x64 MSI。

前置工具：Rust MSVC、Node.js 22、Git、Python 3、WiX 3.14，以及可用的 `tar`。脚本会自行检查工具是否存在。

MSI 输出目录：

```text
edge/companion-app/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/msi/
```

## 3. Burn Bootstrapper

MSI 是底层程序包，不是最终员工安装体验。Burn 方案会把 Companion MSI 和离线 Runtime 安装器串成一个有进度、有检测、有恢复能力的 `Catfish-Companion-Setup.exe`。

实施计划：[`docs/plans/2026-08-18-wix-burn-bootstrapper.md`](../../../docs/plans/2026-08-18-wix-burn-bootstrapper.md)

在 Burn 完成前，不要把当前超大 MSI 宣传为最终“一键安装器”。

## 4. 签名

Windows 正式发布还需要在 Windows 机器上使用 Authenticode 证书和 `signtool.exe` 签名。签名证书、私钥和密码不能进入仓库或构建日志。

## 5. 常见判断

- 需要验证编译：使用 `build-windows.sh`。
- 需要给测试人员 MSI：使用 `build-msi-local.ps1`。
- 需要给员工的一键安装包：等待 Burn Bootstrapper 构建完成。
- 不能在 macOS 上把 raw `.exe` 直接当成 Windows 安装包。
