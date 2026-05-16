# MEMORY-HALFCUT-PLAN — 半切 hermes 记忆方案 (V1 DEPRECATED)

> 作者: 副手 + 鸿波
> 日期: 2026-05-16 (V1) / 2026-05-16 下午 (V2 更正)
> 状态: **⚠️ V1 方向错, V2 是 BL-MEMORY-FULL-HERMES (C 方向真切)**
> 关联: BL-MEMORY-DIAGNOSIS, BL-MEMORY-DISTILL-LIVE, BL-MEMORY-FTS5-RECALL, BL-MEMORY-PROVIDER-ABC, BL-MEMORY-FULL-HERMES

---

## ⚠️ V2 更正 (2026-05-16 下午, 鸿波拍板)

V1 方向 (半切) **不对**. 实盘 5/16 13-14 时数据 + 鸿波 2 次反问点亮真方向:

**鸿波 1**: "现在 catfish_remember 在用不是因为它好, 是因为它简单. 真 fix 是追平 catfish_remember 跟 hermes memory 的差距, 不是砍 hermes"

**鸿波 2**: "你提的 wrapper 方案只是写表 (换存储位置), 没充分利用 hermes memory 的 4 action + consolidate + search + agent 自管能力"

**鸿波 3**: "catfish 中央为什么要记员工写什么? 这逻辑奇怪 — 违反 SOUL 第 2 条 '中央永不记录对话内容'"

V2 方向: **C — 真用 hermes memory 完整能力**, 跟 SOUL 第 1/2 条哲学完美对齐:

- 长期事实 → hermes `memory(action="add", ...)` (跨 session 持久, agent 自管 consolidate)
- 短期速记 → catfish_remember (仅本 session, 跨 session 失忆)
- 数据全在员工本机, catfish 中央不存副本 (回归"边缘主权")
- catfish 治理 (audit / quota / red line) 在 gateway 层做 (model 调用维度), 不做内容维度

V1 (半切, 砍 4 留 5) **作废**. V2 详细规划见下面 "V2 真方向" 章节.

V1 内容保留**作历史参考**, 标 (V1).

---

## V2 真方向 (BL-MEMORY-FULL-HERMES C 完整切)

### V2 4 个原则

1. **catfish 中央不存内容副本** — SOUL 第 2 条 "永不记录对话内容". 客户 IT "想看员工记了啥" 是违背哲学的伪需求.
2. **长期记忆走 hermes memory 4 action 完整 API** — add / replace / remove / search. agent 自管 consolidate (满了自整理).
3. **catfish_remember 收窄到 session-only** — tool description 标注 "**跨 session 必失忆**". SOUL 主动记忆纪律明确 "长期事实必须 memory.add 不要 catfish_remember".
4. **catfish 治理只在调用维度** — gateway audit model 调用次数 / token / 延迟. 不 audit 内容. Red line 在 catfish_remember wrapper 加 (memory tool 是 hermes 自管, 我们不拦).

### V2 实施清单 (5/16 下午 ship)

- ✅ C1: SOUL.md BL-MEMORY-NUDGE 段重写 — 明确 long-term=memory / short=catfish_remember, 加 4 action 用法
- ✅ C2: catfish_remember tool description 标 "**跨 session 必失忆**" 警告 + 列正反场景
- ✅ C3: inject_session_facts 加 banner "下面是临时事实, 长期请用 memory"
- ✅ C5: audit script KPI 改 — catfish_facts 越**少**越好 (session-only), hermes_memories 越**多**越好

### V2 留 P1 (1-2 周后, 看观察期数据)

- ⏳ C6: 砍 session_summarizer (catfish 后台扫总结) — 让 hermes 自管 consolidate
- ⏳ C7: 砍 employee_journal (catfish 670KB 历史) — 迁到 hermes memories/ 分主题文件
- ⏳ C8: 砍 memory_distill (5/16 早上刚上线的) — hermes auto-consolidate 接管

### V2 观察期 (5/16 → 6/15)

audit script 每天 18:00 跑. 6/15 看数据:

| 指标 | 起点 (5/16) | 6/15 目标 (V2 健康) |
|---|---|---|
| `hermes_user_md_bytes` | 846 B | ≥ 5 KB (LLM 真在 memory.add) |
| `hermes_memories_files` | 4 (含 0 字节占位) | ≥ 10 (分主题文件出现) |
| `catfish_facts_bytes` | 9 KB | ≤ 2 KB (session 结束员工清, 不再累积) |
| `catfish_journal_bytes` | 670 KB | 不再涨 (BL-MM 准备砍后台 summarizer) |

