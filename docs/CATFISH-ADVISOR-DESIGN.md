# CATFISH-ADVISOR-DESIGN — 智能参谋 (Phase 7)

> **状态**: 设计稿. 鸿波 5/21 拍板, 待 review 后开工.
> **替代**: `docs/BL-WORKPLAN-DESIGN.md` (Phase 6, 标 DEPRECATED)
> **生效约束**: `docs/CENTRAL-EDGE-DATA-BOUNDARY.md` (新代码全部在 edge/, 不进 central/)

---

## 0. 为什么推翻 Phase 1-6

5/21 鸿波三次点中同一件事:

> "需要的是能够干活的秘书, 不是堆数据"
> "单点就会出现信息断链, 永远都达不到效果"
> "你为什么一直在切断信息的连接"

回头看 Phase 1-6 全错在哪:

| Phase | 形态 | 错在哪 |
|---|---|---|
| 3 (PriorityList) | tier 1/2/3 ranked list | 排数据让你看, 没建议 |
| 5 (Decision) | 主线 + 证据 + 行动 + 风险 | 描述决策框架, 仍不给操作 |
| 6 (Workplan) | 周/日 × 内容/事件/建议 | "建议"是单条文字, 没结构化选项; 7 数据源平铺 prompt, LLM 出总结而不是干活 |

**共同病根**: 把 catfish 定位成"信息呈现工具", LLM prompt 引导"出总结". 排版怎么改都解决不了 — 因为**catfish 该做的不是呈现信息, 是替你想清楚 + 准备好 + 给选项让你 1 分钟拍板**.

Phase 7 推翻这条假设. 重新定位: catfish = 智能参谋, 不是排版工具.

---

## 1. catfish 新定位

### 1.1 角色定义

**智能参谋**. 不是代理执行人.

- 替你**想到** — 看完全部信息, 关联推理, 识别出今天 N 件最重要的事
- 替你**准备** — 邮件草稿 / 汇报材料 / 催办名单 / 数据收集, 都预先做到 80%
- 替你**列选项** — 每件事 2-3 个处理口径, 标注利弊 + 风险
- **绝不替你拍板** — 任何级别都不代行 (5/21 鸿波 3 拍)
- 央国企特殊层 (政治 / 合规 / 上行下达) 也只**给建议选项**, 不替你判断 (5/21 鸿波 4 拍)

### 1.2 KPI

不是"做了多少事", 是"**你节省了多少决策成本 + 避免了多少风险**".

具体指标:
- 早安打开到合上的平均时长 → 短为好 (秘书已准备到位, 你 5 分钟看完拍 3 件)
- 主菜命中率 → 你最终处理的事跟 catfish 列的主菜匹配度 (高为好)
- 草稿采用率 → catfish 起草的回信你点"用这个口径" 而不是"自己重写" 的比例
- 错误标记率 → 你点"这条 catfish 判断错了" 的频次 (低为好)

### 1.3 强约束 (不能违反的红线)

1. **任何级别都不代行**: 不替发邮件 / 不替接受会议 / 不替签字 / 不替拍板
2. **草稿存到 outputs/, 不直接发**: 邮件草稿写 `~/.catfish/outputs/<date>/reply-*.md`, 让你**点开看 / 复制 / 修改后自己发**
3. **决策留痕**: 你最后选了哪个口径写进 `~/.catfish/decisions.jsonl`, 下次类似事可引用 (但仍由你拍, 不自动按上次)
4. **判断透明**: 任何 catfish 给的"建议"必须能展开看依据 — 用了哪些信息, 引用了哪条历史决策, 为什么倾向哪个口径
5. **央国企特殊层保守**: 政治敏感 / 合规事项只 flag + 给口径, 不强行覆盖所有 case; 第一版宁缺勿滥

---

## 2. 职级自动识别

### 2.1 输入

catfish 现有底座数据:

- `~/.catfish/distilled_facts.md` (跨 session 蒸馏长期记忆)
- `~/.catfish/employee_journal.md` (session 总结日记)
- `~/.catfish/projects.md` (员工自己维护的项目跟踪, 可选)
- `~/.catfish/workplan.md` (员工自己写的本周/本月计划, 可选)
- `~/.hermes/state.db` 最近 30 天 session 标题 + 首条 user message
- `~/.catfish/outputs/` 历史文件名 (周报 / 汇报 / 文档)

### 2.2 LLM 推断 prompt

```
你是 catfish 员工画像分析师. 看以下数据, 推断这员工的:
1. 职级 tier ∈ {frontline, mid, senior}
2. 央国企信号强度 ∈ {none, weak, strong}
3. 关心风格 ∈ {合规优先, 业务优先, 关系优先, 数字优先}
4. 关键人脉 (最多 10 人, 含关系类型)
5. 重点项目 (最多 5 个)
6. 推断置信度 ∈ [0.0, 1.0]
7. 证据 (3-5 条, 引用具体语料)

判断依据 (参考但不限于):
- frontline 信号: TODO 颗粒度细 / 关心个人 KPI / 上级出现频率高 / 没有"班子"概念
- mid 信号: 出现"团队/项目/分派" / 同时多个项目 / 对上汇报 + 对下安排
- senior 信号: 出现"班子/季度/战略/拍板" / 关键人物 (局长/总) 频繁 / 例外/异常驱动

央国企信号: "ISO / 资质 / 国资委 / 党组 / 班子会 / 政策 / 局 / 函" 等术语出现

输出严格 JSON, 不要 markdown:
{
  "tier": "mid",
  "central_state": "strong",
  "style": "合规优先",
  "key_people": [{"name": "李局", "relation": "上级"}, ...],
  "key_projects": [{"name": "项目 A", "status": "进行中"}, ...],
  "confidence": 0.82,
  "evidence": [
    "projects.md 在跟 3 个项目, 含项目 A 总盘",
    "7 天对话频繁出现'班子会 / 季度汇报'",
    ...
  ]
}
```

