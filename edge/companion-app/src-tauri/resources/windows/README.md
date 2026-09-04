# Windows 构建资源

本目录是 Windows 构建时的资源输入目录。大型运行时资源通常由 Windows 本地构建脚本生成，不直接提交到 Git。

## 正式资源

| 文件 | 用途 | 生成入口 |
|---|---|---|
| `install.ps1` | Hermes 离线安装脚本 | `edge/hermes-fork/patch_install_ps1_offline.py` |
| `uv.exe` | Python/运行时管理 | `scripts/build-msi-local.ps1` |
| `cpython-3.11.15-embed.zip` | Windows Python embed | `scripts/build-msi-local.ps1` |
| `hermes-agent-bundle.tar.gz` | Hermes 源码和离线 Node 依赖 | `scripts/build-msi-local.ps1` |
| `chromium-embed.tar.gz` | Playwright Chromium 离线资源 | `scripts/build-msi-local.ps1` |
| `catfish-email-dist.tar.gz` | catfish-email wheel 和 Hermes 邮件 skill | `scripts/build-msi-local.ps1` / GitHub Actions |
| `install-catfish-email.ps1` | 首次启动后台任务把邮件 wheel 和 skill 装进 Hermes venv | Companion Windows bootstrap |
| `hermes-deps-dist.tar.gz` | jieba、Playwright 及其依赖 wheel | `scripts/build-msi-local.ps1` / GitHub Actions |

## 构建规则

在 Windows 主机从 Companion 目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-msi-local.ps1
```

构建脚本会按 `.hermes-git-tag` 和 `.hermes-target-version` 校验 Hermes 版本，并在打包前检查资源是否非空。不要用空 placeholder 生成正式 MSI。

Windows 资源只由 `tauri.windows.conf.json` 在 Windows 构建时加载。仓库不再保存
空 placeholder；运行 Windows 构建脚本后才会生成可用于 MSI 的正式运行时资源。

MSI 不再执行 Hermes 或附加组件安装 CustomAction，避免安装时弹出黑窗并阻塞安装。
用户首次启动 Companion 后，隐藏后台任务会按需执行这些脚本；日志位于
`%LOCALAPPDATA%\hermes\logs\catfish-companion-bootstrap.log`。因此发布新版本
后不需要把 Hermes 安装塞进 MSI 事务，旧的 Hermes 核心也不会被重复解压。

Foxmail 如果使用自定义存储目录，可在 `%USERPROFILE%\.catfish\companion.yaml`
中配置，不要把个人盘符写进程序：

```yaml
email:
  foxmail_root: 'E:\\nextcloud\\mailstore\\ffchenhb@chinatelecom.cn'
```

配置后重启 Companion。程序会只调用 Foxmail 适配器，不再探测 Outlook COM；
目录不存在或没有可读邮件文件时，邮件页会显示真实错误，不会伪装成空收件箱。

## MSI 与 Burn 的边界

当前 MSI 仍通过 WiX/Tauri 处理运行时资源。Burn Bootstrapper 完成后，Windows 员工安装应改用 Burn 生成的 `Catfish-Companion-Setup.exe`，而不是直接分发这个 MSI。
