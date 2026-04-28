# 鲶鱼 · SSO 接入设计 (BL-D6)

> **版本**: v1, 2026-04-28
> **拍板人**: 鸿波 (待拍 § 6 关键决策点)
> **状态**: 设计稿, 实施前必须 § 6 全部拍板
> **关联 BACKLOG**: BL-D6 (本) / BL-D7 已做 / BL-D8 RBAC / BL-D9 Quota

---

## 1 · 目标 + 时间线

**目标**: 替代当前 dev token 静态鉴权, 真接入企业 SSO. 是 Phase 2 客户合同里**硬要求** — 没 SSO 任何 IT 团队都不会签合同.

**时间窗口**: Phase 1 末尾 (Week 6-7), 跟 Win 跨平台并行做.

**Phase 2 公告前必须完成**:
- ✅ 至少一个 IdP (推荐飞书) 接入跑通
- ✅ Companion OAuth flow 真机能用
- ✅ 文档站有"怎么接 X IdP" 配置指南
- ⏸ 多 IdP 支持 (钉钉 / 企微 / Azure 等可以 Phase 3 加)

---

## 2 · 当前状态诊断

```python
# central/llm-gateway/src/catfish_gateway/auth.py
@dataclass
class User:
    sub: str                 # unique id (SSO user id)
    department: str = ""     # 给 P1 RBAC 用
    tier: str = "employee"   # employee | admin

def _verify_bearer(authorization):
    # env=dev: 比对 CATFISH_DEV_TOKEN, 所有员工都是 sub="dev-user"
    # env=prod: 直接 501 "not yet implemented"
```

**当前限制**:
- 所有员工共享 sub="dev-user" — audit / quota / RBAC 都没法做 (无法区分谁)
- env=prod 直接 501 — 公司装上 catfish 进 production 就挂
- department / tier 字段是 hard-code, 跟实际 IdP 数据没对接

**已有占位** (实施时复用):
- `User` dataclass 字段名跟 OIDC claims 自然对齐 (sub / department / tier)
- `_verify_bearer` 函数边界清晰 (返 User 或 None, 不抛)
- `can_access(model)` 接口存在, P1 RBAC 直接填

---

## 3 · 客户 IdP 谱系 (现实数据)

100-500 人中国企业实际用的 IdP:

| IdP | 占比估计 | 协议 | 难度 | 一期是否支持 |
|---|---|---|---|---|
| **飞书** (Lark / Lark Suite) | ~35% | OIDC | 低 (官方文档好) | ✅ Phase 1A |
| **钉钉** | ~25% | OIDC + 自定义 | 中 (token 格式特殊) | ⏸ Phase 1B |
| **企业微信** (WeCom) | ~15% | OAuth2 + 自定义 | 中 (不完全 OIDC) | ⏸ Phase 1B |
| **自建 OIDC** (公司 IT 自己搭, 例: Keycloak) | ~10% | OIDC 标准 | 低 | ✅ Phase 1A 顺手 |
| **Microsoft Azure AD / Entra** | ~5% (出海企业) | OIDC | 低 | ⏸ Phase 2 |
| **Google Workspace** | ~3% (互联网型) | OIDC | 低 | ⏸ Phase 2 |
| **传统 LDAP / SAML** (国企 / 老企业) | ~5% | SAML / LDAP | 高 | ⏸ Phase 3 |
| **自建 OAuth2** (没标 OIDC) | ~2% | OAuth2 | 中 | 🚫 case-by-case |

**Phase 1 范围**: 飞书 + 自建 OIDC (覆盖 ~45% 客户)
**Phase 2 加**: 钉钉 + 企微 (覆盖 ~85%)
**Phase 3 加**: Azure / Google / SAML (覆盖海外 + 大企业)

---

## 4 · 协议选型

