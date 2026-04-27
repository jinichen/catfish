# 鲶鱼 · Skill 生命周期管理

> **版本**: v1, 2026-04-27
> **拍板人**: 鸿波
> **用法**: 指导 SOUL.md / catfish-policy / catfish_skill_* 工具的设计.
> 任何对"skill 怎么建 / 怎么改 / 怎么废"的设计变更, 先回这里 align 框架.

---

## 一句话定调

> **Skill 不是写完就放在那, 是有完整生命周期的资产.**
> Plan → Create → Review → Use → Evolve 五阶段, 每阶段都有"质量门"防止脏 skill 污染员工的 catfish.

---

## 五阶段框架

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ 1 Plan   │ → │ 2 Create │ → │ 3 Review │ → │ 4 Use    │ → │ 5 Evolve │
│ 该不该建 │   │ 怎么建   │   │ 建后审   │   │ 用得咋样 │   │ 怎么改/废│
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
```

每阶段都可能产出"坏 skill"(失效 / 退化 / 误删 / 误建 / 淤积). 防御要每阶段都有.

---

## 阶段 1 · Plan (该不该建)

### 触发条件

**B 模式智能建议** (满足全部 3 条才主动建议):
1. **重复**: 员工最近 7 天做过 ≥ 3 次类似流程 (同域名 / 同 API / 同 4-5 步)
2. **无红线**: 不涉及发送邮件 / 删除数据 / 修改外部系统 / 读其他人数据 / 操作凭据
3. **员工没拒**: 之前没说 "算了" / "不要" / "下次别这么做" / "这是一次性的"

**立即模式** (不需要 ≥ 3 次):
- 员工 explicit 说 "存成 skill" / "把这个记下来" / "下次自动跑" / "保存这个流程"

### 反例 (不该建议)

- ❌ 员工 1 次提到的事情 — 单次表达不写
- ❌ 你"觉得这洞察很重要" — 你的觉得不算
- ❌ 涉及凭据 / 红线 — 直接拒绝
- ❌ 一次性任务 ("帮我 X 一下" — 做完就忘的, 不存)

### 防御

- ✅ SOUL "Skill 生成纪律" § 何时建议存
- ✅ SOUL B 模式三条件
- ✅ catfish-policy R6 (no-delete-catfish-skill — 防 LLM 误删现有 catfish skill)

### 已知坑

- 模型容易在"我突然有个洞察"时跑偏成主动建 skill, 实际员工没说过这事 — 三条件防御.

---

## 阶段 2 · Create (怎么建)

### 流程

1. 员工 yes 之后, **步骤先 quote 给员工 review** (不是一上来就调 skill_manage)
2. 员工再次确认 (yes / 改 / 算了)
3. 调 `skill_manage(action=create, ...)`
4. 创建后立即进入阶段 3 Review

### 内容铁律

✅ **只存"步骤模板"**:
- URL pattern (例: `https://feishu.example.com/expense/new`)
- DOM selector / @ref 路径
- 步骤 1-2-3 描述 (无具体数据)
- 决策树 / 失败降级
- 输入参数定义 (例: `amount`, `project_name` 留给员工每次填)

❌ **绝不存**:
- 密码 / API token / cookie / OAuth 任何凭据
- 员工的邮件正文 / 飞书消息 / 联系人列表
- 内网系统里的真实数据
- 员工 explicit 标了"不要存"的步骤

### 命名约定

- 按主题命名: `productivity/expense-submit` / `engineering/code-review-checklist`
- 不按"用户"命名 (避免 USER.md 那种混淆)
- catfish 平台自带的 skill 用 `catfish-` 前缀 (`catfish-email` / `catfish-browser-task` / 等)

### 防御

- ✅ SOUL "Skill 生成纪律" § 内容里绝不存的东西
- ✅ catfish-policy R7 (skill-create-no-secrets — 拦明文密码 / API key / token / 邮箱地址等敏感模式)

### 已知坑

- 模型容易把员工本次任务的具体数据 (报销金额 / 客户名 / 邮件正文) 写进 skill — R7 防御 + SOUL 教训.

