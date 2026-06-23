# catfish-memory 整体分类 + 规则 + scale 路线

**P3.5.82** (2026-06-23 鸿波拍 "现在就应该做一个规划"). 这是 catfish-memory 的
architectural design contract — 决定 "什么数据走 memory.kind, 什么独立 plugin",
"prefetch 怎么不无限膨胀", "数据怎么 lifecycle 不堆积".

写这个 doc 的触发: P3.5.75 → P3.5.78 5 个 sprint 教训. 当时把 bookkeep 做成独立
plugin (P3.5.75), 跟 catfish-memory schema decision tree 撞, LLM 看不见 → fail.
P3.5.78 重构成 `memory.kind=expense` → 一次 ship 修好. **architecture 决策错了,
prompt hack 修不动**. 这个 doc 是防再次走错的规则书.

---

## 1. 现状 audit (2026-06-23 实证, 不瞎猜)

### 1.1 memory 6 kind 完整路由

| kind | storage | 写入路径 | LLM 看到 | distill |
|---|---|---|---|---|
| `identity` | `~/.hermes/memories/USER.md` (cap 3500c) | hermes builtin `memory_tool(target=user)` | prefetch `_render_employee_journal` 段间接含 | 无 (人手维护) |
| `project_fact` | `~/.hermes/memories/MEMORY.md` (cap 5000c) | hermes builtin `memory_tool(target=memory)` | 间接含 | 无 |
| `workflow` | `~/.hermes/skills/<name>/SKILL.md` | hint → LLM 调 `catfish_propose_skill` | `_render_skills_catalog` top-K 5KB | 无 |
| `journal` | `~/.catfish/employee_journal.md` (append) | `_route_to_journal` 直接 append | `_render_employee_journal` 5KB (优先 distilled) | **24h LLM 蒸馏** → `distilled_facts.md` |
| `todo` | macOS Reminders.app (Apple) | hint → LLM 调 `catfish_reminder_create` | 不 inject prefetch (走 Reminders 系统) | Apple 自己管 |
| **`expense`** (P3.5.78 新) | `~/.catfish/bookkeep.jsonl` (append) | `_route_to_expense` 直接 append | `_render_expense_summary` 162 chars (今日/本月/累计) | **无 — 待 ship** |

### 1.2 prefetch 12 段注入顺序 + char budget

| # | section | budget | priority | 触发条件 |
|---|---|---|---|---|
| 1 | `_render_purpose` | ~500c | 强制 | 总注入 |
| 2 | `_render_schema` (AUTHORITATIVE) | ~1500c | 强制 (sparse mode 跳) | 非 advisor |
| 3 | `_render_memory_discipline` | ~800c | 强制 | 总 |
| 4 | `_render_safety_redline` | ~500c | 强制 | 总 |
| 5 | `_render_session_meta` | **500c (cap)** | 强制 | 文件存在 |
| 6 | `_render_expense_summary` (新) | ~200c | 强制 | jsonl 非空 |
| 7 | `_render_employee_journal` | **5000c (cap)** | sparse 跳 | 非 advisor |
| 8 | `_render_wiki_summary` | ~1KB | sparse 跳 | 非 advisor |
| 9 | `_render_skills_catalog` | **5000c (cap, top-K)** | sparse 跳 | 非 advisor |
| 10 | `_render_strategic_docs` | **3000c (cap, top-K)** | sparse 跳 | 非 advisor |
| 11 | `_render_feedback` | **2000c (cap)** | sparse 跳 | 非 advisor |
| 12 | `_render_skill_guard` | **3000c (cap)** | sparse 跳 | 条件 (query 提 skill) |

**实测 (2026-06-23)**: 总 15662 chars ≈ 4K tokens / turn (非 sparse).
sparse mode (P3.5.5 advisor) ≈ 3-4KB / 1K tokens.

### 1.3 distill / archive 现有机制

