# 中央端 vs 边缘端数据边界 (强约束)

> **状态**: 强制纪律. 新代码违反 = 不收 PR. CI lint 自动拦.
>
> **作者**: 鸿波
> **生效**: 2026-05-17
> **关联**: `docs/CATFISH-HERMES-BOUNDARY.md` (catfish-hermes 边界) — 这条是 catfish 内部边界, 那条是 catfish-hermes 边界, **互补不重叠**.

## 规则

```
┌─────────────────────────┬────────────────────────────────────────┐
│ 中央端 (catfish-gateway, │ 边缘端 (Companion, edge/tool-bridge,   │
│   central/identity-server,│   edge/local-search, 员工 Mac 本机)   │
│   central/skills-hub,    │                                        │
│   central/web)           │                                        │
├─────────────────────────┼────────────────────────────────────────┤
│ ✓ 可读: quota_events     │ ✓ 可读: 所有 ~/.hermes/* 和             │
│        metrics 元数据    │         ~/.catfish/* 文件              │
│ ✓ 可写: quota_events     │ ✓ 可写: 所有员工本机数据                │
│        metrics 元数据    │                                        │
│ ✗ 不可碰: session 正文,  │                                        │
│          journal, facts, │                                        │
│          memory, 工具结果,│                                        │
│          截图, 录制       │                                        │
└─────────────────────────┴────────────────────────────────────────┘
```

### "审计数据" 严格定义

**只允许 4 类元数据**:

1. `quota_events`: `(ts, user_email, dept, model, tokens_in, tokens_out, request_id)` — **不含 prompt/response 内容**
2. `gateway_audit (metrics)`: `(ts, user_email, dept, model, latency_ms, status, cache_*_tokens)` — **不含 prompt/response 内容**
3. `request_id` 索引: 用来从 Companion 端追同一请求, **不存内容**

> **8/8 删掉了原第 3 条 (`a2a_audit` 的 `question[前 200 字]`)。**
>
> 那条写着「`question` 是元数据例外允许截断」—— 也就是**授权中央端存员工原话**,
> 只是限长 200 字。它是这份文档里唯一一条允许自由文本落中央的口子。
>
> 而 A2A 整套 **5/26 就砍了** (Plan D Federation 停, 0 真客户): 网关的 7 个
> `a2a_*.py` 全改成 38 行 STUB, `app.py` 里三处 caller 删干净, 只剩注释墓碑。
> 也就是说这条例外从 5/26 起就是**纸面上的** —— 代码不存在, 文档还留着授权。
>
> 危险不在它现在生效, 在于**下一个人照着它写代码**: 文档是"强制纪律, 新代码违反
> 不收 PR", 有人要加跨员工功能时会把它当先例, 理直气壮地存 200 字员工原话。
>
> 真要重启 A2A: 元数据就是元数据 (ts / direction / from_sub / to_sub /
> allow_match), question 一个字都不留。要排查内容去边缘端看。

### 中央只转交、不留存的例外: 同事邮筒 (`room_link_mailbox`)

员工**主动**发给某个同事的东西 (请同事帮忙 P50 的请求 / 授权, 一起填表的邀请 / 回答 / 催填 / 结束,
`docs/FORM-COLLECT-PLAN.md`; 派任务给同事的任务单 / 接下·交回·不接 / 催 / 取消, `docs/COLLEAGUE-TASK-PLAN.md`) 经中央邮筒转交。它不算"中央存员工数据", 条件缺一不可:

1. payload 是不透明字符串: 中央不解析、不校验结构、不写日志 (`tests/test_central_log_no_content.py` 静态拦)。
2. 收件人取走的同一事务里 `payload = NULL`; 没人取, 到期也清空 (请求 / 授权 10 分钟; 填表类截止后 7 天, 最长 30 天)。
3. 中央留下的只有元数据 (from / to / kind / 投取时间), 30 天后删。
4. 每一封都是员工自己点了才发, 没有任何自动上传; 汇总、分析全在收件人本机。

新功能要走邮筒, 照这四条; 要在中央**留着**内容 (哪怕几天) 给 web 看, 不行。