KPI 任一不达标 → V2 也有问题, 重新评估.

---

## ⚠️ 下面是 V1 内容 (已作废, 留作历史参考)

---

## 摘要 (TL;DR)

5/16 早上鸿波本机数据 audit 发现 **catfish 记忆系统问题**:

- catfish 自己写的 9 个记忆模块绝大多数功能跟 hermes 0.13 原生重叠
- hermes 原生 `~/.hermes/memories/` **死了 12-19 天** (memory 工具被 `BL-TOOL-CAP` 砍掉, 已修)
- catfish journal **670KB 被截 99%** 注入时只保留 5KB 尾段
- 9 模块各自一套数据 / 各自调 LLM / 没统一抽象

鸿波早上拍板: **半切 hermes**. **不全切** (会丢企业治理 + 单租户架构错位), **不不切** (双轨浪费). 借 hermes 做它擅长的, catfish 只做企业必需.

本文档定:
- **砍哪 4 个** (跟 hermes 重叠, 让 hermes 接管)
- **留哪 5 个** (hermes 不做, catfish 企业独有)
- **怎么迁** (6 步, 风险从低到高, 每步独立 sprint)
- **怎么判断半切成功** (5 个 KPI)

---

## 一、现状审视

### 1.1 catfish 9 个记忆模块

| 模块 | 数据存储 | 作用 | 大小 |
|---|---|---|---|
| `inject_session_history` | hermes state.db (读) | 注入最近 7 天 session 列表 | ~885 字节 |
| `inject_employee_journal` | `~/.catfish/employee_journal.md` | 注入跨 session 总结 | **670 KB → 5 KB (截 99%)** |
| `inject_session_facts` | `~/.catfish/session_facts.json` | 注入员工 `catfish_remember` 工具记的硬事实 | < 1 KB |
| `inject_session_meta` | `~/.catfish/session_meta.json` | "距上次 N 天" 时间元 | < 100 字节 |
| `inject_feedback` | `~/.catfish/feedback.jsonl` | 员工 👎/改 反馈 | ~500 字节 |
| `session_summarizer` (后台) | 写 employee_journal | LLM 总结结束 session | — |
| `memory_distill` (后台) | 写 `~/.catfish/distilled_facts.md` (今天上线) | LLM 蒸馏老 journal | ~1 KB |
| `conversation_compressor` | inflight 改 messages | 实时压缩长对话中段 | — |
| `tool_archive` | PG (or jsonl fallback) | 大 tool message 落盘 + 摘要 | 视员工活跃度 |

### 1.2 hermes 0.13 原生记忆 (Agent 看真源码确认)

| hermes 能力 | 数据 / 实现 | 我们用了吗 |
|---|---|---|
| `SOUL.md` (persona) | `~/.hermes/SOUL.md` | ✅ catfish identity_inject 读 |
| `USER.md` (画像) | `~/.hermes/USER.md` (agent memory tool 主动写) | ❌ **memory tool 之前被砍**, 12 天没动, 今早已修白名单 |
| `memories/*.md` (per-topic) | `~/.hermes/memories/*.md` | ❌ **同上, 19 天没动** |
| `trajectory_compressor` (压缩) | `protect_last_n_turns=4, target_max_tokens=15250` | ⚠️ catfish 自己写 conversation_compressor 重复造 |
| `state.db` + `messages_fts` (FTS5) | sessions / messages 表 + FTS5 virtual table | ⚠️ catfish 只读 state.db, FTS5 表不用 (tokenizer 不确定, 改用 LIKE) |
| `parent_session_id` 链 | sessions.parent_session_id | ❌ catfish 没用 (sessions 当线性 stream) |
| `MemoryProvider` plugin ABC | `plugins/memory/<name>/__init__.py` | ❌ catfish 从来没用 hermes plugin 系统 |

---

## 二、决策矩阵 — 砍 4 / 留 5

### 🔪 砍 (4 个) — 跟 hermes 重叠, 让 hermes 接管

