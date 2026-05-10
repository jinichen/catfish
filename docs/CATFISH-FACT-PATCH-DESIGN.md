# Catfish 事实补丁系统设计草案 (BL-Q3-FACT v0.1)

> **状态**: 设计草案 (5/10 鸿波 a16z 文章读后定调) · 未启动实施
> **作者**: catfish 团队
> **目标启动**: Q3 (2026-07+, 5/14 demo + 6 月数据反馈后正式立项)
> **核心论断**: "常变是央企/政府的常态" — 通用 LLM 永远卡在训练 cutoff,
> catfish 的护城河是**白盒、可审计、可追踪**的事实跟踪能力

## 一、背景与问题域

### 1.1 客户场景的"常变"

央企、政府、大型国企的日常工作流跟硅谷 SaaS 客户根本不在一个频段。后者的"流程"半年才动一次，前者每个季度都至少有一轮变更：

- **国家级标准**: ISO27001、等保 2.0、个保法实施细则、网安法配套规定每年都有版本号迭代或解释口径调整
- **行业级规范**: 国资委每季度发文、银保监 / 证监会 / 工信部按主题密集发文、央企"两金"压降目标每年调
- **公司级制度**: 财务报销标准、采购流程、信息安全红线、合规审计口径，至少每半年一轮"小修订"
- **人事级规则**: 部门重组、职级晋升、考核口径、对外签字权限边界，每年都改

这不是个别客户问题，**这就是这类客户的本质特征**。

### 1.2 通用 LLM 永远解不了

ChatGPT / Claude / Gemini / DeepSeek / Qwen 等通用大模型有两个无法绕过的硬约束：

1. **训练数据 cutoff**: 模型权重是某个时间点的快照。即使 RAG 接外部知识库，也只是查询时的"临时上下文"，模型本身没有"时间感"。
2. **不知道客户内部规则**: 通用 LLM 训练数据里没有"贵公司财务报销新标准 v3.2.1"，员工在系统提示里塞 5000 字制度全文 LLM 也只能"看一眼记一会"。

这是 a16z 那篇 *Why We Need Continual Learning* 论文的核心论断："retrieval is not learning"。但 a16z 提的解法（改 LLM 权重）在央企场景**不可行** — 客户用的是自有部署或第三方 API，权重不在 catfish 手上，且权重一旦自动学习就丧失审计性，央企合规过不了。

### 1.3 catfish 当前 skill 体系的暗坑

catfish 的 skill 系统是按"某时间点的最佳实践"固化的：

```
skill/                                  例子: skill_run_briefing.md
├── SKILL.md                             "公司周报按周一 8:00 模板提交"
└── script.py                            (固定的截止日期 + 模板路径)
```

如果某个月公司把"周报模板"改了，但 skill 没人更新，会发生：

1. 员工在鲶鱼这边正常说"帮我写本周周报"
2. 鲶鱼调 `skill_run_briefing` skill
3. skill 按**老模板**生成
4. 员工提交 → 被领导驳回 → 员工抱怨 catfish 不准
5. 鲶鱼信任崩溃，用户流失

这是 a16z 文章列的"Logical integration failure"在 catfish 场景的具象化：**事实更新没有传播到下游 skill**。

### 1.4 现状下的应对（不充分）

catfish 当前能部分缓解但解不彻底：

- **BL-MM13/14 catfish_propose_skill_revision**: 员工反馈"skill 输出怪怪的"后，LLM 自检 audit log 提改进。但这是**反应式**的 — 政策已经变了一段时间、员工踩坑后才触发，不是预防性的。
- **BL-MM15 改进有效性 14 天追踪**: 改完后跟踪有没有变好。但前提是有人提改进。
- **SkillsHub publish/订阅**: 员工可以分享 skill，但分享之后 skill 跟原作者解耦，原作者改了别人也不知道。

**真正缺的是"事实变了 → 自动找出受影响 skill → 主动推改进 → 走合规审批"的闭环。**

## 二、价值主张

### 2.1 一句话定位

> **catfish 是企业里唯一让"标准/政策变更"自动同步到员工日常工作流的系统**

强调三个词：

