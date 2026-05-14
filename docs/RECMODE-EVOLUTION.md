# 鲶鱼 · RecMode 演进路径 v0.1

> **版本**: v0.1, 2026-05-15 凌晨 2:15 起草
> **背景**: 5/14 凌晨 ship V2 (selector 漂移 + DOM mutation + 14 天清), 5/15 凌晨鸿波连问 4 个真核心问题暴露 V2 的边界:
>   1. "页面大改 skill 还是要重录, 麻烦"
>   2. "自适应演化为啥难"
>   3. "vision-only fallback 准确度问题"
>   4. "到底哪个才是正确的按钮"
> **目的**: 把这些深度讨论 + 决策落档. 5/22+ RBAC sprint 完后 sprint 真做时直接照这文档执行, 不再凭印象答.
> **决策签名**: 鸿波 5/15 凌晨拍板路径 — 做 Tier 2 + 2.5 + 3 (多模态 grounding), **不做** Tier 4 (自适应演化).

---

## 1. UI 改变的 5 种真实类型 + 处理能力矩阵

| 类型 | 例子 | V2 (5/14 ship) | Tier 2 (#83) | Tier 3 V3 (#86) | 自动率 |
|---|---|---|---|---|---|
| **文本改名** | "应用" → "应用市场" tab | ✅ selector_repair vision fallback | — | — | 100% (V2 已够) |
| **CSS class 重构** | `div.tab-app` → `div.menu-item-x7` | ✅ `find_by_text` 走 text | — | — | 100% |
| **元素位置略改** | tab 从顶部移到侧边 | ✅ vision 看新布局 | — | — | 95%+ |
| **整页重排 + 流程改** | EIS 大版本升级 5 步变 8 步 | ❌ "DOM 大改, 重录" | ✅ 步骤级 patch (员工只演示新增步) | ✅ vision intent fallback | 80% (Tier 2) → 90% (Tier 3) |
| **同名多按钮** | 列表 100 行每行有 "通过 / 驳回" | ⚠️ 多模态 grounding 必需 | — | ✅ 4 信号融合 + active confirmation | 85-90% (Tier 3) |
| **新加二级菜单** | "资质" 现在要先点 "管理" 再点 "资质" | ❌ skill 流程错 | ✅ 步骤级 patch | ✅ vision intent | 80% → 95% |

**V2 ship 的能力**: 解决前 3 类小中改版 (~70% 实际场景). **不能解决**整页大改 + 同名多按钮 (~30% 真实场景但其中很多关键).

---

## 2. 工业界 vision LLM 真实数据 (打消"vision 万能"幻觉)

| 模型 | benchmark | 成功率 | 说明 |
|---|---|---|---|
| GPT-4V | WebVoyager | ~57% | 通用 web 任务 |
| Claude 3.5 Sonnet | WebVoyager | ~55% | |
| Anthropic Computer Use | OSWorld | ~40-50% | 桌面 + web 综合 |
| Gemini 1.5 Pro | WebVoyager | ~52% | |
| **生产 skill 要求** | — | **≥95%** | 国央企可用底线 |

**没人做到 95%**. 国央企 EIS 类系统更低 (中文小字体 + 密集表格 + 二级菜单 + 同名按钮).

**结论**: 单纯 vision (Computer Use 那种) **不是 catfish 真生产路径**. 必须**多模态 grounding** + **active confirmation**.

---

## 3. 4 Tier 演进路径

### Tier 1: 基础 RecMode (✅ 5/14 v0 ship)

CDP listener + aggregator + Companion UI + 14 天隐私清. 录一次跑一次, 不处理 UI 改变.

### Tier 2 (V2): selector 漂移自动修 (✅ 5/14 凌晨 ship)

`selector_repair.py` + `dom_summary.py`. selector_hint 找不到 → vision LLM 看截图 + 录制语境 → 找新位置. confidence ≥ 0.7 才返, 否则报 "DOM 大改重录". 解小中改版.

### Tier 2: 步骤级 auto-repair (待做, #83)

**核心思路**: skill 跑挂时**只补失败那一步, 不重录整个 skill**.

```
skill 跑到第 4 步 "点 资质管理" 找不到
   ↓
RecMode 弹: "这步找不到了, 帮我重做这一步"
   ↓
员工: 在浏览器手动点 → catfish cdp_listener 看员工真点了啥
   ↓
catfish 拿员工新操作 (selector + 截图) patch skill 第 4 步
   ↓
skill 继续跑后面的 (步 1-3 + 改后 4 + 5-end)
   ↓
员工只重做 1 步, 不是整个 skill
```

**工作量**: 5-7 天.
**ROI**: 客户痛点 80% 缓解 (从 "整个重录 1-2 周" → "演示 1 步 30 秒").
**实现**:
1. skill runtime 加 "失败 → 暂停 + 等 patch" 状态机
2. Companion 加 mini-RecMode UI (录单步)
3. gateway `/api/learn/patch_step` endpoint
4. skill 内部步骤版本化 (`step.v1` / `step.v2` / `patch_history`)

### Tier 2.5: telemetry-driven 推荐 (待做, #85)

**核心思路**: 收数据 + 给 admin 看推荐, **不让 ML 自动改**.

```
每次 skill 跑写 telemetry → ~/.catfish/skill_telemetry/<skill>.jsonl
   ├─ 步骤序号 / selector / 时间 / 成功失败 / DOM 快照 hash

admin Companion '健康度' 卡:
   ├─ "skill_X 最近 7 天失败率 5% → 60%"
   └─ 失败率突涨 → gateway 自动跑 vision 看页面 → 推荐新 selector

admin 一键 apply 或拒, 记 'who-when-why' 合规可追
```

**工作量**: 5-7 天.
**ROI**: 80% Tier 4 价值, 0% ML 风险 (人在 loop), 100% 合规.

### Tier 3 V3: 多模态 grounding + active confirmation (待做, #86)

**核心思路**: 不靠单一 vision (它会迷). 4 信号融合 + 不确定时问员工.

详见 §4 多模态 grounding + §5 active confirmation.

**工作量**: 3-4 周.
**目标准确率**: 自动 85-90% + active confirmation 99%+ (剩 1% 走 Tier 2 步骤 patch).

### Tier 4: 自适应 skill 演化 (❌ **不做** — 真理由)

完整分析见 §6. TL;DR: 千万投入 + 2-3 年研发 + 国央企不接受 ML 自动改 + 学术界都没解决 + Tier 2/2.5/3 已经 95%+. 投入产出极差.

---

## 4. 多模态 grounding 架构 (Tier 3 真实现)

vision LLM 单独 50-60% 准确率, **绝不能单独用**. 4 信号融合到 85-90%:

### 4 信号

```
找 "ISO9001 那行的通过" 按钮:

信号 1 (DOM): 找所有 type=button text="通过" 的元素
            → 100 个候选

信号 2 (Vision): 看截图, 锁定 "ISO9001" 那行的 bbox
            → 从 100 候选过滤到 1-2 个 (在那行的)

信号 3 (录制语境): 员工录时说 "点 ISO9001 那行通过, 它是第一行"
            → 验证候选位置在 viewport 顶部

信号 4 (锚点元素): 录制时 LLM 自动记 "通过按钮的左边是 ISO9001 text"
            → cross-check 候选左侧文字
```

### 融合规则 (confidence calibration)

```
4 信号都对 → confidence 0.95 → 自动点
3 信号对 → confidence 0.70 → 弹窗 active confirmation (§5)
2 信号对 → confidence 0.40 → 拒, 退化到 Tier 2 步骤级 patch
< 2     → 直接 Tier 2
```

### 为啥 4 信号比单 vision 强

- **vision 单独**: 100 个 "通过" 都长一样, 看不出哪行 → ~50% 准确率
- **vision + DOM**: vision 锁定 ISO9001 行 + DOM 找该行 button → ~75%
- **+ 录制语境**: 员工录时说"第一行" → 验证位置 → ~85%
- **+ 锚点 cross-check**: 锚点验证 → ~90%

**剩下 10%** 真模糊场景: 走 active confirmation, 不瞎点.

### 实现关键

1. **录制时**, LLM 自动给每步提锚点 (左边文字 / 上面表头 / 在 nav 第几个 tab / viewport 位置)
2. **存到 SKILL.md** 的 step metadata, 跑时一并加载
3. **跑时**, 4 信号并行查询, 等所有结果返回再 calibrate confidence
4. **失败时**, 不是 hard error, 而是 fallback 到下一档 (Tier 3 → Tier 2)

---

## 5. Active Confirmation 模式 (绝不瞎点)

### 为啥必须 active confirmation

国央企 EIS 里 "点错通过" = "给错单位发资质" = **真出事** (上市公司财务 + 央企合规事故). **错了的代价远大于慢**. 宁可问员工, 不可瞎点.

### 3 档 confidence 行为

```
高 confidence (≥0.9):
   静默自动点, 只 audit log
   "auto-clicked: <button>, confidence=0.95"

中 confidence (0.7-0.9):
   弹窗 [截图高亮候选] "我准备点这个, 你确认?"
   员工 5 秒内不点 → 默认 yes (不卡流程, 但记录到 telemetry)
   员工点 No → 退化到 Tier 2 step patch

低 confidence (<0.7):
   暂停 skill, 弹 "我不确定哪个对, 你帮我点一次, 我学着改"
   → 员工演示 → catfish patch 这一步 → 继续
```

### Audit 完整性

- 每次自动点都 log: skill / step / selector_used / confidence / signals_matched
- 中 confidence 弹窗: 员工是否 confirm + 用时
- 低 confidence patch: 员工新操作 + 改了哪步

合规 review 能查 "这单业务谁批的" → 落到 audit log 看是 catfish 自动点还是员工 confirm.

---

## 6. 不做 Tier 4 (自适应演化) 的真理由

Tier 4 = "skill 每次跑都收 evidence, ML 自动演化最稳的 selector 策略". 听起来很好, 但 **11 个真硬骨头**:

### 数据层 (3)

1. **Evidence 怎么收**: 每次跑 ~MB (DOM + 截图 + 调用链), 单公司 1000+ 次/天 = GB/天. 中央存 → 客户 IT 拒. 本地存 → 数据稀疏学不出.
2. **数据稀疏性**: 单公司单 skill 1 年几百次, 远不够 ML. 跨公司聚合破稀疏 → **绝对不可能** (国央企互不通).
3. **Survivorship bias**: 只成功的留下数据, 失败 trace 丢了, 没对照组.

### 归因层 (3)

4. **"成功"定义模糊**: skill returns "completed" 不代表真做对 (LLM 可能误判).
5. **变化归因不可能**: 失败率涨是 UI 改 / LLM 抽风 / 网络 / 浏览器版本? 没法严格归因.
6. **Counterfactual 需要**: "用 selector A 而不是 B 会成功吗" 没真跑过没法知道. Replay 在 dynamic DOM 上不准. Shadow execution 用户不接受.

### 学习算法层 (2)

7. **Online learning 灾难性遗忘**: 在线更新可能把好 skill 学坏. Cold start 没数据怎么学. Exploration vs Exploitation tradeoff.
8. **抽象层次选不对**: 学 selector 太细 / 学 intent 太粗 / 中间结构化抽象真有用但难自动学.

### 工程 + 合规层 (3)

9. **A/B 实验 infra**: 没小心翼翼的 A/B 不知哪个 selector 真好. Netflix 级实验平台 — 工程成本极高 + 客户不要.
10. **可解释性 + 合规**: ML 自动改 skill, IT 怎么审? 国央企每变更必须可追溯. "AI 自己改不知为啥" → CISO 立刻否决.
11. **跟现有架构冲突**: skill = `SKILL.md + main.py` 给人看 + 给 hermes 跑. ML 自动改 main.py → IT 改时被覆盖 → 需要 human authored vs ML overlay 分离 + 冲突解决, 大改造.

### 学术界做到哪一步

| 工作 | 做了 | 缺啥 |
|---|---|---|
| OpenAdapt | 录制 + 重放 + LLM 改 script | 没真在线演化 |
| WebGPT / WebVoyager | Single-agent vision | 不持久化学习 |
| AutoGPT / BabyAGI | Reactive agent | 没记忆 |
| RL for browser agents | 学术 paper | 没生产 |

**Anthropic / OpenAI / Google 公司层面都没做出来真 Tier 4**. Computer Use 是 Tier 3 范式 (vision intent), 不是真持续学习.

### 真做要啥

- 专门 research team (3-5 人, 含 ML 研究员)
- 数据 infra (隐私安全收集 + 聚合 + 查询)
- A/B 实验平台
- **6-12 月**出原型, **2-3 年**出可用产品
- **不保证成功** (是真未解决问题)

成本: 千万级人民币, ROI 不清晰.

### 决策

**不做**. Tier 2 + 2.5 + 3 加起来已经 95%+ 真生产场景, Tier 4 多解决的边际场景 (5%) 不值得千万投入. 客户 + 合规 + 学术现状全反对.

---

## 7. RecMode V3 — 录制时多 invest

跑时准确度上限 = 录制时收集的信息量. 录制要多 invest 才能跑时少痛.

### V3 录制时改动

1. **每步 LLM 自动提多锚点**:
   - 按钮左边文字 (`左 = ISO9001`)
   - 上面表头 (`上 = 资质名称 / 到期日 / 操作`)
   - 在 nav 第几 tab (`= nav 第 1 tab`)
   - viewport 位置 (`= 第 1 行 + 视口顶部`)

2. **录完每步问员工 confirm**:
   - "你点的是 'ISO9001 那行的通过', 不是 'ISO9002', 对吧?"
   - 员工 confirm 后存到 step.hint.disambiguation

3. **录制时鼓励员工说话**:
   - 语音转文字进 step.hint.user_narration
   - "我点的是第一行的通过" → 跑时 vision LLM 看这个 context 直接有指引

4. **录两次取交集** (可选, admin 配):
   - 员工录两次同一流程
   - catfish 自动找两次都用的特征 (锚点 / 位置 / 文字 一致的)
   - 这些是 "稳的" — selector 漂时优先依赖这些

### 录制时 UX

不能让员工觉得"录制好烦". 设计:
- 锚点提取 LLM 后台跑, 员工无感
- confirm 弹窗只对模糊场景弹 (单步 1 个候选时不弹)
- 说话是 RecMode v0 已有 (录音 toggle)
- 录两次是可选 mode, 默认关 (高价值 skill 才开)

---

## 8. 准确度真承诺 (合理化客户预期)

| 场景 | V2 准确率 | Tier 2 后 | Tier 3 V3 后 |
|---|---|---|---|
| 文本改名 (例 "应用" → "应用市场") | 90% | 95% | 98% |
| CSS class 重构 | 95% | 95% | 98% |
| 元素位置略改 | 85% | 90% | 95% |
| 整页重排 (大改) | 0% (报错) | 80% (步骤 patch) | 90% |
| 同名多按钮 (列表 100 行) | 50% (瞎选) | 60% | 90% (多模态 grounding) |
| **加权平均** | ~60% | ~85% | ~92% |
| **+ active confirmation 兜底** | — | ~95% | ~99% |

**对客户讲法**:
- "鲶鱼 RecMode v2 (5/14 ship) 解决小中 UI 改 ~70% 场景, 大改报错让员工重录"
- "v3 (6 月底 ship) 解决大改 + 同名多按钮 ~95% 场景"
- "剩 5% 模糊场景, 鲶鱼**不会瞎点**, 弹窗问员工确认 — 这是国央企'宁慢勿错'底线"
- "100% 自动化做不到 (没人做到, 包括 OpenAI Computer Use), 但 99% 自动 + 1% 员工 1 秒 confirm 是真生产可用"

---

## 9. 实施排程

```
✅ 5/14 凌晨   Tier 1 + 2 V2 ship (#59 #67 #68 #70)
🔵 5/15-5/22  RBAC sprint (#50, 不动 RecMode)
              5/15 早 #66 真测一遍 V2 端到端

⏳ 5/22-6/05 RecMode V3 sprint (3 周)
   week 1 (5/22-5/26):  Tier 2 步骤级 auto-repair (#83) — 5-7 天
   week 2 (5/27-5/30):  Tier 2.5 telemetry + admin 推荐 (#85) — 5-7 天
   week 3 (6/02-6/05):  Tier 3 多模态 grounding + active confirmation (#86) — 5-7 天
                        + RecMode V3 录制时多锚点 (跟 #86 一起做)

⏳ 6/06+      真客户试用 + 反馈 → 微调

❌ 永不做     Tier 4 自适应演化 (千万投入 + 2-3 年 + ROI 极差)
```

---

## 10. 决策签名

> **v0.1** = 2026-05-15 凌晨 2:15 起草.
> 起因: 5/14 ship V2 后, 5/15 凌晨鸿波连问 4 个真核心 (大改怎么办 / 自适应难度 / 准确度天花板 / 哪个按钮才对). 这些深度讨论必须落档防忘.
>
> **决策点**:
>
> 1. ✅ V2 (5/14 ship) 解小中 UI 改, 大改 + 同名多按钮 仍痛 — 承认
> 2. ✅ Tier 2 步骤级 auto-repair (#83) — 5/22+ 做, 5-7 天, ROI 高
> 3. ✅ Tier 2.5 telemetry-driven 推荐 (#85) — 5/27+ 做, 5-7 天
> 4. ✅ Tier 3 V3 多模态 grounding + active confirmation (#86) — 6/02+ 做, 5-7 天
> 5. ❌ Tier 4 自适应演化 — **永不做**. 11 个真硬骨头 + 学术界没解决 + 国央企不接受 + ROI 极差. Tier 2+2.5+3 加起来 95%+, 边际不值千万投入.
> 6. ✅ "宁慢勿错" 底线 — active confirmation 必须有, 国央企"点错通过 = 真出事", 不能用 ML 准确率换效率
> 7. ✅ vision-only 不是路径 — 工业界 50-60% 远不到生产 95%. 多模态 grounding 才是真路径
> 8. ✅ 录制时多 invest (V3 加锚点 + confirm + 多次录) — 跑时少痛, 是关键投资
>
> **客户承诺**: 95% 自动 + 5% active confirmation, 99%+ 真生产可用.
> 这跟 "100% 自动化" 不同 — **诚实**告诉客户 5% 要员工 1 秒 confirm, 不画饼.