| 模块 | 替代 | 砍后影响 | 风险评级 |
|---|---|---|---|
| 1. `session_summarizer` | hermes `memory` tool (agent 主动 add/replace/remove) | journal 不再后台生成 — 改成 agent 用 memory tool 写 USER.md | 🟡 中: hermes memory tool 复活才 1 天 (今早修白名单), 需观察 3-7 天数据再砍 |
| 2. `conversation_compressor` | hermes `trajectory_compressor` (源码完整, 参数公开) | 压缩逻辑搬到 hermes 侧, catfish gateway 不再实时压 | 🔴 高: catfish 自家压缩走 gateway loopback 享 fallback + quota tracking, 切 hermes 后失去这些. 必须先验证 hermes compressor 真在跑 + 跟 catfish 治理不冲突 |
| 3. `memory_distill` (今早刚上线) | hermes auto-consolidate (memory 满了自整理) | 不再后台 LLM 蒸馏 670KB journal | 🟡 中: hermes consolidate 没真实盘过, 不知道质量. 留 1 个月观察期再决定砍 |
| 4. `inject_session_history` 灌全文 (改 FTS5/LIKE 召回) | hermes state.db `messages_fts` (FTS5 表) | 我们今天已用 LIKE 替代, 但仍 catfish 自己实现. 真半切是改成调 hermes 的查询 API | 🟢 低: 今早已部分迁 (LIKE 召回), 完全切到 hermes 需等 hermes 暴露 query API |

### 💼 留 (5 个) — hermes 不做, catfish 企业独有

| 模块 | 为什么留 |
|---|---|
| 1. `inject_feedback` (`feedback.jsonl`) | 员工 👎/改 是企业产品独有信号, hermes 不收 (它是单用户 agent) |
| 2. `tool_archive` (PG 落盘) | 企业 audit / quota / 部门隔离需要. hermes 没 multi-tenant 关注 |
| 3. `inject_session_facts` (`catfish_remember` 工具) | 跟 hermes memory tool **互补不重叠** — 员工**显式**记的硬事实 vs hermes agent 主动记. 留双轨, 但**catfish 这条更明确** |
| 4. `inject_session_meta` ("距上次 N 天") | 客户 demo 体感强 ("它知道我昨天没打卡!"), hermes 不做 |
| 5. `inject_employee_journal` | **短期保留**, 等 hermes USER.md 跑稳 1 个月后再决定砍. 现在砍 = 真没记忆 (USER.md 才 846 字节) |

---

## 三、迁移顺序 (6 Step, 风险从低到高)

每 Step 独立 sprint, **不一口气推**. 完成 + 1 周观察期后才开下一 Step.

### Step 1: 完成 `MemoryProvider` ABC 包装现有模块 (已开始)

**目标**: 把现有 9 个 inject 模块全包成 `MemoryProvider` 实例, 但**逻辑不动** (只换接口). app.py middleware 改成调 `registry.inject_all()`.

**工程**:
- 已完成: `FeedbackProvider` PoC (`memory/providers/feedback.py`)
- 待包: 8 个 (`session_history` / `employee_journal` / `session_facts` / `session_meta` / `skills_catalog` / `identity` / `stats_guard` / `skill_guard`)

**工时**: 2-3 天
**风险**: 🟢 低 (零行为变化, 测试可证)
**回滚**: revert commit. 现有 inject 函数保留, 不删

---

### Step 2: 砍 `inject_session_history` 灌全文 → 完全切到 LIKE/FTS5 召回

**目标**: 我们今早已经做了一半 (LIKE 召回 + jieba), 继续做 FTS5 真集成.

**前置条件**: 验证 hermes 0.13+ state.db 的 `messages_fts` 真在生产环境工作 (不同 macOS sqlite 版本表现)

**工程**:
- 把 LIKE 实现改成 FTS5 优先 + LIKE fallback (中文场景 LIKE 仍优秀)
- 加 hermes `messages_fts_trigram` 表查询 (鸿波本机数据已有)
- 测试: 跨 sqlite 版本兼容性

**工时**: 2 天
**风险**: 🟡 中 (FTS5 中文分词跨版本不一致)
**回滚**: 单 module 切回 LIKE-only

---

### Step 3: 砍 `conversation_compressor` → 用 hermes `trajectory_compressor`

**目标**: 让 hermes 侧自己跑压缩, catfish gateway 不再实时压.

**前置条件**:
- 验证 hermes `trajectory_compressor` 真在 0.13 跑 (鸿波本机 audit 一次)
- 测 hermes 压缩走的是哪个 model (要走 gateway 还是 hermes 自己直调?)
- 如果 hermes 直调 → catfish quota tracking 丢, 这条不能砍