- **企业里唯一**: 通用 LLM、公司知识库、Notion / 飞书文档系统都没这个能力（它们要么不知道客户内部规则，要么知道但传不到员工日常使用工具）
- **自动同步**: 不靠员工记忆、不靠合规部门一份份发邮件强调
- **白盒可审**: 改了什么、为什么改、谁批的、改完效果如何，全程留痕

### 2.2 跟 a16z 文章的理论对齐

| a16z 文章论点 | catfish 落地 |
|---|---|
| "Continual learning 的本质是 compaction（压缩经验进系统）" | catfish 把"政策变更"压缩进 skill 文件改写 |
| "Module 层（adapter / KV cache / 外挂 module）是不动权重的中庸路线" | catfish skill 就是天然的"白盒 module"，事实补丁就是"重写 module" |
| "Weight 层学习的最大障碍是 auditability 丢失" | catfish 不动权重，改 skill 走人工审批 → auditability 完整保留 |
| "用户要 competence 不要 recall" | 员工不需要知道"政策第 3 条第 5 款改了什么"，只需要鲶鱼的输出仍然对 |

### 2.3 为啥这是真护城河

通用 LLM 厂家不会做这个，因为：

1. **他们不知道你公司的政策** — ChatGPT 没法判断"贵司新版财务流程"对哪些员工 workflow 有影响
2. **他们没"在客户公司里运行"的概念** — 训练 - 部署 - 推理三段式，客户内部知识不进训练
3. **他们做了也卖不出去** — 通用厂家的卖点是"通用能力"，不是"贴贵司流程"

国内做企业知识库的厂家（飞书 / 钉钉 / 蓝凌）会试图做，但他们卡在另一头：

1. **他们没"员工日常工具"** — 知识库里有政策但员工日常用的是别的工具（OA/邮件/IM），新规生效到员工执行有断层
2. **他们没"白盒 LLM workflow"** — 知识库 + LLM 拼起来是"对话式查询"，不是"自动改员工 skill"

catfish 站在中间这个真空地带，**两头都做** — Companion 是员工日常工具（占用员工时间）+ skill 是白盒 LLM workflow（可改可审），所以事实补丁能做成闭环。

## 三、用户故事

### 3.1 合规专员小李（主要触发者）

> 国家网信办上周发了《数据出境安全评估办法》修订征求意见稿，小李的工作是把这个新规"落实到公司各业务线"。
>
> 以往：发邮件群发全公司 → 80% 员工不点开 → 几个月后真出事故才发现没人执行。
>
> 接 catfish 之后：小李在 catfish-web 后台上传文件 → 鲶鱼分析 → 标出"全公司有 17 个 skill 涉及数据导出，其中 5 个跟新规冲突" → 自动生成改进 patch → 小李审批 → 员工 Companion 端立刻装到新版 skill。
>
> 小李的核心诉求：**让我知道改了之后到底落实到哪里、有几个员工真的跟着改了**。

### 3.2 业务经理老王

> 老王是采购部经理。公司财务把"5 万以上需总监审批"改成了"3 万以上需总监审批"。
>
> 以往：老王在部门群发通知 → 5 个员工里 3 个真改习惯，2 个忘了。一个月后审计抓出 2 单"超额没审批"。
>
> 接 catfish 之后：财务部门把新规上传 → catfish 自动找出 `skill_send_purchase_request` 这个采购部常用 skill，里面写着"5 万以下走快速通道" → 老王收到 SkillRevisionCard 提示"这个 skill 受新规影响，需改成 3 万" → 一键采纳 → 部门所有装了这 skill 的员工自动同步。
>
> 老王的核心诉求：**让 5 个员工不用我一个个盯**。

### 3.3 员工小赵

> 小赵是研发，平时用 catfish 写技术报告。某天他用了一个 skill 输出的报告，被领导批"格式不是新版的"。
>
> 以往：小赵不知道哪份格式是"新版"，靠自己问同事。
>
> 接 catfish 之后：小赵 Companion 顶部 LearningCard 出红点提示"你装的 3 个 skill 因为公司模板更新可能受影响，建议升级"。点开看 diff，懂了就采纳，不懂的找合规问。
>
> 小赵的核心诉求：**别让我自己去猜什么变了**。

### 3.4 sysadmin 陈鸿波