- **`sync_turn` hook (per-turn)** — 每 5 轮 / 30 min 节流, LLM 总结 → `employee_journal.md` append
- **24h LLM 蒸馏** — `_render_employee_journal` 优先读 `distilled_facts.md` (蒸馏过), fallback raw
- **`on_session_end`** — 仅 CLI atexit / `/reset` 触发, force-flush 剩余 buffer
- **archive / rotation** — **无**. 所有 jsonl + markdown 一直 append, 永不归档.

### 1.4 大数 (rough numbers, 真用一年后)

| 文件 | 1 天 | 1 年 | 物理大小 | prefetch inject |
|---|---|---|---|---|
| `employee_journal.md` | 5-10 段 | ~3000 段 | 几 MB | distilled 后 5KB (cap) |
| `bookkeep.jsonl` (新) | 5-10 笔 | ~3000 笔 | ~500KB | summary 200c (常数, 跟行数无关) |
| `USER.md` / `MEMORY.md` | <1 改 | ~50 改 | cap 3.5/5KB | 间接 |
| `feedback.jsonl` | 1-5 | ~1000 | ~100KB | tail 2KB |

---

## 2. design 原则 (硬规则)

### 2.1 数据进 memory.kind 的 5 个硬条件 (5 yes 才进, 1 no 独立 plugin)

| # | 条件 | 通过测试 |
|---|---|---|
| 1 | **单员工 scope** (员工个人数据) | bookkeep ✓ / 全公司 KPI ✗ |
| 2 | **半结构化** (有 schema 但不严格 SQL) | bookkeep jsonl ✓ / 财务报表 SQL ✗ |
| 3 | **聚合后能 inject prefetch** (summary 几行) | bookkeep 今日/本月 200c ✓ / 实时股价 ✗ |
| 4 | **低频 mutation** (一天 < 100 条) | bookkeep 5-10/天 ✓ / 实时 metrics 1000/秒 ✗ |
| 5 | **跟 chat 紧密** (员工聊出来的) | bookkeep ✓ / 邮件 scheduler ✗ |

5 yes → 加 `memory.kind=X`. 任何 1 no → 必须独立 plugin / 不该塞 memory.

### 2.2 不该塞 memory 的硬红线

绝对不能加 memory.kind:

- **跨员工 / 团队共享** (KPI / calendar / team kanban) — memory 是 single-user, 加跨员工破红线
- **大量结构化** (>10万行 / 严格 SQL relational) — 用 SQLite/PG plugin
- **实时高频** (秒级 mutation) — 用 stream plugin
- **大文件** (>10MB PDF/video) — 用 file storage plugin
- **隐私/合规外** (其他员工数据 / 法律敏感) — 该不存就不存

---

## 3. prefetch budget 演进规则

### 3.1 总 cap 健康度阈值

| kind 数 | prefetch 总 | tokens/turn | 健康度 | 行动 |
|---|---|---|---|---|
| **6 (现)** | 15K chars | ~4K | ✓ 健康 | 不动 |
| 7-10 | 18-22K | ~5-6K | ✓ OK | 每加 kind 配 budget |
| 11-13 | 25-30K | ~7-8K | ⚠️ 警告 | 必须 distill + budget cap |
| **14+** | 35K+ | ~10K+ | ✗ **天花板** | 必须分级 schema 重构 |

### 3.2 加 kind 强制配套规则

每加一个新 `memory.kind=X`, 必须同时 ship:

1. **`_route_to_X` handler** (写入路径, schema validation)
2. **`_render_X_summary` prefetch helper** — 聚合后 cap 200-500 chars 注入
3. **`_BUDGETS["X"]` char cap** — 防 raw 数据无限 inject
4. **lifecycle 计划** (raw → daily / weekly distill → monthly archive)
5. **archive/rotation 脚本** (jsonl 类: 每年归档 `X.YYYY.jsonl`)

**没 ship 这 5 件不允许加 kind**. P3.5.78 expense ship 时只做了 1+2+3, **4+5 是欠债, 半年内必还**.

### 3.3 总 prefetch budget cap (新规则, 待 ship)

总 cap = **20K chars / ~5K tokens**. 超过按 priority 截断:

