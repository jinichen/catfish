# 鲶鱼 · 客户 SSO 接入指引

> 给客户 IT 拿这份能**自己接** SSO, 不需要我们驻场.
> 标准 OIDC 流程, 任何符合 OIDC spec 的 IdP 都能接.

---

## 我们的 SSO 工作原理 (3 段速读)

### 1. 三个组件

```
catfish-identity (8998)         ← 自带 OIDC server (客户没 SSO 时用我们的)
catfish-gateway (8999)          ← LLM 中央路由, 验 IdP 签的 JWT
catfish-companion (Tauri 桌面)  ← 员工客户端, 走 OAuth flow 拿 token
```

### 2. 客户 IdP 接入路径 (4 选 1)

| 路径 | 适用 | 工作量 |
|---|---|---|
| **A. 客户没 SSO** → 用 catfish-identity | 小客户 / SMB | 0 (默认配置) |
| **B. 客户有自建 OIDC** → 直接接 | 央国企 / FFCS-SSO 等 | 30 分钟 |
| **C. 客户用飞书** → 飞书 OIDC adapter | 民企 / 创业公司 | 1 小时 |
| **D. 钉钉 / 企微** → Phase 2 (Q3 2026) | 大型企业 | 等升级 |

### 3. 切换路径几乎免成本

接入 / 切换都改 **3 个文件**:
- `catfish-gateway/.env` — issuer / client_id / audience
- `~/.catfish/companion.yaml` — 同上 + scope
- (可选) `catfish-identity` 关掉 (改用客户 IdP 时)

改完重启即可生效, 30 秒切换. 不需要重 build / 重部署.

---

## 路径 B · 接入客户自建 OIDC (最常见)

> 客户公司有自己的 SSO 系统 (FFCS-SSO / Auth0 / Keycloak / Okta 等). 走这条.

### 客户 IT 给我们以下信息

| 字段 | 例 | 在客户 IdP 哪里找 |
|---|---|---|
| `issuer` | `https://sso.ffcs.cn` | OIDC discovery URL 的根 |
| `client_id` | `catfish-companion` | IdP 创建的 client (我们建议名字 `catfish-*`) |
| `client_secret` | `(IT 给的, 保密)` | client 创建后 IdP 生成 |
| `redirect_uris` | `http://127.0.0.1:0/cb` (任意端口) | 我们注册时填 |
| `audience` | (一般 = client_id) | 部分 IdP 单独配 |
| `jwks_uri` | (默认 `<issuer>/.well-known/jwks.json`) | 多数 IdP 标准路径 |

### 客户 IT 要做的

1. **在 SSO 后台创建 client**:
   - Application type: `Web Application` 或 `Native` (Companion 是桌面 app)
   - Grant type: `Authorization Code`
   - **Redirect URIs** 配:
     ```
     http://127.0.0.1:*/cb
     ```
     (鲶鱼用 random port + /cb path, 写星号或预留多个端口范围都行)
   - Scopes: 至少 `openid email profile`
   - Token endpoint auth method: `client_secret_post` 或 `client_secret_basic`

2. **检查 JWT claims** 含:
   - `sub` (subject, 必须. 鲶鱼用 sub 作 user ID — 我们建议是 email)
   - `email` 或 `preferred_username`
   - `name` (员工名字)
   - 可选: `department`, `groups`, `roles` (Phase 2 RBAC 会用)

3. **测试 OIDC discovery**:
   ```bash
   curl https://sso.ffcs.cn/.well-known/openid-configuration | jq
   ```
   应该返一个含 `authorization_endpoint` / `token_endpoint` / `jwks_uri` 的 JSON.

### 我们部署时配 (你们 IT 不用关心实现, 但有问题可以查)

#### gateway `.env`

```bash
CATFISH_ENV=prod
CATFISH_OIDC_ISSUER=https://sso.ffcs.cn
CATFISH_OIDC_AUDIENCE=catfish-companion
# 可选, 默认 issuer/.well-known/jwks.json
# CATFISH_OIDC_JWKS_URI=https://sso.ffcs.cn/.well-known/jwks.json
```

#### Companion `~/.catfish/companion.yaml`

```yaml
oidc:
  issuer: https://sso.ffcs.cn
  client_id: catfish-companion
  audience: catfish-companion
  scope: openid email profile
  # client_secret 不放这里 — 桌面 app 不存 secret (PKCE 替代, Phase 2)
```

#### 关闭 catfish-identity

不再需要本地 OIDC server. Companion / autostart 配置里删掉它的启动项 (或 `launchctl unload` 那个 LaunchAgent).

---

## 路径 A · 客户没 SSO, 用 catfish-identity

> 适合小客户 / 部门级试用. 5 分钟搞定.

### 步骤