同事群聊 (`docs/GROUP-CHAT-PLAN.md`) 也走这个邮筒: 一条群消息给每个成员各投一封, 群记录只在各成员本机。
第 4 条的唯一特例 (11/5 鸿波定): 定时 / 周期任务建任务时发起人勾了「每次结果发到群里」, 之后每次结果由发起人电脑
自动转发到群 —— 发起人建任务时明确同意、可随时关掉、每一封记「数据外发记录」。别的自动往群里发, 不行。
(11/5 实现时补: 收件人接任务之前也看得到「结果会自动发到群里」; 只转发起人这张任务卡上写了自动发群的, 别人冒充的不收。)
群里「🐟 小鲶」总结 / 起草读的是本机群记录, 用员工自己的身份走网关调模型 (跟对话、会议纪要一样), 中央不留内容。
群里一起改文档 (11/11, `docs/DOC-REVISE-MERGE-PLAN.md`) 不加中央接口: 底稿、交回稿、合并版都是普通的群文件 (端到端加密、取完即删)。
交回稿只发给发起人, 合并在发起人本机做; 冲突处「让小鲶给建议」只把那一小段原文和各人改法送给网关的模型, 不送整份文档。

### 中央只转发、不留存的例外: 手机中转站 (11/01 第一期上线, `docs/MOBILE-CLIENT-DESIGN.md`)

员工在手机 (网页版 / App) 上跟**自己电脑上**的小鲶对话, 经中央「中转站」(`relay_router.py` / `relay_hub.py`) 转发。条件缺一不可:

1. 手机 ↔ 员工电脑**端到端加密**, 中转站只见密文, 没有解密能力; 帧内容不解析、不写日志 (同邮筒的静态拦截)。
2. **不落盘、不排队**: 收到即转, 另一端不在线就丢弃并回「不在线」。
3. 中央只留元数据 (谁、哪台设备、连接起止、字节数), 30 天删。
4. 员工电脑上经中转站发出的回复记进「数据外发记录」(目的地「手机, 经中央中转, 已加密」)。

落实处: 帧的 `d` 字段是 NaCl box 密文 (`tests/test_relay.py` 钉「原样转」); 网关跨进程转发用 PostgreSQL NOTIFY
(即时通知, 无人接收即丢, 不入表); 表只有 `mobile_pair` / `relay_connection` 两张元数据表 (alembic 20261101_017);
运行日志由 `test_central_log_no_content.py` 静态拦截; Companion 外发记录只记字节数 (`mobile_bridge/ops.rs`)。

**飞书 / 企微经中转站分发** (公司共用一个机器人, 按发消息的人转到本人电脑): 平台送来的是明文, 但内容本来就经过了
飞书 / 腾讯的服务器, 属员工已交给这些平台的信息, 中转站只转不存, **不算违反边界** (鸿波 2026-11-01 拍板)。
仍须满足上面 2、3 条 (不落盘、不写日志、只留元数据), 且员工本人在 Companion 里打开「允许从飞书 / 企微找我」才转。

### 员工主动发布的例外: skill / 专家包 (`central/skills-hub`)

员工点「发布」把自己做的 skill 或专家包 (10/18, `docs/EXPERT-PACK-PLAN.md`) 推到中央资源市场, 给同事装。
这是员工主动 push 的**工作成果**, 不是员工数据: 打包时扫掉密码 / 密钥、身份证 / 手机号 / 工号 / 卡号, 撞到就拒发;
专家包里只有人设、选路、工作流和技能, 专家的会话、记忆、运行记录不进包。中央存包本身和审核记录 (谁提交、谁审、结论、时间)。

**违规示例 (不允许)**:
- gateway 把 chat messages 写文件
- gateway 把 LLM response 完整写 PG (即使为了 reproduce bug)
- gateway 缓存 system prompt 到中央 (即使为 prompt cache 命中) → **prompt 内容必须 stateless, 每次从 Companion 拼**

## 为啥这条规则

1. **隐私边界硬约束**: 客户买 catfish 的核心理由 = "员工数据不出公司 / 不出员工电脑". 中央端任何持有用户内容 = 法律 + 合规 + 客户信任**全崩**.
2. **SaaS 化前置条件**: 当前 catfish-gateway 物理上跑在员工 Mac 上 (寄生模式), 直接读 `~/.hermes/state.db` 没问题. 未来真 SaaS (gateway 搬客户机房 / 云) 时, 文件不在那台机器上 → 这种代码全 broken. 现在就按 SaaS 架构写, 物理位置只是部署细节.
3. **故障半径**: 中央端 PG / 文件如果存了 chat 内容, 一旦 leak → 全公司员工的 chat 历史全暴露. 边缘端 leak → 只暴露**那台 Mac 上的员工**.
4. **故障定位**: 中央端只持有元数据, gateway 出 bug 不会让员工"丢 chat 历史". chat 历史只在 Companion, gateway 重启 / 升级 / 重装跟员工 chat 历史完全无关.

