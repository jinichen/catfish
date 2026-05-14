# 鲶鱼 · RBAC 角色访问控制设计 v0.2

> **版本**: v0.2, 2026-05-14 续写 (BL-RBAC P0 + B sprint Day 1)
> **v0.1**: 2026-05-02 起草 (五一假期 Day 6, 3 角色模型 spec + middleware MVP)
> **v0.2 增量** (5/14 鸿波 + 5/14-5/22 sprint):
>   - §10 **OAuth client credentials grant** — hermes-cli 等服务端到服务端 (S2S) 鉴权, 不再走 dev token hack
>   - §11 **per-dept allowed_models / allowed_tools / allowed_skills / allowed_channels** — P0 真按部门拦, 不再"3 角色仅看 audit"
>   - §12 **clients.yaml + ClientRegistry** — OAuth client 注册数据模型, 跟 users.yaml 平级
>   - §13 **B sprint 排程** (5/14-5/22, 8 天)
> **状态**:
>   - v0.1 spec + middleware + JWT role claim — 5/2 ship, 1006 单测稳
>   - v0.2 OAuth client credentials grant — 5/14 设计 + 第一段代码 (Day 1), 5/15 完成实现 (Day 2)
>   - per-dept allowed_* 数据模型 — 5/16 alembic migration, 5/17-5/19 各 endpoint 接
> **决策签名**: 鸿波 5/2 拍板 3 角色模型; 鸿波 5/14 拍板 v0.2 加 client_credentials + per-dept allowed_*

---

## 1. 使命

> 让国央企客户 IT 拿鲶鱼的第一个问题 — "**有没有 RBAC?**" — 鲶鱼答 "Phase 2 Q3 ship, 设计 spec + MVP 已就位".

**Phase 1 不需要 RBAC** — 单员工模型, 数据全本地, 权限边界 = 物理边界 (员工电脑).

**Phase 2 需要 RBAC** — 多员工 + 部门级 quota / skill share / audit 共享. 没 RBAC 客户 IT 拒签合同.

---

## 2. 3 角色模型 (鸿波 5/2 拍板)

```
┌──────────────────────────────────────────────────────────────┐
│  admin     IT 全权                                            │
│            • 看所有员工 audit (gateway / a2a / skill)         │
│            • 改 RBAC 配置 (加角色 / 改用户)                    │
│            • 改 catfish-identity 配置 (加用户 / 重置密码)      │
│            • 看 quota 全局, 改全局 quota 上限                  │
│            • 看部署级 / 集群级监控                             │
│                                                                │
│  manager   部门主管                                            │
│            • 看部门内员工 audit (聚合, 不看单条对话)           │
│            • 看部门 quota 用量, 改部门内分配                   │
│            • 部门 skill 审核 / 发布 (Phase 2.5 Skills Hub)     │
│            • 不能改 RBAC / catfish-identity 配置               │
│                                                                │
│  employee  普通员工 (默认角色)                                 │
│            • 看自己的 audit (token 用量 / 失败 / 模型分布)     │
│            • 调用任何对自己 quota 内的 model                   │
│            • 看部门已发布 skill (调用 / 反馈 / 推荐改进)       │
│            • 不看其他员工任何信息                              │
└──────────────────────────────────────────────────────────────┘
```

**为啥不做 5 角色 / ACL?**

- 国央企 IT 实际 90% 场景 admin/manager/employee 够用 (跟 SAP / Oracle 同模型)
- 5 角色 (加 auditor / superadmin) 增加配置复杂度但客户实际不区分
- ACL (resource-action-user 三元组) 是 AWS IAM 那种重型设计, Phase 3 才需要

**复杂度阶梯**: 5/2 ship 3 角色 → Phase 3 (Q4 2026) 升级到 ACL (跨部门 / 跨组织 / 跨子公司场景).

---

## 3. 权限矩阵