- **不可砍** (1-6 段): purpose / schema / discipline / safety / session_meta / expense_summary
- **可砍 (按 query 相关性 top-K)**: 7-12 段 (journal / wiki / skills / strategic / feedback / skill_guard)

P3.5.5 sparse mode 已经在 advisor 路径做了类似的 (砍 7 段). 应该把规则**统一**到所有路径.

---

## 4. lifecycle 规则 (raw → summary → archive)

### 4.1 三层 lifecycle

```
raw 数据 (append-only)
  └─ daily summary (LLM 蒸馏, 24h cron)
      └─ weekly distill (LLM 浓缩, 7d cron)
          └─ monthly archive (压缩归档, 30d cron)
```

### 4.2 现状 vs 目标

| kind | raw | daily | weekly | monthly archive | 状态 |
|---|---|---|---|---|---|
| `journal` | employee_journal.md | distilled_facts.md (24h) | 无 | 无 | ⚠️ 部分 |
| `expense` | bookkeep.jsonl | **无** | **无** | **无** | ✗ 全无 |
| `identity` / `project_fact` | USER.md / MEMORY.md (cap) | 不需要 (已 cap) | - | - | ✓ |
| `feedback` | feedback.jsonl | 无 | 无 | 无 | ⚠️ |

### 4.3 半年内必 ship (technical debt 还款)

- **P3.5.83 — bookkeep.jsonl rotation** — 每年 archive `bookkeep.2026.jsonl`, 主文件只留近 90 天
- **P3.5.84 — expense daily distill** — 24h cron 蒸馏 "员工消费模式" → `distilled_facts.md` (类似 journal)
- **P3.5.85 — feedback rotation** — 同款 archive 机制

---

## 4.4 完整分类体系 (P3.5.82.1 鸿波 catch 补)

现 6 kind 是历史 ad-hoc 长出来的, 没有 coherent taxonomy. 未来加 kind 不能继续
ad-hoc, 要按一个**完整分类体系**填空, 不然 5-15 kind 全是平行无章, LLM 决策困惑.

### 4.4.1 3 维分类 (主体 × 主题域 × 时间)

每个 memory.kind 实际是 3 维空间一个 cell. 设计 kind 时按 3 维找它的位置, 防重叠
+ 防 gap.

**维 1: 主体 (subject)** — 数据描述谁

| 值 | 含义 | 例 |
|---|---|---|
| `SELF` | 关于员工本人 | 我的偏好/我加油花了多少 |
| `PROJECT` | 关于某个具体项目/事 | 客户 A 的 API 字段 |
| `WORLD` | 关于外部世界 (知识/人/地点) | wiki 概念 / 餐厅信息 |

**维 2: 主题域 (domain)** — 数据属于人生哪个 area

| 值 | 含义 | 例 |
|---|---|---|
| `PERSONAL` | 个人/身份/偏好/关系 | 名字/性格/家人 |
| `HEALTH` | 健康/医疗/锻炼/饮食 | 体检结果/跑步记录 |
| `FINANCE` | 财务/记账/资产 | 收支/股票/房产 |
| `WORK` | 工作/项目/技术 | 客户/代码/会议 |
| `SOCIAL` | 社交/沟通/人脉 | 朋友/联系人/活动 |
| `LEARNING` | 学习/阅读/技能 | 书摘/skill/笔记 |

**维 3: 时间 (temporal)** — 数据时间属性

| 值 | 含义 | 例 |
|---|---|---|
| `STATE` | 当前状态, 持续有效 | 偏好/身份/资产 |
| `EVENT` | 一次性事件, 已发生 | 加油/会议/体检 |
| `GOAL` | 未来计划/待办 | 待办任务/目标 |
| `PROCEDURE` | 重复流程/技能 | workflow/recipe |

### 4.4.2 现 6 kind 在 3 维 cell 的位置

