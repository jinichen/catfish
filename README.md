# 鲶鱼（Catfish）· 企业 AI Agent 平台

鲶鱼由员工侧 Companion、Hermes Agent 集成层和企业中央服务组成，支持在客户内网部署，并通过统一网关接入不同厂商的 LLM。

当前仓库同时包含产品代码、客户交付材料和实验项目。第一次进入项目时，先按下面的入口阅读，不要从历史 CHANGELOG 开始。

## 当前入口

| 目的 | 入口 |
|---|---|
| 当前版本、已验证能力和已知限制 | [`docs/STATUS.md`](docs/STATUS.md) |
| 历史资料索引 | [`docs/HISTORICAL-MATERIALS.md`](docs/HISTORICAL-MATERIALS.md) |
| 开发规范和文件拆分纪律 | [`AGENTS.md`](AGENTS.md)、[`docs/contributors/CONVENTIONS.md`](docs/contributors/CONVENTIONS.md) |
| 中央服务开发 | [`central/README.md`](central/README.md) |
| Companion 开发 | [`edge/companion-app/README.md`](edge/companion-app/README.md) |
| Windows MSI / Burn 构建 | [`edge/companion-app/scripts/README-windows.md`](edge/companion-app/scripts/README-windows.md) |
| 员工本地搜索安装 | [`onboarding/README.md`](onboarding/README.md) |
| 客户部署 | [`README-FOR-CUSTOMERS.md`](README-FOR-CUSTOMERS.md)、[`DEPLOYMENT-RUNBOOK.md`](DEPLOYMENT-RUNBOOK.md) |
| 历史变更记录 | [`CHANGELOG.md`](CHANGELOG.md) |

## 目录边界

| 目录 | 实际职责 |
|---|---|
| `central/` | 企业中央服务：LLM Gateway、Identity、MCP Registry、Skills Hub、Web、Wiki Hub、Telemetry |
| `edge/` | 员工侧组件：Companion、Hermes fork/customization、tool-bridge、local-search、浏览器和邮件能力 |
| `plugins/` | Catfish 的策略、认证和遥测插件 |
| `connectors/` | 外部系统连接器定义和适配说明 |
| `skills/` | 可发布的 Hermes/Catfish skill 包 |
| `onboarding/` | 员工本地搜索和 Hermes 集成的跨平台安装脚本 |
| `delivery/` | 客户交付包和特定客户部署材料，不是通用产品安装器 |
| `infra/` | Docker、Kubernetes、Terraform 等基础设施材料 |
| `docs/` | 当前状态、架构、运维、决策和历史资料 |
| `projects/` | 与主产品并行的独立项目，例如 Daosheng 方案材料 |
| `scripts/` | 仓库维护、审计和验证脚本，不是业务运行时 |

### 中央服务

- `central/llm-gateway/`：模型路由、配额、审计和策略入口。
- `central/identity-server/`：用户、OIDC/SSO 和组织身份。
- `central/mcp-registry/`：MCP 连接器注册和订阅。
- `central/skills-hub/`：技能发布、审核和版本管理。
- `central/web/`：面向组织管理员和运营人员的中央 Web 门户。
- `central/wiki-hub/`：组织知识内容服务。
- `central/telemetry/`：可选的匿名遥测服务。

### 员工侧

- `edge/companion-app/`：Tauri 2 桌面应用，包含 React 前端和 Rust 后端。
- `edge/hermes-fork/`：Hermes 上游版本、离线安装补丁和升级辅助脚本。
- `edge/hermes-plugins/`：Catfish 对 Hermes 的插件扩展。
- `edge/tool-bridge/`：本地工具桥接和沙箱能力。
- `edge/local-search/`：员工本地文件搜索。
- `edge/browser-agent/`、`edge/email-agent/`：浏览器和邮件相关边缘能力。

## 当前工作基线

- Companion 版本：`0.20.0`。
- macOS：使用 Companion 专用的 arm64/x64 构建、签名和 DMG/notarization 脚本。
- Windows：在 Windows 机器上使用 `build-msi-local.ps1` 生成 x64 MSI；WiX Burn 一键安装器正在独立实施中。
- 中央服务：各服务拥有自己的 `pyproject.toml` 或 `package.json`，不要在仓库根目录创建统一虚拟环境来替代服务环境。
- Windows/macOS 的大型运行时资源在构建时生成，通常不直接提交到 Git。

## 常用开发命令

### 启动 LLM Gateway

```bash
cd central/llm-gateway
python3.12 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python -m catfish_gateway.app
```

网关测试脚本位于 `central/llm-gateway/scripts/`，例如：

```bash
bash central/llm-gateway/scripts/test_chat.sh
```

### 开发 Companion

```bash
cd edge/companion-app
npm install
npm run tauri:dev
```

当前不应使用 `npm run tauri:build` 作为发布命令；该命令在 `package.json` 中被有意禁用，因为它不会准备内嵌运行时。macOS 发布请使用：

```bash
npm run tauri:build:arm64
# 或
npm run tauri:build:x64
```

### 构建 Windows MSI

必须在 Windows 主机执行：

```powershell
cd E:\catfish\edge\companion-app
powershell -ExecutionPolicy Bypass -File scripts\build-msi-local.ps1
```

跨编译脚本 `scripts/build-windows.sh` 只能生成用于验证的 raw `.exe`，不能替代 MSI 或 Burn 发布包。

## 变更前检查

```bash
bash scripts/check_file_sizes.sh --strict
git diff --check
```

修改 Python、TypeScript、Rust 或 JavaScript 后，必须按 [`AGENTS.md`](AGENTS.md) 执行文件长度检查和对应测试。不要提交密码、证书、真实 `.env`、运行时目录或编译产物。
