# Catfish Companion

Catfish Companion 是员工侧桌面应用，不是 Hermes Agent 本体，也不是中央管理后台。

它负责把员工需要的本地能力放到一个桌面入口中，包括会话、身份、配额、技能、浏览器自动化和本地运行时状态。组织级的用户、部门、模型、配额和技能管理位于 `central/web/`，通过网关和身份服务与 Companion 连接。

## 当前状态

- Tauri 2（Rust + WebView）桌面应用。
- React 18 + TypeScript + Vite 前端。
- macOS arm64/x64 构建链已存在，发布还需要本机签名和 notarization 凭据。
- Windows x64 raw cross-build 和 Windows MSVC/WiX MSI 本地构建链已存在。
- WiX Burn Bootstrapper 正在作为 MSI 之外的一键安装入口实施，见 [`docs/plans/2026-08-18-wix-burn-bootstrapper.md`](../../docs/plans/2026-08-18-wix-burn-bootstrapper.md)。

当前版本号由以下三个文件保持一致：

```text
edge/companion-app/package.json
edge/companion-app/src-tauri/Cargo.toml
edge/companion-app/src-tauri/tauri.conf.json
```

## 目录

```text
companion-app/
├── src/                         # React 页面、组件、hooks、store、API client
├── public/                      # 前端静态资源
├── src-tauri/
│   ├── src/                     # Rust commands、services、tray、运行时管理
│   ├── resources/               # 构建时注入的 macOS/Windows 运行时资源
│   ├── scripts/                 # 资源准备和文件解析脚本
│   └── wix/                     # Windows MSI WiX 模板和旧版 post-install fragment
├── scripts/                     # 开发、发布、签名、Windows 本地构建脚本
├── package.json                 # 前端和 Tauri 命令入口
└── README.md
```

## 开发

```bash
cd edge/companion-app
npm install
npm run tauri:dev
```

只验证前端：

```bash
npm run build
npm test
```

## macOS 发布

发布命令会准备对应架构的运行时资源、构建应用、检查架构、签名并生成 DMG：

```bash
npm run tauri:build:arm64
# 或
npm run tauri:build:x64
```

签名和 notarization 需要在 macOS 上配置 Developer ID 证书、私钥和 `notarytool` Keychain profile。不要把证书、`.p12`、密码或真实 profile 信息提交到仓库。

## Windows 发布

### raw `.exe` 跨编译

在 macOS 上执行：

```bash
bash scripts/build-windows.sh
```

该命令只用于验证 Windows target 能否编译，不能生成正式 MSI，也不负责 Authenticode 签名。

### x64 MSI 本地构建

在 Windows 主机执行：

```powershell
cd E:\catfish\edge\companion-app
powershell -ExecutionPolicy Bypass -File scripts\build-msi-local.ps1
```

脚本会根据 `.hermes-git-tag` 和 `.hermes-target-version` 准备 Hermes，下载并打包 Python、uv、Chromium，最后运行 Tauri/WiX 生成 MSI。完整前置条件和产物位置见 [`scripts/build-windows-msi-local.md`](scripts/build-windows-msi-local.md)。

当前 MSI 仍然是底层程序包；面向员工的一键安装入口将使用 Burn Bootstrapper，不能把 MSI 内部的长时间 Runtime post-install 当成最终用户体验。

## 相关代码边界

- 中央网关：`central/llm-gateway/`
- 本地工具桥：`edge/tool-bridge/`
- Hermes fork 和离线补丁：`edge/hermes-fork/`
- Hermes 插件：`edge/hermes-plugins/`
- 中央 Web 管理台：`central/web/`

Companion 内部只负责员工侧体验和本地进程/运行时协调；组织级权限、模型目录、部门配额等应通过中央服务获取，不在 Companion 内复制一套管理逻辑。