| kind | 主体 | 主题域 | 时间 |
|---|---|---|---|
| `identity` | SELF | PERSONAL | STATE |
| `project_fact` | PROJECT | WORK | STATE |
| `workflow` | * | * | PROCEDURE |
| `journal` | SELF | * | EVENT (catchall 跨主题) |
| `todo` | SELF | * | GOAL |
| `expense` (P3.5.78) | SELF | FINANCE | EVENT |

**观察**:
- `SELF × HEALTH × EVENT`, `SELF × HEALTH × STATE` 没人填 → 加 medical/fitness 时填这
- `WORLD × *` 没人填 → 加 contact/place/recipe 时填这
- `PROJECT × WORK × EVENT` 没人填 → 加 meeting/decision 时填这

### 4.4.3 完整候选 kind 列表 (按 3 维填空, 18 个)

| 候选 kind | 主体 | 主题域 | 时间 | 状态 | 何时 ship 触发 |
|---|---|---|---|---|---|
| **`identity`** | SELF | PERSONAL | STATE | ✓ 现 | - |
| `preference` | SELF | PERSONAL | STATE | 待 | 员工抱怨"老问我同一件事" |
| `relationship` | SELF | SOCIAL | STATE | 待 | 员工要"记下我跟 X 的关系" |
| **`expense`** | SELF | FINANCE | EVENT | ✓ 现 (P3.5.78) | - |
| `asset` | SELF | FINANCE | STATE | 待 | 员工管股票/房产记录 |
| `medical` | SELF | HEALTH | EVENT | 待 | 员工查体检报告 |
| `fitness` | SELF | HEALTH | EVENT | 待 | 员工记跑步/锻炼 |
| `meal` | SELF | HEALTH | EVENT | 待 | 员工记饮食 |
| `habit` | SELF | * | PROCEDURE | 待 | 员工有重复 daily routine |
| **`journal`** | SELF | * | EVENT (catchall) | ✓ 现 | - |
| **`todo`** | SELF | * | GOAL | ✓ 现 (Apple Reminders) | - |
| `goal` | SELF | * | GOAL (长期) | 待 | 跟 todo 区分: todo<7天, goal>30天 |
| `reading_note` | SELF | LEARNING | EVENT | 待 | 员工读书做笔记 |
| **`project_fact`** | PROJECT | WORK | STATE | ✓ 现 | - |
| `meeting_log` | PROJECT | WORK | EVENT | 待 | 会议总结自动入 |
| `decision_log` | PROJECT | WORK | EVENT | 待 | 关键决策追溯 |
| **`workflow`** | * | * | PROCEDURE | ✓ 现 (catfish_propose_skill) | - |
| `contact` | WORLD | SOCIAL | STATE | 待 | 员工人脉 (vs `relationship` 私人) |
| `place` | WORLD | * | STATE | 待 | 餐厅/医院/景点信息 |
| `recipe` | WORLD | HEALTH | PROCEDURE | 待 | 食谱/教程/外部 procedure |

**统计**: 现 6 ✓, 候选 12 待. 18 全 ship 后 prefetch 估算 ~30K chars (按 P3.5.82 budget 阈值 = 警告区).

### 4.4.4 加 kind 的 design 规则 (基于 taxonomy)

1. **新需求来了, 先在 3 维 cell 找位置** (主体 × 主题域 × 时间)
2. **cell 已有 kind?** → 看是否能扩 (e.g. expense 已 cover SELF×FINANCE×EVENT, 餐饮记账走 expense 不新加 meal_expense)
3. **cell 空?** → 走 P3.5.82 §2 5 yes 决策 tree, 通过加 kind
4. **跨多个 cell (catchall)?** → 不该是 kind, 该是已有 catchall (e.g. journal 是 SELF × * × EVENT 跨主题)
5. **3 维之外** (e.g. 跨员工 / 实时 / 大量) → 独立 plugin

### 4.4.5 SELF 主体 6 维度 cell 覆盖 (员工核心场景)

员工 SELF 主体跨 6 主题域 × 4 时间, 共 24 cell. 但不是每 cell 都需要 kind, 只 ship
LLM 真常用的:

