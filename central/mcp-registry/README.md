# catfish-mcp-registry · 企业 MCP 连接器仓库

> **状态**: 🟢 Phase 1 + Phase 2 完整 ship (5/9). Phase 2.1 真 OAuth 框架就位 (env 切).
> **路线图**: Phase 3 (5/26+) Agent 动态加载 + pod-per-user — demo 后正式做
> **完整设计**: `docs/MCP-REGISTRY-DESIGN.md`

---

## Phase 1+2 服务

```
catfish-mcp-registry FastAPI :8996
  ├─ Phase 1 (列表/部门权限)
  │   ├─ GET    /health
  │   ├─ GET    /v1/mcp/registry              列连接器 (按 dept 过滤 + 含订阅状态)
  │   └─ GET    /v1/mcp/manifest/{id}         单连接器详情
  │
  └─ Phase 2 (订阅 + OAuth + secret-broker)
      ├─ POST   /v1/mcp/subscribe              员工订阅
      ├─ DELETE /v1/mcp/subscribe/{sub_id}     取消订阅
      ├─ GET    /v1/mcp/subscribed             我订阅的列表
      ├─ POST   /v1/mcp/oauth/start            OAuth flow 启 (返 authorize_url)
      └─ POST   /v1/mcp/oauth/callback         code 换 token + 写 secret-broker

manifests/                                     yaml 硬编码 (Phase 4 加管理员 CRUD)
  ├─ jira.yaml         (mcp-server-jira, oauth2, 工程类)
  ├─ gitlab.yaml       (mcp-server-gitlab, oauth2, 工程类)
  ├─ filesystem.yaml   (mcp-server-filesystem, path_allowlist, 全员)
  └─ time.yaml         (mcp-server-time, none, 全员)

数据库 (Phase 2 改 PG 主存储, 5/9 鸿波质疑后改的):
  - PG (CATFISH_DB_URL 设时, 跟 catfish-gateway/identity 同实例)
  - sqlite fallback (~/.catfish/mcp_registry.db, dev/单测/单机)
  - alembic 管 schema migration (跟 gateway 同模式)
  - 表前缀 mcp_ 防跟其他服务冲突 (mcp_subscriptions / mcp_audit)

依赖:
  - catfish-secret-broker :8995 (Phase 2 OAuth token 存储)
  - catfish-gateway :8999 (反向代理 /v1/mcp/* + 注入员工身份)
```

## 启 dev (3 步)

```bash
# 1. mcp-registry venv + 装依赖
cd central/mcp-registry
python3.12 -m venv venv && source venv/bin/activate
pip install -e '.[dev]'  # zsh 必须加引号, bash 可以省

# 2. 启服务 (默认 sqlite, 不需要 PG)
python -m catfish_mcp_registry.app
# → backend=sqlite (dev), 127.0.0.1:8996

# 3. 验证
curl http://127.0.0.1:8996/health
# {"status":"ok","backend":"sqlite",...}

curl http://127.0.0.1:8996/v1/mcp/registry
# 全员可见: filesystem + time

curl -H "X-Catfish-User-Dept: engineering" \
     -H "X-Catfish-User-Sub: alice@catfish.dev" \
     http://127.0.0.1:8996/v1/mcp/registry
# 工程部门可见全 4 个 + subscribed=false / subscriber_count=0
```

## 测试

```bash
python -m pytest tests/ -v
```

预期 ~45 单测全过:
- `test_loader.py` — 11 (manifest 加载 / 部门过滤)
- `test_api.py` — 11 (Phase 1 endpoints / 部门权限)
- `test_db.py` — 11 (sqlite 持久化)
- `test_subscriptions_api.py` — 12 (Phase 2 订阅 / OAuth / secret-broker)

## 跟 catfish-gateway 集成 (反向代理)

gateway 已配反代 (BL-D3 Phase 1 收尾, 5/9):

```yaml
# 默认值, gateway/config.yaml 可覆盖
mcp_registry:
  upstream_url: http://127.0.0.1:8996
  enabled: true
  timeout: 10
```