## 存量违规清单 (2026-05-17 grep · **已于 2026-05-26 全部清零**)

> **10/20 现状**: 下面这 30 个违规已经在 5/25–5/26 全部处理完 (砍死代码 / 改 stub / 搬 edge / 改 header 透传 / 加 `noqa: BOUNDARY`),
> `central/llm-gateway/tests/test_central_edge_boundary.py` 的 `KNOWN_VIOLATIONS_ALLOWLIST` **现在是空集**, 逐条处理记录在那个文件的注释里。
> 这条测试在 CI 的 gateway 任务里跑, 所以现状是「中央端零违规, CI 守住, 新增即红」。
> 下面的表保留作历史记录, 不是待办。

`central/llm-gateway/src/catfish_gateway/` 下 **30 个文件违规** (审计 only 的 quota.py / metrics.py 不算):

### A 类 — Memory inject 链 (chat 时拼 system prompt) · 9 个

| 文件 | 违规 | 该怎么改 |
|---|---|---|
| `inject_session_history.py` | 读 `~/.hermes/state.db` 取最近 7 天 session | Companion 拼好放 request body 里 |
| `employee_journal.py` | 读 `~/.catfish/employee_journal.md` | Companion 拼好 |
| `session_facts.py` | 读 `~/.catfish/session_facts.json` | Companion 拼好 |
| `feedback_inject.py` | 读 `~/.catfish/feedback.jsonl` | Companion 拼好 |
| `memory/providers/employee_journal.py` | 同上 | Companion 拼好 |
| `memory/providers/feedback.py` | 读 feedback.jsonl | Companion 拼好 |
| `memory/providers/hermes_memory.py` | 读 `~/.hermes/memories` | Companion 拼好 |
| `memory/providers/skills_catalog.py` | 读 `~/.catfish/skills/` | Companion 拼好 |
| `identity_inject.py` | 读 `~/.hermes` | Companion 拼好 |

### B 类 — 后台任务 (异步读员工历史 / 写派生数据) · 5 个

| 文件 | 违规 | 该怎么改 |
|---|---|---|
| `session_summarizer.py` | 读 state.db, 写 journal | 整模块搬 Companion |
| `memory_distill.py` | 读 journal, 写 distilled_facts | 整模块搬 Companion |
| `proactive.py` | 读 journal | 整模块搬 Companion |
| `session_meta.py` | 读写 `~/.catfish/session_meta.json` | 整模块搬 Companion |
| `tool_archive/db.py` | 写 `~/.catfish/tool_archives/` | 整模块搬 Companion / 砍 |

### C 类 — UI 直调端点 (catfish-web → gateway) · 4 个

| 文件 | 违规 | 该怎么改 |
|---|---|---|
| `sessions_browse.py` | 读 state.db 列 session | gateway 端点删, catfish-web 直接调 Companion localhost HTTP |
| `tasks_browse.py` | 读 `~/.catfish/tasks.jsonl` | 同上 |
| `recent_outputs.py` | 读 `~/.catfish/output/` | 同上 |
| `skills_loader.py` | 读 `~/.hermes/skills` | 同上 |

### D 类 — A2A federation (跨员工通信) · 5 个 · ~~已废~~ (5/26 整套砍, 7 个模块全 STUB)

| 文件 | 违规 | 该怎么改 |
|---|---|---|
| `a2a_audit.py` | 写 `~/.catfish/a2a_audit.jsonl` | 改成中央 PG (只元数据, 不存 question/answer) |
| `a2a_journal_hook.py` | 写 journal | 搬 Companion |
| `a2a_allow.py` | 读 `~/.catfish/ALLOW.md` | gateway 不读, Companion 调 A2A 前自己 check, gateway 只验 JWT |
| `a2a_jwt.py` | 读 `~/.catfish/identity/private.pem` | 私钥**只该在 Companion**, gateway 只用公钥验签 |
| `a2a_self_register.py` | 读 ~/.catfish/identity | 同上 |