| 资源 / 操作 | admin | manager | employee |
|---|---|---|---|
| **catfish-identity** |  |  |  |
| `POST /authorize` (登录自己) | ✅ | ✅ | ✅ |
| `GET /userinfo` (拿自己 claims) | ✅ | ✅ | ✅ |
| 改自己密码 | ✅ | ✅ | ✅ |
| 改别人密码 | ✅ | ❌ | ❌ |
| 加 / 删 user | ✅ | ❌ | ❌ |
| 改 RBAC 配置 (角色赋值) | ✅ | ❌ | ❌ |
| **catfish-gateway** |  |  |  |
| `POST /v1/chat/completions` (调 LLM) | ✅ | ✅ | ✅ |
| `GET /v1/catalog` (看模型清单) | ✅ | ✅ | ✅ |
| `GET /v1/audit/me` (看自己 audit) | ✅ | ✅ | ✅ |
| `GET /v1/audit/department` (看部门聚合 audit) | ✅ | ✅ (限自己部门) | ❌ |
| `GET /v1/audit/all` (看全员 audit) | ✅ | ❌ | ❌ |
| `GET /v1/quota/me` (自己配额) | ✅ | ✅ | ✅ |
| `GET /v1/quota/department` (部门配额) | ✅ | ✅ (限自己部门) | ❌ |
| `PATCH /v1/quota/department` (改部门配额分配) | ✅ | ✅ (限自己部门) | ❌ |
| `PATCH /v1/quota/global` (改全局上限) | ✅ | ❌ | ❌ |
| **A2A (Plan D Federation)** |  |  |  |
| `POST /a2a/ask` (被同事问) | ✅ | ✅ | ✅ |
| 调别人 (受 ALLOW.md 拦截) | ✅ | ✅ | ✅ |
| **Skills** |  |  |  |
| `catfish_run_skill` 调用部门 skill | ✅ | ✅ | ✅ |
| `catfish_skill_install` 装到自己本机 | ✅ | ✅ | ✅ |
| `catfish_skill_install` 装到部门共享 (Phase 2.5) | ✅ | ✅ (限自己部门) | ❌ |
| `catfish_skill_delete` 自己装的 | ✅ | ✅ | ✅ |
| `catfish_skill_delete` 部门共享的 (Phase 2.5) | ✅ | ✅ (限自己部门) | ❌ |

---

## 4. 数据模型

### 4.1 users.yaml 扩展 (在现 catfish-identity users.yaml 基础上)

```yaml
users:
  - email: chenhongbo@ffcs.cn
    password_hash: $2b$12$...
    name: 陈鸿波
    department: 研发部
    # 五一 sprint 5/2 加: role 字段
    role: admin               # admin / manager / employee, 默认 employee
    # Phase 2 加: manager 管理的子部门
    managed_departments: []   # admin 留空 (隐式管全部); manager 列他管的部门
  - email: alice@ffcs.cn
    password_hash: $2b$12$...
    name: Alice
    department: 研发部
    role: manager
    managed_departments: ["研发部"]
  - email: bob@ffcs.cn
    password_hash: $2b$12$...
    name: Bob
    department: 研发部
    role: employee  # 默认, 可以省略
```

### 4.2 OIDC token 扩展 — `role` claim

JWT payload 加:
```json
{
  "sub": "alice@ffcs.cn",
  "email": "alice@ffcs.cn",
  "name": "Alice",
  "department": "研发部",
  "role": "manager",
  "managed_departments": ["研发部"],
  "iat": 1747000000,
  "exp": 1747007200
}
```

Companion / gateway 验签后直接拿 role + department, **不需要回查 catfish-identity**.

### 4.3 default behavior

- 没填 role → 默认 `employee` (兼容老 users.yaml)
- role=manager 但没填 managed_departments → 视为 employee (防错配)
- role=admin 但是 managed_departments 非空 → 忽略 (admin 隐式管全部)

---

## 5. 实施 (5/2 ship 范围)

### 5.1 catfish-identity 改动

- `users.py` 加 `role` / `managed_departments` 字段解析 + 默认值
- `routes.py::token` 端点签 JWT 时把 `role` / `department` / `managed_departments` 加到 payload