gateway 收 `/v1/mcp/*` → 透传到 mcp-registry, 同时:
- 验 JWT
- 抽 dept/sub 注入 `X-Catfish-User-Dept` / `X-Catfish-User-Sub` / `X-Catfish-User-Role` header
- 不透传原 Authorization (上游不重新 verify, 信任 gateway)

Companion 走单一 origin (gateway 8999), 不直连 mcp-registry. 见 `central/llm-gateway/src/catfish_gateway/mcp_registry_proxy.py`.

## 跟 catfish-secret-broker 集成

Phase 2 OAuth callback 收 token → POST 到 secret-broker 持久化:

```python
# mcp-registry/src/catfish_mcp_registry/secret_broker_client.py
await secret_broker_client.set_secret(
    client, ref="jira-oauth-alice@catfish.dev",
    value=access_token, user_sub=user_sub,
)
```

secret-broker (`:8995`) keyring 后端 (mac Keychain / Win wincred / Linux libsecret).

## Phase 2.1 真 OAuth (5/9 框架就位, demo 后开 env)

默认 `CATFISH_MCP_OAUTH_MODE=mock` — Companion 点订阅 → 立刻 mock callback 完成.

要真接 Jira / GitLab OAuth:

```bash
export CATFISH_MCP_OAUTH_MODE=real
export JIRA_CLIENT_ID=xxx           # 从 Atlassian 拿
export JIRA_CLIENT_SECRET=yyy
export JIRA_INSTANCE=mycompany      # https://mycompany.atlassian.net
export CATFISH_MCP_OAUTH_REDIRECT_URI=https://catfish.your-corp.com/v1/mcp/oauth/callback
python -m catfish_mcp_registry.app
```

`oauth/callback` endpoint 自动调 `manifest.oauth.token_url` (`https://auth.atlassian.com/oauth/token`) 用 authorization_code grant 换 access_token, 失败有详细 audit log.

## Phase 3 路线 (5/26-29, demo 后)

```
+ docker pod-per-user 拉起 (一员工一连接器一 pod)
  - jira-mcp pod 注入 secret-broker token + JIRA_URL
  - 资源限制 (cpu=0.5 / mem=512MB / network=internal)
  - idle 30min auto-stop, 再用时再起
+ catfish-gateway 启动时调 /v1/mcp/subscribed → 聚合 manifest.tools 加到 LLM tools 字段
+ tool call 路由 → MCP server pod (HTTP/SSE 协议)
+ 真 demo: 员工说 '看我 Jira 上的任务' → LLM 调 jira_search → 返真数据
```

## 部署模式

```bash
# dev/单测/单机 (默认)
python -m catfish_mcp_registry.app
# → backend=sqlite (~/.catfish/mcp_registry.db), 不依赖 PG

# prod (跟 catfish-gateway/identity 共享 PG)
export CATFISH_DB_URL=postgresql://catfish:xxx@pg:5432/catfish
cd central/mcp-registry && alembic upgrade head    # 部署前跑迁移
python -m catfish_mcp_registry.app
# → backend=pg
```

## 端口约定 (5/9)

```
8998 catfish-identity        (OIDC + a2a registry)
8999 catfish-gateway         (LLM + mcp 反代 + a2a)
8997 catfish-skills-hub      (历史占, demo 不启)
8996 catfish-mcp-registry    (本服务, 5/9 加)
8995 catfish-secret-broker   (5/9 加, OAuth token 存储)
```

## 决策

- **2026-05-09**: 鸿波拍板方案 B → 跳静态 MVP, 真做 Phase 1+2 一气呵成. spec `docs/MCP-REGISTRY-DESIGN.md`.
- **2026-05-09**: 鸿波 "MCP 也是中央端, 为什么 sqlite, 不统一 PG?" → 改 PG 主存储, 跟 catfish-gateway/identity 同套. sqlite 留 dev/单测 fallback.
- **2026-05-09**: 默认 `CATFISH_MCP_OAUTH_MODE=mock` 给 demo 用. 真 OAuth 框架就位, env 切到 real 即生效.