| 协议 | 用 | 不用 | 备注 |
|---|---|---|---|
| **OIDC** (OpenID Connect) | ✅ 主推 | — | 飞书 / 钉钉 / 企微 / Azure / Google / 自建 都支持. 现代 SSO 事实标准 |
| **OAuth 2.0** | ✅ 当 OIDC 子集 | — | OIDC = OAuth 2.0 + id_token. 实际只用 OAuth 也能 work, 但缺少 sub/email 标准 claim |
| **SAML 2.0** | ⏸ Phase 3 | — | 大企业 / 政府 老系统. XML-based, 复杂. 一期不做, 客户问就告知 Phase 3 |
| **LDAP** | 🚫 不做 | ✅ | 内网协议, 不适合 Web Companion. 客户用 LDAP → 让 IT 部署一个 OIDC bridge |
| **Custom HMAC** | 🚫 不做 | ✅ | 客户自己造的协议, 一对一对接成本高. 拒绝 |

**结论**: 只做 OIDC (含 Phase 3 SAML). LDAP / Custom 让客户 IT 自己接 OIDC bridge.

---

## 5 · 架构分层

```
┌──────────────────────────────────────────────────┐
│  Companion App (员工本机)                          │
│                                                    │
│   OAuth Flow (Tauri webview / 系统浏览器):          │
│   1. Companion 启动 → 没 token → 弹浏览器          │
│   2. 浏览器跳 IdP login 页 (飞书 / 钉钉 / 等)      │
│   3. 员工 SSO 登录 → callback 回 Companion         │
│   4. 拿 access_token + id_token                    │
│   5. 存 token (macOS Keychain / Win Credential)    │
│   6. 自动刷新 (refresh_token, 30 天有效)           │
│                                                    │
└──────────────────────┬───────────────────────────┘
                       ↓ Authorization: Bearer <id_token>
┌──────────────────────────────────────────────────┐
│  catfish-gateway (中央)                            │
│                                                    │
│   AuthProvider (ABC, 新)                           │
│   │                                                │
│   ├── DevTokenProvider (env=dev 现有)              │
│   │      sub = "dev-user", 共享                    │
│   │                                                │
│   ├── OIDCProvider (env=prod, P1 新)               │
│   │      ├── verify_jwt(token) → claims            │
│   │      ├── claims_to_user(claims) → User         │
│   │      └── jwks 缓存 + 自动 refresh              │
│   │                                                │
│   ├── adapters/                                    │
│   │      ├── feishu.py (P1)                       │
│   │      ├── dingtalk.py (P2)                     │
│   │      ├── wecom.py (P2)                        │
│   │      └── generic.py (任意标准 OIDC IdP)        │
│   │                                                │
│   └── SAMLProvider (Phase 3)                       │
└──────────────────────┬───────────────────────────┘
                       ↓ JWKS (公钥) — 启动时拉
┌──────────────────────────────────────────────────┐
│  IdP (公司侧)                                       │
│  飞书 / 钉钉 / 企微 / Azure / Google / 自建        │
└──────────────────────────────────────────────────┘
```

**关键设计**:

- **JWT stateless** — gateway 不存 session, 验证 id_token 签名即可. IdP 撤销 user → catfish 自动失效 (token 过期不再有效).
- **JWKS 缓存** — 启动时拉 IdP 公钥, 缓存 1 小时, 自动 refresh. IdP 升级 key 不影响在线员工.
- **adapter pattern** — 每个 IdP 一个 adapter, 隔离差异 (claims 名 / token 格式 / 端点 URL).

---

## 6 · 关键决策点 (🔴 鸿波拍板)

> 实施前必须 6 条全部拍板, 任何一条没拍板不能开工.

### 决策 1: Phase 1 一期支持哪几个 IdP?

**选项**:
- (A) 只飞书 (1 IdP, 工作量最小, 覆盖 ~35%)
- (B) **飞书 + 自建 OIDC** ✅ 推荐 (~45% 覆盖, 自建 OIDC 几乎零额外工作)
- (C) 飞书 + 钉钉 + 企微 (覆盖 ~85%, 工作量 +5 天)

### 决策 2: 走自建 SSO server 还是直接接 IdP?

- (A) **直接接** ✅ 推荐: gateway 直接验证 IdP 签的 JWT. 简单, 客户 IT 自己配回调 URL.
- (B) 自建 catfish-sso server: 中央 SSO 服务, gateway 调它. 工作量大 (+ 1 周), 客户公司不需要每员工配, 但中央复杂度上升.

