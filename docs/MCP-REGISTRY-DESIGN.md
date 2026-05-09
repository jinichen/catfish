# BL-D3 MCP 连接器仓库 (mcp-registry) — 设计 + 排期

> 状态: 🔵 排期 (5/15-29, 2 周, demo 后启动)
> 决策: 鸿波 5/9 选方案 B — 跳过 MVP 静态版, 直接做完整版
> 主仓库: `central/mcp-registry/` (现 30 行 README, 0 代码)
> 依赖: `central/secret-broker/` (OAuth token 存储, BL-G6 同步启动)

---

## 1. 为啥要做

### 现状问题

鲶鱼现在的工具来源:
- **hermes 自带 tools**: 76 个 (browser / file / memory / 等)
- **catfish 加的 tools**: 25 个 (catfish_browser_* / catfish_screenshot / catfish_run_skill / 等)
- **总计**: 101 个工具

但**企业内部系统接入是 0**:
- 员工说"看 Jira 上我的任务" → 无 Jira tool, 跑去 chrome 用 browser_navigate 翻页 (慢 + 容易撞 SSO)
- 员工说"找 Confluence 上的产品方案" → 无 Confluence tool, 翻 chrome
- 员工说"看代码 review 状态" → 无 GitLab tool

每加一个企业系统都要工程团队**写新 tool + 改 hermes adapter + 改 SOUL** — 不可扩展.

### MCP 是答案

MCP (Model Context Protocol) 是 Anthropic 2024 年开放的协议, 让 LLM agent 通过标准接口接入外部服务. 一个 MCP server = 一个独立进程, 暴露 tool / resource / prompt 给任何 LLM agent.

社区已有的 MCP server: 数百个 (Jira / Linear / GitHub / Slack / Notion / Postgres / Filesystem / Google Drive / ...).

**鲶鱼的机会**: 接入 MCP 生态, 员工想用啥连接器自己订阅, 工程团队不参与每个集成.

### 跟 hermes / Claude Desktop 对照

| 维度 | Claude Desktop MCP | hermes 0.12 MCP | 鲶鱼 mcp-registry (本设计) |
|---|---|---|---|
| 配置位置 | `~/Library/Application Support/Claude/claude_desktop_config.json` 静态 JSON | 同款静态 JSON | **企业中央 registry, 员工订阅式** |
| 鉴权 | 本机 OAuth (员工自己授权) | 同 | **走 Secret Broker (企业 OAuth + 部门权限)** |
| 治理 | 无 | 无 | **管理员审批可见连接器列表 / 部门隔离 / 审计** |
| 扩展 | 员工自己装 | 员工自己装 | **管理员加, 全员可订阅; 员工也能装个人范围** |
| 鲶鱼差异化 | — | — | **企业治理层 (RBAC + 审计 + Secret Broker)** |

---

## 2. 架构