> 鸿波是 catfish 系统超级管理员，关心全公司"政策同步覆盖率"。
>
> 在 catfish-web `/admin/system` 里能看到：本季度 12 次政策变更触发，影响 47 个 skill，41 个已采纳改进，6 个还在 pending。
>
> 鸿波的核心诉求：**全公司 skill 跟新规对齐情况一眼看清**。

## 四、系统架构

### 4.1 四层组件

```
┌──────────────────────────────────────────────────────────────────┐
│  ① 触发层 (Trigger)                                              │
│   - 手动: catfish-web /admin/facts 上传文件 (PDF/Word/MD/邮件)   │
│   - 自动 (P1+): mcp 接公司知识库, 定期 diff 政策文档             │
│   - 自动 (P2+): 关注政府门户 (国资委 / 工信部 RSS) 自动同步      │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│  ② Diff 引擎 (Diff Extractor)                                    │
│   - 解析变更文件 → 提取 "事实点" 列表                            │
│   - 例: "数据出境必须做安全评估" / "采购阈值 5万 → 3万"          │
│   - 输出: fact_change 记录                                       │
│   - 实现: LLM 走结构化 prompt + 现有 file_parse 工具             │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│  ③ 影响分析 (Impact Analyzer)                                    │
│   - 对每个 fact_change, 找出可能受影响的 skill                   │
│   - 三路并行检索:                                                │
│     a. SkillsHub 全文 grep (关键词匹配)                          │
│     b. 向量检索 (BL-L26 BM25 sidecar 同款)                       │
│     c. LLM 语义判断 (高精度但慢, 只跑前 50 条候选)               │
│   - 输出: skill_impact 记录 (skill_id, 信心度 0-1, 影响理由)     │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│  ④ 重写引擎 (Patch Generator)                                    │
│   - 对每个高信心 skill_impact, 让 LLM 生成改进 patch             │
│   - 复用 catfish_propose_skill_revision 工具 (BL-MM13)           │
│   - prompt 增强: 把 fact_change 上下文塞进 prompt                │
│   - 输出: skill_revision_proposal 记录 (跟现有 BL-MM14 同表)     │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│  ⑤ 审批 & 落地 (Approval Pipeline)                               │
│   - 进 SkillRevisionCard (Companion 员工视角)                    │
│   - 进 catfish-web /admin/fact-patches (合规视角全局)            │
│   - 走现有 sysadmin 审批节点 (BL-ARCH1 P1)                       │
│   - 落盘后 BL-MM15 14 天追踪有效性                               │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 跟现有组件对应表

| 新增组件 | 复用现有 | 新建工作量 |
|---|---|---|
| ① 触发层 (手动) | catfish-web /admin/users 同模板 | 中 (~3 天: 上传 UI + 预览) |
| ② Diff 引擎 | file_parse (BL-D17) + LLM 调用 | 中 (~3 天: 结构化 prompt 调优) |
| ③ 影响分析 (grep+BM25) | BM25 sidecar (BL-L26) + SkillsHub 元数据 (BL-D2) | 小 (~1 天) |
| ③ 影响分析 (LLM 语义) | gateway 现有 LLM 调用 | 小 (~1 天) |
| ④ 重写引擎 | catfish_propose_skill_revision (BL-MM13) | **零** (直接复用) |
| ⑤ 审批 | SkillRevisionCard (BL-MM14) + sysadmin 审批 (BL-ARCH1 P1) | 小 (~2 天: 区分 fact 触发 vs 员工反馈触发) |
| ⑤ 追踪 | BL-MM15 14 天有效性 | **零** (直接复用) |

**P0 总工作量估算: 1.5-2 周** — 大部分是复用，新建主要是触发 UI + 影响分析。

### 4.3 数据流（一次完整流程）

以"采购阈值 5 万 → 3 万"为例：

```
[小李在 /admin/facts 上传 "公司财务流程修订 v3.2.docx"]
         ↓
[Diff 引擎] LLM 解析 → fact_change 记录:
  {
    id: "fact-2026-q3-001",
    title: "采购总监审批阈值调整",
    summary: "5 万以上 → 3 万以上需总监审批",
    source_file: "公司财务流程修订 v3.2.docx",
    effective_date: "2026-09-01",
    keywords: ["采购", "总监审批", "审批阈值", "5万", "3万"],
    raw_quote: "原条款 4.2.1: 采购金额 ≥ 5 万元..."
  }
         ↓