### 2.3 profile.json schema

```jsonc
// ~/.catfish/profile.json (catfish 自己写, 员工不直接编辑)
{
  "tier": "mid",
  "central_state": "strong",
  "style": "合规优先",
  "key_people": [
    {"name": "李局", "relation": "上级", "last_contact": "2026-05-19"},
    {"name": "老李", "relation": "客户", "project": "项目 A"}
  ],
  "key_projects": [
    {"name": "项目 A", "status": "进行中", "client": "老李"},
    {"name": "ISO 审核", "status": "day4", "deadline": "2026-05-24"}
  ],
  "confidence": 0.82,
  "evidence": ["...", "...", "..."],
  "updated_at": "2026-05-21T20:48:00+08:00",
  "next_recompute_at": "2026-05-28T00:00:00+08:00"  // 一周后复算
}
```

### 2.4 复算触发

- **定时**: 每周一凌晨自动复算一次
- **事件**: 员工新增项目 / 新加重大客户 / session 数量翻倍时主动复算
- **手动**: catfish 设置页有"重新识别我的画像" 按钮

### 2.5 透明展示

`Companion 设置 → 员工画像` 显示当前 tier + 央国企信号 + 证据. 员工**不能直接改 tier**, 但可以:
- 看到 catfish 怎么判断的
- 标"识别错了" → 下次复算时 LLM 看到这条标记, 调整判断
- 主动补充信息 (e.g. "我刚升任副总") → 写入 `~/.catfish/profile_hints.md`, 下次复算输入

### 2.6 处理方法 (rust 后端)

```
edge/companion-app/src-tauri/src/commands/profile.rs

#[tauri::command]
pub async fn profile_recompute() -> Result<Profile, String> { ... }

#[tauri::command]
pub async fn profile_get() -> Result<Profile, String> { ... }

#[tauri::command]
pub async fn profile_mark_wrong(reason: String) -> Result<(), String> { ... }

// 启动时 lifespan-init, profile 不存在或过期 → 后台 task 跑
async fn profile_init_if_needed() { ... }
```

---

## 3. 早安播报: 三版职级 mockup

**同一份原始数据, 三版关注维度完全不同** — 不是详略问题, 是关心的事本身就不一样.

### 3.1 一线版 (tier=frontline)

视野: 今天 24h, 粒度: TODO 级.

```
晚上好, 王工 · 5/22 周五

[今日 6 件 TODO]

🕐 09:30  晨会 — 你昨天的进度 + 今天计划
          📝 昨日 3 件已做, 今日 4 件待安排: outputs/daily-0521.md

🕐 11:00  急邮件 — 老李问 ISO 资料 (项目 A)
          💡 建议回复口径:
            🅰 直接发附件 (我已找出: outputs/iso-day3-summary.pdf)
            🅱 先确认范围再发 (草稿: outputs/reply-laoli-confirm.md)

🕐 14:00  部门周报截止
          📝 本周 5 件已做整理好: outputs/weekly-draft.md
          ⚠️  你还差: 下周计划段, 我列了 3 个可能方向供选

🕐 16:00  跟王经理沟通: 资质方案分工
          📎 上次会议纪要见 outputs/meeting-0518-notes.md (你的部分高亮)

🕐 17:30  日报提醒
          (下班前 30 分钟我自动弹, 不用现在管)

🕐 其他   收件箱还 4 封低优先邮件已分类, 见邮件 tab

————————————————————————————
📊 catfish 处理记录: 自动分类 12 封邮件 (4 急 / 5 中 / 3 低) | 标了 2 个全天日历
```

### 3.2 中层版 (tier=mid) — 鸿波你这层

视野: 本周 + 今日, 粒度: 项目级 + 团队级.

```
晚上好, 鸿波 · 5/22 周五

[今日 3 件主菜]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1️⃣  老李催资质方案范围  (急, 影响项目 A 客户关系)

    📄 我起草了 3 个回复口径:
      🅰 紧扣 5/18 班子会拍的边界, 不松口
      🅱 按老李原意微调, 保留 2 个余地  (倾向, 平衡)
      🅒 暂缓回复, 周一面谈

    📎 草稿: outputs/2026-05-22/reply-laoli.md
    🔗 上下文: 5/14 你跟老李电话定的口径见 catfish-history://session/...
    ⚠️ 合规提示: 类似回复去年被 ISO 审计追问过 (2025 年 11 月案例),
       🅱 口径稳, 留档备查
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

2️⃣  11:30 班子会季度汇报

    📄 汇报材料按上次格式起草:
      PPT: outputs/2026-05-22/q2-board.pptx
      数据: Q2 营收 / 项目进度 / 风险点 已填

    💡 备 2 个口径:
      🅰 保守 (项目 B 滞后用"资源协调中"表述)
      🅱 积极 (突出项目 A 提前完成, 弱化 B)

    ⚠️ 班子会前 1h 你必须亲自看 — 我标了 3 处需要你确认的数字
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

3️⃣  项目 A: 5/18 班子会拍的 3 件事, 2 件未完

    未完:
      • 张三负责的需求收集 (滞后 4 天)
      • 王五负责的预算预测 (滞后 2 天)

    💡 处理路径建议:
      🅰 温和催办 + 给宽限到周一  — 维持团队和气
      🅱 升级跟张三/王五的领导沟通 — 涉及跨部门权重
      🅒 重新分派 (李四接手) — 但要先跟张三说明

    📨 催办名单 + 建议措辞: outputs/2026-05-22/followup-projectA.md

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 catfish 处理记录: 自动分类 47 封邮件 (低优先归档进收件箱) |
                  2 个非关键日历事件已默认接受 (你可在日历 tab 撤回)
```

### 3.3 高层版 (tier=senior)

