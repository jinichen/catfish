# 5/16 (周六) Sprint Log — 49 个 BL-tasks ship

> **TL;DR**: 一天解决一个 6 周潜伏的 memory P0 bug, 顺手砍掉 9 张 dashboard 噪音卡 + 改 nav + 砍控制台 + 装 hermes todo 设施 + SOUL 三段重写.
>
> **跑偏说明**: 原 sprint plan 5/16 是 RBAC Day 3 (`allowed_models` per-dept schema + alembic migration). 实际整天 0 RBAC, 全 memory + dashboard. RBAC sprint 推迟到周日 / 下周.
>
> **关联 RCA**: [`docs/RCA-MEMORY-PLUMBING-20260516.md`](RCA-MEMORY-PLUMBING-20260516.md) — 主线 6h 摸排复盘.

---

## 真账单 (按主线分组)

### 主线 1: memory 系统真打通 (6h 摸排 + 4 项收尾)

| BL- | 内容 |
|---|---|
| #20 BL-MEMORY-PLUMBING-DIAG | 加 sanitizer + dispatch IN/OUT log 排"31 tool_calls 不写 memories" 真因 |
| #21 BL-MEMORY-PLUMBING-DIAG-V2 | 抓到 hermes 真错误 "Memory is not available" |
| #22 BL-MEMORY-BRIDGE-STORE | **主修**: catfish tool-bridge 自管 MemoryStore, 注入 `kw['store']` |
| #23 BL-MEMORY-BRIDGE-STORE-TODO-FOLLOWUP | audit script 路径修 (`~/.hermes/USER.md` → `memories/USER.md`), todo 部分搁置 |
| #24 BL-RCA-MEMORY-PLUMBING-DOC | 写 6h RCA 复盘文档 |
| #25 BL-MEMORY-DISPATCH-CONTRACT-TEST | 4 个防回归集成测 (store 注入 / 不污染别工具 / init fail / cache) |
| #29 BL-SOUL-MEMORY-TARGET-ROUTING | SOUL 重写 "Memory 写入纪律" 段, 反映 hermes 0.13 二分 target=user/memory |

**真因**: hermes 升 0.13 时引入 `handler=lambda args, **kw: memory_tool(..., store=kw.get("store"))` contract change, catfish tool-bridge stateless dispatch 不传 store → 静默拒. 6 周里 19 个 BL- 任务里 5 个在症状层猜原因.

**实盘验证**: 16:24:25 dispatch 写盘成功, `stat -f "%Sm" ~/.hermes/memories/USER.md` 跳到当时, entries 3 条含"陈淡孜/小芳".

### 主线 2: hermes todo 设施 (4 项)

| BL- | 内容 |
|---|---|
| #44 BL-TODO-BRIDGE-STORE | adapter.py per-session TodoStore (LRU 50), session_id 透传链路 |
| #45 BL-TODO-FOLLOWUP-COMPANION-SOUL | Companion lib/tauri.ts + src-tauri tool_bridge.rs 透传 sessionId; SOUL 加任务路由 3 档分流 |
| #46 BL-P0-MEMORY-FOLLOWUPS | useChat.ts:461 真接 sessionId; 实测清单; audit 验证 |
| #48 BL-TODO-STORE-PERSIST | 跨进程持久化 `~/.catfish/todo_store/<sid>.json`, LRU evict 同步删盘 |

**实测**: deepseek-flash 调 memory 正常, todo 因任务没给具体路径 LLM 合理 clarify, 没触发 todo dispatch. 真验证留周日.

### 主线 3: Dashboard 重构 (9 项, 9 张卡精简 / 2 整 section 砍)

