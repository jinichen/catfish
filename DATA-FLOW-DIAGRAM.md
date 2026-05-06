# 鲶鱼 Catfish · 数据流向图 (2026-05-06, 5/6 晚校准)

> **客户问题**: "员工敏感数据 (社保 / 合同 / 客户信息) 会不会泄到外网?"
> **答**: 默认 prod 配置下 — **不会**, 全部走客户内网闭环. 鲶鱼平台公司**从不见任何客户数据**.

## 0. 隐私 3 道边界 (精确版, 5/6 晚校准)

> 之前的"数据 0 出境" / "全在客户机器" 是含糊词. 央企信安会问深问细. **3 层边界要分清**:

```
员工 mac → 客户内网 (gateway/LLM) → 客户公司大门 → 鲶鱼平台公司

  边界 1            边界 2              边界 3
  ↑                 ↑                   ↑
  产品设计          部署架构选择          商业模式
```

### 边界 1: 员工敏感数据 0 出员工 mac ✅

**永远只在员工本机, 不离开员工 mac**:
- 文件原件 (`~/.catfish/uploads/`)
- journal (`~/.catfish/employee_journal.md`)
- memory (catfish_remember session_facts / user_profile / style_fingerprint)
- chat 历史 SQLite (`~/.catfish/sessions/`)
- USER.md / ALLOW.md (员工自填)
- 私钥 / Keychain 引用值

公司 IT 想看 → 必须 MDM 远控员工 mac (跟鲶鱼无关, 是公司 mac 政策).

### 边界 2: chat prompt 流过客户内网 ⚠️ 部署设计

**会出员工 mac, 但不出客户公司内网**:
- chat 每次发 → prompt 经 gateway → LLM 推理. gateway 跟 LLM 都在**客户内网**
- gateway / LLM 服务器看得到 prompt 文本 (LLM 推理本质改不了)
- gateway 默认 audit 只入 metadata (model / token / latency / user_sub), 不存 prompt
- 客户 IT 想留 prompt 全文 → 改 gateway verbose log 配置即可
- **公司审 chat 到什么粒度看部署架构** (A/B/C, 见 `DEPLOYMENT-RUNBOOK § 11`):
  - A 员工本机 gateway → 减小集中信任面 (高敏感岗)
  - B 部门 gateway (★ 推荐) → 部门 IT 集中管
  - C 公司 gateway → 全公司 IT 集中管
- **chat 是工作产出, 公司有审计权天然存在**. 鲶鱼是公司装的工作工具, 不挡也不该挡公司管理

### 边界 3: 客户公司大门 0 出境 ✅

**默认 prod 配置 0 字节出客户公司网络**:
- 内网 LLM (Hermes / 客户自部 qwen) 跑客户内网 GPU
- gateway / 数据库 / audit 全在客户内网
- **鲶鱼平台公司从不见任何客户数据** — 软件供应商, 部署完撤场
- 跟 ChatGPT 企业版本质区别 (那个 OpenAI 永远在数据链)

外网 fallback (档位 2/3) 客户**显式**开启时才走外网, chat 顶部 banner 提示 "本次走外网".

### 边界 4: 跨雇主可携带 ✅ (产品灵魂, 5/6 晚最终拍板)

**数据是员工的, 不是公司的**:
- chat 历史 / journal / memory / USER.md / skill 库全在员工本机 `~/.catfish/`
- 员工跳槽 → cp `~/.catfish/` 到新 mac → 鲶鱼继续懂员工, 6 个月积累不丢
- 新公司 LLM 接上, 鲶鱼无缝服务新雇主
- 这是**对抗不良雇主**的设计 — 员工的工作画像 / 技能库不被公司绑架
- 类比公司给员工配 ThinkPad: 笔记本是公司的, 但你装的软件 / 浏览器历史是你的

**默认部署架构 = A (员工本机 gateway)**, B/C 是公司想集中控制时的退化选项. 鲶鱼是给员工配的, 不是给公司装来监控员工的.

---

## 1. 架构总览