### E 类 — RecMode (CDP 录制截图) · ✅ **5/26 全清 (0 剩, was 3)** · **极高敏感**

| 文件 | 状态 | 备注 |
|---|---|---|
| ~~`recmode/aggregator.py`~~ | ✅ **5/25 已搬** (batch 0) | → `edge/tool-bridge/src/catfish_tool_bridge/recmode/aggregator.py`. central 端 stub. gateway `/api/learn/analyze` thin proxy. |
| ~~`recmode/selector_repair.py`~~ | ✅ **5/25 同批搬** | 同上目标位置, central stub fail-loud. gateway `/api/learn/repair_selector` thin proxy. |
| ~~`recmode/cdp_listener.py`~~ | ✅ **5/26 已搬** (batch 1) | → `edge/tool-bridge/.../recmode/cdp_listener.py`. central stub. gateway `/api/learn/{start_recording, stop_recording, active, status}` 4 个 endpoint thin proxy. tool-bridge `server.py` 加 4 个 JSON-RPC method. 测试搬到 `edge/tool-bridge/tests/recmode/test_cdp_listener.py`. |
| ~~`recmode/cleanup.py`~~ | ✅ **5/26 同批搬** | → `edge/tool-bridge/.../recmode/cleanup.py`. central stub. gateway `/api/learn/cleanup` thin proxy. 顺手加 `recmode/list_with_meta` JSON-RPC method 给 future catfish-web admin 用. |

**E 类全清意义**: 录屏全链路 (录制 / 综合 / 清理 / selector 修复) 100% 在 edge tool-bridge 进程. central 代码 0 行读写 `~/.catfish/recordings/` `~/.catfish/skills/`. PrivacyCard "录屏 100% 本机" 卖点真正兑现.

#### aggregator 迁移技术备注 (5/25)

- gateway 加 `tool_bridge_rpc.py` (Unix socket JSON-RPC client, 16MB readline limit 跟 tool-bridge 同源)
- tool-bridge `server.py` 加 2 个 JSON-RPC method: `recmode/analyze`, `recmode/repair_selector` (不挂 tools/dispatch 避免出现在 LLM tool list)
- gateway 端 `/api/learn/analyze` + `/api/learn/repair_selector` HTTP API 表面不变 (Companion / catfish_browser 老 caller 0 改动), 但 body → JSON-RPC 转发, central 进程不再读写 `~/.catfish/`
- ToolBridgeUnreachable → HTTP 502, ToolBridgeRPCError → 透传 code (INVALID_PARAMS=400/404, INTERNAL=502)
- 404 vs 400 区分: tool-bridge 抛"session_dir...不存在"映回 404 (跟老语义一致)
- SaaS 化时这层 proxy 会 fail-loud (员工 Mac 上的 socket gateway 连不到), 强制下次 Companion 直连

### F 类 — Facts 上传 / 存储 · 2 个 · **8/9 复核, 原处方推翻**

> **11/14: 本类已不存在。** 政策同步整体下线, `facts_router.py` / `facts_pipeline.py` / `facts_db.py` / `facts_schema.py` 全部删除, `CATFISH_FACTS_DIR` 也从两个 docker-compose 和 .env.example 拿掉。下面内容只作历史记录。

> ⚠️ **别跟员工附件上传搞混 —— 是两条完全无关的路。**
>
> | | 这里说的 "Facts 上传" | 员工附件上传 |
> |---|---|---|
> | 谁传 | **管理员** (`upload_fact` 有 `_require_admin`) | 员工 (聊天里拖文件) |
> | 传什么 | 公司/政府发的变更通知/标准/规章 | 员工自己的文件 |
> | 谁落盘 | gateway (`facts_router.py`) | **Companion Rust, 网关零参与** |
> | 落哪儿 | `$CATFISH_FACTS_DIR` (生产=中央容器卷) | `~/.catfish/uploads/<ts>-<name>` |
> | 状态 | 见下表 | ✅ **5/5 起就在边缘端** (`file_parse.rs:263` / `attachments.rs:477`) |

