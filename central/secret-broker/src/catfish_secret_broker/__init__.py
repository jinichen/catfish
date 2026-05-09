"""catfish-secret-broker — token / 凭据集中存储 (BL-G6, 5/9 ship Phase 2 dev MVP).

设计:
    POST   /v1/secret           写 (key=ref, value=token)
    GET    /v1/secret/{ref}     读 (返 token)
    DELETE /v1/secret/{ref}     删
    GET    /v1/secret/{ref}/exists  存在性 (不返值, 用于 mcp-registry 检查)
    GET    /health

后端:
    dev:  Python keyring (mac → Keychain, Win → wincred, Linux → libsecret)
    prod: KMS (BL-G6 5/22+ 加, 接公司 KMS / aws-kms / azure)
    内存兜底: keyring 装/启失败时, in-memory dict (只本进程, 重启丢, 但能跑)

Phase 2 dev MVP 范围:
- ✅ 三 endpoint 跑通
- ✅ keyring 后端 / 内存兜底
- ✅ JWT 鉴权 (跟 catfish-gateway 同一 issuer, 信任 X-Catfish-User-Sub)
- ❌ KMS 加密 (留 prod, 后端可换)
- ❌ ACL (留 prod, 谁能读谁的 token)

跟 mcp-registry 集成:
    OAuth callback 收 token → POST /v1/secret { ref: "jira-{user}", value: token }
    Phase 3 拉 mcp pod 时 GET /v1/secret/jira-{user} → 注入 pod 环境变量
"""

__version__ = "0.1.0"