---

## 阶段 3 · Review (建后审) 🚧 部分空白

### 应该做的

1. **创建后立即 dry-run** 一次 (不实际改外部系统, 走 `--dry-run` 或读模式), 给员工看效果
2. **失败立即回滚** skill (不留半成品在员工 skills/ 里)
3. **重复检查**: skill_manage 创建前先 `skill_view` / `skill_list` 看有没有相似的 (name 相似 / 触发词重叠)
   - 重叠 → 提示员工: "你已经有 X skill 干类似事, 是要改它还是真新建?"

### 当前覆盖度

- ✅ 步骤 quote 给员工 review (SOUL 已写)
- 🚧 **dry-run 验证还没做** (BACKLOG.md C8 待补)
- 🚧 **重复检查还没做** (BACKLOG.md 新增)

### 已知坑 (没 Review 会撞)

- ❌ skill 创建成功了但实际跑不通 (引用了不存在的工具 / DOM selector 错的 / URL pattern 漏掉参数)
- ❌ description 太空泛 / 写错触发词 → 模型乱触发
- ❌ description 太具体 → 永远不触发
- ❌ 重复造轮子: 已有相似 skill 又建一个差不多的, 互相抢触发

---

## 阶段 4 · Use (用了咋样) 🚧 全空白

### 应该做的