视野: 季度 + 异常, 粒度: 战略 + 关键关系 + 例外.

```
晚上好, 张总 · 5/22 周五

[今日 2 件关键 + 1 件例外]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1️⃣  周五 Q2 投资 review 班子会  (战略级)

    上次 review 决议: outputs/archive/q1-review-decisions.md
    本次新增 3 个投资项 + 5 个决策点

    💡 5 个决策点, 建议方向供拍板:
      项目 X — 🅰追加 / 🅱观察 / 🅒退出   (倾向 🅱)
      项目 Y — 🅰扩大 / 🅱维持           (倾向 🅰, 5/14 李局表态过)
      项目 Z — 🅰立项 / 🅱推迟           (倾向 🅱, 资金缺口)
      [其他 2 项见 brief]

    📄 班子会 brief: outputs/2026-05-22/q2-review-brief.md (15 分钟读完)
    ⚠️ 上次 review 王副总反对的 2 个项目这次都改了, 态度待观察
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

2️⃣  关键关系节点

    ▪ 李局 5/19 问的"集团数字化进展", 你答应"周五回", 今天 deadline
      💡 我起草了 3 个回复口径:
        🅰 数据汇总 (突出 ISO 通过 / 项目 A 上线)
        🅱 战略陈述 (强调下一步方向)
        🅒 邀请来访 (顺便铺垫 Q3 合作)
      📎 草稿: outputs/2026-05-22/reply-lijuel.md

    ▪ 王总 (兄弟单位) 半年没沟通了, 本月内建议联络
      💡 借机问候选项: 端午前打电话 / Q3 战略 review 邀请 / 茶话会
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ 异常需关注

3️⃣  项目 A 财务异常 +3%

    财务说: 是 5 月加急采购造成
    💡 我查了过去 6 月同类异常 (3 次), 建议处理路径:
      🅰 接受 (符合上次 review "Q2 加速"决议)
      🅱 质疑 (要求财务出明细 — 我列了 3 个具体追问点)
      🅒 暂记 + 月报合并解释

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 catfish 处理记录: 团队 / 日常事务 / 邮件 — 中层秘书已处理, 不在你视野
```

### 3.4 三版差异归纳

| 维度 | frontline | mid | senior |
|---|---|---|---|
| 主菜数 | 5-8 件 | 3-4 件 | 1-2 件 + 异常 |
| 视野 | 今天 24h | 本周 + 今日 | 季度 + 异常 |
| 粒度 | TODO 级 | 项目 / 团队级 | 战略 / 节点级 |
| 关心人 | 自己 + 直接上级 | 团队 + 上级 | 关键关系 (10-20 人) |
| 行动深度 | 做事 | 决策 + 分派 | 拍板 + 巡查例外 |
| 风险维度 | 几乎没有 | 交付风险 | 合规 / 政治 / 投资风险 |
| 关系节点 | 不主动列 | 偶尔 (项目相关) | 主动列 (核心场景) |
| 央国企信号 | 弱时不显 | 强时嵌入相关主菜 | 强时单独段落 |

---

## 4. 6 个 LLM Tool 详细设计

LLM 看完全料后, 调这些 tool 做**预生成 + 检索 + 风险扫描**. tool 实现在 `edge/tool-bridge/`, 不进 central.

### 4.1 catfish_draft_email_reply

```yaml
name: catfish_draft_email_reply
description: |
  起草邮件回复. 输入邮件 thread_id 和你想写几个备选口径, 返回 N 个草稿文件路径.
  catfish 不替员工发, 只起草存到 outputs/, 让员工点开看 / 复制 / 改后自己发.
parameters:
  thread_id: 邮件 thread ID (Mail.app 内)
  tones:
    type: array of strings
    items: enum [strict, balanced, friendly, formal, urgent, hold]
    description: 想要的口径风格 (1-3 个常用)
  context_refs:
    type: array of strings
    description: 关联上下文 (项目/人/历史决策的 catfish-history:// URI)
returns:
  drafts:
    - tone: balanced
      path: ~/.catfish/outputs/2026-05-22/reply-laoli-balanced.md
      preview: "李总好, 关于资质方案范围, 经班子会..."
      compliance_flags: [iso_audit_relevant]
    - tone: strict
      path: ~/.catfish/outputs/2026-05-22/reply-laoli-strict.md
      preview: "..."
      compliance_flags: []
```

### 4.2 catfish_draft_meeting_brief

```yaml
name: catfish_draft_meeting_brief
description: |
  起草会议汇报材料 / brief. 按上次同类会议格式 (从 outputs/ 历史找), 自动填进当前数据.
parameters:
  event_id: 日历事件 ID
  format_hint: enum [pptx, md, docx]  # 优先 pptx 班子会, md 部门例会等
  source_data:
    type: array of strings
    description: 数据源 (项目状态 / 财务 / 进度 等)
returns:
  brief_path: ~/.catfish/outputs/2026-05-22/q2-board.pptx
  slides_count: 12
  highlighted_uncertain:  # catfish 标的不确定数字, 员工要确认
    - slide: 5
      cell: "Q2 营收 1234 万"
      reason: "源自财务初稿, 未定稿"
  tone_variants: []  # 班子会通常一版, 一线日报可能多版
```

### 4.3 catfish_compose_followup_list

```yaml
name: catfish_compose_followup_list
description: |
  根据某项目的未完事项, 出催办名单 + 多种沟通口径.
parameters:
  project: 项目名 (从 profile.key_projects 取)
  decision_ref: 哪次会议拍的 (从 decisions.jsonl 取)
returns:
  followup_list:
    - person: 张三
      task: 需求收集
      original_deadline: 2026-05-18
      days_overdue: 4
      options:
        - tone: gentle
          message: "张总, 上次班子会拍的需求收集, 看下..."
          path: ~/.catfish/outputs/.../followup-zhang-gentle.md
        - tone: escalate
          message: "张工, 这事已经..."
          path: ~/.catfish/outputs/.../followup-zhang-escalate.md
```

