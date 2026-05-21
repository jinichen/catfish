# BL-WORKPLAN-DESIGN · 鲶鱼工作总结 framework (Phase 6, 5/21 鸿波拍板)

> **状态**: 设计稿. 鸿波 review 拍板后再 ship 代码.
>
> **决策日期**: 2026-05-21
>
> **背景**: 5/21 一天 ship 了 Phase 1-5 (L3 化简 / priority ⭐ / ranked / 详情段 / LLM Decision), 鸿波看完 Phase 5 后点出**整个方向错** — 不是"今日单点 LLM 综合", 是**周/日双层工作总结**. 央国企节奏 = 周/月节奏, 不是天.
>
> **作 Phase 5 反思**: 我之前的 Phase 3 是机械分桶, Phase 5 是单层主线. 都没切到"周报+进度" 这两个央国企核心维度. 这次先**设计先行**, 鸿波 align 后再写代码.

---

## 1. Framework: 3 × 2 矩阵

|   | **本周** (5/19-5/25) | **今日** (5/21 周四) |
|---|---|---|
| **工作内容** | 这周做什么 / 已做什么 (回顾 + 计划) | 今天做什么 (落地) |
| **关键事件** | 这周重要时间点 (会议 / ddl / 邮件需回) | 今天具体事件 |
| **鲶鱼建议** | 这周整体节奏 + 风险 | 今天怎么干 |

跟现状对比 (Phase 5):

| Phase 5 (今日单层) | Phase 6 (周/日双层) |
|---|---|
| 1 主线 + 证据 + 行动 + 风险 | 6 块矩阵 (周/日 × 内容/事件/建议) |
| 单 LLM 调用出 4 段 | 2 LLM 调用 (周维度 + 日维度) 或 1 长 prompt 出 6 块 |
| 受众: 互联网产品节奏 | 受众: 央国企周报节奏 |

---

## 2. 数据源 (扩到 7 类, 加 2 类核心)

| 数据源 | 现状 | Phase 6 怎么用 |
|---|---|---|
| **行事历** | EventKit binary (Phase 5 之后) | 今日 + 本周 7 天事件 |
| **TODO** | journal-agent 抽 `- [ ]` + `TODO:` + ⭐ priority | 本周累计 + 今日落地 |
| **邮件** | Apple Mail / iMAP 拉, LLM 评急/中/低 | 今日 + 本周未回邮件 |
| **历史记忆** | gateway memory_distill 蒸馏的 `~/.catfish/distilled_facts.md` (1271 字节) | 长期员工画像 + 当前项目 |
| **对话历史** | `~/.hermes/state.db` sessions 表 | 最近 7 天对话 (扩大窗口) |
| **🆕 周报** | catfish-leadership-briefing / weekly-report skill 输出 | 看上周/本周已 ship 的周报 → 提取本周计划 |
| **🆕 进度跟踪** | (当前无数据源 — 设计决策点!) | 跨周项目状态 (草稿 60% / 待发 / 已交付) |

### 数据源决策点 (鸿波拍)

#### 决策 A: 周报数据怎么拿?

| 选项 | 优势 | 劣势 |
|---|---|---|
| **A1**: catfish-leadership-briefing skill 输出存 `~/.catfish/outputs/`, scanned by Tauri command | 已 ship, 文件存在 | 文件无结构, 要 LLM 重新读 markdown |
| **A2**: 加 `~/.catfish/workplan/weekly/<YYYY-WW>.md` 员工自己维护周计划 | 结构化 (跟 journal 同模式), 可手动可 LLM 写 | 员工要养习惯, 第一周空 |
| **A3**: gateway 端跑 weekly 蒸馏 (类似 distilled_facts 但每周一份) | 自动 | 工程量大, 要改 gateway |

→ **推荐 A1+A2 组合**: 周报输出文件**已经有**, scan `~/.catfish/outputs/weekly-*.xlsx` + 员工可选维护 `~/.catfish/workplan.md` 给 LLM 上下文.

#### 决策 B: 进度跟踪数据从哪来?

| 选项 | 优势 | 劣势 |
|---|---|---|
| **B1**: `~/.catfish/projects.md` 员工手动维护 (markdown bullet list) | 轻, 跟 journal 同模式 | 员工要养习惯 |
| **B2**: LLM 从 journal 长期 + chat history 推断 ("看 5 周 journal 推断你在做哪些项目, 各到哪步") | 自动 | LLM 推断不准, hallucination 风险 |
| **B3**: 加 catfish 内置任务管理 (新 tab) | 完整 | 大工程, 1 周+ |