| 文件 | 现状 (8/9 复核) | 该怎么改 |
|---|---|---|
| `facts_router.py` | 落盘位置 5/26 已 env 化 (`CATFISH_FACTS_DIR`), `central/` 与 `delivery/catfish-poc/` 两个 docker-compose 都配到容器卷; `Path.home()` 分支只是 dev fallback + startup warning。**剩余缺口不是落盘位置, 是 PG 写入面无闸** —— `pg_write_facts_json(fact_id, facts)` 写整个 dict, 内含 `raw_quote` (schema 明确要求逐字摘录 50-200 字), 既无结构性约束也无 CI gate, 全靠 `_require_admin` 一道运行时检查 | 给 facts PG 写入面加 CI 闸 (对标 `tests/test_central_log_no_content.py`)。**不搬 Companion** —— 见下 |
| `facts_db.py` | jsonl 兜底落 `~/.catfish/` | 砍 jsonl 兜底 (PG-only, 见 BL-QUOTA-SQLITE-DEPRECATE) |

**为什么 facts 不搬 Companion** (8/9 改; 原处方「facts upload 端点搬 Companion / gateway 只做 proxy 不存」作废):

1. **输入不是员工数据。** `upload_fact` 有 `_require_admin(user)`, 传的是公司/政府发的变更通知 (`EXTRACT_PROMPT_SYSTEM`)。原表格标的「(员工上传文件)」是 5/17 的误标 —— 跟代码从来就对不上。
2. **处理结果本来就该在中央。** extract → find_impact → generate_patch 是给**全公司的 skill** 生成 patch, 搬到某一个员工的 Companion 上讲不通。
3. **「gateway 只做 proxy」这个处方, 本仓库 3 次实践一次都没用。** 员工附件 (5/5) Rust 直接落盘、网关零参与; `/api/tasks/me` (5/17) 直接砍端点; `/api/learn/*` 先做了 thin proxy, 6/20 P3.5.45 连 proxy 一起砍成 Companion → unix sock 直调 (理由:「4 跳 + 2 HTTP 中转 + token 验签 cascade fail」)。三次全选"边缘直做", 零次停在 proxy。**上面 E 类那节写的 thin proxy 是 5/26 的中间态, 已被 6/20 推翻。**

真正该守的是**字段级**, 不是端点级: 判据是「**这个字段的值来自哪里**」, 不是「这次的内容是谁的」—— 同 8/8 `facts_pipeline` 去掉 `raw` 那次 (那个字段接的是 LLM 原始输出, 只写不读, 已删)。

### G 类 — Resolver / metadata · 2 个 (临界, 借边缘库读"映射")

| 文件 | 违规 | 该怎么改 |
|---|---|---|
| `user_model_resolver.py` | 读 state.db sessions.model | Companion 在请求 header 带 `X-Catfish-User-Model` |
| `session_goals.py` | 读 `~/.catfish/session_goal.txt` | Companion 拼好 |

### H 类 — 合规 (audit only) · 2 个

| 文件 | 状态 | 备注 |
|---|---|---|
| `quota.py` | ✓ 合规 (PG) | 但仍有 sqlite 兜底死代码, 见 BL-QUOTA-SQLITE-DEPRECATE |
| `metrics.py` | ⚠️ 半合规 | 默认写 `~/.catfish/gateway_audit.jsonl` — 该挪 PG only |

### 基础设施 (不算违规)

| 文件 | 备注 |
|---|---|
| `inflight_streams.py` | 写 `~/.catfish/inflight_streams/<request_id>.json` — request_id + 状态, 不含 user content. 但物理位置在员工本机, 跟 SaaS 化矛盾 — 该挪 PG / Redis |

## 迁移路线图

### Phase 0 — 锁规则 (今天, 已完成)

1. 本文档 ship → 强制纪律生效
2. `tests/test_central_edge_boundary.py` lint test → CI 自动拦新违规 (新 PR 不许新增 `Path.home()` / `~/.hermes` / `~/.catfish` 调用)
3. 已知 30 个违规进上面 backlog, 每个独立 BL ticket

### Phase 1 — 砍中央端 sqlite/jsonl 兜底 (Day 8, 1 天)

`BL-QUOTA-SQLITE-DEPRECATE`: quota.py / facts_db.py / tool_archive db.py / metrics.py 没 PG → 直接 raise, 不再 fallback. 单测保留 sqlite tmp 文件 (单测隔离, 不算生产路径).

### Phase 2 — Memory inject 链搬 Companion (季度级, 2-3 周)

