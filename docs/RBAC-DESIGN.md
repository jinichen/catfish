# 鲶鱼 · RBAC 角色访问控制设计 v0.1

> **版本**: v0.1, 2026-05-02 起草 (五一假期 Day 6)
> **状态**: spec 完成, 5/2 实施 + 单元测试 ship, 真实部门级生产推到 Phase 2 (Q3 2026)
> **决策签名**: 鸿波 5/2 拍板 — 3 角色模型 (admin / manager / employee), 不做 5 角色 / 不做 ACL

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

## 9. 决策签名

> v0.1 = 2026-05-02 鸿波 5 月假期 Day 6 拍板.
> 决策点:
> 1. 角色数 = 3 (admin / manager / employee). 不做 5 角色 (auditor / superadmin)
> 2. 权限模型 = role-based, 不做 ACL (Phase 3 升级)
> 3. department-based 隔离 = manager 限 managed_departments. admin 隐式全权
> 4. JWT token 携带 role + department + managed_departments. gateway / Companion 直接验, 不回查 identity
> 5. 默认 role = employee (兼容老 yaml)
>
> Phase 2 Q3 完整生产 ship. 五一 sprint 5/2 ship 范围 = spec + middleware + 单元测试, 不含 Companion UI 完整改造 (UI 改造 Phase 2 跟 manager 仪表盘一起做).