→ **推荐 B1+B2 组合**: 加 `~/.catfish/projects.md` 文件 (员工可选), 如果空 → LLM 走 B2 推断. 1-2 周后员工觉得有用就自己维护.

---

## 3. LLM Prompt 设计

### 决策 C: 一次 vs 两次 LLM 调用?

| 选项 | 延迟 | Token | 质量 |
|---|---|---|---|
| **C1**: 1 次调用喂 7 数据源出 6 块 JSON | ~15-25s (长 prompt + 长输出) | ~6-8k input, ~1.5k output | 综合好 (LLM 一次看全) |
| **C2**: 2 次并发 (周维度 + 日维度) | ~10s (并发) | 每次 ~4k input | 周/日产出独立, 关联弱 |

→ **推荐 C1**. 关联推理 (本周 ISO 审核 → 今天必须发资质方案) 是综合判断的核心. 拆两次会丢这层关联.

### Prompt 结构 (草稿)

```
你是中国央国企员工的资深 AI 副手. 综合 7 类数据出 周/日 工作总结.

# 数据
## 行事历 (今日 + 本周 7 天)
[events]

## 工作 TODO (含 ⭐ priority)
[todos]

## 邮件 (今日 + 未读, 含 LLM 评级)
[emails]

## 长期员工画像 (memory_distill)
[distilled_facts]

## 最近 7 天对话主题
[recent_sessions]

## 上周/本周周报 (catfish weekly-report 输出)
[weekly_reports]

## 项目进度 (~/.catfish/projects.md 或 LLM 推断)
[projects]

# 任务
出严格 JSON, 不要 markdown 包裹:
{
  "week": {
    "summary": "本周做什么 / 已做什么 (≤80 字)",
    "events": [{"date": "5/22 周五", "title": "班子会", "context": "..."}],
    "advice": "本周整体节奏 + 风险 (≤80 字)"
  },
  "today": {
    "summary": "今天落地什么 (≤60 字)",
    "events": [...],
    "advice": "今天怎么干, 具体步骤 (≤100 字)"
  }
}
```

---

## 4. UI 布局

### 决策 D: 6 块怎么排?

| 选项 | 优势 | 劣势 |
|---|---|---|
| **D1**: 2 行 (本周 / 今日) × 3 列 (内容/事件/建议) | 矩阵感强, 央企报表风 | 屏幕宽度需要够 |
| **D2**: 2 大段 (本周 / 今日), 每段内 3 子段 | 阅读自然 (上而下) | 视觉没矩阵感 |
| **D3**: Tab 切换 (本周 tab / 今日 tab), 每 tab 3 段 | 简洁 | 看周/日要切换不直观 |

→ **推荐 D2**: 跟央企周报阅读习惯一致 (从总到分). 1 屏装得下, 不需要滚动太多.

### 视觉草图

```
鲶鱼工作总结 · 5月21日 周四                        [刷新]

█ 本周 (5/19 - 5/25)
  📋 工作内容
     ISO 资质审核 Day3-4 完成 / 季度汇报准备中 / 客户资质方案待发
  📅 关键事件
     • 5/22 周五 14:00 班子会 (季度汇报需提前发)
     • 5/23 周六 季度汇报 ddl
     • 5/24 周日 资质方案客户 review
  💡 鲶鱼建议
     资质方案先做 (老李催 2 次), 季度汇报留周末完整时段. 风险: 周末 2 件并行

█ 今日 (5/21 周四)
  📋 工作内容
     主线: 资质方案 + ISO Day4 收尾
  📅 关键事件
     • 11:30 老李邮件待回 (急)
     • 14:00-16:00 写资质方案
     • 17:30 ISO Day4 总结会
  💡 鲶鱼建议
     1. 11:30 前先回老李锁范围
     2. 14:00-16:00 复用上次审核结论写方案
     3. 17:30 会前 1h 自审一遍

──────── 详情区 (展开看明细) ────────
▶ 📅 日历详情 · 1 件
▶ ✅ 工作计划 · 4 件 (点开标完成 / 删)
```

---

## 5. 跟现有 Phase 1-5 关系