```
            STATE        EVENT          GOAL         PROCEDURE
PERSONAL    identity ✓   journal(*)     todo(*)      -
                         preference 待
HEALTH      -            medical 待     -            habit 待
                         fitness 待
                         meal 待
FINANCE     asset 待     expense ✓      -            -
WORK        -            project_fact(*) -           workflow(*) ✓
                         meeting 待
                         decision 待
SOCIAL      relationship 待 -            -            -
LEARNING    -            reading 待     -            -
```

(\* 标的 catchall 已 cover 多 cell, 不需要细分到每 cell 都加 kind)

### 4.4.6 分级 schema future (kind > 12 触发)

到 12-15 kind 时启动 **2 级 dispatch**:

```python
schema = {
    "domain": ["personal", "health", "finance", "work", "social", "learning"],
    "kind": "..."  # 按 domain 限制可选 kind
}

# LLM 调:
memory(domain="finance", kind="expense", amount=300, ...)
memory(domain="health", kind="medical", ...)
memory(domain="work", kind="meeting_log", ...)
```

LLM 决策从 12+ flat enum 改 6 主类 × 3-5 sub-kind 二级. 决策空间小, 命中率高.

迁移路径: 现 6 kind flat → 加到 ~12 kind 仍 flat → 到 12+ 时**plugin 内部 mapping**:
- `expense` → `domain=finance, kind=expense`
- `journal` → `domain=*, kind=journal` (catchall 保留)
- ...

LLM 暂时仍能用旧 flat kind (兼容). 新 LLM 用 2 级. 6 个月过渡期.

---

## 5. future 12+ kind 时的分级 schema 重构

到 12-15 kind 触发**分级 schema**, 类似 SQL table normalization:

```
现 flat 6 kind:
  identity / project_fact / workflow / journal / todo / expense

未来分级 (5 主类 × 3-5 sub-kind):
  personal/
    ├─ identity (谁)
    ├─ profile (偏好/习惯)
    └─ relationship (人脉)
  health/
    ├─ medical (病历)
    ├─ fitness (锻炼)
    └─ meal (饮食)
  finance/
    ├─ expense (记账, 现 P3.5.78)
    └─ asset (资产)
  work/
    ├─ project_fact (现)
    ├─ journal (现)
    └─ workflow (现)
  social/
    ├─ todo (现)
    └─ note (杂记)
```

LLM 看 schema: 先选主类 (5 选 1), 再选 sub-kind (3-5 选 1). 决策空间从 12 选 1 降为 5×3 = 双层决策, 命中率高.

**何时触发**: prefetch >25K chars 或 kind 数 >12. 现在不动.

---

## 6. catfish-memory vs 独立 plugin 的判断 decision tree

新需求来了, 5 步决策:

```
新数据来了, 该塞 memory.kind 还是独立 plugin?

1. 是单员工个人数据吗?
   ├─ 否 (跨员工) → 独立 plugin
   └─ 是 → 继续

2. 数据频率高吗 (>100 条/天)?
   ├─ 是 → 独立 plugin
   └─ 否 → 继续

3. 聚合后能 inject prefetch (summary 几行) 吗?
   ├─ 否 → 独立 plugin
   └─ 是 → 继续

4. 数据结构严格 SQL (需要 join/transaction)?
   ├─ 是 → 独立 plugin (with DB)
   └─ 否 → 继续

5. 跟 chat 紧密 (员工聊出来) 吗?
   ├─ 否 (后台/定时) → 独立 plugin (e.g. email scheduler / cron)
   └─ 是 → ✓ **memory.kind=X**, 走 P3.5.78 同款架构
```

### 6.1 例子 (假想 future 需求)

