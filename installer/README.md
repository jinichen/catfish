# 安装器目录说明

这里目前只保留安装器产品边界说明，尚未放置一个可发布的统一安装器实现。

## 当前可用的两个入口

### 员工本地搜索 / Hermes 集成

使用仓库根目录的：

```text
onboarding/install-catfish.sh
onboarding/install-catfish.ps1
```

它们负责创建员工侧 `~/.catfish` / `%USERPROFILE%\.catfish` 环境，安装 `catfish-search`，并在 Hermes 已安装时注册本地 MCP。它们不负责安装 Companion、中央服务或 Windows MSI。

### Companion 桌面应用

- macOS：见 `edge/companion-app/README.md` 和 `scripts/README-deploy.md`。
- Windows MSI：见 `edge/companion-app/scripts/README-windows.md`。
- Windows 一键安装器：WiX Burn Bootstrapper 正在实施，计划见 `docs/plans/2026-08-18-wix-burn-bootstrapper.md`。

## 不要使用的旧设计

以下内容只是早期设想，目前没有对应的实现文件，因此不能按此 README 执行：

- `installer/install.sh`
- `installer/bootstrap.py`
- `installer/verify.py`
- `curl https://catfish.internal.company.com/install | bash`

真正的客户交付包位于 `delivery/`，中央服务分发设计位于 `central/distribution/`；两者都不是通用员工安装器。