### 决策 3: 用户身份 (User.sub) 格式

- (A) **email** ✅ 推荐: 跨 IdP 通用, 跟 audit / RBAC / quota 自然对齐
- (B) 工号: 中国企业常用, 但跨 IdP 不通用 (飞书没工号字段, 要从 employee_no claim 拿 — 各家叫法不同)
- (C) `<idp>:<user_id>` 组合: 最严格, 但 user 切 IdP 就变两个人

### 决策 4: 离线 / IdP 不可达兜底

- (A) **catfish 进入"只读模式"** ✅ 推荐: 能查历史 / 用本地 catfish_*, 不能发 LLM 请求
- (B) 缓存 user info 30 天 + 离线允许全功能
- (C) 永远依赖 IdP 在线 (最严格, 但出差 / 弱网 = 不能用)

### 决策 5: session 存哪 (跟决策 4 配套)

- (A) **JWT stateless** ✅ 推荐: 中央不存任何 session, IdP 撤销立刻失效. 唯一缺点: 主动 logout 不可能 (要等 token 过期). 给短 TTL (1h) + refresh 解决.
- (B) 中央 Redis: 可以主动 logout, 但中央有状态. 跟 STRATEGY "中央最小" 略冲突.

### 决策 6: dev_token 是否保留作为生产兜底?

- (A) **保留, 走 env var `CATFISH_DEV_TOKEN`** ✅ 推荐: 客户 IT 救急 / SSO 配错时能临时进. 警告 banner "你正在用 dev token, 不是真 SSO".
- (B) env=prod 时彻底禁用 dev_token: 最严格, 但 IT 撞错 SSO 配置 → catfish 整体宕机.

---

## 7 · 实施 phase 切分

### Phase 1A · AuthProvider 抽象重构 (1-2 天)

**目标**: 引入 AuthProvider ABC, 把现有 dev token 逻辑包装进 DevTokenProvider, 行为不变.

具体改:
- 新 `auth/__init__.py` (替代 `auth.py`)
- `auth/base.py`: `AuthProvider` ABC + `User` dataclass (从 auth.py 搬过来)
- `auth/dev_token.py`: `DevTokenProvider` (现有逻辑搬过来)
- `auth/__init__.py`: 工厂方法 `make_auth_provider(config)` 根据 env 返不同 provider
- `app.py`: `get_current_user / _optional` 改成调 `app.state.auth_provider.verify_bearer(...)`
- 测试: 所有现有测试不应改, dev token 行为 100% 不变

**验收**: 现有测试全过, 新增 5 case 验证 AuthProvider ABC 边界.

### Phase 1B · OIDCProvider + 飞书 adapter (3-4 天)

**目标**: env=prod 时能用飞书 SSO 真接入.

具体改:
- `auth/oidc.py`: `OIDCProvider` 通用实现 (jwks 拉取 + 缓存 + JWT 验证 + claims → User 映射)
- `auth/adapters/feishu.py`: 飞书 OIDC adapter (issuer URL / scopes / claims 映射)
- `auth/adapters/generic.py`: 标准 OIDC adapter (给自建 IdP 用)
- `config.py`: 新增 `auth:` 段配置 (idp_type / issuer / client_id / client_secret / scopes / claims_mapping)
- `models.yaml` / `.env.example` 更新示范配置
- 测试: mock IdP / mock JWT / 多 case (token 过期 / 签名错 / claims 缺字段)

**配置示范**:

```yaml
# config/models.yaml 新增 auth 段
auth:
  provider: oidc
  oidc:
    idp_type: feishu       # feishu / generic / dingtalk / wecom
    issuer: https://open.feishu.cn/connect/oauth
    client_id: ${FEISHU_CLIENT_ID}
    client_secret: ${FEISHU_CLIENT_SECRET}
    scopes: ["openid", "email", "profile"]
    claims_mapping:
      sub: email           # 决策 3 拍 email 的话, sub 字段从 claims.email 拿
      department: department_path
      tier: tier            # 默认 "employee", admin 由 claim 决定
  fallback_to_dev_token: true  # 决策 6 拍 (A) 时为 true
```

### Phase 1C · Companion OAuth flow (2-3 天)

