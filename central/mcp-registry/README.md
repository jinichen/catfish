# catfish-mcp-registry · 企业 MCP 连接器仓库

> **状态**: 🔵 5/9 Phase 1 ship (服务骨架 + 4 manifest + Dashboard 卡只读)
> **路线图**: Phase 2 (5/22+) OAuth + Secret Broker · Phase 3 (5/26+) Agent 动态加载 + pod-per-user
> **完整设计**: `docs/MCP-REGISTRY-DESIGN.md`

---

## Phase 1 (5/9 ship) — 在做啥

```
catfish-mcp-registry FastAPI 服务 (默认 :8996)
  ├─ GET /health                  健康 + manifest 数
  ├─ GET /v1/mcp/registry         列连接器 (按 X-Catfish-User-Dept 部门过滤)
  └─ GET /v1/mcp/manifest/{id}    单连接器详情 (含 OAuth / mcp_command 内部字段)

manifests/                         硬编码 yaml (Phase 2+ 加 PG 持久化)
  ├─ jira.yaml         (社区 mcp-server-jira, 工程类部门)
  ├─ gitlab.yaml       (社区 mcp-server-gitlab, 工程类)
  ├─ filesystem.yaml   (社区 mcp-server-filesystem, 全员)
  └─ time.yaml         (社区 mcp-server-time, 全员, 极轻量验 stack)

Companion Dashboard 第四组 "服务 / 配额" 加 McpRegistryCard
  - 列出可订阅连接器 + 部门过滤 + 展开看 tools / 部门权限
  - 订阅按钮 disabled (Phase 2 接通)
```

## 启 dev

```bash
cd central/mcp-registry
python3.12 -m venv venv && source venv/bin/activate
pip install -e .

# 启服务 (默认 127.0.0.1:8996)
python -m catfish_mcp_registry.app

# 验证
curl http://127.0.0.1:8996/health
curl http://127.0.0.1:8996/v1/mcp/registry            # 全员可见: filesystem + time
curl -H "X-Catfish-User-Dept: engineering" \
     http://127.0.0.1:8996/v1/mcp/registry            # 工程部门可见全 4 个
```

## 测试

```bash
pip install -e .[dev]
python -m pytest tests/ -v
```

预期 22 单测全过 (loader 11 + api 11).

## 跟 catfish-gateway 集成 (反向代理)

为了让 Companion 走单一 origin (`config.gatewayUrl`), gateway 需要反代:

```yaml
# central/llm-gateway/config.yaml (新加)
mcp_registry:
  upstream_url: http://127.0.0.1:8996
  prefix: /v1/mcp
```

gateway 收到 `/v1/mcp/*` 请求 → 透传到 mcp-registry, 同时把员工的 `dept`
从 JWT 抽出加到 `X-Catfish-User-Dept` header.

(Phase 1 dev 也可直连 8996, McpRegistryCard 走 `config.gatewayUrl` 默认 8999, 还得在 gateway 加反代.)

---

## Phase 2 路线 (5/22-25)

```
+ POST   /v1/mcp/subscribe                员工订阅
+ DELETE /v1/mcp/subscribe/{id}           取消订阅
+ GET    /v1/mcp/subscribed               我订阅的
+ POST   /v1/mcp/oauth/start              启 OAuth flow
+ POST   /v1/mcp/oauth/callback           OAuth 回调

+ central/secret-broker (BL-G6 同期启)    KMS 加密 token 存储
+ alembic migrations + asyncpg PG 池
+ Companion 订阅按钮接通
```

## Phase 3 路线 (5/26-29)

```
+ docker pod-per-user 拉起 (一员工一连接器一 pod)
+ catfish-gateway 启动时拉员工订阅列表 → 聚合 manifest tools 加到 LLM tools 字段
+ tool call 路由 → MCP server pod (HTTP/SSE 协议)
+ mcp_audit 表记 tool call (user, connector, tool, ts, ok)
+ 真 demo: 员工说 "看我 Jira 上的任务" → 真返数据
```

---

## 跟其他模块关系

| 模块 | 关系 |
|---|---|
| `central/llm-gateway` | 反向代理 `/v1/mcp/*` + Phase 3 注入订阅 tool 列表 |
| `central/secret-broker` (BL-G6) | **强依赖** Phase 2, OAuth token 存储 |
| `central/catfish-skills-hub` | **互补**: skill = workflow / mcp = atomic tool |
| `edge/companion-app` Dashboard | McpRegistryCard 渲染目录 |

---

## 决策

- **2026-05-09**: 鸿波 "可以完成了" + "现在就开始做, 为什么又要拖" → 跳静态 MVP, Phase 1 ship 真服务. spec 在 `docs/MCP-REGISTRY-DESIGN.md`.