### 4.4 catfish_check_compliance

```yaml
name: catfish_check_compliance
description: |
  扫一段文本 (邮件草稿 / 汇报材料 / 决策口径) 的合规风险.
  央国企 catfish 第一版重点: ISO / 政策 / 法务 / 财务审计.
parameters:
  content: 要扫的文本
  context: 涉及哪个项目 / 客户
returns:
  flags:
    - type: iso_audit_relevant
      severity: medium
      reason: "提到资质方案范围, 类似回复曾被 ISO 审计追问"
      precedent: outputs/archive/iso-audit-2025-11.md
      suggestion: "保留邮件原文留档, 引用上次班子会决议作依据"
    - type: financial_review
      severity: low
      reason: "提到金额未脱敏"
      suggestion: "..."
```

### 4.5 catfish_political_sensitivity_scan

```yaml
name: catfish_political_sensitivity_scan
description: |
  央国企特殊层. 扫一段文本对相关人物 / 上级关系 / 平级关系的政治敏感度.
  第一版保守 — 只 flag + 给口径选项, 不强行覆盖所有 case.
parameters:
  content: 文本
  related_people: 涉及的人 (从 profile.key_people 取)
returns:
  flags:
    - type: upper_level_tone
      person: 李局
      severity: medium
      reason: "用'希望'对上级口径偏弱, 上次类似措辞李局有反馈"
      suggestion: "改用'拟于' 或'计划' 更稳"
      alternative_phrasings: ["拟于本周内完成", "计划下周提交初稿"]
    - type: peer_relationship
      person: 王副总
      severity: low
      reason: "提及项目 B 未提其贡献, 王副总此前牵头过"
      suggestion: "..."
```

### 4.6 catfish_recall_decision_history

```yaml
name: catfish_recall_decision_history
description: |
  按主题 / 人 / 项目 拉过往决策口径, 让 LLM 现在的建议跟历史保持一致 (不背离).
parameters:
  topic: 主题关键词 (e.g. "资质方案范围" / "Q2 投资")
  person: 相关人 (optional)
  project: 相关项目 (optional)
  limit: 最多返几条 (默认 5)
returns:
  decisions:
    - date: 2026-05-14
      session_ref: catfish-history://session/abc123
      topic: 资质方案范围
      person: 老李
      decision_summary: "你跟老李电话拍的边界: 不含 Y 模块"
      verbatim_excerpt: "..."  # 原文片段
    - date: 2025-11-08
      ...
```

---

## 5. 新 LLM Prompt (替代 Phase 6 SYSTEM_PROMPT)

```
你是 catfish — 中国央国企员工的智能参谋. 严格按以下规则工作:

# 角色边界 (绝不违反)
- 你是参谋, 不是代理. 任何级别都**不替员工拍板**.
- 你的输出是: 主菜识别 + 已准备好的材料 + 建议选项 + 风险提示.
- 你**绝不**替员工发邮件 / 接受会议 / 签字 / 拍板任何事.
- 你**只**起草到 outputs/ 让员工自己看 / 改 / 发.

# 输入信息
- 员工 profile (tier / 央国企信号 / 关键人脉 / 重点项目 / 风格)
- 全部 catfish 数据底座 (邮件 / 日历 / TODO / 长期记忆 / 7 天对话 / 周报 / 项目)
- 当前时间

# 工作步骤
1. **看完全部信息**, 内部关联推理. 不要分块看, 要把人/项目/历史/事件横向连起来.
2. **按职级识别主菜**:
   - frontline: 5-8 件具体 TODO, 按时间排
   - mid: 3-4 件项目级主菜, 团队进度 + 风险 + 汇报
   - senior: 1-2 件战略级 + 异常例外 + 关键关系节点
3. **每件主菜调 tool**:
   - 涉及邮件 → catfish_draft_email_reply (起草 2-3 个口径)
   - 涉及会议 → catfish_draft_meeting_brief (起草材料)
   - 涉及催办 → catfish_compose_followup_list
   - 涉及决策 → catfish_recall_decision_history (引用历史口径, 不背离)
4. **每件主菜跑合规 + 政治扫描** (中层 / 高层 + 央国企信号 strong 时):
   - catfish_check_compliance (草稿内容)
   - catfish_political_sensitivity_scan (涉及关键人物时)
5. **输出结构化主菜 list** (严格 JSON):
   ```
   {
     "tier": "mid",
     "main_tasks": [
       {
         "id": 1,
         "title": "老李催资质方案范围",
         "urgency": "high",
         "reason": "影响项目 A 客户关系",
         "options": [
           {"label": "A", "tone": "strict", "summary": "紧扣班子会边界", "draft_path": "..."},
           {"label": "B", "tone": "balanced", "summary": "微调保留余地", "draft_path": "...", "ai_lean": true},
           ...
         ],
         "compliance_flags": [...],
         "political_flags": [...],
         "context_refs": ["catfish-history://session/..."]
       },
       ...
     ],
     "handled_silently": [
       {"type": "email_archive", "count": 47, "category": "low-priority"},
       {"type": "calendar_accept", "events": ["..."]}
     ]
   }
   ```

# 强约束
- 不允许 "建议你 X" 这种被动建议. 改为 "我起草了 A/B 两个口径, 你点这里看".
- 不允许"出总结". 总结是 Phase 6 的错路, Phase 7 不要.
- 不允许"全分析完后给一段文字". 必须结构化, 让 UI 渲染成主菜卡片.
- 央国企信号 strong 时, 任何邮件草稿 / 决策口径必须跑过 check_compliance + 
  political_sensitivity_scan 后才能出.
```

---

## 6. 草稿存储 + 决策留痕

### 6.1 outputs 目录结构