**目标**: 员工首次启动 Companion, 自动跳浏览器 SSO 登录, 拿到 token 自动配置.

具体改:
- `companion-app/src-tauri/src/commands/auth.rs`: Tauri command `auth_login` / `auth_logout` / `auth_status`
- 用 Tauri webview 或系统默认浏览器跳 `<gateway>/auth/login?idp=feishu`
- gateway 加 `/auth/login` `/auth/callback` `/auth/refresh` 端点处理 OAuth flow
- access_token 存 macOS Keychain (`security` CLI) / Win Credential Manager
- 现有 `gateway_get_dev_token` Tauri command 改造成支持读 keychain 优先, fallback dev_token

**用户流程**:
```
1. 员工首次开 Companion
2. Companion 检查 keychain 没 token → 弹浏览器跳 IdP login
3. 飞书登录页 → 员工授权
4. 飞书回调 → catfish-gateway/auth/callback → 拿到 id_token + access_token
5. gateway 把 token 转给 Companion (deep link callback catfish://auth?token=...)
6. Companion 存 keychain
7. 后续所有 LLM 请求带 Bearer <token>
```

### Phase 2 · 钉钉 + 企微 adapter (2-3 天)

复用 Phase 1B 的 OIDCProvider 模板, 写两个新 adapter.

钉钉特别点: 钉钉的 OIDC 支持不完整, 要用 access_token 调钉钉 API 拿 user info 再映射成 User.

企微特别点: 企微 OAuth2 不是标准 OIDC, 要单独写 `WecomOAuth2Provider` (不继承 OIDCProvider).

### Phase 3 · 其他 IdP / SAML (按需做)

- Azure AD / Google: 复用 generic OIDC adapter, 配置文件改一下
- SAML: 用 `python-saml` 库, 单独 SAMLProvider

---

## 8 · 跟其他系统的衔接

### Audit (BL-D7 已做)

- 现有 `log_request_metadata` 的 `user` 字段从 `User.sub` 拿. SSO 后 `user` 是真 email/工号, 不是 dev-user.
- `gateway_audit.jsonl` 字段不变, 只是 `user` 从单一值变成多值 (按员工分布).
- 客户 IT 的审计脚本 `grep "user":"alice@company.com"` 立刻能用.

### RBAC (BL-D8 待做)

- `User.department` / `User.tier` 字段从 IdP claims 直接拿
- `can_access(model)` 实现根据 department + model.tier 过滤 (例: 销售部不能用代码模型 / admin tier 才能调 vision)
- 配置: `models.yaml` 加 `allowed_departments: [...]` 字段

### Quota (BL-D9 待做)

- 按 `User.sub` 单维计数 (per-user quota)
- 按 `User.department` 维度合并 (per-department quota, 部门预算)
- 数据从 `gateway_audit.jsonl` 拿 (read_events with user_filter / since_unix)

### Skill / Memory / 其他边缘服务

- tool-bridge / Companion 不直接验证 SSO — 它们都是员工本机, 信任本机
- 但 tool-bridge 需要从 Companion 拿 user.sub 用于 audit (现在 audit 没 user 字段, P2 加)

---

## 9 · 隐私 / 合规边界

### catfish 看到什么

- ✅ 员工 email / 工号 (用作 sub)
- ✅ 员工 department / tier (用作 RBAC + 审计维度)
- ✅ access_token / id_token (短期内存 + keychain 存)
- ✅ refresh_token (长期 keychain 存, 用于自动刷新)

### catfish **不看** 什么

- ❌ 员工 IdP 密码 — 永远不碰. OAuth flow 把员工跳到 IdP 登录, 密码只在 IdP 输
- ❌ 员工 IdP 上的其他个人信息 (生日 / 身份证 / 钱包等) — 只取 OIDC scopes 里明确的字段
- ❌ 公司其他员工的数据 — JWT 只代表本人, 跨员工查询 RBAC 会拦

### 注销 / 撤销

- 员工在 IdP 撤销 catfish 授权 → refresh_token 失效 → catfish 下次刷新失败 → 自动 logout
- 员工离职公司 → IT 在 IdP 删账号 → 同上自动失效
- 不需要主动通知 catfish (JWT stateless 设计)