1. **客户 IT 准备员工列表**:
   ```yaml
   # /opt/catfish/identity-server/users.yaml
   users:
     - email: alice@ffcs.cn
       password_hash: $2b$12$xxx     # bcrypt, 见下面生成命令
       name: Alice
       department: sales
       tier: employee
     - email: bob@ffcs.cn
       password_hash: $2b$12$yyy
       name: Bob
       department: engineering
       tier: admin
   ```

2. **生成 bcrypt password hash** (员工临时密码 → 客户 IT 给员工后让员工立即改):
   ```bash
   PYTHONPATH=src python3 -c \
     'from catfish_identity.users import hash_password; print(hash_password("TempPass123!"))'
   ```

3. **启 catfish-identity** (Companion autostart 自动管, 或手动):
   ```bash
   cd /opt/catfish/identity-server
   PYTHONPATH=src python3 -m catfish_identity
   ```

4. **gateway `.env`** 指向本地:
   ```bash
   CATFISH_ENV=prod
   CATFISH_OIDC_ISSUER=http://127.0.0.1:8998
   CATFISH_OIDC_AUDIENCE=catfish-companion
   ```

5. **Companion 默认配置**就指向本地 `http://127.0.0.1:8998`, 不用改.

### 注意事项

- catfish-identity 是**简易 OIDC server**, 适合 demo / 部门试用. 大客户 (>500 人) 应该用客户自己的 SSO (路径 B).
- 密码改密码 / 员工自助找回**Phase 2** 加 (现在 IT 改 yaml 重启即生效).
- 失败计数 / 锁账户 Phase 2 加 (现在没限制).
- 加 / 删用户改 yaml 后**重启 catfish-identity** 生效 (Phase 2 加 hot-reload).

---

## 路径 C · 飞书

### 飞书开放平台创建自建应用

1. 浏览器开 https://open.feishu.cn/
2. 用**企业版**飞书账号登录 (个人飞书不行)
3. 进 "开发者后台" → "创建自建应用"
4. 填:
   - 应用名: `鲶鱼 Companion`
   - 描述: `员工 AI 副手`
5. 创建后, 进应用详情页
6. 左侧 "凭证与基础信息":
   - 拿 **App ID** = OIDC `client_id`
   - 拿 **App Secret** = OIDC `client_secret`
7. 左侧 "添加能力" → "网页应用" / "OAuth 登录"
8. 配回调 URL: `http://127.0.0.1:0/cb` (鲶鱼用 random port + /cb path)
9. 申请 scope: `openid email profile`

### gateway / Companion 配置

```bash
# gateway .env
CATFISH_OIDC_ISSUER=https://passport.feishu.cn/suite/passport/oauth   # 注意飞书的非标准 issuer
CATFISH_OIDC_AUDIENCE=<App ID>
CATFISH_OIDC_JWKS_URI=https://open.feishu.cn/.well-known/jwks.json     # 飞书具体路径需查最新文档
```

```yaml
# Companion ~/.catfish/companion.yaml
oidc:
  issuer: https://passport.feishu.cn/suite/passport/oauth
  client_id: <App ID>
  audience: <App ID>
  scope: openid email profile
```

### 注意事项

- 飞书 ID Token 不严格符合 OIDC spec, 部分字段名跟标准不同 (例如 sub 可能叫 user_id). Phase 2 加飞书专用 adapter 处理.
- 现在用 catfish 接飞书需要我们写一个 `feishu_oidc.py` 适配器, 客户 IT 反馈"我们用飞书" → 我们给定制版.

---

## 故障排查 FAQ

### Q1: 员工点登录后浏览器报"无法连接服务器"

**现象**: 浏览器开 IdP 登录页 → 输密码 → 跳回 `http://127.0.0.1:<port>/cb` → 浏览器显示无法连接.

**原因**:
1. Companion 临时 HTTP server 60 秒超时, 员工花太久才登录
2. 防火墙拦了 127.0.0.1 内部端口
3. Redirect URI 不匹配 IdP 注册的范围

**解法**:
1. 让员工 30 秒内完成登录, 慢了重试
2. macOS 防火墙 / 公司网络策略检查, 127.0.0.1 应该全允许
3. IdP 注册的 redirect_uri 必须包含 `http://127.0.0.1:0/cb` 或 wildcard

### Q2: 登录成功但 Companion 显示"OIDC 配置错"

**原因**: `~/.catfish/companion.yaml` 没存对位置 / 格式错 / `oidc` 段 `issuer` 字段缺.

**解法**:
```bash
cat ~/.catfish/companion.yaml
# 应该看到 oidc: 段
# 如果没有, 删了文件让 Companion 启动时自动重新生成默认
rm ~/.catfish/companion.yaml
osascript -e 'quit app "Catfish Companion"' && open "/Applications/Catfish Companion.app"
```

### Q3: gateway 报 "Token validation failed: Invalid issuer"

**原因**: gateway `.env` 的 `CATFISH_OIDC_ISSUER` 跟 IdP 实际签的 token `iss` 字段不一致.