```
~/.catfish/outputs/
├── 2026-05-22/                  # 按日期分目录, 当天产物
│   ├── reply-laoli-balanced.md
│   ├── reply-laoli-strict.md
│   ├── q2-board.pptx
│   ├── followup-projectA.md
│   └── q2-review-brief.md
├── 2026-05-21/
│   └── ...
├── archive/                     # 跨日重要文档 (会议纪要 / 决议)
│   ├── q1-review-decisions.md
│   └── iso-audit-2025-11.md
└── _index.jsonl                 # 全部 outputs 元数据 (路径/日期/类型/关联实体)
```

### 6.2 decisions.jsonl schema

```jsonc
// ~/.catfish/decisions.jsonl  (append-only, 每行一条决策记录)
{
  "ts": "2026-05-22T11:42:00+08:00",
  "main_task_id": 1,
  "task_title": "老李催资质方案范围",
  "options_offered": [
    {"label": "A", "tone": "strict"},
    {"label": "B", "tone": "balanced"},
    {"label": "C", "tone": "hold"}
  ],
  "ai_lean": "B",          // catfish 当时倾向哪个
  "user_choice": "B",      // 员工最后选了哪个 (null = 没选/自己重写了)
  "user_action": "sent",   // sent / drafted_but_held / overridden / ignored
  "compliance_flags_at_decision": ["iso_audit_relevant"],
  "context_refs": ["catfish-history://session/abc"],
  "draft_path_chosen": "outputs/2026-05-22/reply-laoli-balanced.md"
}
```

### 6.3 决策留痕用途

- 下次类似事 (e.g. 老李又催范围), `catfish_recall_decision_history(topic="资质方案", person="老李")` 拉到这条
- catfish 不强行按上次, 但显示在 UI: "上次 (5/22) 你选了 🅱 平衡口径, 是否参考?"
- 长期统计: catfish 哪些建议被采纳 / 哪些被员工重写, 反馈到 prompt 调优

### 6.4 Rust 后端

```
edge/companion-app/src-tauri/src/commands/
├── drafts.rs           # 读写 outputs/<date>/
└── decisions.rs        # append decisions.jsonl + 按 topic/person 查询
```

---

## 7. UI 组件结构

```
edge/companion-app/src/tabs/Briefing/
├── AdvisorView.tsx          # 主菜列表容器 (替代 WorkplanView)
├── components/
│   ├── ActionCard.tsx       # 单个主菜卡片
│   ├── OptionSelector.tsx   # N 选项 UI, 点了走 decisions.jsonl
│   ├── DraftPreview.tsx     # 草稿展开预览 (Markdown / PPTX 占位)
│   ├── ComplianceFlag.tsx   # 合规/政治风险标 (黄色提示)
│   ├── ContextRefLink.tsx   # 上下文引用链接 (点开看 session 历史)
│   └── HandledSilently.tsx  # 底部"已默认处理" 折叠区
```

### 7.1 ActionCard 视觉规范

- 单卡片宽度 = 早安 tab 内容区宽度 (约 700px)
- 标题: 18px 加粗, 高优先级带红色边框, 中优先级带橙色, 低优先级灰色
- 副标题: 12px 灰, 说明 urgency / reason
- 选项块: 3 个并列 (🅰 🅱 🅒), 选项卡 hover 时高亮, 点击进 OptionSelector
- catfish ai_lean: 选项卡角标小标"我倾向" (灰色, 不喧宾夺主)
- 草稿链接: 显眼按钮"📄 看草稿" → 弹 DraftPreview
- 合规标: 选项卡内黄底小标, hover 显原因
- 上下文引用: 卡片底部小字"🔗 历史: 5/14 与老李电话", 点击进 ContextRefLink

### 7.2 OptionSelector 交互

- 员工选 🅱 → 弹确认: "确认选 🅱 平衡口径? 草稿见 outputs/..."
- 确认后:
  1. append decisions.jsonl 一条
  2. 卡片折叠, 显示"✓ 已选 🅱, 草稿打开"
  3. 自动 `open ~/.catfish/outputs/.../reply-laoli-balanced.md` (系统默认编辑器)
  4. **不替员工发**. 员工自己复制到 Mail.app 发.

### 7.3 "已默认处理" 折叠区

底部小字: `📊 catfish 处理记录: 自动分类 47 封邮件 (低优先归档) | 2 个非关键日历事件已默认接受`

点开展开:
- 哪些邮件归档了 (员工能撤回)
- 哪些会议默认接受了 (员工能撤回)
- catfish 的理由

**注**: 即便是"默认处理", 也只是分类 + 标志, **不真发任何东西**. 邮件归档是 Mail.app 内部标 label, 不删除. 会议接受是日历内标 tentative, 不是 accepted.

---

## 8. 5/17 BOUNDARY 边界对齐

按 `docs/CENTRAL-EDGE-DATA-BOUNDARY.md`, Phase 7 全部新代码在 edge/, 不进 central/:

| 模块 | 位置 | 说明 |
|---|---|---|
| profile 识别 | `edge/companion-app/src-tauri/src/commands/profile.rs` | 员工画像, 边缘数据 |
| 草稿存储 | `edge/companion-app/src-tauri/src/commands/drafts.rs` | outputs/ 在员工本机 |
| 决策留痕 | `edge/companion-app/src-tauri/src/commands/decisions.rs` | decisions.jsonl 在员工本机 |
| 6 个 LLM tool | `edge/tool-bridge/src/catfish_tool_bridge/draft_*.py` 等 | tool 实现读员工本机数据 |
| UI | `edge/companion-app/src/tabs/Briefing/` | 前端 |
| 早安 LLM 调用 | `edge/companion-app/src/lib/briefing_advisor.ts` | 调 gateway, gateway 只过 LLM 流量 |

**唯一经过 central**: LLM 调用流量 (走 gateway → 上游 LLM). gateway 不持有员工 profile / 草稿 / 决策, prompt 拼装在 edge 完成, gateway 只透传.

