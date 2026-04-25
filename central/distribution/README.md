# 分发服务

> **状态**：🟡 P0.5（推广种子员工时做）
>
> **定位**：托管安装脚本 / 私有 PyPI / 策略包签名 / Companion App 安装包。

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