A 类 9 个文件. 设计 Companion → gateway 请求体协议 (memory pre-injected by Companion), gateway 不再 inject. 这是规则核心: chat 主路径合规.

### Phase 3 — 后台任务搬 Companion (季度级, 2-3 周)

B 类 5 个. summarizer / distill / proactive / session_meta / tool_archive 整体搬 Companion. gateway 不再有 background task fire.

### Phase 4 — UI 直调端点搬 (季度级, 2-3 周) · ~~原名 "UI / RecMode / A2A 搬"~~

原写"C/D/E/F 类 14 个" —— 8/9 复核后**只剩 C 类 4 个**:
D 类 5/26 整套砍 (7 模块全 STUB), E 类 5/26 全清且 6/20 连 proxy 一起砍,
F 类 `facts_router` 经复核**不搬**(输入是管理员上传的公司规章, 不是员工数据; 见 F 类那节)。

C 类 4 个: catfish-web 改成混合调用 (gateway 走 metadata 端点 + Companion localhost 走数据端点)。

### 全部完成后

`central/llm-gateway/` 应该是个**纯无状态 inference router**: 验 JWT → RBAC 拦 → 路由 LLM → 写 audit → 返回. 1000 行内.

## CI / Lint 防新违规

每个 PR 跑 `pytest tests/test_central_edge_boundary.py`:

```python
# 测试逻辑 (伪码):
for py in (Path("central/llm-gateway/src/catfish_gateway").rglob("*.py")):
    if py.name in ALLOWLIST:  # 已知违规, backlog 排期, 暂允许
        continue
    src = py.read_text()
    assert "Path.home()" not in src, f"{py}: 中央端不许读员工本机文件"
    assert "/.hermes/" not in src, f"{py}: 中央端不许读 hermes 边缘数据"
    assert "/.catfish/" not in src, f"{py}: 中央端不许读 catfish 边缘数据"
```

ALLOWLIST 每完成一个 BL ticket 就移除一项. **不许加新条目** (新代码违规 = 不收).

## FAQ

**Q: 我开发期跑 gateway 在自己 Mac, 它就是要读 ~/.hermes/state.db, 这跟规则矛盾?**

A: 不矛盾. 写这条时 (5/17) gateway 还**寄生**在 Companion 的本机, 那是过渡状态; 5/26 起中央端代码已不读员工本机文件, 规则就是现状。 未来 SaaS 化时 gateway 不在员工 Mac, 那时这些代码自动 broken — 现在按规则写就是为了未来不返工.

**Q: 规则啥时候真生效?**

A: 已经生效. Phase 0 lint 立刻拦新代码. 存量 30 个违规曾走 ALLOWLIST 排期, 5/26 已全部清零, ALLOWLIST 现在是空集, **不许新增**.

**Q: 边缘端能不能也按这条规则反过来约束 (Companion 不读 gateway 中央 PG)?**

A: 不需要. Companion 主动调 gateway HTTP 是正常单向数据流, 它本来就需要从中央拉 audit 看自己用量. 单向规则只对中央端单向约束.

**Q: 如果 Companion 跨员工 federation (A2A) 需要中央做 relay, 那 gateway 必然要碰跨员工数据?**

A: **这个问题目前不适用 —— A2A 整套 5/26 已砍** (Plan D Federation 停, 0 真客户;
网关 7 个 `a2a_*.py` 全是 STUB)。保留问答是因为将来若重启会再遇到。

答案本身仍然成立, 但 8/8 收紧了一处: relay 只传密文 (JWT 包裹 question), gateway
当代理转发不解密; 即使转发的是明文, gateway **不存** —— 元数据进 PG, question
内容流过即丢, 跟 HTTP proxy 同性质。**元数据不含 question 的任何片段** (原文这里
写着可存前 200 字, 8/8 删除, 理由见上面"严格定义"那段)。

## Sources

- 本文档触发讨论: 2026-05-17 21:00 鸿波 vs 鲶鱼 (审计页 P0 修完后, 鸿波拍板"中央端坚决不能碰用户侧任何数据")
- 配套 lint: `tests/test_central_edge_boundary.py`
- 配套 sprint: BL-QUOTA-SQLITE-DEPRECATE (Phase 1 first half)
- 配套 boundary: `docs/CATFISH-HERMES-BOUNDARY.md` (catfish vs hermes — 互补)