### 5.2 gateway 中间件

新文件 `central/llm-gateway/src/catfish_gateway/rbac_middleware.py`:

```python
class Permission(Enum):
    AUDIT_VIEW_DEPARTMENT = "audit.view_department"
    AUDIT_VIEW_ALL = "audit.view_all"
    QUOTA_PATCH_DEPARTMENT = "quota.patch_department"
    QUOTA_PATCH_GLOBAL = "quota.patch_global"
    USER_MANAGE = "user.manage"

ROLE_PERMISSIONS = {
    "admin": {p for p in Permission},   # 全权
    "manager": {
        Permission.AUDIT_VIEW_DEPARTMENT,
        Permission.QUOTA_PATCH_DEPARTMENT,
    },
    "employee": set(),
}

def require_permission(perm: Permission):
    """FastAPI dependency: 验当前 token 用户有这个权限."""
    async def dep(request: Request):
        user = request.state.user  # 来自 OIDC middleware
        role = user.get("role", "employee")
        if perm not in ROLE_PERMISSIONS.get(role, set()):
            raise HTTPException(403, f"role={role} 无 {perm.value} 权限")
        return user
    return dep
```

应用到 endpoint:
```python
@app.get("/v1/audit/department")
async def audit_department(user = Depends(require_permission(Permission.AUDIT_VIEW_DEPARTMENT))):
    department = user.get("department")
    if user.get("role") == "manager":
        # manager 限自己部门
        managed = user.get("managed_departments", [])
        if department not in managed:
            raise HTTPException(403, "无权访问该部门 audit")
    # 真返该部门 audit
    ...
```

### 5.3 Companion UI

- `IdentityCard` 显示当前角色 (徽章风格): `🛡 admin` / `👔 manager (研发部)` / `👤 员工`
- Dashboard 卡片按角色 hide:
  - `AuditCard` 部门聚合段: manager / admin 才显示
  - `QuotaCard` 改部门配额按钮: manager 自己部门只读 / admin 全开
- 没角色权限的 endpoint 调用拒绝时, UI 显示 "你的角色 (employee) 无权访问该信息" — 不是 stack trace

---

## 6. 测试 (五一 sprint 5/2)

```python
# test_rbac_middleware.py
def test_admin_has_all_permissions(): ...
def test_manager_can_view_own_department_audit(): ...
def test_manager_cannot_view_other_department(): ...
def test_employee_cannot_view_audit(): ...
def test_default_role_is_employee(): ...  # 老 yaml 没 role 字段
def test_role_in_jwt_token(): ...  # token 签 / 解 role
```

目标: 8-12 个 pytest case, 覆盖核心权限逻辑.

---

## 7. 5 月 demo 应对话术

客户 CISO 问 "**RBAC 怎么做**":

> "Phase 1 我们 ship 单员工模型 — 员工的鲶鱼对话全在他自己电脑, 不走部门维度共享, 所以 Phase 1 不需要 RBAC.
>
> Phase 2 Q3 ship 团队版, 我们设计了 **3 角色模型** — admin / manager / employee. 跟 SAP / Oracle 一样的国央企标准.
>
> - admin: IT 全权, 看所有 audit + 改配置
> - manager: 看自己部门 audit (聚合不看单条对话), 改部门 quota 分配
> - employee: 看自己, 不看别人
>
> 完整 spec 在 `docs/RBAC-DESIGN.md` (300 行), 含权限矩阵 + JWT token 扩展 + 客户端 / 服务端鉴权链路. 五一假期已经 MVP 实施 (catfish-identity 加 role 字段, gateway 加 RBAC middleware, 单元测试 12 个全过), 部门级真实生产 Q3 ship.
>
> 你们 IT 现在就可以审 spec, 提改进意见, 我们 Q3 按你们的合规要求最后定稿."

→ 客户感受: 不是"我们正在规划", 是"已经设计 + 实施 MVP, 等你们意见". 信任 +30%.

---

## 8. 演进路线 (Phase 3+)