跟 5/17 BOUNDARY"中央只元数据" 严格符合.

---

## 9. 风险 + 保守原则

### 9.1 LLM tool 链路鲁棒性

6 个 tool 一次性引入, 单 tool 挂可能让整个早安降级.

**应对**:
- tool 调用全部加 try-catch, 失败 → 卡片降级为"无草稿, 只列选项"
- 不阻塞主菜识别 (LLM 在 tool 挂了时也能给文字建议)
- 错误 log 详细记录, 帮排查

### 9.2 LLM 起草质量

邮件 / 汇报材料质量必须"改 30% 能发". 不达门槛员工反而烦.

**应对**:
- prompt 设计阶段(第 4 步) 给至少 1 天反复调
- 上线后 7 天观察 `decisions.jsonl`: user_choice=null + user_action=overridden 占比 > 50% → 紧急回炉 prompt
- 提供"改 5%" / "重写" 反馈按钮, 喂回去优化

### 9.3 职级识别错位

中层被识别成一线 → 看一堆 TODO 厌烦.

**应对**:
- profile 透明 (设置页显示判断依据)
- 员工标"识别错了" → 下次复算 LLM 看到此标记调整
- 复算频率: 一周 + 大变化 + 手动 3 种触发

### 9.4 央国企特殊层 LLM 能力上限

政治敏感 / 合规判断难度高, LLM 可能误判.

**应对** (第一版保守原则):
- 只 flag 风险, 不强行覆盖所有 case
- 给 2-3 口径供选, 不给"权威建议"
- 涉及红线事项 (e.g. 党组决议 / 上级直接指示) 一律降级为"提醒人工核对", 不给口径
- 上线 30 天观察员工标记的"判断错" 频次, 失误率 > 10% → 暂时关闭这两个 tool

### 9.5 草稿暴露隐私

outputs/ 在员工本机, 但**草稿内容可能含项目敏感数据 / 客户信息**.

**应对**:
- outputs/ 权限 0700 (只员工可读)
- catfish 不上传 outputs/ 到任何中央 (重申 5/17 BOUNDARY)
- 草稿生成时不发去 gateway 做"草稿质量分析" 这种二次处理 — 一次性出, 用完拉倒

---

## 10. Phase 6 → Phase 7 迁移

### 10.1 砍掉的 (Phase 6 残留)

```
delete:
- edge/companion-app/src/tabs/Briefing/components/WorkplanView.tsx
- edge/companion-app/src/lib/briefing_workplan.ts
- BriefingCard.tsx 内: fetchWorkplan / setWorkplan / workplan state / WorkplanView 调用
- docs/BL-WORKPLAN-DESIGN.md (改名 BL-WORKPLAN-DESIGN.deprecated.md + 头加废弃说明)
```

### 10.2 保留的 (新方案复用)

```
keep & reuse:
- edge/companion-app/src-tauri/src/commands/briefing_context.rs  # 7 数据源拉取, 新方案的输入
- edge/companion-app/src/lib/briefing.ts  # urgencyMap + emailDigest 部分仍需要
- BriefingCard.tsx 框架 (header / loadAll / refresh button)
```

### 10.3 全新建的 (Phase 7)

```
new:
- edge/companion-app/src-tauri/src/commands/profile.rs
- edge/companion-app/src-tauri/src/commands/drafts.rs
- edge/companion-app/src-tauri/src/commands/decisions.rs
- edge/companion-app/src/lib/briefing_advisor.ts (替代 briefing_workplan.ts)
- edge/companion-app/src/tabs/Briefing/AdvisorView.tsx (替代 WorkplanView)
- edge/companion-app/src/tabs/Briefing/components/ActionCard.tsx
- edge/companion-app/src/tabs/Briefing/components/OptionSelector.tsx
- edge/companion-app/src/tabs/Briefing/components/DraftPreview.tsx
- edge/companion-app/src/tabs/Briefing/components/ComplianceFlag.tsx
- edge/companion-app/src/tabs/Briefing/components/ContextRefLink.tsx
- edge/companion-app/src/tabs/Briefing/components/HandledSilently.tsx
- edge/tool-bridge/src/catfish_tool_bridge/draft_email_reply.py
- edge/tool-bridge/src/catfish_tool_bridge/draft_meeting_brief.py
- edge/tool-bridge/src/catfish_tool_bridge/compose_followup_list.py
- edge/tool-bridge/src/catfish_tool_bridge/check_compliance.py
- edge/tool-bridge/src/catfish_tool_bridge/political_sensitivity_scan.py
- edge/tool-bridge/src/catfish_tool_bridge/recall_decision_history.py
- docs/CATFISH-ADVISOR-DESIGN.md (本文件)
```

### 10.4 期间早安 Tab 状态

砍 Phase 6 后到 Phase 7 全做完之前 (~3 周), 早安 Tab 临时空白:

```
晚上好, 鸿波 · 5/22 周五

(catfish 正在重新设计早安播报, 智能参谋功能开发中. 你可以先用工作台或邮件 tab.)

上次同步 20:48
```

Companion 启动默认 tab 改成"工作台", 不是早安. 等 Phase 7 真做出来再切回早安默认.

---

## 11. 实施顺序

按依赖关系连着做, **不切 sprint**:

```
[砍 Phase 6 + 早安临时空白]   ─ 0.5 d
        │
        ▼
[职级识别基础设施]           ─ 2 d
        │
        ├──────────────────────────┐
        ▼                            ▼
[草稿存储 + 决策留痕]         [6 个 LLM tool]   ─ 1.5 + 5 d (可并行)
        │                            │
        └──────────────┬─────────────┘
                       ▼
              [新 LLM Prompt 设计]   ─ 1 d
                       │
                       ▼
            [早安 LLM 调用层 advisor.ts]  ─ 1 d
                       │
                       ▼
                  [UI 渲染层]       ─ 3 d
                       │
                       ▼
            [集成测试 + 调 prompt]   ─ 2 d
                       │
                       ▼
                [文档收尾]          ─ 0.5 d
```

