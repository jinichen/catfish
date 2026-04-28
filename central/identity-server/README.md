# Catfish Identity · 自建 OIDC server

5 个端点的 OIDC Authorization Code flow server. 给 catfish-gateway 提供 SSO,
客户没自己 SSO 时直接用; 有的话切到客户的, 关掉本服务即可.

## 端点

```
GET  /.well-known/openid-configuration   discovery JSON
GET  /.well-known/jwks.json              公钥 (RS256)
GET  /authorize                           登录页 (HTML form)
POST /authorize                           表单提交 → redirect with code
POST /token                               code → ID token + access token
GET  /userinfo                            access_token → user claims
GET  /healthz                             liveness
```

## 启动

```bash
cd central/identity-server
PYTHONPATH=src python3 -m catfish_identity
```

默认 `127.0.0.1:8998`. 改 env:

```bash
CATFISH_IDENTITY_HOST=0.0.0.0 \
CATFISH_IDENTITY_PORT=8998 \
CATFISH_IDENTITY_ISSUER=https://sso.example.com \
PYTHONPATH=src python3 -m catfish_identity
```

## Demo 用户

`config/users.yaml` 默认 2 个 demo user:

| email | password | role |
|---|---|---|
| chenhongbo@ffcs.cn | catfish123 | admin |
| demo@ffcs.cn | demo123 | employee |

**生产部署立即改密码 + 删 demo user**.

## 加用户

1. 生成 bcrypt hash:
   ```bash
   PYTHONPATH=src python3 -c \
     'from catfish_identity.users import hash_password; print(hash_password("YOUR_PWD"))'
   ```
2. 编辑 `config/users.yaml` 加一条:
   ```yaml
   - email: alice@example.com
     password_hash: $2b$12$...
     name: Alice
     department: sales
     tier: employee
   ```
3. 重启 server (Phase 2 加 hot-reload)

## 验证 server 跑起来

```bash
# discovery
curl http://127.0.0.1:8998/.well-known/openid-configuration | python3 -m json.tool

# 公钥
curl http://127.0.0.1:8998/.well-known/jwks.json

# health
curl http://127.0.0.1:8998/healthz
```

浏览器访问 `http://127.0.0.1:8998/authorize?client_id=test&redirect_uri=http://localhost/cb&response_type=code` → 看到登录页.

## 跟客户 SSO 切换

客户上线后想用他们自己的 SSO?

1. 关掉 catfish-identity (Companion 不启动它即可)
2. gateway `.env` 改 issuer / client_id / client_secret 指向客户 SSO
3. 重启 gateway, 30 秒搞定

## 端口约定

| 服务 | 端口 |
|---|---|
| catfish-gateway | 8999 |
| catfish-identity | 8998 |
| Catfish Chrome CDP | 9222 |
| tool-bridge socket | unix `~/.catfish/tool-bridge.sock` |

## 安全声明

Phase 1B-1 实现了**核心** OIDC flow, 但**简化了**这些 (Phase 2 加固):

- 没 PKCE (Phase 1B-2 加, Companion 端用)
- 没 refresh_token rotation (Phase 1C 加)
- 没 client 注册 + redirect_uri 白名单 (现在接受任意 redirect_uri)
- 没 CSRF token 在 login form (现在没保护登录页)
- 没 rate limit / 登录失败计数 (Phase 2 加)
- access_token 是 JWT 不是 opaque (简化设计, 客户端可以解 payload)

**这是 demo / dev server**. 客户生产部署前 Phase 2 加固.

## 测试

```bash
PYTHONPATH=src python3 -m pytest tests/
# 31 passed
```

覆盖:
- jwt_signer: 密钥生成 / 持久化 / 加载 / 签 / JWKS 导出 / 跨实例验签 (5 case)
- users: YAML 加载 / case insensitive / 密码验证 / timing-safe / 边界 (10 case)
- routes: discovery + JWKS + authorize + token + userinfo 完整 flow (16 case)