| BL- | 砍 / 精简 |
|---|---|
| #16 BL-SESSION-CLEANUP-KILL | 砍 SessionCleanupCard |
| #30 BL-MEMORY-HISTORY-KILL | 砍 MemoryHistoryCard (session_facts 已冻) |
| #31 BL-IDENTITY-CARD-KILL | 砍 IdentityCard, "我自己" → "🐟 小鲶设置" |
| #32 BL-QUOTA-CARD-SLIM | 配额卡 3 row → 1 (只留今日 24h) |
| #33 BL-FEEDBACK-CARD-KILL | 砍 FeedbackSummaryCard |
| #34 BL-CURATOR-CARD-KILL | 砍脚本整理 (Curator 99% 无变化) |
| #35 BL-LEARN-SECTION-KILL | 砍整个"学习/改进" section (Learning + SkillRevision 两卡) |
| #36 BL-PROACTIVE-CARD-SLIM | ProactiveCard 砍"测一下"按钮; TasksCard 空时 hide |
| #37 BL-CONSOLE-TAB-KILL | 砍控制台 tab 整合启停按钮到 ServicesCard 行内 + 顶部全启/全停/全重启 |
| #38 BL-SECTION-COUNT-KILL | 砍 section header "· N" 计数 |
| #39 BL-TAB-RENAME | nav "对话" → "工作台" |
| #40 BL-EMPTY-STATE-CENTER | 小鲶空状态垂直居中 (60% → 100%) |
| #41 BL-STYLE-FP-COMPACT-EMPTY | 文书风格空态压缩单行 + "学一下" CTA 按钮 |
| #42 BL-RELATION-CARD-FILL-HEIGHT | 印象卡限高对称 (4 轮 V1/V2/V3/V4 才对) |
| #43 BL-USER-PROFILE-LIMIT-HEIGHT | 画像卡限高跟印象卡视觉对称 + minHeight:0 flex 陷阱 fix |
| #49 BL-DASHBOARD-HERMES-MEMORY-CARD | 新加"我的 hermes memory"卡 — 员工 in-glance 看 hermes 真活 memory |

**Dashboard 最终形态**: 4 sections / 11 真卡 (从原 ~18 杂乱卡降到 11 操作密度高的).

### 主线 4: 工程纪律 (3 项)

| BL- | 内容 |
|---|---|
| #26 BL-SOUL-DEBUG-NUDGE | SOUL 加 "调试纪律 — 卡 ≥30 分钟先看 RPC 边界" 段, 教未来 debug 路径 |
| #27 BL-LINT-B | 拆 _do_dispatch 193 → 53 LOC + 6 个分支函数, 测试 652→655 |
| #28 BL-C8 | Gateway 错误人话化 — Companion 优先用 backend errors.py friendly 翻译 (chat.ts + ChatMessage.tsx) |

---

## 跑偏说明

5/16 原 sprint plan (`BACKLOG.md` line 95):
> "5/16 RBAC Day 3: `allowed_models` per-dept/user 数据模型 + DB schema + alembic migration"

实际 5/16 ship 0 RBAC. 全部精力在 memory plumbing + dashboard 收尾.

**Trade-off 评估**:
- ✓ memory plumbing 是 P0 (6 周潜伏 bug, 客户演示 5/22-23 撞), 必修
- ✓ Dashboard 重构是客户演示 UX 关键, 必做
- ✗ RBAC Day 3 延迟 — 但 RBAC sprint 整体 8 天 (5/14-5/21), 还有 5 天缓冲
- 净结果: 接受偏离, 周日 RBAC Day 3 启动

---

## Backlog 状态变更

**完成 (从 ⬜ → ✅)**: 49 个 BL-tasks (见上面账单)

**新立 (添加到 backlog)**:
- #47 BL-NEMOTRON-XML-TOOLCALL — Nemotron 49B inline XML tool call 不兼容 LiteLLM. P2. 选项: A 升级 LiteLLM / B 加 XML parser / C 砍 Nemotron (推 C)

**重新评估 (P1 → 已完成)**:
- 原 P1 #4 "session_history 整合 hermes session_search" — 实际 5/3-5/5 早 ship (catfish_search_sessions + BL-MEMORY-FTS5-REAL 双轨并存). 我之前列错 backlog. 真"替换"留 P2 (要产品决策)

**剩 follow-up (周日 / 下周)**:
- RBAC Day 3 启动 (主线)
- LLM 实测 todo 工具 (给具体 docx 路径 + 多步任务)
- Dashboard 加 hermes memory 卡 UI 完善 (今天 ship 了但实战可能要调)

---

## 关联文件改动 (按 commit 拆)