总: **16.5 工程日** (单工程师), 可压到 12 d 如果第 3 步 6 tool 并行.

---

## 12. 待对齐 (鸿波 review 时拍板)

设计稿写完, 开工前需要鸿波确认以下:

1. **mockup 是不是这个意思?** 三版职级 (一线/中层/高层) 早安各看到的内容, 跟你想的一致吗?

2. **6 个 tool 够吗?** 列了 draft_email_reply / draft_meeting_brief / compose_followup_list / check_compliance / political_sensitivity_scan / recall_decision_history. 还缺哪个? 比如 "起草请假/调休邮件" / "起草工作总结" / "起草绩效自评" 这种?

3. **"已默认处理" 折叠区** 你接受不? — catfish 自动给低优邮件归档 / 非关键会议接受 tentative. 你之前说"绝不代行任何事", 这两类算"代行"吗? 我倾向算"分类不是代行"(没真发任何东西), 但你拍.

4. **草稿是 Markdown 还是 PPTX/DOCX?** 邮件草稿 markdown 简单. 但班子会汇报材料是 PPTX, 涉及 python-pptx 复用 catfish-pptx skill. 起草质量门槛拉高. 第一版要不要 PPTX, 还是只出 markdown 模板, 你自己复制到 ppt?

5. **outputs/ 命名规范** — 我设的是 `outputs/<YYYY-MM-DD>/<task-type>-<key>.md`. 接受吗? 还是按项目分目录 (`outputs/<project>/<date>.md`)?

6. **执行人是你一人还是有团队?** — 16.5d 工程量, 单人意味两周半. 团队的话可压到 2 周.

---

## 13. 实施记录 (5/21 晚 一口气 ship)

鸿波 5/21 晚拍板"先开工", 一口气推完第 0-6 步, 第 7+8 步简化 (鸿波"测试数据先不要管, 先做出来, 等以后再调整").

### 各步实际交付

| 步 | 状态 | 交付 |
|---|---|---|
| 0 砍 Phase 6 | ✅ | 删 WorkplanView/briefing_workplan, BriefingCard 缩成壳, 默认 tab → chat, BL-WORKPLAN-DESIGN 标 DEPRECATED |
| 1 profile 基础 | ✅ | `src-tauri/commands/profile.rs` (258) + `src/lib/profile.ts` (350, 含 LLM 真推断 inferProfileFromContext). 6 Tauri command, 单测 (cargo test) |
| 2 草稿/决策存储 | ✅ | `commands/drafts.rs` (203) + `commands/decisions.rs` (204) + `src/lib/drafts.ts` + `decisions.ts`. 7 Tauri command, path-traversal 防御单测 |
| 3 6 个 LLM tool | ✅ | `advisor_io.py` (80) + `advisor_drafts.py` (169) + `advisor_scans.py` (223, 含央国企合规/政治 30+ 关键词库) + `advisor_recall.py` (117). schema + dispatch 注册, smoke test 通 |
| 4+5 prompt + advisor.ts | ✅ | `src/lib/briefing_advisor.ts` (530): SYSTEM_PROMPT + buildUserPrompt + fetchBriefingAdvisor + JSON parser. 不挂 AbortSignal (Tauri suspend 经验) |
| 6 UI | ✅ | `AdvisorView.tsx` (252, 顶层容器) + `ActionCard.tsx` (296, 卡片含 options/flags/draft 子段) + `HandledSilently.tsx` (66, 折叠区) + `BriefingCard.tsx` (68, 缩成壳) |
| 7 测试 | ⚪ 简化 | smoke test (Python 端 3 tool 跑通) + TS check 0 error. 鸿波拍"测试数据先不要管", 真集成测试留迭代 |
| 8 文档收尾 | ✅ | 本"实施记录" 段, Phase 6 design doc 已标 DEPRECATED |

### 总代码

- Rust (Tauri commands): 665 行 (3 文件)
- Python (tool-bridge): 589 行 (4 文件)
- TypeScript (前端): 1638 行 (8 文件, 含改 BriefingCard 缩 + ui.ts default tab 切换)

新文件 11 个, 改文件 5 个 (mod.rs / lib.rs / catfish_tools.py / catfish_tool_schemas.py / ui.ts).

### 红线状态

全部新文件 < 800. 修改文件:
- `catfish_tools.py`: 744 → 763 (+19 行 dispatch, 仍 < 800)
- `catfish_tool_schemas.py`: 2122 → ~2280 (schema 文件按 CLAUDE.md 例外, 不卡)

### 未完成 (待后续迭代)

1. **profile LLM 真推断的 prompt 调优**: 当前用通用 prompt, 可能 confidence 评估不准. 用 catfish 跑几天后看实际 profile.json 内容决定要不要改 prompt.
2. **6 个 advisor tool 第二版**:
   - check_compliance / political_sensitivity_scan 当前 keyword 匹配, V2 可换 LLM 智能扫描
   - recall_decision_history 当前 substring 匹配, V2 可加向量检索 / LLM 语义
