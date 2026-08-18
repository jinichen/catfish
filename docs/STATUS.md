# Catfish 当前状态

> 快照：2026-08-18
> 本文只记录仓库中已能由代码、配置或构建脚本确认的状态；历史过程和未验证设想不写成“已完成”。

## 当前基线

| 范围 | 状态 | 证据 |
|---|---|---|
| Companion | `0.20.0` | `edge/companion-app/package.json`、`src-tauri/Cargo.toml`、`tauri.conf.json` |
| macOS 应用 | 有 arm64/x64 构建、签名和 DMG/notarization 脚本 | `edge/companion-app/scripts/` |
| Windows raw 编译 | 可在 macOS/Linux 做 Windows GNU target 编译 | `scripts/build-windows.sh` |
| Windows MSI | 有 Windows x64 本地构建脚本和 WiX 模板 | `scripts/build-msi-local.ps1`、`src-tauri/wix/` |
| Windows Burn | 方案已确定，代码尚未完成 | `docs/plans/2026-08-18-wix-burn-bootstrapper.md` |
| 中央服务 | 按服务独立维护 | `central/*/pyproject.toml`、`central/web/package.json` |
| 员工本地搜索安装 | 有 macOS/Linux 和 Windows onboarding 脚本 | `onboarding/install-catfish.sh`、`install-catfish.ps1` |

## 当前可用入口

### Companion 开发

```bash
cd edge/companion-app
npm install
npm run tauri:dev
```

### macOS 发布

```bash
cd edge/companion-app
npm run tauri:build:arm64
# 或 npm run tauri:build:x64
```

签名和 notarization 依赖开发者本机的证书、私钥和 Keychain profile，不应提交到仓库。

### Windows MSI

```powershell
cd E:\catfish\edge\companion-app
powershell -ExecutionPolicy Bypass -File scripts\build-msi-local.ps1
```

该脚本会在 Windows 主机准备 Hermes、Python、uv、Chromium 运行时，并生成 MSI。它不是 Burn Bootstrapper。

## 正在处理

1. 将 Companion MSI 与 Hermes Runtime 解耦，新增 WiX Burn 一键安装器。
2. 收敛 macOS、Windows 和客户交付文档的入口，避免使用过期的 Demo 文案。
3. 按 `AGENTS.md` 拆分超过 800 行的源文件。

## 明确不作为当前状态依据的文件

- `docs/PROJECT-STATUS.md`：历史仪表盘，已停止更新。
- `docs/FEATURE-TRACKS.md`：历史研发轨迹，保留用于追溯，不代表 2026-08 当前百分比。
- `docs/ROADMAP.md`：产品路线图，不是已交付功能清单。
- `CHANGELOG.md`、`docs/DAILY-*`、`docs/SPRINT-*`：时间记录，不是操作手册。
- 根目录和客户目录下带日期的 Demo/演示材料：仅用于相应演示或客户交付场景。

## 变更前检查

```bash
bash scripts/check_file_sizes.sh --strict
git diff --check
```
