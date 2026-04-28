"""Catfish Identity — 自建 OIDC server.

# 为啥需要

客户公司没自己 SSO 时, 鲶鱼自带一套. 客户有 SSO 时, 切到他们的, 关掉本服务即可.

# 为啥不用 Keycloak / Authentik

  - Keycloak: Java + DB, 几十万行, 跟 catfish 全栈不一致, 客户审计难
  - Authentik: Python 但用 Django + Celery + Redis, 重
  - 自写: ~300 行, 跟 catfish 一致, 客户 IT 一眼看完, 审计友好

# 范围 (Phase 1B-1)

实现 OIDC Authorization Code flow 核心 5 端点:
  - GET  /.well-known/openid-configuration  (discovery)
  - GET  /.well-known/jwks.json             (公钥)
  - GET  /authorize                          (登录页 + 发 code)
  - POST /token                              (code → ID token)
  - GET  /userinfo                           (token → claims)

不实现 (Phase 2+):
  - PKCE (Phase 1B-2 加, Companion 端用)
  - refresh_token rotation
  - JWE 加密 token
  - 多租户
  - dynamic client registration

# 架构对齐

跟 STRATEGY.md "中央最小" 一致 — 这是个独立小服务, 客户不需要时关掉.
跟 catfish-gateway 同进程模型 (uvicorn fastapi), 跟 Companion 启动管理一致.
"""

__version__ = "0.1.0"
