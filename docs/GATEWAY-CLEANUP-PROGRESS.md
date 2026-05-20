# Gateway 减负进度 — Post-Hermes-Cutover

> **作者**: 鸿波 + Claude
> **始于**: 2026-05-19 hermes cutover 完成后
> **目标**: gateway 从"agent runtime" 退化成纯 LLM proxy, 删冗余 ~4000 LOC (~50%)
> **使用方式**: 随时查 — 当前进度 / 下个动作 / 长期目标

## TL;DR

```
现在 (5/20):           -1330 LOC ✓ (Week 1 删)
Day 17+ (Step E 后):  -2845 LOC (Step E 等观察期通过)
Q2 后续:               -3945 LOC (audit 中 ~1100 LOC 可再清)
Q3 + 长期:             -4000+ LOC (调研后)
```

## 已完成 ✓ — Week 1 (5/19-5/20)

| Item | LOC | 引用 |
|---|---|---|
| `compound_intent.py` 删 | -217 | commit `f191753` |
| `self_critique.py` 删 | -455 | 同上 |
| 2 个旧测试删 (`test_compound_intent` + `test_self_critique`) | -658 | 同上 |
| `app.py` 2 处 caller 改注释 | +-N (中性) | 同上 |
| `test_gateway_chat_integration.py` 5 scenario 新增 | +442 | 同上 |
| **Week 1 净减** | **-1330 LOC** | `gateway-pre-cleanup-week1` tag |

设计依据: hermes 自管 plan-execute (run_agent.py:12614 主循环 + max_iterations=90 兜底), gateway 这两层 prompt 注入冗余.

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

## 还可减的 — 5/19 audit 列过, 没启动

需要 spike 调研每个 hermes 是否真有等价能力替代. 估算 LOC 是大约值.

### 高优先级 (Step E 完成后立刻起)

#### `inject_unified` / `inject_subset` memory 注入层 (~200 LOC)

**位置**: `app.py:2566-2572`
```python
if os.environ.get("CATFISH_MEMORY_UNIFIED", "1") != "0":
    body["messages"] = _registry.inject_unified(...)
else:
    body["messages"] = _registry.inject_subset(...)
```

**为啥可删**: 现在 catfish-memory plugin `prefetch()` 已经做 system prompt 注入 (5 个 catfish 边缘源 + employee_journal distilled). gateway 这层 inject 是 5/19 BL-MEMORY-OWNERSHIP-FIX 之前的老路径残留, 跟 plugin 重复.

**风险**: 5/19 BL-MEMORY-OWNERSHIP-FIX Phase 2-3 已经"跳过所有 provider 注册" (catfish_gateway.memory.bootstrap log), 但 inject_unified/inject_subset 调用入口还在. 验证它们实际是不是 no-op, 是的话直接删 caller.

**工程量**: 1-2 周 (测试覆盖大 — 这条路径涉及 chat completion 主路径)

### 中优先级 (Q2 sprint 内)

#### `tool_retry_hint.py` (~200 LOC)

**位置**: `app.py:2599-2614`

**为啥可删**: hermes `max_iterations=90` 兜底 tool retry, BL-HERMES-AUTO-CONTINUE-LIMIT 已经在 hermes runtime 实现等价机制. gateway 这层 hint 跟 hermes 重复.

**风险**: 5/18 BL-HERMES-AUTO-CONTINUE-LIMIT 引入了 "hard cap 优先于 soft hint" 设计 — gateway 仍在 hard cap 路径用 (line 2602-2614). 需要确认 hermes 是不是真接管了 hard cap, 不是单 soft hint.

**工程量**: 1 周

#### `inject_session_goal.py` (/goal 命令, ~100 LOC)

**位置**: `app.py:2581-2582`

**为啥可删**: hermes 0.13 Ralph loop 有 /goal 原生支持. 问题: Companion 是不是切到 hermes 原生?

**风险**: Companion UX 可能仍依赖 catfish gateway 的 /goal 处理. 需要先调研 Companion 路径.

**工程量**: 1 周 (调研 + 切 Companion)

#### `memory.bootstrap` 死代码 (~150 LOC)

**位置**: `catfish_gateway/memory/bootstrap.py`

**为啥可删**: 5/19 BL-MEMORY-OWNERSHIP-FIX Phase 2-3 已"跳过所有 provider 注册". 代码壳还在, function-level 死代码 (永不被调到的 provider 注册逻辑).

**工程量**: 半天 (静默删 + 测试)

### 低优先级 (Q3 调研)

#### `proactive` 模块 (主动 starter, ~300 LOC)

**位置**: `catfish_gateway/proactive/*`

**为啥可能删**: 不确定 hermes 有没有等价主动 starter. 需要调研 hermes plugin 系统.

**工程量**: Q3 sprint, 调研 + 调整

#### `a2a_journal_hook.py` (~150 LOC)

**位置**: `catfish_gateway/a2a_journal_hook.py`

**为啥可能删**: a2a 协议 hermes 接管? 需要调研.

**工程量**: Q3 sprint

### 累计估算

| 优先级 | LOC 估 | 时机 |
|---|---|---|
| 高 (inject_unified/subset) | ~200 | Step E 后 |
| 中 (tool_retry / session_goal / memory.bootstrap) | ~450 | Q2 后续 |
| 低 (proactive / a2a_journal_hook) | ~450 | Q3 调研 |
| **可再减总计** | **~1100 LOC** | |
| **加上 Week 1 + Step E** | **~3945 LOC** | |

Gateway 当前估约 ~7000-8000 LOC, 净减 4000 LOC = **~50% 减半**.

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

## Trajectory 时间线

```
2026-05-19  Week 1 (compound_intent + self_critique 删)            -1330 ✓
2026-05-20  Week 2 plugin 写路径 deploy + 双跑期开始
2026-05-27  Day 7 双跑通过 → LEGACY=0 切换
2026-06-03  Day 14 plugin 独占 7 天通过
2026-06-04+ Step E 硬删 session_summarizer/memory_distill/employee_journal  -2845
2026-06 月  inject_unified/inject_subset 删                          -3045
2026-07-08 Q2 末   tool_retry_hint + session_goal + bootstrap 死代码 -3945
2026-Q3     proactive + a2a_journal_hook (调研后)                   -4000+
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

*最后更新: 2026-05-20 13:00 (Day 2.5 完工后)*
