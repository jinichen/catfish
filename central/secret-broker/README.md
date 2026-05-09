# catfish-secret-broker · 凭据集中存储

> **状态**: 🔵 5/9 Phase 2 dev MVP ship (FastAPI :8995, keyring 后端)
> **路线图**: prod 切 KMS / Vault (BL-G6 完整版, 5/22+)

---

## 服务

```
catfish-secret-broker FastAPI :8995
  ├─ GET    /health                  健康 + backend (keyring/memory)
  ├─ POST   /v1/secret                写 (ref + value, 已存在覆盖)
  ├─ GET    /v1/secret/{ref}          读返 value
  ├─ GET    /v1/secret/{ref}/exists   存在性 (不返值)
  └─ DELETE /v1/secret/{ref}          删 (idempotent)
```

后端:
- **dev**: Python keyring (mac → Keychain, Win → wincred, Linux → libsecret)
- **prod (5/22+)**: KMS / Vault (BL-G6 完整版)
- **fallback**: keyring 不可用 → 内存 dict (单进程, 重启丢)

鉴权:
- Phase 2 dev: 信任 `X-Catfish-User-Sub` header (gateway 已验 JWT 注入)
- Prod (BL-G6 后续): mTLS / service token

## 启 dev

```bash
cd central/secret-broker
python3.12 -m venv venv && source venv/bin/activate
pip install -e .

# 启服务 (默认 :8995)
python -m catfish_secret_broker.app

# 验证
curl http://127.0.0.1:8995/health
curl -X POST http://127.0.0.1:8995/v1/secret \
     -H "X-Catfish-User-Sub: alice@catfish.dev" \
     -H "Content-Type: application/json" \
     -d '{"ref":"jira-alice","value":"my-token"}'
curl -H "X-Catfish-User-Sub: alice@catfish.dev" \
     http://127.0.0.1:8995/v1/secret/jira-alice
```

测试:

```bash
pip install -e '.[dev]'
python -m pytest tests/ -v
```

预期 16 单测全过 (storage 8 + api 8).

## 跟其他模块关系

| 模块 | 关系 |
|---|---|
| `central/mcp-registry` | **强依赖** Phase 2 OAuth callback 后写 token, Phase 3 拉 mcp pod 时读 token 注入环境变量 |
| `central/llm-gateway` | 反代 (Phase 2 后续加, 现 Companion 直连 8995 dev OK) |
| `edge/tool-bridge/secret_resolver` | 长期可统一 (现 wincred:// / keychain:// 直读, 未来切 secret-broker:// ref 走中央服务) |

## 端口约定 (5/9)

```
8998 catfish-identity
8999 catfish-gateway
8997 catfish-skills-hub
8996 catfish-mcp-registry
8995 catfish-secret-broker (本服务)
```

## 决策

- **2026-05-09 dev MVP**: keyring 后端, 内存兜底, 信任 gateway 注入 header. 给 mcp-registry Phase 2 OAuth flow 用. KMS / Vault 留 5/22+ BL-G6 完整版.