3. **草稿 PPTX/DOCX**: 第一版只出 markdown. 班子会 PPTX 起草留 V2 (复用 catfish-pptx skill).
4. **真集成测试**: 三版职级用 mock data 跑端到端.
5. **首次 chat SOUL 引导段** (task #23): chat tab 第一次聊时自然提问员工身份, 不阻塞 advisor. 涉及 central/gateway 注入逻辑 + 5/17 BOUNDARY 重新审视, 跟搬迁一起做.
6. **LLM 服从程度调优**: 5/22 实测发现 LLM 偶尔只出 1 个 option (没 B/C), tone="prepare" 等非 enum 值, 没调 catfish_draft_email_reply 等 tool. 需迭代 prompt 强约束 + tone enum fallback.

---

## 14. 5/22 cold start 补丁记录

5/22 跑通后续补 4 轮 bug fix, 全是 cold start 路径上的 race / 死锁 / 硬编码问题:

### 第 1 轮: cold start 1-3 (鸿波"新员工怎么识别角色性格?")

详见 §13 实施记录前面段. 4 条命题:
1. 自动识别职级 (从历史推, 员工不填表)
2. 三版 (一线/中层/高层)
3. catfish 绝不代行任何事, 只给建议
4. 央国企特殊层 (政治/合规/上行下达) 给建议选项

实际改动:
- `Profile.personality` 字段 (Verbosity / Structure / Formality / SignatureWords / SampleSentences / SourceCount)
- `inferProfileFromContext` 加 3 外部数据源 (emailListFetch / calendarWeekFetch / catfish_style_fingerprint_get) 解决新员工 ~/.catfish/ 空时的 cold start
- `AdvisorView` confidence 分层 UI (< 0.3 / 0.3-0.7 / > 0.7 三档提示)

### 第 2 轮: 修死锁 (鸿波本机 LLM 第一次挂时占位锁一周)

- `profile_needs_recompute` Rust 端加 `confidence < 0.3 → return true`, 占位/低置信度立即重试
- `ensureProfileFresh` 加 `force` 参数 + `onDone` 回调

### 第 3 轮: 修 race + JSON 鲁棒解析 + in-flight 锁

5/22 实测 console log 发现:
- React StrictMode dev useEffect 双调 → recomputeProfile 调 2 次 LLM (浪费 token + quota)
- LLM 偶尔返带前后解释文字的 JSON (`"现在我已经分析完..."` 之类), 我原始 strip 只去 markdown 反引号, JSON.parse 直接挂 SyntaxError
- AdvisorView race: profile.json 不存在时立即 setPhase("no_profile") return, 后台 recompute 完写好 profile 但 UI 不知道, 主链路靠手动刷新才跑

修:
- `robustJsonParse` helper: 截 `{` 到 `}` 之间的子串再 parse, 容忍前后解释文字 (profile.ts + briefing_advisor.ts 各加一份)
- `recomputeProfile` + `fetchBriefingAdvisor` 各加 in-flight 锁: 已在跑就复用 Promise, StrictMode 双调时第 2 次拿到同一结果
- `ensureRecomputed(model, force)` 同步版替代 `ensureProfileFresh` fire-and-forget. AdvisorView await 这个 → 主链路顺序执行不靠 useEffect 多跑

### 第 4 轮: 修硬编码 model (鸿波"是不是硬编码模型了?")

- `recomputeProfile(model)` model 必传, 删 fallback hardcode `catfish-public-deepseek-flash`
- `ensureRecomputed(model, force)` + `ensureProfileFresh(model, force, onDone)` 同步加 model 参数
- `AdvisorView` 调用时传 `useChatStore.model`, 跟员工 chat 同款
- 严格遵循 5/17 BL-INTERNAL-MODEL-FOLLOW-USER (员工选哪个 model, 内部任务都用同款)

### 5/22 默认 tab 切回早安

`store/ui.ts` `activeTab` 从 5/21 临时设的 `"chat"` 切回 `"briefing"`. 员工打开第一眼直接看主菜 + 选项 + 草稿.

### 5/22 跑通的真实截图证据

鸿波本机第一次 ship 后早安主菜内容:

```
📊 已识别: mid / 合规优先 / 2 关键人 / 5 项目. 置信度 65%, 继续用会更准.

鲶鱼参谋 · tier=mid

1️⃣  ISO现场审核末次会议（今天8:30 @ 409会议室）  急

    末次会议是ISO审核的收官环节，审核结论直接影响公司资质体系结论，
    必须准时出席、记录整改意见

    [A] 已确认8:30 409会议室。建议提前5分钟到，带上审核期间记录的
        所有不符合项清单，末次会议通常会现场宣读审核结论和整改要求。
        如有整改项，会后立即记入journal并设deadline

    ⚠️ 合规 (high): ISO现场审核末次会议，审核结论直接影响公司资质体系认证结果
    建议: 末次会议后建议当天将整改项录入tracking，避免后续追溯遗漏
```

跟之前 Phase 1-6 排版数据的差距:
- Phase 6: "邮件 9 封 · 中 2 · 低 7 · 日历 2 件 · 工作计划 4 件未完"
- Phase 7: 具体事 + 准点 + 地点 + 风险 + 后续追溯建议

"干活的秘书" 跟"堆数据" 的真实差别在此. 鸿波 5/21 反复点中的方向真做出来.

### 跑通验证 (鸿波本机)

```bash
# 1. Rust 编译
cd ~/person_task/catfish/edge/companion-app/src-tauri && cargo build

# 2. Rust 单测
cd ~/person_task/catfish/edge/companion-app/src-tauri && cargo test profile drafts decisions

# 3. Python tool 单测
cd ~/person_task/catfish/edge/tool-bridge && pytest tests/  # 老测试不破

# 4. TS 检查 (已自检 0 error)
cd ~/person_task/catfish/edge/companion-app && npx tsc --noEmit

# 5. Companion dev 跑起来
cd ~/person_task/catfish/edge/companion-app && npm run tauri dev
# → 默认进 chat tab (Phase 7 期间设置)
# → 手动切到早安 tab 看 AdvisorView 渲染:
#   - profile.json 不存在 → "员工画像识别中" 引导
#   - 后台触发 recomputeProfile → 调 LLM → 写 profile.json
#   - 数据拉完 → 调 fetchBriefingAdvisor → LLM ReAct 调 6 tool → 主菜 + 选项 + 草稿
```

设计稿到此. 期待真实用 + 反馈调优.