### 合规 (Phase 2 律师 review 前提)

- catfish 不参与员工身份认证, 只验证 IdP 签的 token. **catfish 不是身份提供方**, 不在 GDPR / 个保法的 "controller" 角色里 (是 processor).
- audit log 里 `user` 字段是 PII (email), 客户 IT 自己定保留期 / 加密 (catfish 提供工具).

---

## 10 · 离线 / 失败 fallback

### IdP 不可达

| 场景 | 行为 |
|---|---|
| 启动时 IdP DNS 解析不到 | Companion 弹错: "公司 SSO 不可达, 检查 VPN. 想用本地功能? 进只读模式" |
| Token 过期 + IdP 不可达 (refresh 失败) | 缓存的 user info 还在 → 切只读模式 (能查历史会话, 不能发新 LLM 请求) |
| 长期 IdP 挂 (> 30 天) | 缓存清掉, 强制重新 SSO |

### emergency dev_token (决策 6 拍 A 才有)

- 客户 IT 在 `~/.catfish/emergency.env` 放 `CATFISH_DEV_TOKEN=xxx`
- gateway 启动时如果探到这个文件, 接受 dev token 并在所有 audit log 加 `auth_method: emergency_dev_token` 标记
- Companion banner 红色警告 "你在用 emergency 模式, 通知 IT 修复 SSO"

---

## 11 · 测试策略

| 层级 | 怎么测 | 谁负责 |
|---|---|---|
| **AuthProvider ABC** | 单元测试, mock provider | 开发 |
| **DevTokenProvider** | 现有测试不变 | 开发 |
| **OIDCProvider** | mock IdP + 自签 JWT + 各种 claim 边界 | 开发 |
| **每个 IdP adapter** | mock 该 IdP 的 token 格式 + claims | 开发 |
| **Companion OAuth flow** | 手动 e2e (无法自动化, 因要真浏览器) | 真机 |
| **集成测试** | 飞书 sandbox 账号跑全链路 | 真机 |
| **性能** | JWKS 缓存命中率 / verify_jwt 延迟 (< 1ms 目标) | 性能 bench |

---

## 12 · 风险点

### 风险 1: 客户 IdP 配置千奇百怪

- 各家飞书 / 钉钉 callback URL / scopes / claims 不一致
- 应对: 写"按客户名一键配置脚本", IT 跑 `catfish-admin auth setup --idp=feishu --client-id=...`

### 风险 2: IdP API 升级断兼容

- 飞书 2024 改过 OAuth 端点格式
- 应对: 加 IdP version 字段, 多版本兼容代码; 出问题 audit log 能定位到具体版本

### 风险 3: 中国 to-B 客户 IT 团队对 OIDC 概念陌生

- 应对:
  - 详细配置文档 (每个 IdP 一份, 含截图)
  - 一键配置工具 `catfish-admin doctor auth` 自动诊断
  - 公开示范客户 (Phase 2 公告时找 1-2 家配合做 case study)

### 风险 4: 员工离职 / 撤销授权后 token 还能用一段时间 (JWT TTL)

- 决策: TTL 设短 (1 小时), refresh 触发时检查 IdP 撤销状态
- 实在严格要求 → 改决策 5 用 (B) Redis 有状态 session

### 风险 5: 自建 OIDC 客户配错 issuer / jwks_uri

- 应对: 启动时 catfish-gateway 主动 GET issuer 的 `.well-known/openid-configuration`, 失败立刻 fail-fast 报错给 IT (不是等员工登录时才挂)

---

## 13 · 决策签名 + 拍板状态

> 此设计 v1 (2026-04-28) 写于 `docs/AUTH-DESIGN.md`. 6 条关键决策点 § 6 待鸿波拍板, 实施前必须全部拍板 (在本文档对应位置加 ✅ + 决策原文).
>
> 修改本文需要主理人 (鸿波) 显式同意, 在 git log 留下变更原因.

**决策状态** (2026-04-28 鸿波拍板, 全部采纳推荐):

