# Catfish Memory Ownership Architecture (5/18 拍板版)

> **状态**: ✅ **Phase 1+2 完整 ship (5/19 凌晨)**. Phase 3+4 留 backlog
> (Phase 3 cutover 看 Phase 2-2B chat.ts 切换实盘验证, Phase 4 中央部署独立排期).
>
> **触发原因**: 5/18 深夜鸿波点穿 "memory 都交给 hermes, gateway 为什么还要去干预".
> 现状 gateway `memory_registry` 10 provider 直读 `~/.hermes/` 是历史包袱
> (5/4-5/13 一路补的补丁), 不是设计意图.

## ✅ 完成进度 (5/19 凌晨 ship)

- ✅ **Phase 1** 设计 + safety net + plugin POC (#17-#19)
  - 本文档 (架构决策 + 10 provider 分流 + 路径图)
  - `gateway memory_registry` env flag (CATFISH_GATEWAY_DISABLE_MEMORY)
  - `edge/hermes-plugins/catfish-memory/` plugin (400+ 行 + 22 单测 + 实盘
    `hermes memory status` 显 active ✓)
- ✅ **Phase 2-1** 安装 + 调研 (#20-#21)
  - `edge/hermes-plugins/install-catfish-memory.sh` 5 步幂等
  - `docs/HERMES-OPENAI-SERVER-RESEARCH.md` Companion 切换 spec
- ✅ **Phase 2-2A** 配置 + 认证 design (#22-#23)
  - `services/hermes_api_config.rs` + tauri.ts wrapper
  - `docs/COMPANION-HERMES-AUTH-DESIGN.md` API_SERVER_KEY 同步方案
- ✅ **Phase 2-2B** chat.ts 切 endpoint (#24)
  - `lib/chat.ts` 加 hermes 路径 (hermes_api.enabled=true → 调 8642, 否则 fallback gateway)
  - hermes auth header 走 Rust Tauri 命令拼好 (key 不暴露 JS)
  - 不传 tools (hermes 内部管 tool calling)
  - 灰度: enabled=false 仍走老 gateway
- ✅ **Phase 2-3** gateway memory_registry deprecated (#25)
  - `memory/bootstrap.py` 清空 (不再 register 任何 provider)
  - provider 模块保留作 reference (跟 sessions_browse.py 同模式)
  - 老 integration 测试 module-level skip
- ✅ **Phase 2-4** lint 防回归 (#26)
  - `scripts/check-gateway-no-edge-fs.sh` warning 模式
  - 发现 33 处 gateway 读 ~/.hermes/~/.catfish (memory 之外 quota/facts/
    tool_archive/proactive/etc), 这些是 Phase 4 中央部署前 backlog
- ✅ **配套** Phase 2 catfish-edge 安装 (#27)
  - `scripts/setup-catfish-edge.sh` 一键装机 (key 随机生成 + 同步两边 +
    plugin 软链 + hermes restart)
  - `--rotate` 季度 key 轮换模式

## ⬜ 待做 (Phase 3+4, 留 backlog)

- ⬜ Phase 3 cutover 实盘验证 (Phase 2-2B 真机 e2e + 灰度全员)
- ⬜ Phase 4 中央部署 (gateway 真跑公司机房, 解决剩 33 处 gateway 读 edge FS)
- ⬜ 全删 `memory/providers/` 文件 (cutover 稳定后再 hard delete)

## 1. 现状审计 — gateway 当前的 10 个 memory provider

| # | Provider | priority | budget | 真实数据源 | 该归谁 |
|---|---|---|---|---|---|
| 1 | `HermesUserMemoryProvider` | 10 | 1800B | `~/.hermes/memories/USER.md` | **hermes 自带, 删** |
| 2 | `SessionFactsProvider` | 20 | 6000B | **已 deprecated, 默认 return None** | **直接删** |
| 3 | `SessionMetaProvider` | 30 | 500B | `~/.catfish/session_meta.json` (catfish 自己写) | **hermes plugin** |
| 4 | `SkillsCatalogProvider` | 40 | 20000B | `~/.catfish/skills/` + 公司 hub | **hermes plugin** |
| 5 | `StatsGuardProvider` | 45 | 1500B | catfish gateway 内 stats (20% 触发) | **删 or LLM tool** |
| 6 | `SessionHistoryProvider` | 50 | 3000B | `~/.hermes/state.db` (FTS5 召回) | **hermes 自带, 删** |
| 7 | `SkillGuardProvider` | 55 | 3000B | 静态 prompt (条件触发) | **hermes plugin** |
| 8 | `EmployeeJournalProvider` | 60 | 5000B | `~/.catfish/employee_journal.md` + `~/.catfish/distilled_facts.md` | **hermes plugin** |
| 9 | `HermesMemoryProvider` | 65 | 2600B | `~/.hermes/memories/MEMORY.md` | **hermes 自带, 删** |
| 10 | `FeedbackProvider` | 70 | 2000B | `~/.catfish/feedback.jsonl` | **hermes plugin** |

**分类汇总**:
- **3 个本来就是 hermes 的, 完全可删** (#1 #6 #9): hermes 0.13+ 自己有这能力, gateway 是重复造轮子
- **1 个已 deprecated 留壳, 直接清** (#2): SessionFacts catfish_remember 已黑名单, provider 默认 return None, 删
- **5 个 catfish 自己加的扩展, 改 hermes plugin** (#3 #4 #7 #8 #10): 通过 hermes plugin manifest 接入, 让 hermes 在自己 inject 阶段拉
- **1 个语义模糊, 重新考虑** (#5 StatsGuard): 是 gateway 内部 stats 触发的, 真要的话改 LLM tool (catfish_get_stats), LLM 主动调

## 2. 目标架构 (重构后)

### 2.1 责任划分

```
┌─────────────────────────────────────────────────────────────┐
│  纯 UI 客户端 (Companion / 微信 ClawBot / catfish-web)       │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │
│  - 用户交互                                                  │
│  - 不管 memory, 不管 prompt 拼装                             │
│  - 只调 hermes serve OpenAI 兼容 API                         │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTPS OpenAI 兼容
                       │ (本机 localhost:11434 等)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  hermes serve (员工本机, agent runtime, 唯一 memory 责任人)   │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │
│  内置 memory 能力:                                            │
│   - session_history (state.db FTS5 召回)                      │
│   - hermes_memory (memories/MEMORY.md)                        │
│   - user_memory (memories/USER.md)                            │
│   - session_facts                                             │
│                                                              │
│  catfish 通过 hermes plugin 加的扩展 memory provider:         │
│   - catfish-employee-journal-plugin                          │
│       读 ~/.catfish/employee_journal.md + distilled_facts.md │
│   - catfish-skills-catalog-plugin                            │
│       读 ~/.catfish/skills/ + 公司 Hub                       │
│   - catfish-feedback-plugin                                  │
│       读 ~/.catfish/feedback.jsonl                           │
│   - catfish-session-meta-plugin                              │
│       读 ~/.catfish/session_meta.json (时间感)               │
│   - catfish-skill-guard-plugin                               │
│       静态 prompt 条件触发 (员工提 skill 时注铁律)            │
│                                                              │
│  agent loop:                                                 │
│   - inject memory (内置 + plugin)                            │
│   - tool calling (native + tool-bridge 提供的 catfish_*)     │
│   - 调上游 LLM (通过 gateway)                                │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTPS Bearer service_token
                       │ (gateway 看的是已拼好的 system prompt)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  catfish-gateway (公司机房中央, 纯 LLM 代理)                  │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │
│  唯一职责:                                                    │
│   - JWT 验签 (auth/oidc.py 不变)                              │
│   - RBAC + quota check                                       │
│   - audit (元数据, 永不存正文)                                │
│   - tools_sanitizer (schema 修复 + 黑名单 + cap 50)           │
│   - LiteLLM 转发上游 LLM                                      │
│                                                              │
│  不该做:                                                      │
│   ❌ 不读 ~/.hermes/                                          │
│   ❌ 不读 ~/.catfish/                                         │
│   ❌ 不修改 messages 内容 (sanitize tools 不算修改 content)   │
│   ❌ 不拼 system prompt                                       │
│   ❌ 不知道员工"个人偏好" "项目事实"                          │
│                                                              │
│  保留的 helper 中间件 (跟 memory 无关):                       │
│   ✓ model_handoff (跨 model 切换兼容)                         │
│   ✓ tool_retry_hint (hard cap + soft hint, 看 messages 模式) │
│   ✓ self_critique (幻觉完成 hint)                            │
│   ✓ tools_sanitizer (schema 修)                              │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTPS
                       ▼
                  上游 LLM (Qwen / Gemini / 等)
```

### 2.2 数据流 (一次员工 chat)

```
1. 员工在 Companion 输入 "找张三那封工资邮件"

2. Companion useChat → fetch hermes serve /v1/chat/completions
   body: {messages: [user "找张三..."], model: "catfish-private-main"}
   Authorization: Bearer <id_token>  (hermes 也接受 id_token, 跟员工同身份)

3. hermes serve 收到:
   ├─ memory inject:
   │   ├─ 内置: session_history (state.db FTS5) + hermes_memory + user_memory
   │   ├─ catfish-employee-journal-plugin: 注入 ~/.catfish/employee_journal.md
   │   ├─ catfish-skills-catalog-plugin: 注入 catfish skills 列表
   │   ├─ catfish-feedback-plugin: 注入员工 thumbs 反馈
   │   └─ catfish-session-meta-plugin: 注入时间感
   ├─ 拼好完整 system prompt + messages
   └─ 调 catfish-gateway/v1/chat/completions
       body: {messages: [system拼好, user "找张三..."], tools: [...], model}
       Authorization: Bearer <service_token>  (hermes 用 service token)
       X-Catfish-On-Behalf-Of: chenhongbo@ffcs.cn  (hermes 告诉 gateway 真员工)

4. catfish-gateway 收到:
   ├─ JWT 验签 → 拿到 service:hermes-cli, on-behalf-of=chenhongbo
   ├─ RBAC: 看 X-Catfish-On-Behalf-Of 用员工身份 quota / audit
   ├─ tools_sanitizer (修 schema, 不动 content)
   ├─ tool_retry_hint.should_hard_cap (看 messages 重复模式)
   ├─ self_critique 等 hint 注入 (这些不是 memory, 是协议)
   └─ litellm.acompletion → 上游 Qwen → SSE 流回

5. SSE → hermes serve → 解析 tool_call → 调 catfish-tool-bridge
6. 流回 Companion → 显示给员工
```

**关键变化**:
- Companion **不再直调 gateway**, 走 hermes serve
- gateway **不再读员工本机文件**, 看到的是 hermes 拼好的 messages
- 谁是员工 (RBAC / audit) 通过 `X-Catfish-On-Behalf-Of` header 传给 gateway (类似 GitHub Actions / GitHub App on-behalf-of 模式)

### 2.3 部署形态

| 组件 | 物理位置 | 数量 | 部署方式 |
|---|---|---|---|
| **Companion** | 员工 Mac | 每员工一份 | 用户安装 .app |
| **hermes serve** | 员工 Mac | 每员工一份 | catfish 安装时启 launchd |
| **catfish-tool-bridge** | 员工 Mac (hermes plugin) | 每员工一份 | hermes plugin 安装 |
| **catfish memory plugins** | 员工 Mac (hermes plugin) | 每员工一份 | hermes plugin 安装 |
| **catfish-edge 数据目录** | 员工 Mac `~/.catfish/` | 每员工一份 | 安装时建 |
| **hermes 数据目录** | 员工 Mac `~/.hermes/` | 每员工一份 | hermes 自带 |
| **catfish-gateway** | **公司机房** | 1 个 (集群可) | docker / k8s |
| **catfish-identity-server** | **公司机房** | 1 个 | docker |
| **catfish-admin** | **公司机房** | 1 个 (manager 用) | docker |
| **catfish PG** | **公司机房** | 1 个 | docker |
| **catfish skills-hub** | **公司机房** | 1 个 | docker |

**红线** (BL-CENTRAL-EDGE-BOUNDARY 严守):
- 员工 Mac 文件 (`~/.hermes/` `~/.catfish/`) 永不上行公司机房 PG
- 公司机房 PG 只存元数据 (token 计数 / 时间 / 模型 / 用户 / 部门)
- LLM 调用 prompt 含 memory **会过公司机房 gateway**, 但 gateway in-process 拼装 + 转发 + 不存

## 3. 重构 sprint 拆解

### Phase 1 — 设计 + 准备 (1 周, 鸿波 + 1 工程师)

**BL-MEMORY-ARCH-DOC**: 本文档 ✓ (今天就是)

**BL-HERMES-PLUGIN-INTERFACE-SPEC**: 调研 hermes plugin 接口
- 看 hermes 0.14 是否有 `register_memory_provider` / `inject_provider` 抽象
- 没有的话: 走 hermes plugin hook (transform_messages?) 在 system prompt 拼装阶段插
- 写 `docs/HERMES-PLUGIN-MEMORY-PROVIDER.md` 接口规范

**BL-CATFISH-EDGE-SERVE**: 评估"Companion 调 hermes serve 还是另起 catfish-edge-server"
- 选 A: Companion 直调 hermes serve (港) — 简单, 但耦合 hermes 升级
- 选 B: 起 catfish-edge-server 在员工本机 11436 端口, 它转发 hermes serve + 加 catfish 中间件
- 倾向 A, 但留 B 余地

### Phase 2 — 实现 (2 周)

**BL-CATFISH-MEMORY-PLUGIN-MNEMOSYNE** (重要修正 5/19 凌晨):
- 原拆"5 个独立 plugin (employee-journal/skills-catalog/feedback/session-meta/skill-guard)" **错了**
- hermes MemoryManager 有 **one-external-provider limit** — 只能装一个外部 provider
- 改成: 1 个 catfish 总 provider (e.g. `catfish-mnemosyne` 或 `catfish-memory`), 在它内部
  prefetch + system_prompt_block 里聚合所有 5 个 catfish 数据源
- 估时不变 ~1 周, 工程量反而小 (一个 plugin 装一次)

**BL-COMPANION-SWITCH-TO-HERMES-SERVE**: Companion `chat.ts` / `useAuth` / `fetchWithAuth` 改成调 hermes serve, 不再调 gateway. 401 reauth 路径调整 (1 周)

**BL-GATEWAY-MEMORY-CODE-DELETE**:
- 删 `memory/providers/` 下 3 个 hermes 自带的 provider (#1 #6 #9)
- 删 #2 (deprecated SessionFacts)
- 标 #5 StatsGuard 为 deprecated
- 删 #3 #4 #7 #8 #10 — **前提是 hermes plugin 已 ship 并验证**
- `bootstrap.py` 清空, 留 `register_memory_provider` 接口给将来万一要拼非 memory 的中间件用
- 整个 `memory_registry` 模块标 deprecated, 但保留代码作 reference (3-5 天)

**BL-GATEWAY-NO-EDGE-FS**: gateway 代码 lint 加规则 `禁止 import Path.home() / ~/.hermes/ / ~/.catfish/`. CI 强制. 已知例外 (e.g. dev_token 兜底) 加 noqa 标记 (1 天)

### Phase 3 — 切换 + 清理 (1 周)

**BL-COMPANION-CUTOVER**: 实盘验证 Companion 走 hermes serve 完整体验. 灰度 (env 开关), 全量上线

**BL-GATEWAY-CUTOVER**: gateway 关 memory inject, 实盘看 hermes plugin 是否覆盖所有原 provider 行为. 漏的补

**BL-DEPLOY-DOC-UPDATE**: 写 `docs/DEPLOYMENT-GUIDE.md`:
- 公司机房装 gateway + identity + admin + PG
- 每员工 Mac 装 hermes + Companion + catfish-tool-bridge + 5 个 catfish memory plugin
- 不再有"gateway 跟 ~/.hermes 同机" 的隐含依赖

### Phase 4 — 中央化部署 (1 周, 可选)

**BL-PROD-DEPLOY-CENTRAL-GATEWAY**: 把 gateway 真部署到公司机房一台机器, 一个员工 Mac 实盘连过去用. 验证完整链路

## 4. 关键决策记录

### 4.1 为什么 Companion 不自己 inject memory 直接调 gateway?

**短答**: 重复造轮子. hermes 是 agent runtime, memory + agent loop 是它的核心能力. Companion 自己实现等于又重造一遍, 跟 hermes 双轨.

**长答**:
- Companion 跟 hermes 是不同抽象层 — Companion 是 UI 客户端, hermes 是 agent runtime
- 让 Companion 拼 prompt 等于"UI 客户端管 agent 逻辑", 抽象漏
- 未来微信 / catfish-web / 其它 UI 都走 hermes serve 一个统一入口, memory + agent 行为一致

### 4.2 为什么 catfish 扩展 memory 是 hermes plugin 不是独立服务?

- hermes 已有 plugin 体系 (.hermes/plugins/), 标准化, 安装/升级走 hermes plugin 命令
- 独立服务 (e.g. catfish-edge-sidecar 提供 memory) 引入 IPC 复杂度, 没必要
- catfish memory plugin 跟 hermes 同进程 import, 性能好

### 4.3 为什么 gateway 还保留 tool_retry_hint / self_critique / model_handoff?

- 这些**不是 memory**, 是 LLM 调用层中间件
- 看 messages 模式 (5 次同 tool err → hard cap) 不需要读员工本机文件
- 是 gateway 这一层合适的责任 (跨员工通用)
- BL-CENTRAL-EDGE-BOUNDARY 红线是"不碰用户数据", tool_retry_hint 看的是 messages 的元数据模式, 不持久化

### 4.4 为什么 service_token + X-Catfish-On-Behalf-Of, 不直接传员工 id_token?

- hermes daemon 没员工浏览器 session, 没法走 OAuth authorization_code 拿 id_token
- service_token 长期有效 (30 天), hermes 跑批不依赖员工登录
- 但 gateway 要知道员工身份做 RBAC / quota — `X-Catfish-On-Behalf-Of: chenhongbo@ffcs.cn` 显式标
- gateway 校验: service_token 必须 audience=catfish-gateway, on-behalf-of email 必须存在 users.yaml. 防 hermes-cli 假冒别人

### 4.5 为什么 5/14 决定让 hermes 走 client_credentials 不直接复用 BL-MEMORY-OWNERSHIP-FIX 顺手做?

- 5/14 时这个分工错没意识到 (本文档 5/18 才写)
- 5/14 加 client_credentials 是先解 hermes "每小时过期" 的紧急问题, 当时假设 hermes 还是配套 (Companion → gateway, hermes → gateway 平级)
- 5/18 才意识到正确架构是 Companion → hermes → gateway, 3 层

### 4.6 已知 risk / open question

1. **hermes plugin 接口稳定性**: hermes 是 NousResearch fork, plugin API 升级我们要跟. 风险 medium
2. **Companion ↔ hermes serve 401 reauth**: hermes serve 是否 OpenAI 兼容 OIDC? 还是要 catfish-edge-server 转一道? 等 BL-CATFISH-EDGE-SERVE 决策
3. **catfish-edge 安装包**: hermes + Companion + 5 个 catfish memory plugin + tool-bridge 一个 .pkg 安装, 维护成本上升. 需要 IT 流程

## 5. 时间线 (粗估)

| 阶段 | 时长 | 触发 |
|---|---|---|
| Phase 1 设计准备 | 1 周 | 立即开始 (5/19) |
| Phase 2 实现 | 2 周 | Phase 1 完成 |
| Phase 3 切换清理 | 1 周 | Phase 2 ship 验证 |
| Phase 4 中央化部署 | 1 周 | 第二台 Mac 上线前 |
| **合计** | **4-5 周** | **完成时间 ~6/20** |

**绝对截止**: 第二台 Mac 上线前必须完成 Phase 1-3. Phase 4 可缓.

## 6. 这份文档之后的下一步

1. 鸿波 review + sign-off 这份文档
2. 建子 ticket (BL-MEMORY-ARCH-DOC ✓ / BL-HERMES-PLUGIN-INTERFACE-SPEC / 5 个 plugin / Companion switch / gateway delete / deploy doc)
3. 排到 5/19+ sprint backlog 第一位
4. 不在分工没定的情况下继续往 gateway memory_registry 加新 provider — 加进 hermes plugin 体系

---

**最后**: 这份文档是 5/18 深夜在 22+ 小时 sprint 末尾鸿波点穿后写的. 之前 AI 助手 (我) 两次回答 memory 架构都没看穿这个分工错误, 是真 bug. 写这份文档是为了**承认 + 锁住正确方向**, 别明天又乱.
