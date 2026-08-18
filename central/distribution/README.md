# 分发服务

> **状态**：设计占位，尚未形成可发布的分发服务实现。
>
> **定位**：未来托管安装脚本、私有 PyPI、策略包签名和 Companion 安装包的分发服务。

当前不要把本目录当成安装入口：

- 员工本地搜索/Hermes 集成：`onboarding/`；
- macOS Companion 发布：`edge/companion-app/`；
- Windows MSI：`edge/companion-app/scripts/build-msi-local.ps1`；
- Windows 一键安装器：WiX Burn 仍在实施；
- 客户特定交付包：`delivery/`。

---

## 职责

- `install.sh` 稳定 URL
- 私有 PyPI（PEP 503 兼容）托管公司插件
- 策略包签名下载
- Companion App 的 .dmg / .exe 分发
- 自动升级通道

---

## MVP 技术栈

- Nginx 静态托管
- `devpi` 或简单 PEP 503 目录
- `cosign` / `minisign` 签名

---

## 对外端点

```
GET https://catfish.internal/install          → install.sh
GET https://catfish.internal/pypi/simple/...  → Python 包
GET https://catfish.internal/policy/latest    → 最新策略
GET https://catfish.internal/companion/...    → 桌面 App 安装包
```