- 决策 1 (一期 IdP): ✅ **B** — 飞书 + 自建 OIDC (~45% 客户覆盖)
- 决策 2 (自建 vs 直接接): ✅ **A** — gateway 直接验 IdP 签的 JWT (中央最小)
- 决策 3 (sub 格式): ✅ **A** — email (跨 IdP 通用, audit log 可读)
- 决策 4 (离线兜底): ✅ **A** — catfish 进入"只读模式" (本地 catfish_* 可用, 不发 LLM 请求)
- 决策 5 (session 存哪): ✅ **A** — JWT stateless (1h access + 7d refresh, 中央零状态)
- 决策 6 (dev_token 保留): ✅ **A** — 保留作生产兜底 (warning banner + audit 标 auth_method=dev_token)

**拍板上下文** (2026-04-28 demo sprint):
- 5 月中旬客户交流, 必须看到多用户登录 (不能再 dev_token 单用户硬编码)
- 目标客户: 中国电信内部 + 其他央国企部门 (都用飞书, 多数有自建 SSO)
- 决策依据见 `docs/SSO-RATIFY.md` (5 分钟精简版, 含今天 demo sprint 学到的考量)
- 全部采纳推荐 = Phase 1A→B→C 7 天交付, 5 月 demo 前 ship

**实施时间线**:
- Phase 1A · AuthProvider ABC 重构: 1-2 天 (开始 2026-04-29)
- Phase 1B · OIDCProvider + 飞书 adapter: 3-4 天
- Phase 1C · Companion OAuth flow: 2-3 天
- 总计 6-9 天, 5 月 demo 前完工

---

## 跟其他文档的关系

```
STRATEGY.md             ─→  Phase 1 范围 (Mac+中央优先, BL-D6 在此)
BACKLOG.md              ─→  BL-D6 SSO / BL-D8 RBAC / BL-D9 Quota
COMPETITIVE-DIFF.md     ─→  "企业鉴权" 是硬差异化点
SKILL-LIFECYCLE.md      ─→  无关 (skill 不需要 SSO)
AUTH-DESIGN.md (本)     ─→  SSO 落地路径
                       └→  Phase 1B/1C/2 拆 task 进 BACKLOG.D 段
```

---

## 附录 A · DevTokenProvider 行为不变保证

Phase 1A 重构关键: 现有 dev token 流程 100% 不变, 没人感知. 验收条件:

```python
# 重构后这段调用应该完全等价
os.environ["CATFISH_ENV"] = "dev"
os.environ["CATFISH_DEV_TOKEN"] = "test123"

# 旧路径 (现在)
user1 = await get_current_user(authorization="Bearer test123")
assert user1.sub == "dev-user"

# 新路径 (Phase 1A 后)
provider = make_auth_provider(config)
user2 = await provider.verify_bearer("Bearer test123")
assert user2.sub == "dev-user"

# 行为完全等价
assert user1 == user2
```

---

## 附录 B · 一份示范配置文件 (.env.example 加段)

```bash
# ============================================================
# SSO / OIDC 配置 (Phase 1B 起)
# ============================================================

# Phase 1A 兼容: env=dev 时仍允许 dev_token 路径
CATFISH_ENV=prod  # dev | prod (prod 才走 SSO)

# OIDC 配置 (Phase 1B+)
CATFISH_AUTH_PROVIDER=oidc            # oidc | dev_token | saml (Phase 3)
CATFISH_OIDC_IDP_TYPE=feishu          # feishu | dingtalk | wecom | generic | azure | google

# 飞书配置 (从飞书开放平台拿)
FEISHU_CLIENT_ID=cli_xxx
FEISHU_CLIENT_SECRET=xxx

# 通用 OIDC (自建 IdP) 配置
CATFISH_OIDC_ISSUER=https://sso.example.com
CATFISH_OIDC_CLIENT_ID=catfish-gateway
CATFISH_OIDC_CLIENT_SECRET=xxx
CATFISH_OIDC_SCOPES=openid,email,profile

# Emergency dev_token (生产 IT 救急用, 可选)
# CATFISH_EMERGENCY_DEV_TOKEN=xxx
```

---

*文档由 catfish team 维护. 实施前先在 § 6 拍 6 条决策, 然后按 § 7 phase 切分进 BACKLOG.*