```
┌─────────────────────── 员工电脑 (内网) ──────────────────────────┐
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │ Companion    │ →  │ tool-bridge  │ →  │ catfish_*        │  │
│  │ (Tauri)      │    │ (Unix sock)  │    │ (Python tools)   │  │
│  │ - chat UI    │    │ - dispatch   │    │ - read_file      │  │
│  │ - file 上传  │    │ - audit      │    │ - browser        │  │
│  │ - 桌宠       │    │              │    │ - skills         │  │
│  └──────┬───────┘    └──────────────┘    └──────────────────┘  │
│         │                                                        │
│         │ HTTP 127.0.0.1:8000 (5/6 起强制本地)                  │
│         ↓                                                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ catfish-gateway (FastAPI)                                │  │
│  │ - SSO/OIDC RS256 验签                                    │  │
│  │ - prompt_security: 检测密码/token 不入 audit             │  │
│  │ - quota / RBAC (ALLOW.md)                                │  │
│  │ - 双层 audit: gateway (metadata) + tool-bridge (本机)   │  │
│  └──────┬───────────────────────────────────────────────────┘  │
│         │ HTTPS + 客户内网 SSL                                  │
└─────────┼────────────────────────────────────────────────────────┘
          │
          │  ⚠️ 数据出员工电脑 = 这一条线
          ↓
┌─────── 客户企业内网 (闭环) ─────────────────────────────────────┐
│                                                                 │
│  Hermes / 客户自建 LLM (内网部署)                              │
│  - api_base: ${INTERNAL_LLM_BASE_*} (env 注入, 不入版本库)    │
│  - 数据**不出客户内网**                                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

可选 (默认关) ↓
┌─────── 公网 LLM (仅客户显式开 fallback 才用) ─────────────────┐
│  - dashscope (qwen-flash / qwen-vl-max) 阿里云                │
│  - generativelanguage (gemini-flash) Google                    │
│  ⚠️ 数据出境, 客户合规审批后才能开启                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 数据出员工电脑的 4 条路径

### 路径 A: 员工 chat 发给 LLM
- **去向**: gateway → 上游 LLM (内网/公网, 看 config)
- **内容**: prompt + chat history + 上传文件 preview (前 5 页/ 5K 字)
- **过滤**: `prompt_security.py` 检测 ≥40 种 secret 模式, 检测到的密码值不入 audit log
- **persistence**: 不入 gateway db, 只入 metadata audit (`gateway_audit.jsonl`)

### 路径 B: SSO 鉴权
- **去向**: gateway ↔ 客户 IdP (内网, RS256 JWT)
- **内容**: 员工 SSO token (短期, RS256 验签)
- **persistence**: token 仅 in-memory, 不落盘

### 路径 C: skills hub 拉 skill
- **去向**: tool-bridge → skills-hub (客户内网部署, 不接外网)
- **内容**: skill metadata + 文件
- **校验**: 5/6 起 sha256 校验 + `CATFISH_HUB_REQUIRE_HASH=1` 严格模式拒装无签名 skill

### 路径 D: audit log 上报 (可选)
- **去向**: 员工本机 → 客户 SIEM (syslog / fluent-bit)
- **内容**: gateway_audit.jsonl 的 metadata (时间 / 模型 / token 数 / user_sub), 不含 prompt 内容
- **default**: 关, 客户自己接 SIEM 时打开

---

## 3. 数据 NOT 出员工电脑的部分

| 数据 | 位置 | 不出网保证 |
|---|---|---|
| 员工 chat 历史 | `~/.catfish/sessions/*.sqlite` | tool-bridge 不上报 |
| 上传文件原件 | `~/.catfish/uploads/<ts>-<name>` | gateway 只见 preview, 完整文件留本地 |
| tool-bridge audit | `~/.hermes/.catfish_audit.jsonl` | 含 args_preview, 仅本机 |
| 员工身份信息 | `~/.catfish/USER.md` (员工自填) | 员工本机, 不出 |
| local-search 索引 | `~/.catfish/local-search.db` | SQLite FTS, 仅本机 |
| catfish_remember 记忆 | `~/.catfish/memory/*.md` | 仅本机 |
| keychain://xxx 引用值 | macOS Keychain (员工设的) | secret_resolver 解析时本机, 不上报 |

---

## 4. 客户配置档位 (建议)

### 档位 1: 完全内网闭环 (推荐 prod 默认)
```yaml
# config/models.yaml
default_provider: hermes-internal     # 客户内网 LLM
fallback_chain: []                    # 无 fallback, 内网炸了直接给员工报错
upstream_endpoints:
  hermes-internal:
    api_base: ${INTERNAL_LLM_BASE_HERMES}    # 客户 IT 注入
```
**数据出境**: 0 (除路径 B SSO 走客户 IdP, 也是内网)

### 档位 2: 内网主 + 外网 fallback (开发期)
```yaml
default_provider: hermes-internal
fallback_chain: [catfish-public-qwen-flash]   # 外网 fallback
```
**数据出境**: 内网炸时 chat 走 dashscope (阿里云), 客户 chat 顶部 banner 提示 "本次走外网 LLM"

### 档位 3: 仅外网 (不建议 prod)
全部走 dashscope/openai. 内部测试用, 不给员工开。

---

## 5. 客户预期问答

**Q: 员工把社保 PDF 上传, 320 人身份证号会不会发到 LLM?**
A: 默认配置 prod = 全走客户内网 LLM (Hermes), 不出客户网。如果客户开 fallback 到外网 (qwen 公网), 走前 chat 顶部 banner 显式提示员工 "本次会用外网 LLM, 数据将经阿里云"。
   PDF 原件**永远只在员工本机** (`~/.catfish/uploads/`), gateway 只见 preview (~5K 字 / 前 5 页 + meta), 完整内容靠 LLM 调 execute_code 在员工本机读。

**Q: 员工用 chat 问"我们公司当前财务状况", 数据会出去吗?**
A: 整段 prompt 会发上游 LLM (内网/外网看配置). prompt_security.py 检测密码/token 不入 audit, 但 prompt 文字本身一定会发上游 LLM (这是 chat 的本质). 客户应在员工培训里讲清楚: "敏感财务数据建议用内网 LLM, 别用外网 fallback".

**Q: 我作为信安部门, 怎么实时审计?**
A: gateway 每次调用写 `gateway_audit.jsonl`:
```json
{"ts":"2026-05-06T09:30:00Z","user_sub":"emp001","model":"hermes-internal","tokens_in":1234,"tokens_out":567,"latency_ms":890,"security_concern":null}
```
含 metadata, **不含 prompt 内容** (合规). 员工本机 tool-bridge 单独的 jsonl 含 args_preview (本机, 不出). 接 SIEM 配 `CATFISH_AUDIT_PATH=/var/log/catfish/...`.

**Q: 万一员工 prompt 不小心粘了密码, 客户会暴露吗?**
A: prompt_security.py 检测 ≥40 种 secret 模式 (api key / SSH key / JWT / 数据库连接串 / 密码 / token), 检测到时:
- 密码值**不写进 audit log** (只标记 `prompt_credential_detected`)
- 给员工弹 banner: "改用 keychain://xxx 引用"
- prompt 本身**仍会发上游 LLM** (技术上拦不掉, 但客户 audit 看不到)

---

*相关文档*: `SECURITY-REVIEW-2026-05-06.md` (威胁模型 + gap 清单)