**工程**:
- 改 hermes 的 `ContextCompressor` config 让它走 gateway loopback (而非自己直接调 LLM)
- 把 catfish `conversation_compressor.py` 标 deprecated + 30 天后删

**工时**: 1 周
**风险**: 🔴 高 (跨 catfish 跟 hermes 边界)
**回滚**: 保留 catfish compressor 不删, 改 config 切回去

---

### Step 4: 砍 `memory_distill` (今早刚上线的) → 用 hermes consolidate

**目标**: hermes memory tool 满了自动 consolidate, catfish 不再后台 LLM 蒸馏.

**前置条件**:
- hermes memory tool 实盘 1 个月 (memory 工具白名单今早修, 等 6/15 数据)
- USER.md / memories/ 真在涨, 内容质量稳
- catfish journal 已经不再是模型的主要"长期记忆来源"

**工程**:
- 标 memory_distill deprecated
- distilled_facts.md 改成 hermes USER.md 的 read-only 镜像

**工时**: 3 天
**风险**: 🟡 中 (hermes consolidate 没实盘验证)
**回滚**: 改 1 行 env flag 重启 memory_distill

---

### Step 5: 砍 `session_summarizer` → 用 hermes memory tool

**目标**: hermes agent 主动用 memory tool 写 USER.md, catfish 不再后台扫整个 session 做 LLM 总结.

**前置条件** (跟 Step 4 重合):
- hermes USER.md 真在写 (内容质量好)
- catfish session_summarizer 跑出的 journal 跟 hermes USER.md 内容质量对比 — hermes 不输甚至更好

**工程**:
- 标 session_summarizer deprecated
- journal 文件保留 (历史归档), 不再后台写
- inject_employee_journal 改成兼容: hermes USER.md 在 → 用它; 否则 fallback 老 journal

**工时**: 3 天
**风险**: 🟡 中 (砍掉的是 catfish 长期对客户讲的"自动记忆" 卖点)
**回滚**: env flag 重启

---

### Step 6: 评估 `inject_employee_journal` 命运

**前置条件**: Step 5 跑稳 ≥ 1 个月

**决策树**:
- 如果 hermes USER.md 内容**完全覆盖** journal 价值 → 砍 inject_employee_journal, 改成 inject hermes USER.md
- 如果 hermes USER.md **有但不够** → 保留双轨 inject, 但 journal 改成 short summary (~1 KB)
- 如果 hermes USER.md **没起来** → 不砍 journal, 接受双轨永久

**工时**: 1 周
**风险**: 取决于上一步真实数据

---

## 四、半切**完成定义** (KPI)

不靠感觉. 5 个 KPI 必须满足:

| KPI | 当前 | 目标 |
|---|---|---|
| 1. 跨 session 记忆**写**的来源 | catfish session_summarizer 100% | hermes memory tool ≥ 70% |
| 2. system prompt **总 inject 字节** | ~15 KB (含 journal 5KB + history + facts + ...) | ≤ 10 KB (蒸馏 + 召回精炼后) |
| 3. **fan-out 调用** (1 chat 触发 inject 次数) | 1.x (今早修后) | ≤ 1.5 |
| 4. **记忆系统 LLM 调用** / chat | 1-3 次 (summarizer + compressor + distill) | ≤ 1 次 (只 hermes memory tool 自管) |
| 5. **代码行数** (memory 相关) | ~3000 行 (9 模块 + 测试) | ≤ 1500 行 (砍 4 个后) |

每 Step 后测 KPI, 没达标不开下 Step.

---

## 五、回滚策略

每 Step 必须:
1. **保留老代码** — `git tag pre-halfcut-step-N` 标位
2. **加 env flag** — `CATFISH_USE_HERMES_MEMORY=true/false` 一行回滚
3. **测试覆盖** — 老代码留下时, 测试也留下
4. **数据兼容** — `journal.md` 文件不删, 30 天后才清

如果某 Step 砍后客户反馈"它忘事了" → 立刻回滚, 30 分钟内. 不等下一 sprint.

---

## 六、未决问题 (鸿波需要拍板)

### Q1: hermes USER.md 多账号怎么办?

- catfish gateway 是 central server, 多员工共享
- hermes 是单 home dir, 一个员工一份 `~/.hermes/USER.md`
- 如果 catfish gateway 跟员工本机的 hermes 通信, 是怎么映射员工身份的?

