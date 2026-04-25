# 鲶鱼（Catfish）· 企业 AI Agent 平台

> **让每个员工拥有一个会上网、会收邮件、越用越懂他的数字副手**
>
> 基于 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 深化构建。

---

## 📖 入口文档

**所有设计决策的权威来源**：
👉 [`../catfish-design.md`](../catfish-design.md)

读完它就懂：做什么 / 不做什么 / 为什么 / 4 周路线图 / 完整目录结构。

**其他常看的**：
- [`CHANGELOG.md`](CHANGELOG.md) — 每日进展日志
- [`docs/IDEAS.md`](docs/IDEAS.md) — 核心完成后的"好玩功能"清单（18 个创意）
- [`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md) — 运维故障排查
- [`docs/contributors/CONVENTIONS.md`](docs/contributors/CONVENTIONS.md) — 代码规范
- [`docs/contributors/AI-USAGE.md`](docs/contributors/AI-USAGE.md) — AI 协作与软著合规

---

## 🏗️ 目录速查

| 目录 | 职责 | 状态 |
|---|---|:---:|
| `central/llm-gateway/` | ✅ LLM 治理网关 | 已完成 |
| `central/skills-hub/` | 组织级技能市场 | P1 |
| `central/mcp-registry/` | 内部系统连接器 | P1 |
| `central/secret-broker/` | 短期令牌发放 | P1 |
| `central/distribution/` | 分发安装脚本 | P0.5 |
| `central/telemetry/` | 匿名遥测 | P1 |
| `edge/companion-app/` | ★ 员工桌面 App（Email Agent 主载体） | Week 3 |
| `edge/hermes-customizations/` | Hermes 深化（Browser Agent） | Week 2 |
| `edge/web-ui/` | 极简浏览器 UI | P2 |
| `plugins/catfish-policy/` | ✅ 红线策略 | 代码完成 |
| `plugins/catfish-gateway-auth/` | SSO 自动配置 | P0 剩余 |
| `plugins/catfish-telemetry/` | 客户端侧遥测 | P1 |
| `connectors/` | MCP 连接器（Jira/Confluence/GitLab） | P1 |
| `skills/` | Hermes skill 包 | Week 3 起 |
| `installer/` | 一键安装器 | P0.5 |
| `docs/` | 文档（模块架构、运维、贡献指南） | 持续 |
| `infra/` | IaC (Docker/K8s/Terraform) | 按需 |
| `scripts/` | 平台团队运营脚本 | 按需 |
| `tests/` | 跨服务测试 | 持续 |

---

## 📏 代码规范（必读）

- [`docs/contributors/CONVENTIONS.md`](docs/contributors/CONVENTIONS.md) — 规模约束、拆分时机、AI 协作规则
- [`docs/contributors/AI-USAGE.md`](docs/contributors/AI-USAGE.md) — AI 辅助协作与软著合规规范

**提交前三件套自检**：
```bash
bash scripts/check_file_sizes.sh       # 扫文件尺寸
bash scripts/check_ai_tells.sh         # 扫 AI 痕迹（软著合规）
ruff check src/                        # 静态检查
```

---

## 🚀 今天能跑起来的

```bash
cd central/llm-gateway
python3.12 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # 填入 INTERNAL_LLM_KEY 和 CATFISH_DEV_TOKEN
python -m catfish_gateway.app
```

然后另开终端：
```bash
./scripts/test_chat.sh
```

端到端验证见设计文档 §11。

---

## 🧭 三大能力支柱

### 1. Browser Agent（Week 2）
让 agent 自己操作 Chrome 完成网页任务。基础是 Hermes 已有的 browser tools，要做多模态、登录态、多tab 深化。

**不是**"员工用浏览器跟 agent 聊天"。

### 2. Email Agent → Companion App（Week 3–4）
员工通过**全局快捷键 Ctrl+Shift+A** 召唤浮窗。浮窗+Chrome 各司其职：浮窗是指挥中心，Chrome 是工作台。100% 客户端路径，不依赖邮件服务器（因为 IMAP 企业已关）。

### 3. Self-Evolution（Week 1）
Hermes 自带的 memory/skill/cross-session search + 我们新搭的 Skills Hub。先验证 Hermes 现有机制真跑通，再叠组织层。

---

## 🎯 下一步

- **今晚/明天**：休息，明天连内网后测 `catfish-private-main`（内部 Qwen3.5）
- **Week 1**：Self-Evolution 体检
- **Week 2+**：按设计文档 §9 推进

Built with ☤ by Catfish Platform Team.
