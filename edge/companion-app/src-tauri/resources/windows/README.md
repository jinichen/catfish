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
| `hermes-deps-dist.tar.gz` | jieba、Playwright 及其依赖 wheel | `scripts/build-msi-local.ps1` / GitHub Actions |

## 构建规则

在 Windows 主机从 Companion 目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-msi-local.ps1
```

构建脚本会按 `.hermes-git-tag` 和 `.hermes-target-version` 校验 Hermes 版本，并在打包前检查资源是否非空。不要用空 placeholder 生成正式 MSI。

仓库中的 placeholder 只用于满足 Tauri 的跨平台资源路径校验；它们不是员工安装时可用的运行时资源。

## MSI 与 Burn 的边界

当前 MSI 仍通过 WiX/Tauri 处理运行时资源。Burn Bootstrapper 完成后，Windows 员工安装应改用 Burn 生成的 `Catfish-Companion-Setup.exe`，而不是直接分发这个 MSI。