[影响分析]
  - grep "采购" "审批" "5万" → 23 个 skill 候选
  - BM25 → 排序前 10
  - LLM 语义 → 5 个真受影响:
    * skill_send_purchase_request (采购部高频)  信心度 0.95
    * skill_quarterly_budget_review            信心度 0.78
    * ...
         ↓
[重写引擎] 对每个受影响 skill:
  catfish_propose_skill_revision(
    skill_path="skill_send_purchase_request",
    context_fact="采购总监审批阈值 5 万 → 3 万",
    raw_quote="..."
  )
  → patch:
    diff:
      - 当采购金额 >= 50000 时, 必须走总监审批
      + 当采购金额 >= 30000 时, 必须走总监审批
    rationale: "依据公司财务流程修订 v3.2 第 4.2.1 条 (effective 2026-09-01)"
         ↓
[审批]
  - SkillRevisionCard 出现在采购部所有装了这 skill 的员工 Companion 上
  - catfish-web /admin/fact-patches 给小李看全局: 5 patches pending, 0 approved
  - 小李一键 "全部采纳" → 走 sysadmin 审批 → 落 SkillsHub 新版本
         ↓
[追踪]
  - BL-MM15 14 天监测各 skill 调用质量分数
  - 9/15 看到分数: 4 个 skill 改进后分数 ↑, 1 个 ↓ → 自动建议回退那个
```

## 五、数据模型

### 5.1 新增 PG 表（4 张）

跟 BL-D2 Phase 2 / BL-ARCH1 P1 同 PG 实例，加 `fact_*` 前缀避免冲突：

```sql
-- 一次政策变更
CREATE TABLE fact_changes (
    id              TEXT PRIMARY KEY,           -- 例 fact-2026-q3-001
    title           TEXT NOT NULL,
    summary         TEXT NOT NULL,              -- 一句话
    source_file     TEXT,                       -- 原文件名
    source_path     TEXT,                       -- ~/.catfish/facts/<id>/<file>
    effective_date  DATE,                       -- 政策生效日期
    keywords        JSONB,                      -- ["采购", "审批", ...]
    raw_quotes      JSONB,                      -- 原文摘录数组
    uploaded_by     TEXT NOT NULL,              -- email
    uploaded_at_ms  BIGINT NOT NULL,
    status          TEXT NOT NULL,              -- pending/analyzing/published/dismissed
    extracted_facts JSONB                       -- LLM 解析出的多条事实点
);

-- 一个 skill 受一个 fact_change 影响的关联记录
CREATE TABLE fact_skill_impacts (
    id                TEXT PRIMARY KEY,
    fact_change_id    TEXT REFERENCES fact_changes(id),
    skill_namespace   TEXT NOT NULL,
    skill_name        TEXT NOT NULL,
    skill_version     TEXT NOT NULL,
    confidence        REAL,                     -- 0-1
    detection_method  TEXT,                     -- grep/bm25/llm
    impact_reason     TEXT,                     -- LLM 判断理由
    created_at_ms     BIGINT NOT NULL,
    UNIQUE (fact_change_id, skill_namespace, skill_name, skill_version)
);

-- 由 fact_change 触发的 skill_revision (跟现有 skill_revisions 表关联)
-- 现有 skill_revisions (BL-MM14) 表加一列 trigger_source:
ALTER TABLE skill_revisions ADD COLUMN trigger_source TEXT DEFAULT 'user_feedback';
ALTER TABLE skill_revisions ADD COLUMN fact_change_id TEXT
  REFERENCES fact_changes(id);

-- 全局 fact 操作审计 (合规要)
CREATE TABLE fact_audit (
    id              BIGSERIAL PRIMARY KEY,
    ts_ms           BIGINT NOT NULL,
    action          TEXT NOT NULL,              -- upload/analyze/approve/reject/...
    fact_change_id  TEXT REFERENCES fact_changes(id),
    by_user         TEXT NOT NULL,
    meta            JSONB
);
```

### 5.2 现有表的扩展

```sql
-- skill_revisions (BL-MM14) 加触发源, 区分员工反馈 vs 政策触发
ALTER TABLE skill_revisions
  ADD COLUMN trigger_source TEXT DEFAULT 'user_feedback'
  CHECK (trigger_source IN ('user_feedback', 'fact_patch', 'self_critique'));
