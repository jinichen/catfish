# Plan D · Catfish Federation 协议规范 v0.1

> **版本**: v0.1, 2026-05-03 起草 (五一 sprint Day 3)
> **状态**: 设计阶段, 5/5 单机 mock 验证, 5/6+ 跨机真测
> **决策签名**: 鸿波 4-30 拍板 — 协议 B (MCP JSON-RPC 2.0 over SSE), 隐私 A (ALLOW.md 默认 DENY), Registry A (catfish-identity yaml)

---

## 1. 背景 + 使命

鲶鱼 Phase 3 的核心差异化: **跨员工 agent-to-agent 协作**.

> SaaS 永远做不到 (它们是中心化架构, 改不出来).
> 同质化国内产品没设计这层 (它们是工具不是基础设施).

**Phase 3 ship 时间**: 2026 Q4 (12 个月内, 给客户的承诺).

**Federation 含义**: 多个员工各自的 catfish 实例, 通过协议交换信息 — 但**严格隐私边界** (默认拒绝 + 显式授权).

```
员工 A 的 catfish (Alice)         员工 B 的 catfish (Bob)
┌───────────────────────┐         ┌───────────────────────┐
│ Companion + gateway   │         │ Companion + gateway   │
│ + tool-bridge         │         │ + tool-bridge         │
│ + ALLOW.md (Alice 的) │         │ + ALLOW.md (Bob 的)   │
└──────────┬────────────┘         └──────────┬────────────┘
           │                                 │
           └──────────────┬──────────────────┘
                          ↓
            catfish-identity registry
            (中央, 仅记 user → endpoint 映射 + JWKS)
            **不转中转流量, 不看对话内容**
```

---

## 2. 协议风格 (B: MCP JSON-RPC 2.0 over SSE)

### 2.1 Wire 格式

#### 请求 (A → B)

```http
POST https://bob.catfish.local:9999/a2a/ask
Content-Type: application/json
Accept: text/event-stream
Authorization: Bearer <A 给 B 签的 JWT>

{
  "jsonrpc": "2.0",
  "id": "req-uuid-1",
  "method": "ask",
  "params": {
    "from_sub": "alice@ffcs.cn",
    "from_endpoint": "https://alice.catfish.local:8999",
    "to_sub": "bob@ffcs.cn",
    "question": "项目 X 上周进展怎么样?",
    "context_hint": "alice 要给老板汇报",
    "purpose": "周报",
    "max_tokens": 500
  }
}
```

字段说明:

| 字段 | 必填 | 说明 |
|---|---|---|
| `from_sub` | ✅ | 发起方员工 SSO sub (跟 catfish-identity 一致) |
| `from_endpoint` | ✅ | 发起方 catfish endpoint URL (registry 反查也行, 但提示更稳) |
| `to_sub` | ✅ | 目标方员工 SSO sub. B 端必须验 sub 跟自己 catfish 绑定 |
| `question` | ✅ | A 替员工问 B 的问题 (1 句话, 不超 500 字) |
| `context_hint` | ❌ | A 解释问这个的原因, 帮 B 的 LLM 决定是否答 / 怎么答 |
| `purpose` | ❌ | 用途分类 ("周报"/"汇报"/"咨询" 等), B 的 ALLOW.md 可以按 purpose 限流 |
| `max_tokens` | ❌ | 限制 B 答的长度. 默认 500 |

#### 响应 (B → A, SSE 流)

```
HTTP/1.1 200 OK
Content-Type: text/event-stream

event: message
data: {"jsonrpc":"2.0","id":"req-uuid-1","result":{"chunk":"上周完成"}}

event: message
data: {"jsonrpc":"2.0","id":"req-uuid-1","result":{"chunk":" Phase 1 收尾, 主要进展:"}}

event: message
data: {"jsonrpc":"2.0","id":"req-uuid-1","result":{"chunk":"\n1. SSO 全链路 ship\n2. 跨 session 上下文..."}}

event: done
data: {"jsonrpc":"2.0","id":"req-uuid-1","result":{"final":true,"audit_id":"aud-789","total_chunks":3}}
```

#### 拒绝 (隐私 spec 不允许)

```
event: message
data: {"jsonrpc":"2.0","id":"req-uuid-1","error":{"code":-32001,"message":"target user denied this question type","details":"not in ALLOW.md"}}
```

### 2.2 Error 码