- **Phase 3 (Q4 2026)** — 跨部门 ACL: 当客户问 "我有 100 部门, 3 角色不够" → 升级 fine-grained ACL (resource-action-user 三元组). 不动现有 3 角色 (语法糖 = ACL 子集).
- **Phase 4 (2027 Q2+)** — 跨组织 federation RBAC: 集团 + 子公司 + 客户共用, 类似 AWS Organizations.

---

## 10. OAuth client credentials grant (v0.2, 5/14 加)

### 10.1 为什么要

v0.1 设计的认证链 = 用户走 OIDC `authorization_code` flow (浏览器 → 登录页 → code → /token → access_token). 这套对**人**没问题, 对**服务端进程**不好用:

- **hermes-cli** (升级 0.13 后, 见 BL-HERMES013-INTEGRATE #49) 跑批 / 跑 skill 时没浏览器, 没办法走 authorization_code flow
- 5/13 之前的临时方案: hermes-cli 用一个 hardcoded `dev_token`, gateway `auth/dev_token.py` 认它. 这是 hack, 真生产部署客户 IT 会拒
- 跨服务调用 (e.g. catfish-gateway → catfish-skills-hub) 也得有自己的"机器身份", 不能用人的 token

### 10.2 设计 — 标准 OAuth 2.0 client_credentials grant (RFC 6749 §4.4)

```
hermes-cli (或任何 service)
    │
    │  POST /token
    │   grant_type=client_credentials
    │   client_id=hermes-cli
    │   client_secret=<bcrypt-verified>
    │   scope=chat.completions audit.write     ← 可选, 默认拿 client 的 allowed_scopes
    │
    ▼
catfish-identity
    │
    │  1. ClientRegistry.verify(client_id, client_secret)
    │  2. scope ⊆ client.allowed_scopes (越权直接 400 invalid_scope)
    │  3. 签 access_token (RS256 JWT, 同 user token 公钥 — gateway 不区分验签)
    │
    ▼
{
  "access_token": "eyJ...",   ← payload 含 client_id / scope / role / department / token_use=service
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "chat.completions audit.write"
}
```

**关键**: 不发 `id_token` (服务调用没 user sub, OIDC 概念不适用).

### 10.3 access_token JWT payload schema

```json
{
  "iss": "http://identity.catfish/",
  "sub": "client:hermes-cli",          ← 服务身份 sub 用 client: 前缀, 跟 user sub (email) 区分
  "aud": "catfish-gateway",
  "client_id": "hermes-cli",
  "token_use": "service",              ← user token 是 "access" / "id", service token 是 "service"
  "scope": "chat.completions audit.write",
  "role": "service",                   ← 加新角色 "service" — 不在 admin/manager/employee 体系里
  "department": "infra",               ← service 也归一个部门 (用来 quota / audit 归账)
  "iat": 1747000000,
  "exp": 1747003600
}
```

### 10.4 gateway 验签 + 鉴权链 (5/16-5/17 接)

`auth/oidc.py` 复用现有逻辑, **多一个分支**:

```python
def verify_token(token: str) -> AuthIdentity:
    payload = jwt.decode(token, jwks_pub, algorithms=["RS256"], audience="catfish-gateway")
    if payload.get("token_use") == "service":
        # 服务身份 — 不查 users 表, 直接拼 AuthIdentity
        return AuthIdentity(
            kind="service",
            client_id=payload["client_id"],
            sub=payload["sub"],                   # client:hermes-cli
            scopes=set(payload.get("scope", "").split()),
            role="service",
            department=payload.get("department", ""),
        )
    # 否则走原 user 路径
    user = registry.find(payload["sub"])
    ...
```

`rbac.py` 加 `service` 角色权限映射 — 默认权限 = scope 决定 (e.g. scope 含 `chat.completions` 才允许 `POST /v1/chat/completions`).

### 10.5 deprecate `auth/dev_token.py`

5/14-5/22 sprint 完成后: `dev_token.py` 加 deprecation warning + 生产 prod env 默认禁用 (`CATFISH_GATEWAY_ENV=production` 时启动直接 raise). dev / test 仍可用 (因为不可能每个 pytest fixture 都跑真 OAuth flow).

Phase 2.5 (Q3 2026) 完全删 — 那时所有 service 都迁完.

### 10.6 不实现的 (留 Phase 2+)

- **client secret rotation** (轮换密钥) — Phase 2 加 `POST /admin/clients/{id}/rotate_secret`
- **PKCE for public client** — 服务客户端不需要 (没浏览器), 跳过
- **refresh_token for service** — 服务每次 expire 重换, 1h TTL 短到不需要 refresh
- **client 自助注册 endpoint** — admin 改 yaml / DB, 服务不能自己注册

---

## 11. per-dept allowed_models / allowed_tools / allowed_skills / allowed_channels (v0.2, P0 真做)

### 11.1 v0.1 的局限

v0.1 的 3 角色 RBAC 只回答了 "**谁能看哪些 audit / 改哪些 quota**". 没回答客户 IT 真正关心的:

- 销售部 vs 研发部 — 销售部能调 `catfish-private-main` (122B 大模型) 吗? IT 只想给销售调便宜的 `private-light`
- 销售部能用 `catfish_browser_*` tool 吗? 国央企怕销售拿浏览器 tool 跑去外网拉客户名单
- 销售部能装 `engineering` namespace 下的 skill 吗? 不能 — 销售只看本部门 skill
- 销售部能进哪些 chat (Multi-Agent Kanban scope 2 的 channel)? 只能进自己部门的频道

**v0.1 ship 时承认的 trade-off**: "Phase 2 真按部门拦, v0.1 只 ship 框架". 5/14 鸿波拍板 v0.2 P0 真做 — 客户 IT 不接受空头承诺.

### 11.2 数据模型 — 4 张 join 表 (PG)

```sql
-- 11.2.1 部门 → 模型白名单
CREATE TABLE dept_allowed_models (
  department      TEXT NOT NULL,
  model_name      TEXT NOT NULL,                    -- 跟 ModelConfig.name 对齐
  max_tokens_day  BIGINT,                           -- 可选: 日 token cap (NULL = 不限)
  added_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  added_by        TEXT NOT NULL,                    -- admin email
  PRIMARY KEY (department, model_name)
);

-- 11.2.2 部门 → tool 白名单 (native + MCP, 命名空间统一)
CREATE TABLE dept_allowed_tools (
  department      TEXT NOT NULL,
  tool_name       TEXT NOT NULL,                    -- e.g. "catfish_browser_navigate", "mcp:slack:send"
  added_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  added_by        TEXT NOT NULL,
  PRIMARY KEY (department, tool_name)
);

-- 11.2.3 部门 → skill 白名单
CREATE TABLE dept_allowed_skills (
  department      TEXT NOT NULL,
  skill_namespace TEXT NOT NULL,                    -- e.g. "engineering", "sales", "personal"
  skill_name      TEXT,                             -- NULL = 整个 namespace 都允许
  added_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  added_by        TEXT NOT NULL,
  PRIMARY KEY (department, skill_namespace, COALESCE(skill_name, ''))
);

-- 11.2.4 部门 → channel 白名单 (Multi-Agent Kanban scope 2 用)
CREATE TABLE dept_allowed_channels (
  department      TEXT NOT NULL,
  channel_id      TEXT NOT NULL,                    -- e.g. "ch_engineering_general", "ch_announcements"
  can_post        BOOLEAN NOT NULL DEFAULT TRUE,    -- false = 只读
  added_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  added_by        TEXT NOT NULL,
  PRIMARY KEY (department, channel_id)
);
```

### 11.3 默认行为 (空白名单 = 全开还是全禁?)

**全开** — 没任何记录 = 允许全部. 理由:

1. 单员工部署 (catfish 早期客户) 没 admin 配 — 默认应该能用
2. "白名单空 = 全禁" 会让升级到 v0.2 时所有现有部署都挂
3. admin 真要锁 — 显式加白名单条目, 或用 `allow_all=false` 总开关 (见 §11.4)

### 11.4 总开关 — `dept_access_policy`

```sql
CREATE TABLE dept_access_policy (
  department    TEXT PRIMARY KEY,
  models_mode   TEXT NOT NULL DEFAULT 'allow_all',  -- 'allow_all' | 'whitelist'
  tools_mode    TEXT NOT NULL DEFAULT 'allow_all',
  skills_mode   TEXT NOT NULL DEFAULT 'allow_all',
  channels_mode TEXT NOT NULL DEFAULT 'allow_all',
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by    TEXT NOT NULL
);
```

- `allow_all` (默认) — 不查白名单, 全允许
- `whitelist` — 只允许 `dept_allowed_*` 表里的

### 11.5 拦截链 (gateway 端)

```
请求 → JWT 解出 user.department
     ↓
1. /v1/chat/completions  → 查 dept_access_policy.models_mode
                          → whitelist 时查 dept_allowed_models, miss 返 403
2. tool 调用 (gateway 注入 tools)  → sanitize_tools 阶段过滤掉 dept 没权限的
3. catfish_skill_install / list  → 按 dept_allowed_skills 过滤
4. /api/channels/{id}/post  → 查 dept_allowed_channels.can_post
```

### 11.6 admin UI (5/20 Day 7)

`catfish-web/admin/access` 页:
- 部门列表 + mode toggle (allow_all / whitelist)
- 每个部门展开看白名单 (4 个 tab: Models / Tools / Skills / Channels)
- "+ 加白名单" 弹窗 (autocomplete model/tool/skill/channel 名)
- 删白名单 inline confirm

---

## 12. clients.yaml + ClientRegistry (v0.2, 5/14 Day 1 第一段代码)

### 12.1 clients.yaml schema

```yaml
# central/identity-server/config/clients.yaml
clients:
  - client_id: hermes-cli
    client_secret_hash: $2b$12$xxx               # bcrypt
    name: Hermes CLI
    description: 鲶鱼 hermes 0.13 CLI 跑 skill / 跑批
    allowed_grant_types:
      - client_credentials
    allowed_scopes:
      - chat.completions
      - audit.write
      - tools.invoke
      - skills.run
    department: infra                            # service 归到一个虚拟部门
    role: service                                # 固定 = service (跟 user role 体系隔离)
    enabled: true
    created_at: "2026-05-14T15:00:00Z"
    created_by: chenhongbo@ffcs.cn

  - client_id: catfish-skills-hub
    client_secret_hash: $2b$12$yyy
    name: Skills Hub
    description: 内部 skill 发布服务
    allowed_grant_types:
      - client_credentials
    allowed_scopes:
      - skills.read
      - skills.write
    department: infra
    role: service
    enabled: true
```

### 12.2 ClientRegistry API (仿 UserRegistry)

```python
class IdentityClient:
    client_id: str
    client_secret_hash: str
    name: str
    description: str
    allowed_grant_types: list[str]          # 默认 ["client_credentials"]
    allowed_scopes: list[str]
    department: str
    role: str = "service"
    enabled: bool = True

class ClientRegistry:
    def reload(self) -> None: ...
    def find(self, client_id: str) -> IdentityClient | None: ...
    def verify_secret(self, client_id: str, client_secret: str) -> IdentityClient | None:
        """timing-safe bcrypt 比对, 跟 UserRegistry.verify_password 同模式"""
    def __len__(self) -> int: ...
```

### 12.3 默认 demo client (5/14 Day 1 ship 时填)

仅 ship `hermes-cli` 一个 (够 5/15 Day 2 联调 hermes 0.13). `catfish-skills-hub` / 其他服务 5/22 真做 deprecate dev_token 时再加.

---

## 13. B sprint (BL-RBAC P0 + hermes B) 排程 (5/14 - 5/22)

| 日期 | Day | 范围 | 输出 |
|---|---|---|---|
| 5/14 (Thu) | Day 1 | OAuth client credentials grant 设计 + clients.py + clients.yaml + /token client_credentials 分支 + 单测 | `clients.py` ~150L · `routes.py` +60L · `tests/test_clients.py` ~120L · 8-10 单测全过 |
| 5/15 (Fri) | Day 2 | gateway 端 verify_token 加 service 分支 + AuthIdentity.kind="service" + hermes-cli e2e 验证 (start hermes 0.13, 调 chat.completions 走 OAuth, 不再用 dev_token) | gateway `oidc.py` +40L · e2e demo script |
| 5/16 (Sat) | Day 3 | per-dept `dept_allowed_models` + `dept_access_policy` 表 + alembic migration `004_dept_allowed.py` + 单测 (RegistryAdapter) | migration · `dept_access.py` ~100L · 6-8 单测 |
| 5/17 (Sun) | Day 4 | gateway `/v1/chat/completions` 接 dept_allowed_models filter + `/v1/catalog` 按 dept 过滤 model 列表 | catalog.py / chat.py 改 ~40L · 集成测 |
| 5/18 (Mon) | Day 5 | `dept_allowed_tools` 接 sanitize_tools (gateway tools 注入阶段过) | tool sanitize 改 ~30L · 单测 |
| 5/19 (Tue) | Day 6 | `dept_allowed_skills` 接 catfish_skill_install / list + `dept_allowed_channels` (跟 hermes 0.13 命名对齐) + catfish-web `/admin/access` UI 启动 (页面骨架) | skill filter ~30L · admin UI 骨架 ~200L |
| 5/20 (Wed) | Day 7 | `/admin/access` UI 完整 (4 tab + 加删白名单 + mode toggle) | admin UI ~400L 完整 |
| 5/21 (Thu) | Day 8 | 测试回归 + 文档 (本文件 → v0.3 落实施记录) + dev_token deprecation warning + `CATFISH_GATEWAY_ENV=production` 拦截 | 测试 + docs + dev_token cleanup |
| 5/22 (Fri) | 缓冲 | 漏项补 (尤其 quota_users / departments 表对齐, 给 5/23 RED-2-PG 落 FK 用) | 缓冲 |

---

## 14. 决策签名

> **v0.1** = 2026-05-02 鸿波 5 月假期 Day 6 拍板.
> 决策点:
> 1. 角色数 = 3 (admin / manager / employee). 不做 5 角色 (auditor / superadmin)
> 2. 权限模型 = role-based, 不做 ACL (Phase 3 升级)
> 3. department-based 隔离 = manager 限 managed_departments. admin 隐式全权
> 4. JWT token 携带 role + department + managed_departments. gateway / Companion 直接验, 不回查 identity
> 5. 默认 role = employee (兼容老 yaml)
>
> Phase 2 Q3 完整生产 ship. 五一 sprint 5/2 ship 范围 = spec + middleware + 单元测试, 不含 Companion UI 完整改造 (UI 改造 Phase 2 跟 manager 仪表盘一起做).
>
> **v0.2** = 2026-05-14 鸿波拍板, BL-RBAC P0 + B 合一 sprint 启动.
> 增量决策点:
> 6. **OAuth client_credentials grant** 真做 — 服务端到服务端 (S2S) 鉴权, 不再走 dev_token hack
> 7. **新角色 `service`** — 跟 admin/manager/employee 隔离, 权限由 scope 决定不由角色决定
> 8. **token_use 字段** — `access` / `id` (用户) / `service` (机器), 验签后第一时间分流
> 9. **per-dept allowed_models/tools/skills/channels** P0 真做 — 不再 Q3 推 (客户 IT 真不接受空头承诺)
> 10. **空白名单 = 全开** — 兼容现有部署不挂. admin 显式 `whitelist` mode 才拦
> 11. **service token 不发 id_token** — OIDC 概念不适用服务调用
> 12. **dev_token 5/21 deprecate** — prod env 启动直接 raise, dev 仍可用
>
> v0.2 ship 范围 = 5/14 - 5/22 共 8 天 sprint, 含完整 admin UI.