1. **审计事件流** (借鉴 IDEAS #20 EvolutionEvent): 每次 dispatch_tool 命中 skill 路径 → 写 `~/.hermes/skills/.audit.jsonl` 一行事件
   - 字段: `{ts, skill_name, action, ok, error, latency_ms, session_id}`
2. **Skill 健康面板**: catfish_today_summary 加 `skill_invocations_today` / `skill_failures_today` / `skill_unused_30d` 字段; LearningCard UI 显示
3. **30 天未用提醒**: 模型每周扫一次 audit.jsonl, 超 30 天没触发的 skill, 主动问员工: "X skill 30 天没用了, 还要留吗?" 员工 yes 留 / no 删
4. **失败率告警**: skill 失败率 > 30% 连续 1 周 → 主动建议员工"这 skill 最近老挂, 是公司流程变了吗? 我帮你重新过一遍"

### 当前覆盖度

- 🚧 **完全空白** — skill 调用次数 / 成功率 / 失败原因 都没记
- 优先级: P1 (BACKLOG.md C 段加)

### 已知坑 (没 Use 监控会撞)

- ❌ 员工 1 个月前存了 skill, **从来没用过**, 占 token 但没价值 (memory pollution)
- ❌ skill 触发 100 次失败 80 次, 但没人知道
- ❌ 大量"近似 skill"互相抢触发, 每次模型选错
- ❌ 公司流程变了, 旧 skill 失败率飙升, 员工骂"鲶鱼怎么变笨了"

---

## 阶段 5 · Evolve / Retire (改 / 废) 🚧 大部分空白

### 应该做的

#### Evolve (改)

1. **必须 quote diff 给员工 review** (catfish-policy R10)
2. **自动 backup 老版** 到 `~/.hermes/skills/<ns>/<skill>/.versions/<unix-ts>.md`
3. **版本号 bump**: SKILL.md frontmatter 加 `version: 0.3.2` 字段, 每次 update 自动 +0.0.1
4. **回滚机制**: 员工说 "回退" → 模型从 .versions/ 拿最近一版替换

#### Retire (废)

1. 员工 explicit 同意才 delete (R6 例外: 员工原话同意时允许)
2. delete 前 **backup 老版** 到 .versions/ 留作历史
3. **catfish-* skill 永远不能 delete** (这层 R6 不放过)

#### 共享 (P2 Skills Hub)

1. 员工想 push skill 到 Skills Hub → **员工自己 agent 夜间 dry-run 验证** (借鉴 IDEAS #21 分布式验证)
2. 验证通过才能共享, 防脏 skill 污染全公司

### 当前覆盖度

| 防御 | 状态 |
|---|---|
| **R10 update 必须 diff** | ✅ **今天加** (Layer 1 核心) |
| **自动 backup .versions/** (catfish_skill_backup tool) | ✅ **今天加** (Layer 1 核心) |
| 版本号系统 | 🚧 P2 (Skills Hub 同期) |
| Audit log (.audit.jsonl) | 🚧 P1 |
| 30 天未用提醒 | 🚧 P1 |
| 失败率告警 | 🚧 P2 |
| 共享分布式验证 | 🚧 P2 (Skills Hub 启动时) |

### 已知坑 (Evolve 空白会撞 — 最危险)

- ❌ **skill 退化**: 模型自作主张 update skill, 把好用的步骤改坏了, 没回滚
- ❌ 没版本: 改了多次后回不到任何已知好版本
- ❌ 公司流程变了, 旧 skill 还在跑, 失败率飙升
- ❌ 员工换岗了, niche skill 一直留着
- ❌ Skills Hub 上一旦共享脏 skill, 污染全公司

---

## 当前防御清单 (跨 5 阶段)

| 防御 | 阶段 | 在哪 | 状态 |
|---|---|---|---|
| B 模式智能建议 (≥3 次重复) | 1 | SOUL § Skill 生成纪律 | ✅ |
| 立即模式 (员工原话) | 1 | SOUL § 何时立即建 | ✅ |
| 内容里不存敏感 | 2 | SOUL § Skill 内容里绝不存 | ✅ |
| 步骤先 quote review | 2 | SOUL | ✅ |
| 命名约定 | 2 | SOUL § 命名约定 | ✅ |
| R6 防误删 catfish-* skill | 2/5 | catfish-policy | ✅ |
| R7 防敏感数据进 skill | 2 | catfish-policy | ✅ |
| **R10 update 必须 diff** | 5 | catfish-policy | ✅ **今天加** |
| **catfish_skill_backup tool** | 5 | tool-bridge native tool | ✅ **今天加** |
| dry-run 验证 | 3 | (待) BACKLOG | 🚧 P1 |
| 重复检查 | 3 | (待) | 🚧 P1 |
| Audit log | 4 | (待) | 🚧 P1 |
| 健康面板 | 4 | learning.rs / LearningCard | 🚧 P1 |
| 30 天未用提醒 | 4 | (待) | 🚧 P1 |
| 失败率告警 | 4 | (待) | 🚧 P2 |
| 版本号系统 | 5 | (待) | 🚧 P2 |
| 共享分布式验证 | 5+协同 | (待) | 🚧 P2 |

---

## 决策原则 (设计 skill 防御时遵守)

1. **每阶段都有质量门** — 不是只盯创建那一刻
2. **回滚比创新重要** — 员工要能"撤销" 你的所有 skill 改动
3. **审计可追溯** — 每个 create/update/delete/invoke 都有事件
4. **Reactive 不 Proactive** — 跟员工反复确认每一步, 不替员工拍板
5. **Open by default, locked by policy** — 默认让员工自由, 红线在 policy 拦
6. **Hub 共享前先验证** — Skills Hub 共享必经 dry-run 网关 (P2)

---

## 跟其他文档的关系

```
STRATEGY.md             ─→  开闭分界 (Skills Hub 闭源, 设计在 STRATEGY)
SOUL.md "Skill 生成纪律" ─→  阶段 1-2 + 阶段 5 部分纪律 (实施层)
catfish-policy rules    ─→  R6 (防删) / R7 (防敏感) / R10 (强制 diff) 红线
catfish_skill_backup    ─→  阶段 5 backup 工具 (今天加)
SKILL-LIFECYCLE.md (本)  ─→  5 阶段框架, 元文档, 前面三个改动都引用本文
BACKLOG.md              ─→  P1/P2 待补的防御项追踪
```

---

## 决策签名

> 此文档代表 2026-04-27 的 Skill 生命周期管理框架.
>
> 修改本文需要主理人 (鸿波) 显式同意.
>
> **凡是设计 skill 相关防御 (SOUL / policy / native tool / UI), 都先回这里 align 阶段定位再实施**.