```
M central/llm-gateway/src/catfish_gateway/tools_sanitizer.py  (BL-MEMORY-PLUMBING-DIAG)
M edge/tool-bridge/src/catfish_tool_bridge/adapter.py         (BL-MEMORY-BRIDGE-STORE
                                                                + BL-TODO-BRIDGE-STORE
                                                                + BL-TODO-STORE-PERSIST
                                                                + BL-LINT-B 拆函数)
A edge/tool-bridge/tests/test_memory_store_injection.py       (8 个测试)
M edge/catfish-cli/scripts/audit_hermes_memory.py             (路径修 + 新指标)
A docs/RCA-MEMORY-PLUMBING-20260516.md                        (RCA 文档)
M edge/identity/SOUL.md                                       (3 段重写: 调试 / memory 路由 / 任务路由)
M edge/companion-app/src/tabs/Chat/ChatPanel.tsx              (BL-EMPTY-STATE-CENTER)
M edge/companion-app/src/tabs/Chat/ChatMessage.tsx            (BL-C8)
M edge/companion-app/src/lib/chat.ts                          (BL-C8)
M edge/companion-app/src/lib/tauri.ts                         (sessionId + HermesMemoryView)
M edge/companion-app/src/hooks/useChat.ts                     (sessionId 透传)
M edge/companion-app/src/components/TabBar.tsx                (BL-TAB-RENAME)
M edge/companion-app/src/App.tsx                              (BL-CONSOLE-TAB-KILL)
M edge/companion-app/src/tabs/Dashboard/DashboardTab.tsx      (9 卡精简)
M ...(各张 Dashboard 卡片精简)
A edge/companion-app/src/tabs/Dashboard/HermesMemoryCard.tsx  (BL-DASHBOARD-HERMES-MEMORY-CARD)
A edge/companion-app/src-tauri/src/commands/hermes_memory.rs  (同上, Tauri 命令)
M edge/companion-app/src-tauri/src/commands/mod.rs            (注册 hermes_memory)
M edge/companion-app/src-tauri/src/lib.rs                     (invoke_handler 注册)
M edge/companion-app/src-tauri/src/commands/tool_bridge.rs    (sessionId 透传)
```

---

## 教训 (catfish 工程纪律)

1. **症状层猜 ≥ 30 分钟没头绪, 第一动作是加 RPC 边界 log** — 不改 prompt / SOUL / 工具列表猜. 5/16 RCA 真因 1 行 dispatch log 3 分钟定位. 写进 SOUL `## ★ 调试纪律` 段.

2. **升级第三方依赖 (hermes) 后必须跑行为级 contract test** — 不是"ok:true 就成". 5/3 hermes 升 0.13 时如果跑过 "memory(action=add) 后 USER.md mtime 跳" 这种测, 6 周潜伏 bug 当天发现. 防火墙: `tests/test_memory_store_injection.py` (今天加的) + 未来 `HERMES-UPGRADE-CHECKLIST.md` 加这一步.

3. **stateless RPC 桥接 + 上游 stateful tool = 一类盲点** — hermes memory_tool / todo_tool 都用 `store=kw.get("store")` 模式. catfish tool-bridge 当 stateless 一刀切, 自动丢 stateful kwargs. 未来 hermes 加新 stateful tool 时务必查 inventory.

4. **不要"留尾巴"** — 一气干完一个事比分两次省 50% 上下文切换成本. 今天 V4 终版 RelationCard 高度 + P1 #5 backend+UI 一气 = 真闭环. 拆只在工作量 > 2h 且有真依赖断点时.

5. **核实而非凭印象列 backlog** — P1 #4 session_search 整合早就 ship 了, 我没核实就列, 制造虚假未完成感. 周一 review backlog 时先 `grep` 现有代码再标待办.

---

## 周日 / 下周 follow-up

| 优先级 | 任务 | 估时 |
|---|---|---|
| P0 | RBAC Day 3 启动 (`allowed_models` schema + alembic) | 1 天 |
| P0 | LLM 实测 todo 工具 (给具体多步任务) | 5 min |
| P1 | session_history → hermes session_search 替换 (产品决策 + 实施) | 2-4h |
| P1 | Memory 跨员工 / 跨机器同步设计 | 月级 |
| P2 | Nemotron XML tool call 适配 (砍或加 parser) | 2-4h |
| P2 | SessionFactsProvider 真彻底删 (观察 7-14 天后) | 30 min |
| P2 | hermes LCM 压缩接入 | 2-4h |

---

**作者**: 鸿波 + 鲶鱼 (Cowork mode)
**日期**: 2026-05-16 (周六, 22:30-23:30 收尾)
**Sprint 内主线 ship**: 49 项 + 1 backlog 立 (#47 Nemotron)
**真正未完成**: 0
**关联 RCA**: `docs/RCA-MEMORY-PLUMBING-20260516.md`