| Code | 含义 | 行为 |
|---|---|---|
| -32600 | 无效 JSON-RPC | A 端弹错误 |
| -32601 | 方法不存在 (只支持 `ask`) | A 端弹错误 |
| -32001 | 隐私拒绝 (ALLOW.md 没匹配) | A 端弹"B 不允许问这个" |
| -32002 | JWT 验证失败 | A 端报 "B 不信任你的身份" |
| -32003 | rate limit (B 已经回答太多 A 了) | A 端等 1 分钟重试 |
| -32004 | B 离线 / 不可达 | A 端弹"B 鲶鱼当前不在线" |
| -32005 | LLM 推理失败 | A 端报"B 自己出错了" |

### 2.3 为什么用 MCP 风格 + SSE

**MCP (JSON-RPC 2.0)**:
- ✅ 跟未来 MCP 生态对接, B 鲶鱼天然是 MCP server, 第三方 MCP client 也能调
- ✅ 错误码标准, request id 标准
- ✅ 文档/SDK 现成

**SSE (Server-Sent Events)**:
- ✅ FastAPI 原生支持 (`EventSourceResponse`)
- ✅ A 鲶鱼能流式显示 B 的回答 (打字机效果, 体感好)
- ✅ 浏览器 / curl 都能调试
- ❌ 单向 (A → B 是 HTTP, B → A 是 SSE), 不像 WebSocket 双向

为啥不用 WebSocket: 双向不必要 (A 问 B 答是单次 RPC), WS 复杂度更高, FastAPI 用 starlette 但调试比 SSE 麻烦.

---

## 3. JWT 互信 (Mutual Auth)

### 3.1 信任根

每个 catfish 实例自己签 JWT. **不依赖中央 catfish-identity 签发**, 它只做 JWKS 公钥分发.

```
Alice 的 catfish-identity:
  - 自签 RSA key pair (~/.catfish/identity/private.pem, public.pem)
  - 暴露 JWKS endpoint: https://alice.catfish.local:8998/.well-known/jwks.json
  - 注册到中央 catfish-identity registry: 
    yaml { sub: alice@ffcs.cn, jwks_uri: ..., catfish_endpoint: ... }

Bob 同上, 各自管自己的 key.
```

### 3.2 A 给 B 签 JWT

```python
# A 端 (gateway 内, A2A client 模块)
import jwt, time

def sign_a2a_token(from_sub: str, to_sub: str, to_endpoint: str) -> str:
    payload = {
        "iss": from_sub,                     # Alice
        "sub": from_sub,                     # 同上
        "aud": to_sub,                       # Bob (audience)
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,       # 5 分钟
        "jti": str(uuid.uuid4()),            # 防重放
        "scope": "a2a.ask",                  # 调用域
    }
    return jwt.encode(payload, A_PRIVATE_KEY, algorithm="RS256")
```

### 3.3 B 验 A 的 JWT

```python
# B 端 (gateway 内, A2A server 模块)
def verify_a2a_token(token: str, expected_aud: str) -> dict:
    # 1. 不验签先 decode 拿 iss (alice@ffcs.cn)
    unverified = jwt.decode(token, options={"verify_signature": False})
    iss = unverified["iss"]
    
    # 2. 通过中央 registry 查 iss 的 jwks_uri
    registry_entry = registry_lookup(iss)
    if not registry_entry:
        raise PermissionError(f"unknown issuer: {iss}")
    jwks = fetch_jwks(registry_entry["jwks_uri"], cache_ttl=300)
    
    # 3. 验签 + 验 aud + 验 exp + 验 jti (防重放, 用 sqlite 保存近 1h jti)
    payload = jwt.decode(
        token,
        jwks,
        algorithms=["RS256"],
        audience=expected_aud,  # 必须是 B 自己 sub
    )
    if jti_seen_recently(payload["jti"]):
        raise PermissionError("token replay detected")
    record_jti(payload["jti"], payload["exp"])
    
    return payload
```

### 3.4 中央 registry 的角色

**只查 → 不转**. registry 只做:
1. `GET /registry/lookup?sub=alice@ffcs.cn` → `{catfish_endpoint, jwks_uri}` 
2. `POST /registry/register` (catfish 启动时自报家门)

**不做**:
- ❌ 中转 A2A 流量 (隐私边界考虑, 中央看不到内容)
- ❌ 替员工签 JWT (信任根在员工本机)
- ❌ 决定 A 能不能问 B (那是 B 的 ALLOW.md 决定)

---

## 4. 隐私 spec — ALLOW.md (默认 DENY)

### 4.1 文件位置

`~/.catfish/ALLOW.md` — 员工自己写, 不上传, 不同步.

### 4.2 格式 (markdown + 关键词匹配, 不用 LLM 判断)