```

## 六、实施阶段

### 6.1 P0 (Q3 第 1-2 周)：手动触发 + 单 skill diff + 复用审批

**范围**:
- IT/合规人员**手动上传**变更文件 (不做自动扫)
- 单 skill 独立分析 (不做跨 skill 依赖图)
- 走现有 SkillRevision 审批流 (sysadmin 审批节点直接复用)
- catfish-web `/admin/facts` 三个页面: 上传 / 列表 / 详情

**交付物**:
- PG migration: 4 张新表
- catfish-web 3 个新路由 + Card 组件
- gateway 3 个新 endpoint: `/api/facts/{upload, analyze, list}`
- skills-hub 1 个新 endpoint: `/skills/impact-search` (给影响分析用)
- 文档: 客户使用手册一份

**估时**: 1.5-2 周 (复用度 70%+)

**5/14 demo 必备演示物**: 即使没真做 P0，也要演示**底座** (skill_revision + 14 天追踪 + sysadmin 审批) 让客户看到 "P0 启动后无非加触发入口"。

### 6.2 P1 (Q3 第 3-4 周)：自动扫 + 跨 skill 依赖

**范围**:
- 接 mcp 通道扫公司内部知识库 (Confluence / 飞书 / SharePoint) 定期 diff
- 跨 skill 依赖图: skill A 引用 skill B 输出，B 受 fact 影响时联动标记 A
- 影响分析升级: 加"历史调用频率"权重 (高频 skill 优先)

**估时**: 2-3 周

### 6.3 P2 (Q4)：合规专属审批 + 政府门户同步

**范围**:
- 合规部门专属审批 workflow (跟 sysadmin 普通审批分开)
- 法律风险标注: skill 改动是否涉及合规红线 (调政策插件 BL-D7)
- 政府门户 RSS 自动同步 (国资委、工信部、地方政府)
- 跨公司 skill federation (Plan D BL-M4.1) 联动: 我公司的 fact_change 不会污染 Bob 公司的 skill

**估时**: 3-4 周

## 七、跟现有系统复用关系

| 现有系统 | 提供给 FACT | 是否需改 |
|---|---|---|
| skill_revision (BL-MM13) | LLM 重写工具，直接复用 | 否 |
| SkillRevisionCard (BL-MM14) | UI 审批界面 | 微调 (加 fact 触发标识) |
| 14 天有效性追踪 (BL-MM15) | 落地后追踪 | 否 |
| SkillsHub (BL-D2) | skill 元数据 + 全文检索源 | 微调 (加 impact-search endpoint) |
| BM25 sidecar (BL-L26) | 影响分析向量检索 | 否 |
| sysadmin 审批 (BL-ARCH1 P1) | 合规审批节点 | 否 |
| catfish-web /admin (BL-ARCH1) | 后台界面框架 | 加 /admin/facts 路由 |
| mcp registry (BL-D3) | 接公司内部知识库的通道 (P1+) | 否 |
| file_parse (BL-D17) | PDF/Word 解析 | 否 |
| 政策插件 BL-D7 | 法律风险判定 (P2+) | 否 |

**复用度评估**: P0 阶段 70%+ 直接复用，新建主要是触发 UI + 影响分析逻辑 + 数据模型 schema。

这正是 catfish 现有架构的设计回报 — skill_revision、SkillsHub、sysadmin 审批这些组件**单独看价值有限**，组合在一起承载 fact patch 时**价值放大 10 倍**。

## 八、风险与开放问题

### 8.1 误判风险（False Positive）

skill 没真受影响也被标了 → 浪费合规人员审核时间。

**缓解**:
- 三路检索 (grep + BM25 + LLM) 取交集，不取并集
- 每个 impact 带 confidence 分数，低于阈值不进审批队列
- 合规人员可以"驳回"，驳回信号反哺到分析模型 (类似 BL-MM6 feedback)

### 8.2 漏判风险（False Negative）

真受影响的 skill 没标到 → 政策没落实，合规事故。

**缓解**:
- 兜底机制: skill 调用时打 fact_change 标签，运行时检测"我用了 X skill 但是 Y fact 之后没更新过它" → 主动提示员工
- 14 天追踪: BL-MM15 监测 skill 输出质量，掉分异常的会被独立排查

### 8.3 政策文件解析准确性

LLM 把 "5 万元" 错解成 "5 千元"，patch 全错。

**缓解**:
- 解析后必经"小李确认"环节 (P0 不让 LLM 自动落)
- 关键数字提取走结构化 schema，不只 LLM free text
- 跟原文件 raw_quote 强绑定，审批时小李能对照原文看

### 8.4 LLM 重写的事实性 (Hallucination)

LLM 改 skill 时编造一个不存在的子流程。

**缓解**:
- 改写 prompt 强制"只能基于 fact_change.raw_quote 改"，不允许引入文件外知识
- 改完跑 self-check (再调一次 LLM 验证 patch 跟原 fact 对齐)
- 必经人工审批，不允许自动落盘

### 8.5 审批堵塞

合规小李一周才看一次系统，导致 patch 堆积，员工还在用老 skill。

**缓解**:
- 加紧急度标识 (生效日期临近优先)
- 邮件 / IM 推送 (复用 BL-D5 messaging)
- 临时锁定: 已识别受影响的 skill 在 patch 落定前自动加红色 banner "可能受 X 政策影响，使用前确认"

### 8.6 跨公司 federation 污染

Plan D BL-M4.1 跨公司 skill 共享场景下，A 公司的 fact_change 不能污染 B 公司装的 skill。

**缓解**:
- fact_change 永远 scope 到单公司，不进 federation
- 共享 skill 改写时分叉成"私有版本"留在本公司

### 8.7 开放问题

- **变更频率上限**: 一周 50 个政策变更扛不扛得住？需要排队 / batch 处理?
- **多语言**: 英文政策能不能同样解析？海外子公司场景。
- **历史回溯**: 客户能不能问"我们公司过去半年有哪些 fact 变了"？需要时间序列查询。
- **量化效果**: 怎么向客户证明 fact patch 真的减少了合规事故？需要"事故归因"机制。

## 九、商业策略

### 9.1 现在不动手的理由

- **5/14 demo 主线**: 当前 BL-ARCH1/ARCH2/VOICE2/VOICE3 全是 ship 完待真实使用观察的状态，需要 5/11-5/13 三天稳定期
- **客户期望管理**: 客户上周看了"想用"，鸿波想拖一阵，**不是为了拖延**，是为了让产品再打磨一轮再放出来收第一批合同
- **设计先行价值**: 这份文档本身就是延期期间的产出，给客户看"我们在做精细化设计"比"我们在写代码"更有说服力

### 9.2 商业定价方向

FACT 系统天然适合作为**合规模块单独定价**，不进基础订阅：

- **基础包**: Companion + skill + SkillsHub + Web 后台 (本文档之外的所有现有功能)
- **合规包 (FACT)**: 事实补丁 + 跨 skill 依赖 + 政府门户 RSS — 按"政策变更 × 影响 skill 数"或固定包年定价
- **企业包 (Plan D federation)**: 跨子公司 skill 共享 — 按子公司数定价

这种分层定价既能让基础包卖得便宜降低进入门槛，又能让有合规刚需的大客户为高价值模块付溢价。

### 9.3 客户期望管理 (5/14 demo 怎么讲)

如果客户问"什么时候能用"：

> "P0 我们 Q3 启动，4 周交付。但底座今天就在跑 — 您看 LearningCard 这个 skill_revision 体系，14 天追踪有效性这个机制，sysadmin 审批节点，这些都是 FACT 系统的下层，已经稳定运行。Q3 我们补一个'政策上传 + 影响分析'入口，闭环就完整了。
>
> 我们不急着今天就给您演示半成品，是因为这是央企合规级别的功能，必须做扎实再上。先给您看完整设计文档（递这份 .md），看完后您评估这个方向跟贵司合规需求是否匹配，匹配的话 Q3 联合试点。"

**关键传递的信号**:
1. 我们已经想清楚了 (不是空头支票)
2. 大部分基础设施已就绪 (不是从零开始)
3. 我们尊重合规级别的严肃性 (不急着 ship 半成品)
4. 我们想让您参与设计 (Q3 联合试点 = 客户深度绑定)

## 十、5/14 Demo 演示脚本

如果客户在 demo 现场问到"以后政策变了 skill 不就过时了吗"：

**不要**回答："Q3 我们会做..."（空头支票）
**要**回答（按这个剧本）：

1. "对，您问到了 catfish 跟通用 LLM 的核心差异 — 通用 LLM 训练数据停在某个时间点，永远没法跟上贵司变化。"
2. "catfish 在底层做了一套**白盒持续学习**机制。我演示给您看 —" *(打开 LearningCard)*
3. "这是某个员工昨天提的一个 skill 改进建议（pending）。我点'采纳'之后 —" *(点采纳)*
4. "改进就进入 14 天有效性追踪（指那个监测条），如果实际效果没改善，鲶鱼会主动建议回退。"
5. "现在的触发是员工**反馈**。Q3 我们加一个**主动**入口：合规部门把新规丢进来，鲶鱼自动找出哪些 skill 受影响，自动生成改进给您审批。"
6. *(打开这份设计文档的架构图)* "整个架构 70% 是已经在跑的，Q3 加 30% 就完整。"
7. "这就是为什么我们说 catfish 是**会成长的实习员工**，不是接 LLM 的工具 — 通用 LLM 永远是 15 岁天才不知道贵司规矩，catfish 是来贵司实习的，会跟着贵司一起更新。"

这个剧本的核心结构: **从可演示的现状 (LearningCard) → 推到 Q3 路线 (FACT) → 升华到产品定位 (会成长的伙伴)**。

## 十一、关键决策记录

| 决策 | 时间 | 由谁 | 理由 |
|---|---|---|---|
| 启动 BL-Q3-FACT 设计 | 5/10 夜 | 鸿波 | 看 a16z continual learning 文章, 认为这是央企真痛点 |
| 不立刻实施 | 5/10 夜 | 鸿波 | "想再拖一阵", 用空档做精细化设计 |
| 不动 LLM 权重 | 5/10 夜 | 共识 | 央企 auditability 边界 + 客户用自有模型权重不在我们手上 |
| 走 module 层 (skill 改写) | 5/10 夜 | 共识 | catfish 现有 skill_revision 体系天然契合, 70% 复用 |
| 单独定价 | 5/10 夜 | 鸿波 | 合规模块价值高, 应分层定价收溢价 |

---

## 附录 A: 快速 FAQ (内部对齐用)

**Q: 为啥不直接跟通用 LLM 厂家合作让他们 RAG 公司知识库?**
A: 通用 LLM RAG 是查询时拉政策文档让 LLM 临时看一眼, **不会**主动找出"哪些已固化的 workflow 受影响"。catfish skill 是**已固化的 workflow**, FACT 系统是 workflow ↔ 政策的双向连接。

**Q: 那像飞书 / 钉钉这种企业知识库不是也能做?**
A: 他们没"白盒可执行 workflow"概念。他们的知识库 + LLM = 对话式查询, 不会改员工日常工具。catfish 是改员工日常工具 (Companion 是员工日常打开 N 次/天的工具)。

**Q: 客户能不能自己用我们的 skill_revision 工具?**
A: 能, 但只是 BL-MM13 反应式触发 (员工反馈了才动)。FACT 是主动式触发 (政策变了就主动找)。是质的差异。

**Q: 这事 OpenAI / Anthropic / DeepSeek 也能做啊?**
A: 不能。他们要做必须深入客户公司, 知道客户内部所有 workflow 和政策文档, 这是 catfish 商业模式的本身, 不是他们的 (他们卖通用 API)。他们要进这个赛道等于变成做企业 SI, 跟自己定位冲突。

**Q: 我们多久能赶在通用 LLM 厂家做这事之前?**
A: 时间窗口判断: 通用 LLM 厂家不会做企业 SI, 这是结构性壁垒。catfish 反而要担心的是国内 toB 厂家 (蓝凌 / 致远 / 用友) 的方向转型, 但他们 LLM 工程能力跟不上。综合判断窗口期 18 个月+。