| 需求 | 决策 | 理由 |
|---|---|---|
| 记账 (现) | ✓ memory.expense | 5 yes |
| 体检数据 (年 2 次) | ✓ memory.medical | 5 yes |
| 锻炼记录 (天 1 条) | ✓ memory.fitness | 5 yes |
| 全公司 KPI dashboard | ✗ 独立 plugin | 跨员工 |
| 实时股价 monitor | ✗ 独立 plugin | 高频 + 不在 chat |
| 团队 calendar | ✗ 独立 plugin | 跨员工 |
| 财务系统 PG 报表 | ✗ 独立 plugin | 严格 SQL + 大量 |
| 邮件自动回复 scheduler | ✗ 独立 plugin | 后台 workflow |
| 阅读笔记 (天 5-10 条) | ✓ memory.note | 5 yes |

---

## 7. 实施路线 (Roadmap)

### 7.1 现状 (P3.5.82 写完即真)

- 6 kind 健康, prefetch 15K (4K tokens), 在 budget 内
- distill 只 journal 有, expense 欠债

### 7.2 周末 sprint (鸿波 6/23 拍 — P3.5.83-86 + 87 stretch)

**5 个 sprint 一气 ship 跨 1 周末**:

- **P3.5.83**: bookkeep.jsonl 年度 rotation
  (每年 archive `bookkeep.YYYY.jsonl`, 主文件留近 90 天)
- **P3.5.84**: expense daily distill (24h cron LLM 蒸馏消费模式)
  (复用 P3.5.74 cron picker 联动, 跟 journal distill 同款架构)
- **P3.5.85**: feedback.jsonl rotation (复用 P3.5.83 helper)
- **P3.5.86**: prefetch 总 budget cap (统一 20K 上限, 按 priority 截断)
  (现 sparse mode 已 demo, 统一规则到所有路径)
- **P3.5.87 (stretch)**: 第一个 future kind 候选 ship 跑通 taxonomy 流程
  (鸿波拍选: preference / habit / asset / 别的; 配套 5 件套验证 design 规则)

各 sprint 估计 2-4h, 一周末 (12-20h) 全 ship.

### 7.3 1 年内 (P3.5.90+ 候选, 周末 sprint 完成后)

- 加 1-2 新 kind (按 §4.4 taxonomy 选, e.g. medical / fitness / asset)
- 每个配套 5 件 (route + summary + budget + distill + archive)
- 仍走 flat 6-8 kind, 不分级

### 7.4 2 年内或 prefetch 触阈值时 (P3.6.X 大重构)

- 触发条件: prefetch >25K **或** kind 数 >12
- 动作: 分级 schema 重构 (5 主类 × 3-5 sub-kind)
- 兼容: 旧 flat kind 自动映射到对应主类 (e.g. `expense` → `finance.expense`)

---

## 8. 总结 (memory architecture 红线 5 条)

1. **memory 是单员工记忆中心**, 跨员工数据走独立 plugin
2. **加 kind 必配套 5 件** (route / summary / budget / distill / archive)
3. **总 prefetch budget cap 20K chars / 5K tokens**, 超过按 priority 截断
4. **所有 jsonl 必有 rotation** (每年 archive 老的)
5. **12+ kind 触发分级 schema 重构**, 不允许 flat 平铺到 15+

---

## 9. 相关 sprint 历史 (引用)

- P3.5.42: memory_enforce hook (LLM 写 memory 内容校验)
- P3.5.74: cron picker 联动 (memory 用 picker 选的 model 蒸馏)
- **P3.5.75-78** (本 doc 起源): bookkeep 独立 plugin → fail → 重构 memory.kind=expense ✓
- P3.5.79-80: 微信 picker 联动 (memory 全 platform 一致)
- P3.5.82 (本 doc): architecture 规则化

---

## 10. 待 review 决策点 (鸿波拍板)

写完这个 doc, 几个点要鸿波拍:

1. **半年内 P3.5.83-86 4 个 sprint 优先级** — 现在排进 backlog 还是看实际负担再说?
2. **20K prefetch budget cap** — 同意这个数字? 还是 15K 更保守?
3. **分级 schema 触发条件** — kind 数 12 还是 10? prefetch 25K 还是 20K?
4. **medical/fitness 等新 kind** — 现在 demo 推动还是等需求自然来?

不拍 = 现状 6 kind 不动, 半年后看实际负担再说. 也是合理选择 (现在没痛).
