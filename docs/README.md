# Catfish 文档目录

文档按用途分为“当前状态、开发规范、架构/决策、运维交付、历史记录”五类。不要把 CHANGELOG 或历史 Feature Tracks 当成当前操作手册。

## 当前入口

| 文档 | 用途 |
|---|---|
| [`STATUS.md`](STATUS.md) | 当前版本、可用构建入口、已知限制和进行中的工作 |
| [`HISTORICAL-MATERIALS.md`](HISTORICAL-MATERIALS.md) | 阶段性资料索引和归档边界 |
| [`contributors/CONVENTIONS.md`](contributors/CONVENTIONS.md) | 代码组织、测试和拆分规范 |
| [`contributors/AI-USAGE.md`](contributors/AI-USAGE.md) | AI 协作和软著合规要求 |
| [`operations/troubleshooting.md`](operations/troubleshooting.md) | 运维排查 |
| [`../DEPLOYMENT-RUNBOOK.md`](../DEPLOYMENT-RUNBOOK.md) | 客户部署总流程 |
| [`../README-FOR-CUSTOMERS.md`](../README-FOR-CUSTOMERS.md) | 客户决策人和 IT 的产品说明 |

## 设计和决策

- `CATFISH-CENTRAL-MANIFESTO.md`：中央/边缘职责和产品边界。
- `CENTRAL-EDGE-DATA-BOUNDARY.md`：数据流和边界。
- `CATFISH-HERMES-BOUNDARY.md`：Catfish 与 Hermes 的职责边界。
- `AUTH-DESIGN.md`、`RBAC-DESIGN.md`、`MCP-REGISTRY-DESIGN.md`：身份、权限和连接器设计。
- `TEACHING-SOP.md`、`SKILL-LIFECYCLE.md`：技能教学和生命周期。
- `plans/`：尚未完成的实施计划，完成后应回写 `STATUS.md`。

## 运维和交付

- `operations/`：通用运维资料。
- `../delivery/`：客户特定交付包，不是产品代码。
- `../onboarding/`：员工本地搜索/Hermes 安装脚本。
- `../edge/companion-app/scripts/`：Companion 的开发、发布和 Windows 构建说明。

## 历史资料

以下文件保留用于追溯，不代表当前完成度：

- `FEATURE-TRACKS.md`
- `PROJECT-STATUS.md`
- `ROADMAP.md`
- `BACKLOG*.md`
- `CHANGELOG.md`
- `DAILY-*`、`SPRINT-*`、`MAY-*`、`P3.*`

### 文档维护规则

1. 新功能完成后先更新代码和测试，再更新 `STATUS.md`。
2. 路线图只写目标，不把目标写成已交付能力。
3. 过程日志写入 CHANGELOG，不把临时诊断命令复制到 README。
4. 客户文案不得引用仓库外的绝对路径。