```markdown
# 我允许其他鲶鱼问我什么

## 工作公开内容 (任何同事鲶鱼都可以问)
- 项目 X / Y / Z 进展
- 部门安排 / 周计划
- 公开会议时间
- 27000 / ISO27001 资质审核状态
- 产品 demo / 产品定位

## 工作偏好 (限同部门同事)
allow_to: department=研发部
- 我对什么技术感兴趣
- 我的工作时段 (避免周五下午被打扰)
- 我擅长 / 不擅长哪些技术栈

## 限定特定 purpose (例如周报场景)
allow_purpose: 周报
- 上周做了什么
- 本周计划

## 显式拒绝 (默认 DENY 之外, 这些是高强度拒绝, 错误信息也要严肃)
deny:
- 任何涉及薪资 / 个人财务的问题
- 私人日程 (健身房 / 医院 / 家庭)
- 客户隐私数据
- 商务机密 / 竞品信息
```

### 4.3 匹配逻辑 (关键词 / 正则, 不用 LLM)

```python
# B 端 (gateway 内, A2A server 模块)
def check_allow(question: str, from_sub: str, purpose: str) -> tuple[bool, str]:
    """
    1. 先扫 deny 段, 命中就拒
    2. 再扫 allow 段, 看 from_sub / purpose 限制是否符合 + 关键词命中
    3. 都不命中走默认 DENY
    
    返 (allowed, reason).
    """
    # 1. deny 优先 (高强度拒绝, 不让 allow 段绕过)
    for line in DENY_RULES:
        if any(kw in question for kw in line.keywords):
            return False, f"matched deny rule: {line.text}"
    
    # 2. allow 段
    for section in ALLOW_SECTIONS:
        if section.allow_to and not match_constraint(section.allow_to, from_sub):
            continue
        if section.allow_purpose and section.allow_purpose != purpose:
            continue
        for line in section.rules:
            if any(kw in question for kw in line.keywords):
                return True, f"matched allow: {line.text}"
    
    # 3. 都不命中, 默认拒
    return False, "no allow rule matched (default DENY)"
```

### 4.4 为什么不用 LLM 做 ALLOW 判断

- **稳定性**: LLM 拒绝判断 unstable, A 客户拿到拒绝后无法 reproduce
- **可审**: 关键词匹配 / 正则可以让客户 IT 直接看 `~/.catfish/ALLOW.md` 知道员工允许了什么
- **快**: 关键词匹配 1ms, LLM 判断 200-500ms, A 等不起
- **离线**: 关键词不依赖 LLM 服务, A2A 调用 VPN 断了也能拒绝

LLM 在 Phase 3+ 可能加 — 当员工说"我希望鲶鱼用语义判断, 别局限关键词", 那时再升级. v0.1 用关键词够.

---

## 5. Audit 双方记录

### 5.1 A 端 (~/.catfish/a2a_audit.jsonl)

```jsonl
{"ts":"2026-05-05T10:30:00Z","direction":"outbound","to_sub":"bob@ffcs.cn","question":"项目 X 上周进展","jti":"...","status":"ok","duration_ms":3200,"chunks":3,"audit_id_remote":"aud-789"}
{"ts":"2026-05-05T11:15:00Z","direction":"outbound","to_sub":"charlie@ffcs.cn","question":"我能借用你的合规模板吗","jti":"...","status":"denied","error_code":-32001,"error_msg":"matched deny rule: 商务机密 / 竞品信息"}
```

### 5.2 B 端 (~/.catfish/a2a_audit.jsonl, 同文件不同方向)

```jsonl
{"ts":"2026-05-05T10:30:00Z","direction":"inbound","from_sub":"alice@ffcs.cn","question":"项目 X 上周进展","jti":"...","status":"ok","allow_match":"工作公开内容/项目 X / Y / Z 进展","duration_ms":3100,"audit_id":"aud-789"}
```

### 5.3 中央 registry audit (gateway audit JSONL 现已 ship)

跟现 gateway audit 同 JSONL, 字段加:
```json
{"event_type":"a2a_lookup","from_sub":"alice","to_sub":"bob","ts":"...","ok":true}
```

**不记 question 内容 / answer 内容**. 只记 metadata + jti, 跟现有 audit 隐私边界一致.

---

## 6. Registry 数据格式 (catfish-identity yaml)

`central/identity-server/registry.yaml`:

```yaml
agents:
  alice@ffcs.cn:
    catfish_endpoint: https://alice.catfish.local:8999
    jwks_uri: https://alice.catfish.local:8998/.well-known/jwks.json
    department: 研发部
    last_seen: "2026-05-05T08:00:00Z"
    capabilities: ["a2a.ask", "a2a.skill_share"]    # Phase 3.5 加 skill share
  bob@ffcs.cn:
    catfish_endpoint: https://bob.catfish.local:9999
    jwks_uri: https://bob.catfish.local:9998/.well-known/jwks.json
    department: 研发部
    last_seen: "2026-05-05T08:00:00Z"
    capabilities: ["a2a.ask"]
```