**待鸿波回答**: 客户实际部署架构 — gateway 集中 / hermes 每员工本机, 数据怎么流?

### Q2: hermes 升级风险

- hermes 0.13 → 0.14 时 USER.md 格式 / state.db schema 可能改
- 我们半切后强耦合 hermes, 上游一动我们抖
- catfish 是不是该锁定 hermes 0.13 不升?

**待鸿波回答**: hermes 升级策略 — 跟随上游 / 锁版本 / 自维护 fork?

### Q3: NousResearch (hermes 上游) 关停风险

- hermes-agent 是 NousResearch 开源, MIT, 但不保证持续维护
- 如果上游停更, catfish 必须 fork 自维护
- 半切到 hermes 后, 这风险有多大?

**待鸿波回答**: 是否做"上游消失备份计划" — fork 一份 hermes 锁版本

### Q4: 客户 demo 故事

- 我们卖的是"鲶鱼自动学你, 一年比同事更懂你"
- 半切后这功能名义上"借 hermes 实现", 销售怎么讲?
- 客户看到 `~/.hermes/USER.md` 写"hermes 出品" 怎么解释?

**待鸿波回答**: 跟销售 + 客户 IT 对齐故事

### Q5: 砍 `conversation_compressor` 的真**收益**?

- 我们今天 5/15 还在修 BL-COMPRESS-BOUNDARY (Qwen 400 边界), 跟 hermes 同样的问题
- 砍它换 hermes compressor, 是不是也要踩一遍同样的坑?
- 还是 hermes 已经填好这些坑了?

**待鸿波回答**: 是否值得花 1 周做 Step 3, 还是接受 catfish 自己的 compressor 继续跑

---

## 七、推荐执行顺序

按"风险从低到高 + 客户体感影响从小到大":

```
本周 (5/19-5/23):
  ✅ Step 1 完成 (MemoryProvider ABC 包装现有 9 模块)
  - 工时: 2-3 天
  - 客户体感: 零变化
  - 验收: 测试全过 + 1 天本机 dogfood

下周 (5/26-5/30):
  ✅ Step 2 (FTS5 真集成)
  - 工时: 2 天
  - 客户体感: 召回精度小幅提升
  - 验收: 鸿波本机跑 100 次 chat 看召回质量

6 月上 (6/2-6/15):
  📊 **观察期** — hermes memory tool 实盘 1 个月
  - 每周 audit ~/.hermes/USER.md / memories/*.md 涨势
  - 跑 KPI 1 (跨 session 记忆**写**的来源占比)
  - 如果 hermes 那条没起来 → 修 hermes 触发条件, 不进 Step 3

6 月下 (6/16-6/30):
  ⚠️ Step 3-5 (砍 compressor / distill / summarizer)
  - 工时: 2 周
  - 客户体感: 最大风险点, 砍后客户可能感到"它变笨了"
  - 必须有回滚 env flag + 24 小时监控

7 月:
  📋 Step 6 (评估 employee_journal 命运) + 收口
```

---

## 八、风险评估总结

| 风险 | 等级 | 缓解 |
|---|---|---|
| hermes USER.md 没起来, 砍 catfish 后真没记忆 | 🔴 高 | 1 个月观察期, KPI 1 不达标不进 Step 3-5 |
| hermes 0.13 → 0.14 schema 变 | 🟡 中 | 锁定 0.13 + 单元测试每 hermes upgrade 跑 |
| NousResearch 停更 | 🟡 中 | fork 锁版本备份 |
| 半切后 catfish 治理 (quota/audit/RBAC) 丢失 | 🟡 中 | MemoryProvider Registry 包装层加 hook (BL-MEMORY-PROVIDER-ABC 已设计) |
| 销售故事被打乱 | 🟢 低 | 解释 "鲶鱼基于 hermes 加企业治理", 客户接受度高 |

---

## 九、鸿波 review checklist

请鸿波在签字前确认:

- [ ] 6 个 Step 的顺序对齐你的优先级
- [ ] 5 个 KPI 是否合理 (尤其 KPI 1 阈值 70%)
- [ ] 4 个砍 / 5 个留的决策没漏
- [ ] 5 个未决问题你愿意拍板的是哪几个
- [ ] 推荐执行顺序 (本周 Step 1, 下周 Step 2, 6 月观察) 是否接受
- [ ] 回滚 env flag (`CATFISH_USE_HERMES_MEMORY`) 设计是否够

签字 → 进 Step 1 真迁移 sprint.