```
┌─────────────────────────────────────────────────────────────┐
│  Companion (员工端)                                          │
│   Dashboard → MCP Registry Card                              │
│     - 列出可用连接器 (来自 catfish-identity 部门权限)       │
│     - 订阅 / 取消订阅                                         │
│     - 授权 (OAuth flow → Secret Broker)                     │
└────────────┬────────────────────────────────────────────────┘
             │ HTTPS (gateway 转发)
             ▼
┌─────────────────────────────────────────────────────────────┐
│  central/mcp-registry · FastAPI 服务                         │
│   - GET  /v1/mcp/registry      (列可用连接器)               │
│   - POST /v1/mcp/subscribe     (员工订阅)                   │
│   - DELETE /v1/mcp/subscribe/{id}   (取消)                  │
│   - GET  /v1/mcp/subscribed     (我订阅的列表)              │
│   - POST /v1/mcp/oauth/start    (启 OAuth flow)             │
│   - POST /v1/mcp/oauth/callback (OAuth 回调)                │
│   - GET  /v1/mcp/manifest/{id}  (单连接器 schema)           │
│  PostgreSQL:                                                  │
│   - mcp_connectors (id, name, version, manifest_url,         │
│       allowed_dept[], status, mcp_command, env_vars[])       │
│   - mcp_subscriptions (user_sub, connector_id, status,       │
│       subscribed_at, oauth_token_ref)                        │
│   - mcp_audit (user_sub, connector_id, action, ts, ...)     │
└────────────┬─────────────────────────────────┬──────────────┘
             │                                  │
             │ secret_ref (e.g.                 │ HTTP/SSE
             │ "secret://jira-oauth-{user}")    │
             ▼                                  ▼
┌────────────────────────────┐    ┌──────────────────────────┐
│ central/secret-broker      │    │ MCP Server Pool          │
│  (BL-G6, 同期启)            │    │  (Docker containers, 每  │
│  - OAuth token 存储 (KMS    │    │   员工一个 pod 隔离)     │
│    加密)                    │    │  - jira-mcp (uvx ...)    │
│  - GET /v1/secret/{ref}     │    │  - gitlab-mcp            │
│    返 token, 审计           │    │  - confluence-mcp        │
└────────────────────────────┘    │  - filesystem-mcp        │
                                   │  - postgres-mcp           │
                                   └────────────┬─────────────┘
                                                │
                                                ▼
                                  ┌─────────────────────────┐
                                  │ Hermes / catfish-gateway │
                                  │  动态加载订阅的 tool     │
                                  │  → LLM 看到额外 tools    │
                                  └─────────────────────────┘
```

### 关键决策

1. **Pod-per-user 隔离**: 每员工每连接器一个 docker pod, 用员工 OAuth token 跑. 避免 token 泄漏跨员工.
   - 替代方案: 共享 server + 请求级 token 注入 (复杂, 隔离弱)
   - 选 pod-per-user — 央企客户对数据隔离敏感

2. **OAuth 走 Secret Broker, 不直接落 mcp-registry**: 跟 BL-G6 设计一致, KMS 加密 + 审计.

3. **manifest 协议 = MCP standard**: 不发明新 schema, 兼容社区现有 MCP server (uvx / npx 一行启动).

4. **订阅 = 配置 + 拉起**: 员工订阅 = 写一行 `mcp_subscriptions` + 拉 docker pod. 取消 = 停 pod + 删 subscription, **不删 audit**.

5. **跟 catfish-skills-hub 关系**: skills-hub 是 prompt-level 工作流, mcp-registry 是 tool-level 接口. 互补:
   - skill = "把这 5 步串起来" (workflow)
   - mcp tool = "暴露一个原子操作" (atomic)

---

## 3. API 接口

### 3.1 列可用连接器

```http
GET /v1/mcp/registry?dept=engineering&status=active
Authorization: Bearer <jwt>

Response 200:
{
  "connectors": [
    {
      "id": "jira",
      "name": "Jira",
      "version": "0.5.2",
      "description": "Atlassian Jira issue tracking",
      "allowed_dept": ["engineering", "product", "qa"],
      "tools": ["jira_search", "jira_create", "jira_update", "jira_comment"],
      "auth_type": "oauth2",
      "oauth_endpoints": {
        "authorize": "https://your-jira.atlassian.net/oauth/authorize",
        "token": "https://your-jira.atlassian.net/oauth/token",
        "scopes": ["read:jira-work", "write:jira-work"]
      },
      "subscribed": false,
      "subscriber_count": 12
    },
    ...
  ],
  "total": 8
}
```

### 3.2 订阅

```http
POST /v1/mcp/subscribe
{
  "connector_id": "jira"
}

Response 200:
{
  "subscription_id": "sub_abc123",
  "status": "pending_oauth",
  "next_step": "oauth",
  "oauth_start_url": "/v1/mcp/oauth/start?subscription_id=sub_abc123"
}
```

### 3.3 OAuth flow