| 现状 | Phase 6 做法 |
|---|---|
| **Phase 1 L3 化简**: 删 GoalInput + 4 详情段调用 + emoji 减量 | ✅ 保留 (L3 风格不变) |
| **Phase 2 priority ⭐**: journal-agent + Rust + 前端 is_priority 字段 | ✅ 保留 (Phase 6 LLM prompt 用) |
| **Phase 3 PriorityList ranked**: 机械分桶 | ⚠️ **删** (被 Phase 6 周/日矩阵取代) |
| **Phase 4 详情段恢复**: EventsDetail / TodosDetail 默认收起 | ✅ 保留 (作底部 "明细区") |
| **Phase 5 DecisionView 主线**: 单层 LLM Decision | ⚠️ **删** (被 Phase 6 周/日矩阵取代) |
| **EventKit Swift binary**: 替代 osascript | ✅ 保留 (跟 Phase 6 无关) |
| **CORS 债修复**: gateway query param + 前端 5 处 | ✅ 保留 (跟 Phase 6 无关) |

### 净改动 (Phase 6 实施时)

- ❌ 删: PriorityList.tsx + briefing_decision.ts + DecisionView.tsx + helpers.ts:computePriorities
- ➕ 加: briefing_workplan.ts (新 LLM call) + WorkplanView.tsx (新 UI 组件) + projects.md 读取 + 周报扫描
- ✏️ 改: BriefingCard.tsx (PriorityList → WorkplanView), briefing_context_fetch (加 weekly_reports + projects)

---

## 6. 工程量评估 (诚实数)

| 步骤 | 时间 |
|---|---|
| Rust command 扫 `~/.catfish/outputs/weekly-*` + 读 `~/.catfish/projects.md` | 1.5h |
| briefing_context_fetch 扩 — 加 weekly_reports + projects + 7 天 sessions (现在只 24h) | 1h |
| briefing_workplan.ts 写 LLM 调用 + JSON 解析 + fallback 规则版 | 3h |
| WorkplanView.tsx 渲染 2 大段 × 3 子段 (~250 行 TSX) | 2.5h |
| BriefingCard 集成 (替代 PriorityList) + 测试 | 1h |
| 删 Phase 3+5 死代码 (PriorityList / briefing_decision / DecisionView) | 0.5h |
| 单测 + 文档 | 1.5h |
| **合计** | **~11h (1.5 工作日)** |

---

## 7. 风险点

1. **周报数据可能不存在** — leadership-briefing skill 输出落 `~/.catfish/outputs/` 但格式不标准 (.docx / .xlsx). LLM 读 docx 不直接, 需要先 parse. **解法**: 用 parse_file.py 路径 (Companion 已有) extract markdown
2. **进度跟踪冷启动** — projects.md 第一周员工没写, LLM 推断不准. **解法**: 第一次跑 UI 提示 "鲶鱼帮你扫了 5 周 journal 推断你在做以下项目, 对吗?" 让员工 confirm 写入
3. **LLM 输出质量** — 15s 长 prompt 输出 6 块 JSON 不易稳定. **解法**: prompt 工程要严谨, retry 1 次, fail 走规则版
4. **跟客户实际节奏的对齐** — 你 (鸿波) 是 founder, 节奏可能比一般央企员工快. 5 周后到客户场试效果要校准
5. **数据源不齐时 graceful degrade** — 第一次跑没周报 / 没 projects / chat history 少, UI 不能空白. **解法**: 每块 ≥ 1 行内容兜底

---

## 8. 鸿波要拍 4 个决策

1. **周报数据**: A1 (扫 ~/.catfish/outputs/) / A2 (加 workplan.md 员工写) / **A1+A2 组合** ✓
2. **进度跟踪**: B1 (~/.catfish/projects.md 手动) / B2 (LLM 推断) / **B1+B2 组合** ✓ 
3. **LLM 调用**: **C1 (1 次喂全数据出 6 块)** ✓ / C2 (2 次并发)
4. **UI 布局**: D1 (2×3 矩阵卡片) / **D2 (2 大段 × 3 子段, 上而下阅读)** ✓ / D3 (Tab 切换)

每个选项给了**推荐项 ✓**, 你不动就走推荐, 不同意改另一个.

---

## 9. 实施前 checklist (鸿波确认)

- [ ] 4 个决策拍板 (第 8 节)
- [ ] 框架 6 块矩阵描述准确 (第 1 节)
- [ ] 数据源 7 类齐 (第 2 节)
- [ ] LLM prompt 草稿可接受 (第 3 节)
- [ ] UI 草图视觉风格对 (第 4 节)
- [ ] 工程量 ~11h 接受 (第 6 节)
- [ ] 风险评估认可 (第 7 节)

确认 7 项都 ✓ 后, 鸿波说一句 "开干" 我就开始写代码. 之前 4 处 align 都没做就埋头敲, 这次设计先行.

---

**5/21 教训**: 一天 ship 5 个 Phase 没 align 方向, 最后被一句"完全错了"推翻. 下次大改先写 design.md, 等 review.
