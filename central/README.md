# Central Services

`central/` 是 Catfish 的组织级服务区。每个服务独立维护依赖、配置、迁移和测试，不在仓库根目录共享一个运行环境。

## 服务目录

| 服务 | 目录 | 主要职责 |
|---|---|---|
| LLM Gateway | `llm-gateway/` | 模型路由、配额、策略、审计和统一 API |
| Identity | `identity-server/` | 用户、OIDC/SSO、组织身份和员工目录 |
| MCP Registry | `mcp-registry/` | MCP 连接器注册、订阅和权限过滤 |
| Skills Hub | `skills-hub/` | 技能发布、版本、审核和组织分发 |
| Web | `web/` | 管理员、部门和运营后台 |
| Wiki Hub | `wiki-hub/` | 组织知识内容服务 |
| Telemetry | `telemetry/` | 可选匿名遥测 |

## 开发原则

- 服务目录内的 `pyproject.toml` 或 `package.json` 是该服务的依赖真源。
- 数据库连接、模型目录、配额和用户 seed 使用各服务的 `.env.example` / example 配置；真实配置不提交。
- 中央服务存放组织元数据和审计信息，不应把员工本地私有会话、密码或原始文件复制到中央目录。
- 生产部署和客户特定材料位于 `delivery/` 或 `infra/`，不要把客户配置直接写进服务代码。

## 常用入口

```bash
cd central/llm-gateway
python3.12 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python -m catfish_gateway.app
```

其他服务请进入对应目录阅读本地 README，并使用该目录自己的测试命令。当前仓库没有 `central/secret-broker/` 目录；相关凭据交换能力由现有身份、网关和连接器边界承担，README 不再把不存在的目录列为服务。