```
Client → POST /v1/mcp/oauth/start?subscription_id=sub_abc123
Server  → 302 redirect to Jira authorize URL with state=sub_abc123

(员工在 Jira 授权)

Jira → GET /v1/mcp/oauth/callback?code=xxx&state=sub_abc123
Server: 
  1. 用 code 换 token
  2. POST secret-broker /v1/secret { ref: "jira-oauth-{user_sub}", value: token, kms_key: ... }
  3. 更新 mcp_subscriptions.oauth_token_ref + status='active'
  4. 拉 docker pod (jira-mcp container, 注入 secret_ref)
  5. 通知 catfish-gateway 重新加载该员工的 tool 列表
  6. → 重定向 Companion '订阅成功'
```

### 3.4 Agent 加载

`catfish-gateway` 接到 chat completion 请求后:
1. 看员工 sub
2. 查 `mcp_subscriptions` 拿活跃订阅列表
3. 动态聚合每个 MCP server 的 tool list (cache 5 min)
4. 把这些 tool 加到 `tools` 字段一起发给 LLM

LLM 调一个 mcp tool → gateway 路由到对应 MCP server pod → 拿结果回填.

---

## 4. 第一批连接器 (P0, 5/15-29 必做)

| ID | 名称 | 来源 | 优先级 |
|---|---|---|---|
| jira | Jira (Atlassian) | uvx mcp-server-jira | P0 (工程团队主流) |
| gitlab | GitLab | uvx mcp-server-gitlab | P0 (代码 review / MR) |
| confluence | Confluence | uvx mcp-server-confluence | P0 (文档) |
| feishu | 飞书 | 自己写 (无社区 MCP) | P1 (中国客户必备) |
| dingtalk | 钉钉 | 自己写 | P1 |
| filesystem | Local Filesystem | uvx mcp-server-filesystem | P0 (员工本地文件) |
| postgres | PostgreSQL | uvx mcp-server-postgres | P1 (BI / 数据查询) |
| time | Time / Clock | uvx mcp-server-time | P0 (轻量, 验证 stack) |

**5/15-29 实际目标**: ship Jira / GitLab / Filesystem / Time 4 个 (社区现成, 不用自己写).
飞书 / 钉钉 / Confluence / PostgreSQL 排 6 月做.

---

## 5. 阶段拆分

### Phase 1 — 基础设施 (5/15-21, 1 周)

**目标**: registry 服务跑起来, manifest 列表能展示, 还没真 OAuth.

```
✅ central/mcp-registry/src/catfish_mcp_registry/
   - app.py (FastAPI 服务骨架)
   - models.py (Pydantic schema)
   - db.py (SQLAlchemy + asyncpg)
   - migrations/001_init.sql
✅ central/mcp-registry/manifests/
   - jira.yaml / gitlab.yaml / filesystem.yaml / time.yaml
     (硬编码 manifest, 第二阶段才动态加)
✅ API: GET /v1/mcp/registry / GET /v1/mcp/manifest/{id}
✅ 单测覆盖 list / get / 部门权限 filter
✅ docker-compose: catfish-mcp-registry + postgres
✅ Companion Dashboard '已可用 MCP 连接器' 卡 (只读, 显示列表)
```

**交付物**: 员工能在 Dashboard 看见 "Jira / GitLab / Filesystem / Time" 4 个连接器图标 + 描述, **但还点不了订阅** (按钮 disabled, 提示"Phase 2 接通").

### Phase 2 — OAuth + Secret Broker 集成 (5/22-25, 0.5 周)

```
✅ Secret Broker (BL-G6) 同期启动
   - central/secret-broker/src/...
   - KMS 加密 (mac keychain dev / aws kms prod)
   - GET /v1/secret/{ref} + POST /v1/secret 接口
✅ mcp-registry 加 OAuth flow
   - POST /v1/mcp/subscribe → status=pending_oauth
   - POST /v1/mcp/oauth/start → 302 to provider
   - POST /v1/mcp/oauth/callback → exchange + 存 secret-broker
✅ Companion '订阅' 按钮接通 → 跳浏览器授权 → 回 Companion 显示成功
✅ 单测 + e2e (mock OAuth provider)
```

### Phase 3 — Agent 动态加载 (5/26-29, 0.5 周)