每个员工 catfish 启动时:
1. `POST /registry/register` 自报家门 + JWKS URL
2. registry 验员工身份 (走 SSO, 跟现 catfish-identity 一致)
3. 写入 yaml (Phase 2 升级 PG, BL-D17)

`last_seen` 用于"在线" 判断 — 超过 2 分钟没 register 视为离线, A2A 调用直接 -32004.

---

## 7. 5/5 单机 mock 验证目标

5 天 sprint Day 5 单机模拟 2 员工, 验证:

### 7.1 启动 2 个 catfish 实例

```bash
# 终端 1: Alice
CATFISH_HOME=~/.catfish-alice \
CATFISH_GATEWAY_PORT=8999 \
CATFISH_IDENTITY_PORT=8998 \
catfish-companion --user alice@ffcs.cn

# 终端 2: Bob  
CATFISH_HOME=~/.catfish-bob \
CATFISH_GATEWAY_PORT=9999 \
CATFISH_IDENTITY_PORT=9998 \
catfish-companion --user bob@ffcs.cn
```

### 7.2 Bob 写 ALLOW.md

`~/.catfish-bob/ALLOW.md`:
```markdown
## 工作公开
- 项目 X 进展
- 周报
```

### 7.3 Alice 调 Bob

```python
# Alice 的 chat: "问下 Bob 项目 X 上周进展"
# A 端 invoke 内部 a2a.ask:
result = await a2a_ask(
    from_sub="alice@ffcs.cn",
    to_sub="bob@ffcs.cn",
    question="项目 X 上周进展怎么样?",
    purpose="周报",
)
# Alice 收到 SSE 流, 文字 chunk 显示在 Companion
```

### 7.4 验收点

- ✅ JWT 跨实例验签通 (Alice 签, Bob 用 Alice 的 JWKS 验)
- ✅ ALLOW.md 命中 (项目 X 进展 → allowed)
- ✅ ALLOW.md 不命中 (Alice 问 "Bob 工资多少" → denied)
- ✅ SSE 流式 chunk 在 Alice Companion 实时显示
- ✅ 双方 audit jsonl 都记一行

---

## 8. 跨机真测留 5/6+

5/5 单机 mock 通了 = 协议 + 代码 ship. 跨 2 台真机测试需要:
- 公司网络环境 (Alice / Bob 各一台 macbook, 同 LAN)
- 公司 SSO 真实多账号 (alice@ffcs.cn / bob@ffcs.cn 都能登)
- TLS 证书 (mTLS Phase 2 加, v0.1 走自签 / IP 白名单)

留 BL-X-A2A 一项: 5/6 后跟公司 ASR / Journal 向量召回一起在公司机器上做.

---

## 9. 跟现有架构的关系

| 现有组件 | Plan D 改动 |
|---|---|
| catfish-gateway | 加 `/a2a/ask` SSE endpoint, 加 `a2a_audit.jsonl` 写入 |
| catfish-identity | 加 `/registry/lookup` + `/registry/register`, 加 yaml store |
| Companion | 加 A2A invoke 命令 + UI (Alice 问 Bob 显示在对话) |
| tool-bridge | 不动 (A2A 是 gateway 之间的事, 不走 tool 调用) |
| SOUL.md | 加铁律 "调用 a2a 必须先看 ALLOW.md" |

---

## 10. 未来演进 (Phase 3.5+)

- **A2A skill share**: A 把 skill 分享给 B (Skills Hub MVP 单机版扩展, 跨实例 sync)
- **A2A broadcast**: 部门级广播 (Alice → 整个研发部所有 catfish)
- **A2A 群组**: 多人协作 (Alice + Bob + Charlie 三方对话)
- **LLM-based ALLOW 判断**: 关键词不够细时, 升级 LLM 判断 (员工偏好开关)
- **跨组织 federation**: 鲶鱼 A 公司 + 鲶鱼 B 公司, 通过 OIDC federation 协议互认 (Phase 4)

---

## 11. 决策签名

> v0.1 = 2026-05-03 起草, 2026-05-05 单机 mock 实施验证.
> 决策点 (鸿波 4-30 拍板):
> 1. 协议 B (MCP JSON-RPC over SSE) — 不用 REST 简单版, 跟未来 MCP 生态对接
> 2. ALLOW.md A (默认 DENY 关键词匹配) — 不用 LLM 判断, 不稳定
> 3. Registry A (catfish-identity yaml) — 短期 yaml, Phase 2 升级 PG (BL-D17)
>
> 跨机真测 + LLM-based ALLOW 留 Phase 3 末.
