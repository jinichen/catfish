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
3. `a2a_audit` 跨员工通信元数据: `(ts, direction, from_sub, to_sub, allow_match, question[前 200 字])` — `question` 是元数据例外允许截断, 不允许存 answer
4. `request_id` 索引: 用来从 Companion 端追同一请求, **不存内容**

**违规示例 (不允许)**:
- gateway 把 chat messages 写文件
- gateway 把 LLM response 完整写 PG (即使为了 reproduce bug)
- gateway 缓存 system prompt 到中央 (即使为 prompt cache 命中) → **prompt 内容必须 stateless, 每次从 Companion 拼**

## 为啥这条规则

1. **隐私边界硬约束**: 客户买 catfish 的核心理由 = "员工数据不出公司 / 不出员工电脑". 中央端任何持有用户内容 = 法律 + 合规 + 客户信任**全崩**.
2. **SaaS 化前置条件**: 当前 catfish-gateway 物理上跑在员工 Mac 上 (寄生模式), 直接读 `~/.hermes/state.db` 没问题. 未来真 SaaS (gateway 搬客户机房 / 云) 时, 文件不在那台机器上 → 这种代码全 broken. 现在就按 SaaS 架构写, 物理位置只是部署细节.
3. **故障半径**: 中央端 PG / 文件如果存了 chat 内容, 一旦 leak → 全公司员工的 chat 历史全暴露. 边缘端 leak → 只暴露**那台 Mac 上的员工**.
4. **故障定位**: 中央端只持有元数据, gateway 出 bug 不会让员工"丢 chat 历史". chat 历史只在 Companion, gateway 重启 / 升级 / 重装跟员工 chat 历史完全无关.

## 当前违规清单 (2026-05-17 grep)

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

### D 类 — A2A federation (跨员工通信) · 5 个

| 文件 | 违规 | 该怎么改 |
|---|---|---|
| `a2a_audit.py` | 写 `~/.catfish/a2a_audit.jsonl` | 改成中央 PG (只元数据, 不存 question/answer) |
| `a2a_journal_hook.py` | 写 journal | 搬 Companion |
| `a2a_allow.py` | 读 `~/.catfish/ALLOW.md` | gateway 不读, Companion 调 A2A 前自己 check, gateway 只验 JWT |
| `a2a_jwt.py` | 读 `~/.catfish/identity/private.pem` | 私钥**只该在 Companion**, gateway 只用公钥验签 |
| `a2a_self_register.py` | 读 ~/.catfish/identity | 同上 |

### E 类 — RecMode (CDP 录制截图) · 3 个 · **极高敏感**

| 文件 | 违规 | 该怎么改 |
|---|---|---|
| `recmode/aggregator.py` | 写 `~/.catfish/skills/` | 整模块搬 Companion (截图含业务数据, 绝不能进中央) |
| `recmode/cdp_listener.py` | 写 `~/.catfish/recordings/` | 同上 |
| `recmode/cleanup.py` | 清 recordings | 同上 |

### F 类 — Facts 上传 / 存储 · 2 个

| 文件 | 违规 | 该怎么改 |
|---|---|---|
| `facts_router.py` | 写 `~/.catfish/facts/<id>/raw.ext` (员工上传文件) | facts upload 端点搬 Companion / gateway 只做 proxy 不存 |
| `facts_db.py` | jsonl 兜底落 `~/.catfish/` | 砍 jsonl 兜底 (PG-only, 见 BL-QUOTA-SQLITE-DEPRECATE) |

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

### Phase 4 — UI / RecMode / A2A 搬 (季度级, 2-3 周)

C/D/E/F 类 14 个. catfish-web 改成混合调用 (gateway 走 metadata 端点 + Companion localhost 走数据端点).

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

A: 不矛盾. 当前 gateway**寄生** Companion 的本机是过渡状态. 规则是**目标架构**, 不是即刻 enforce. 未来 SaaS 化时 gateway 不在员工 Mac, 那时这些代码自动 broken — 现在按规则写就是为了未来不返工.

**Q: 规则啥时候真生效?**

A: 已经生效. Phase 0 lint 立刻拦新代码. 存量 30 个违规走 ALLOWLIST 排期, 每个独立 ticket, **不许新增**.

**Q: 边缘端能不能也按这条规则反过来约束 (Companion 不读 gateway 中央 PG)?**

A: 不需要. Companion 主动调 gateway HTTP 是正常单向数据流, 它本来就需要从中央拉 audit 看自己用量. 单向规则只对中央端单向约束.

**Q: 如果 Companion 跨员工 federation (A2A) 需要中央做 relay, 那 gateway 必然要碰跨员工数据?**

A: A2A relay 只传**密文** (JWT 包裹 question), gateway 当代理转发, 不解密内容. 即使转发的 question 是明文, gateway**不存** — 元数据进 PG (`a2a_audit`), question 内容流过即丢. 跟 HTTP proxy 同性质.

## Sources

- 本文档触发讨论: 2026-05-17 21:00 鸿波 vs 鲶鱼 (审计页 P0 修完后, 鸿波拍板"中央端坚决不能碰用户侧任何数据")
- 配套 lint: `tests/test_central_edge_boundary.py`
- 配套 sprint: BL-QUOTA-SQLITE-DEPRECATE (Phase 1 first half)
- 配套 boundary: `docs/CATFISH-HERMES-BOUNDARY.md` (catfish vs hermes — 互补)