```
✅ catfish-gateway 接 mcp-registry 拿员工订阅列表
   - 启动时 / chat 请求时拉一次 (cache 5 min)
   - 聚合 manifest tool list 加到 tools 字段
✅ MCP server pod 拉起逻辑
   - docker run jira-mcp --token-from-secret-ref=jira-oauth-{user_sub}
   - pod-per-user, 一个员工一个 pod
   - Agent 调 tool → gateway 路由到 pod
✅ 真 demo: 员工说 "看我 Jira 上的任务" → LLM 调 jira_search → 返回真数据
✅ 审计: mcp_audit 表记每次 tool call (user, connector, tool, ts, result_ok)
```

### Phase 4 (后续, 6 月做)

- 第二批连接器 (飞书 / 钉钉 / Confluence / Postgres)
- 自定义 MCP server 上传 (员工/管理员加自己写的)
- 部门管理员审批 / 隔离
- BL-MM7 user_profile 集成 ("员工常用 Jira 项目=PROD" 自动 inject context)

---

## 6. 跟其他 BL 的关系

| BL | 关系 |
|---|---|
| BL-G6 Secret Broker | **强依赖**, 5/15-21 同期启动 |
| BL-D14 Plan D 协议 | 无关 (Plan D 是 a2a, 这是 mcp) |
| BL-MM9 propose_skill | **互补**: skill 是 prompt 工作流, mcp 是原子 tool |
| BL-E1 鲶鱼晨报 | **依赖**: 晨报需要 Jira / GitLab MCP 拉数据 |
| BL-E6 内部系统懒人代理 | **依赖**: 该 task 直接基于 mcp-registry |
| BL-E7 代码改完更新文档 | **依赖**: 需要 GitLab MCP |
| BL-E25 三层统一搜索 | **依赖**: 内部那层走 mcp-registry |
| BL-FE-Dashboard | 加新卡 (P1 同步做) |

**MCP 接通后解锁的卖点 BL**: E1 / E6 / E7 / E25 — 都是真央企客户卖点, MCP 是基础.

---

## 7. 风险

| 风险 | 缓解 |
|---|---|
| docker pod-per-user 资源开销大 (8 员工 × 4 连接器 = 32 pod) | Phase 1 用 hostmode, Phase 4 加 idle pod 自动 stop |
| MCP server 安全审查不充分 (社区 server 可能 backdoor) | Phase 1 只用 Anthropic 官方仓库的 MCP server, 第三方进 P2 审查 |
| Secret Broker 没做 → 卡进度 | 同 sprint 做, 不分先后, 设计 BL-G6 跟 BL-D3 同期 |
| 跟 hermes 0.12 自带 mcp 配置冲突 | Phase 1 不动 hermes; Phase 3 在 gateway 层注入, hermes 自带的本地 mcp 也共存 |
| 飞书 / 钉钉无社区 MCP server | 排 6 月做; 5/15-29 只 ship 4 个英文社区现成的 |

---

## 8. 测试

- Phase 1: 单测 30+ (model / db / API / 部门权限)
- Phase 2: e2e mock OAuth 5+
- Phase 3: 真 jira-mcp 集成测试 (用 dev jira instance), 5+ scenario
- 总计 50+ 单测 / e2e

---

## 9. Demo 后客户卖点话术

- "鲶鱼支持企业 MCP 协议接入. 你公司 Jira / GitLab / Confluence / 飞书 / 钉钉 / 内部 OA, 一份 manifest 配置就接通."
- "员工在 Dashboard 自己订阅, 不用 IT 一个个配. 部门权限自动隔离."
- "OAuth token 走 Secret Broker KMS 加密, 央企等保合规."
- "MCP 是 Anthropic 开放标准. 跟 Claude Desktop 同生态, 数百个社区连接器现成可用."

---

## 决策签名

> **2026-05-09**: 鸿波拍板方案 B (跳过静态 MVP), 5/15 demo 后启动. 估时 2 周, 4 连接器 (Jira / GitLab / Filesystem / Time). Secret Broker 同期启动.
> **次轮 review**: 5/15 sprint kickoff 时确认 Secret Broker 协议 + 第一个连接器选型.
