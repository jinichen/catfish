# Gateway 减负进度 — Post-Hermes-Cutover

> **作者**: 鸿波 + Claude
> **始于**: 2026-05-19 hermes cutover 完成后
> **目标**: gateway 从"agent runtime" 退化成纯 LLM proxy, 删冗余 ~4000 LOC (~50%)
> **使用方式**: 随时查 — 当前进度 / 下个动作 / 长期目标

## TL;DR

```
现在 (5/20):           -4118 LOC ✓ (Week 1 + Q1+Q4 audit)
Day 17+ (Step E 后):  -5633 LOC (Step E 等观察期通过)
Q2 后续:               -5983 LOC (audit verdict 后再清 350 LOC, tool_retry_hint 留)
Q3 + 长期:             -6000+ LOC (proactive / a2a 不动, 它们是企业能力)
```

**5/20 下午 audit 修正**: 之前估"Q2 后续 ~1100 LOC 可清"严重低估. Subagent spike 后:
- Q1+Q4 实际 ~2788 LOC (今天已删, 比估的 350 多 8 倍)
- Q5 proactive + Q6 a2a_journal_hook 移到 "永远不动" (企业能力, charter 第 2 层)
- Q2 tool_retry_hint 留 (hermes 没等价, 兜底 89 次重试事故)
- Q3 inject_session_goal 暂留 (等 #26 daily-briefing 重审)

## 已完成 ✓

### Week 1 (5/19-5/20 早) — compound_intent + self_critique 删

| Item | LOC | 引用 |
|---|---|---|
| `compound_intent.py` 删 | -217 | commit `f191753` |
| `self_critique.py` 删 | -455 | 同上 |
| 2 个旧测试删 (`test_compound_intent` + `test_self_critique`) | -658 | 同上 |
| `app.py` 2 处 caller 改注释 | +-N (中性) | 同上 |
| `test_gateway_chat_integration.py` 5 scenario 新增 | +442 | 同上 |
| **Week 1 净减** | **-1330 LOC** | `gateway-pre-cleanup-week1` tag |

设计依据: hermes 自管 plan-execute (run_agent.py:12614 主循环 + max_iterations=90 兜底), gateway 这两层 prompt 注入冗余.

### Q1+Q4 audit 删除 (5/20 下午) — memory/ 抽象层

5/20 spike audit 发现 5/19 BL-MEMORY-OWNERSHIP-FIX 之后 `memory/` 整目录已是 no-op 死代码, catfish-memory plugin (hermes 侧 prefetch) 接管 5 个 provider 的 system prompt 注入路径.

| Item | LOC | 引用 |
|---|---|---|
| `memory/bootstrap.py` 删 | -74 | commit ? (待 push) |
| `memory/registry.py` 删 | -449 | 同上 |
| `memory/__init__.py` 删 | -160 | 同上 |
| `memory/providers/*` 9 文件删 | -704 | 同上 |
| `test_memory_provider.py` 删 | -296 | 同上 |
| `test_memory_provider_step1a.py` 删 | -244 | 同上 |
| `test_memory_provider_step1b.py` 删 | -255 | 同上 |
| `test_memory_registry_integration.py` 删 | -374 | 同上 |
| `test_lean_inject.py` 删 (lean 模式 contract test, 跟 inject_subset 绑死) | -232 | 同上 |
| `app.py` caller 删 (bootstrap_registry + InjectContext + inject_unified/subset dispatch) | -50 | 同上 |
| `test_central_edge_boundary.py` allowlist 同步删 4 entry | +-N | 同上 |
| **Q1+Q4 净减** | **-2788 LOC** | `gateway-pre-memory-registry-deletion` tag |

测试: 1274 passed + 4 skipped + 1 deselected (Task #15 预存 bug, 不阻塞).

设计依据:
- 5/19 BL-MEMORY-OWNERSHIP-FIX Phase 2-3 已 disable provider 注册 → inject_unified/subset 是 no-op
- 5/20 Day 2 catfish-memory plugin prefetch 接管 5 provider (session_meta / employee_journal / skills_catalog / feedback / skill_guard)
- hermes builtin memory 接管 4 个其它 (MEMORY.md + USER.md)

### 累计 Week 1 + Q1+Q4

**-4118 LOC** ✓ (相当于 gateway 削掉约 ~55%, 假设原 7000-8000 LOC).

## 进行中 ⏳ — Step E 阻塞在观察期 (Day 10-16 启)

Step E 三个 module 一起删, 由 catfish-memory plugin 接管. 当前 (5/20) plugin 写路径已 deploy + 验证工作 (commit `0acfa97` / `f33ad30` / `696625f`), 等 7+7 天双跑观察通过.

| Item | LOC | 接管 |
|---|---|---|
| `session_summarizer.py` 删 | -527 | catfish-memory plugin `sync_turn` |
| `memory_distill.py` 删 | -747 | plugin distill 路径 |
| `employee_journal.py` 删 | -241 | plugin 直接写 `~/.catfish/employee_journal.md` |
| 2 处 app.py caller 删 | -14 | env gate 也删 |
| **Step E 净减** | **-1515 LOC** | Task #24 |
| **Week 1 + E 累计** | **-2845 LOC** | |

**阻塞条件**:
- Day 3-9 (5/21-5/27): 双跑期观察 7 天 (LEGACY=1 + plugin 并行, mtime 5s 幂等防 dup)
- Day 10-16 (5/28-6/3): LEGACY=0 plugin 独占, 再观察 7 天
- Day 17+ (6/4+): 鸿波 explicit approval + 跑 Week 1 整套 git tag rollback drill 验证 → 硬删

## 还可减的 — 5/20 audit verdict 后修正

需要 spike 调研每个 hermes 是否真有等价能力替代. 估算 LOC 是大约值.

### ✅ 已完成 (5/20 audit verdict)

- **Q1+Q4 inject_unified/subset + memory/ 整目录**: -2788 LOC, 见上面"已完成"段

### 中优先级 (Q2 sprint 内)

#### `tool_retry_hint.py` (350 LOC) — ❌ **留**

**位置**: `tool_retry_hint.py` + caller `app.py:2599-2614`

**Audit verdict (5/20)**: ❌ 留. hermes `max_iterations=90` 只兜"硬上限", **不识别"连续同 tool 同 error N 次"语义**. `should_hard_cap` (扫 30 条历史看 5 次同 tool 失败) 是 gateway 独有 + 跨 LLM/agent 共用层. 5/18 BL-HERMES-AUTO-CONTINUE-LIMIT 引入因鸿波本地撞过 89 次重试烧 token 事故.

**唯一可优化**: Task #14 BL-LEAN-GATE-MISSING — lean 模式不需要 retry hint, 加 gate 跳过 (省 50 LOC if). 单独修补, 不删模块.

#### `inject_session_goal.py` (/goal 命令, 228 LOC) — ❓ **暂留**

**位置**: `session_goals.py` + `app.py:2434-2445, 2581-2582`

**Audit verdict (5/20)**: ❓ 暂留. hermes API server (8642, Companion 走的) **没 /goal 拦截**, 只 CLI 有. gateway 这层 `detect_goal_command` 是 Companion → hermes → gateway 链上唯一 /goal 拦截点. hermes Ralph loop "evaluate_after_turn" 是 *持续* 拉回, gateway 现版本只做 *事前 inject*, **功能不等价**.

**何时重审**: 等 Task #26 BL-COMPANION-DAILY-BRIEFING-MVP 设计完一起讨论.

### ❌ 永远不动 — Q5/Q6 是 catfish 企业能力护城河

5/20 audit 推翻之前"低优先级 Q3 删" 的判断 — 这两个跟 charter 第 2 层"非技术员工 UX" + 第 3 层"业务数据接入" 强绑定, 是 catfish 跟 hermes 的差异化卖点:

#### `proactive` 主动 starter 模块 (481 LOC + 473 test)

**位置**: `proactive.py` + Dashboard ProactiveCard.tsx + useProactiveScheduler.ts / useProactiveTriggers.ts

**Audit verdict**: ❌ 留 (永远). Companion 产品 UX 特性: 9:30/14:00/17:30 时段主动 starter + 信号触发 (silence / deadline / focus) + macOS notification. hermes 完全无等价. 客户买 Companion 的差异化点之一. 关联 Task #26 daily-briefing — proactive 是当前 MVP, daily-briefing 是 next-gen 演化.

#### `a2a_journal_hook.py` (164 LOC + 223 test)

**位置**: `a2a_journal_hook.py` + `a2a_server.py:495-519`

**Audit verdict**: ❌ 留 (永远). a2a 协议 (BL-FED2.x 系列) 是 catfish 独家发明的**跨员工咨询**协议. alice 问 bob, bob 答完后自己 mac 写 [a2a-help] journal, 下次 expertise extract 加权 — 自学习闭环. **hermes 0 等价**. charter 第 2 问 "客户买单 → 是" + 第 3 问 "企业能力 → catfish 留".

### 累计估算 (5/20 audit 修正后)

| Item | LOC | 状态 |
|---|---|---|
| Week 1 (compound_intent + self_critique) | -1330 | ✓ 完成 |
| Q1+Q4 (memory/ + 4 test + lean_inject + caller) | -2788 | ✓ 完成 |
| Step E (session_summarizer + memory_distill + employee_journal) | -1515 | ⏳ Day 17+ |
| Q3 inject_session_goal | ~228 | ❓ 暂留, #26 重审 |
| Task #14 tool_retry_hint lean gate | ~50 (单独修补) | ⏳ pending |
| **可减总计 (含 Step E)** | **~-5683 LOC** | |
| **永远不动 (Q2 / Q5 / Q6 / 护城河)** | ~1500 LOC | charter 第 2-4 层 |

Gateway 现约 7000-8000 LOC, 净减 ~5700 LOC = **约 75%** (5/19 估 50% 偏低, 因 Q1+Q4 实际 8x 用户原估).

## 永远不动 (Gateway 核心)

| Item | 为什么留 |
|---|---|
| `auth/*` (OIDC / dev_token / Composite) | catfish 第 4 层护城河 (RBAC/审计) |
| `quota` (部门 / 角色 / 客户 quota) | catfish 第 4 层护城河 |
| `catalog` / `models.yaml` / `fallback.py` | gateway 本职 — LLM proxy + 配额 + fallback |
| `tools_sanitizer` (BL-TOOL-CAP) | catfish 独有 — 员工 99 tool 削到 50 |
| `facts_router` / `tool_archive_router` (Q3 业务数据) | 5/20 拍板"业务数据 + tool 暴露 LLM" pattern, 业务核心 |
| `session_meta` / `session_facts` (业务数据) | catfish 员工本机经验积累 |
| `audit` 写 `gateway_audit.jsonl` | 第 1+4 层护城河 (合规审计) |
| `secret_scanner` (prompt 明文密码检测) | 第 1 层护城河 (企业合规) |
| `tool_retry_hint.py` | Audit 5/20 verdict: hermes 没"连续同 tool 同 error" 检测, 兜 89 次重试事故 |
| `proactive.py` | Audit 5/20 verdict: Companion UX 特性, hermes 0 等价 |
| `a2a_journal_hook.py` | Audit 5/20 verdict: catfish 独家跨员工咨询协议 |

## Trajectory 时间线

```
2026-05-19   Week 1 (compound_intent + self_critique 删)                       -1330 ✓
2026-05-20 早 Week 2 plugin 写路径 deploy (Day 1 + 2 + 2.5)                       
2026-05-20 中 Q1+Q4 audit 删除 (memory/ + 4 test + lean_inject + caller)        -4118 ✓
2026-05-21   Week 2 双跑期 Day 3 起 (LEGACY=1 + plugin 并行 7 天观察)
2026-05-27   Day 7 双跑通过 → LEGACY=0 切换 plugin 独占
2026-06-03   Day 14 plugin 独占 7 天通过
2026-06-04+  Step E 硬删 session_summarizer/memory_distill/employee_journal     -5633
2026-Q2末    inject_session_goal 跟 #26 daily-briefing 一起重审, 视情况删
2026-Q3+     不动 (Q2 / Q5 / Q6 / 其它护城河 ~1500 LOC 留)
```

## 跟 charter 三问对齐

每个减负项过 5/19 charter 三问:

1. **hermes 已做了吗?** → 是 → gateway 这层重复, 删
2. **客户企业买单吗?** → 否 (gateway 内部技术细节, 客户看不到) → 删了不影响销售
3. **AI 能力还是企业能力?** → AI 能力 (prompt 注入 / agent loop / memory) → 借 hermes, 不重复造轮子

每条 gateway 减负都通过这三问. 留着的 (auth/quota/RBAC/audit/business store/secret scanner) 全是企业能力, 不动.

## Rollback 路径

每个 Step 都有 git tag rollback 锚:

| Step | tag |
|---|---|
| Week 1 | `gateway-pre-cleanup-week1` |
| Week 2 / Step E | `gateway-pre-cleanup-week2` |
| Q1+Q4 audit 删除 | `gateway-pre-memory-registry-deletion` |
| Q2 后续 | 创建 `gateway-pre-cleanup-q2` |

回滚命令: `git reset --hard <tag>; git push --force-with-lease origin main` (谨慎用).

实际更稳的是 env gate 回滚 (1 分钟内, 不动代码):
- Step C `CATFISH_GATEWAY_LEGACY_SUMMARIZE=1` (老 caller 重启)
- 同套路给后续每个减负 step 加 env gate, 部署时支持快速回退

---

*Ref*:
- *CATFISH-POSITIONING-2026-05-19.md (charter 三问)*
- *CATFISH-DEBT-AUDIT-2026-05-19.md (全栈减负 audit, 这文档是 gateway-only 子集)*
- *GATEWAY-CLEANUP-WEEK1-DELETE-PLAN.md*
- *GATEWAY-CLEANUP-WEEK2-MIGRATION-PLAN.md*
- *GATEWAY-CLEANUP-WEEK2-STEP-D-DEPLOY.md*
- *Task #10 / #21 / #22 / #23 / #24 / #29 / #30 / #31*

*最后更新: 2026-05-20 13:30 (Q1+Q4 audit 删除 + audit verdict 修正)*