**解法**:
```bash
# 拿 token decode 看 iss 字段
# (复制 ID token 到 https://jwt.io 解码, 看 iss)
# 跟 .env 的 CATFISH_OIDC_ISSUER 对齐 (注意末尾有没有 / 不一样会不匹配)
```

### Q4: gateway 报 "Token validation failed: Audience verification failed"

**原因**: token `aud` claim 跟 gateway `CATFISH_OIDC_AUDIENCE` 不一致.

**解法**: 改 gateway `.env` 的 `CATFISH_OIDC_AUDIENCE` 跟 token 实际 `aud` 一致 (decode token 看). 多数 IdP `aud = client_id`.

### Q5: gateway 报 "kid not found in jwks"

**原因**: token header 的 `kid` (key ID) 不在 `jwks_uri` 返回的公钥列表里. IdP 旋转了密钥但缓存没刷新.

**解法**:
1. 重启 gateway (清 JWKS 缓存)
2. 或等 10 分钟自然过期
3. 持续撞: 联系 IdP 团队确认 jwks_uri 返的 keys 含当前签 token 的 kid

### Q6: 端到端登录成功, 但 Companion 仪表盘看不到 user info

**原因**: ID token 缺 `name` / `email` / `department` claim.

**解法**:
- 客户 IT 在 IdP 后台**显式 enable** 这些 claim (有的 IdP 默认只给 `sub`)
- gateway `.env` 不需要改 — gateway 不强制要求这些字段

---

## 安全配置建议 (生产部署 checklist)

### 必做

- [ ] **HTTPS**: 客户 IdP / gateway 都走 HTTPS (除非全内网纯 HTTP). issuer URL 用 https.
- [ ] **client_secret 保密**: gateway `.env` 文件权限 0600, 不进 git.
- [ ] **JWT 签名验证**: gateway 默认走 jwks_uri 验签, 不能关掉.
- [ ] **token TTL 短**: ID token 1 小时 (默认), 不延长.
- [ ] **audit log 留档**: gateway `~/.catfish/gateway_audit.jsonl` 定期归档 (磁盘满会被覆盖).
- [ ] **dev_token 保密**: 生产环境 `CATFISH_DEV_TOKEN` env 不设 / 设了 IT 单独管 (救急用).

### 应做

- [ ] **PKCE** (Phase 2): 桌面 app 不存 client_secret, 用 PKCE 替代. catfish-identity / 客户 IdP 都得支持.
- [ ] **refresh_token rotation** (Phase 2): 防 refresh_token 泄漏长期失效.
- [ ] **失败计数 / 锁账户** (Phase 2): 防爆破密码.
- [ ] **审计代码 review**: 客户 IT 看 gateway / catfish-identity 代码确认没后门 (~600 行 SSO 代码).

### 可做

- [ ] **集成 LDAP / AD**: catfish-identity 默认 YAML 用户表, Phase 2 接 LDAP.
- [ ] **多租户**: Phase 3 集团级部署需要.
- [ ] **mTLS 客户端证书** (高安全要求): Phase 2.

---

## 验收标准 (客户 IT 接入完成的标志)

### Day 1: 基础接入

- [ ] catfish-gateway / catfish-identity (或客户 IdP) 都跑起来, healthz 正常
- [ ] 1 个员工浏览器登录 Companion → 看到主界面 + user info 显示员工名
- [ ] curl 调 gateway `/v1/catalog` 返 `authenticated: true`

### Day 2: 端到端验证

- [ ] 员工调用业务 skill (例如抓 EIS 数据) 全流程不报错
- [ ] gateway audit JSONL 记录: user / model / token 数 / TTFT / status
- [ ] Companion 仪表盘 AuditCard 显示数据
- [ ] 员工**注销 / 重启 Companion** 后仍能正常登录 (Keychain 持久化)

### Day 3: 安全验证

- [ ] 员工登录后不能调用其他员工的接口 (需要 Phase 2 RBAC, 现在 sub 字段隔离)
- [ ] gateway audit 不含对话内容 (字段 grep 验证)
- [ ] 员工密码 / API key 不进 LLM context (`grep -r "secret_ref" ~/.catfish/gateway_audit.jsonl`)
- [ ] 客户 IT 在 IdP 后台**禁用员工** → 1 小时内 Companion 自动失效 (token 过期)

---

## 联系方式 / 升级路径

接入遇到问题 → 联系 chenhongbo@ffcs.cn

接入成功后:
- **Phase 2** (Q3 2026): SSO 加固 (PKCE / id_token 验签 / refresh rotation) + RBAC + Skill share + Win 跨平台. 升级不需要重新接入 SSO.
- **Phase 3** (Q4 2026): Catfish Federation 跨员工 agent 协作. SSO 不变, 加 mTLS / agent identity.
- **Phase 4** (2027 Q2+): 集团级 multi-tenant. 跨子公司 SSO 联邦. 单独谈方案.
