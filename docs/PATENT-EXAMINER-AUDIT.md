# Catfish 5 个候选方向 — 资深专利审查员 Audit (Adversarial)

> 视角切换说明: 前一份 `PATENT-LANDSCAPE.md` 是 **advisor 视角** ("narrow 一下可能能过"). 本份是 **审查员视角** — CNIPA / USPTO / EPO 一线审查员 default reject 姿态, 严格按法条 + 引证驳回. 两份对比看, 给你更接近真实 OA (Office Action) 结果的预期.
>
> 我审 5 件 (假设我是有 8+ 年经验的电学发明审查员, 对 LLM / agent 领域熟悉, 见过 50+ AI 申请). 视野按 **CNIPA** 标准走主审, 兼顾 USPTO §101 Alice-Mayo + EPO 52(2)(3) 软件 patent excluded subject matter.

---

## 我的审查框架 (default reject 姿态)

每件按这 5 关过, 一关不过就 OA reject:

| 关卡 | 中国 | USPTO | EPO |
|---|---|---|---|
| **客体适格** | 专利法第 25 条 / 审查指南第二部分第九章 ("智力活动规则", "纯算法", "纯信息呈现" 不授权) | 35 USC §101 Alice/Mayo 二步法 | EPC 52(2)(3) 数学方法 / 智力活动 / 信息呈现 excluded |
| **新颖性** | 22 条 2 款 (单一对比文件全覆盖 = X 类) | 35 USC §102 | EPC 54 |
| **创造性** | 22 条 3 款 (本领域技术人员显而易见 = Y 类组合) | 35 USC §103 KSR-Graham | EPC 56 (problem-solution approach) |
| **充分公开** | 26 条 3 款 (实施例支持权利要求保护范围) | 35 USC §112(a) written description | EPC 83 |
| **清楚 / 必要技术特征** | 26 条 4 款 (功能性限定要有具体实施方式) | 35 USC §112(b) | EPC 84 |

**审查员的默认心理**: "申请人想 frame 一个尽量宽的 claim 给自己留余地. 我的任务是用最近 prior art 把这个 claim 缩到最窄, 直到不构成保护意义". 这跟 advisor "怎么让 claim 活下来" 是反向.

---

## 方向 1: 客户端零外泄 + 中央 metering 双盲

### 审查员决策树

#### 客体适格 (§101 / 第九章): **PASS**

- claim 1 (拟): "一种 LLM 调用代理系统, 包括: 客户端设备 (含 SQLite 持久化模块, 网络接口模块), 中央服务器 (含 metering 接收模块)..."
- 含具体硬件 (设备 / 数据库 / 网络接口) + 解决具体技术问题 (数据隐私 + 企业审计)
- 中国第二部分第九章 6.1.2 "包含技术特征的方法" → 客体过
- USPTO Alice step 2: SQLite 持久化 + network interface 是 "additional inventive concept" → 客体过
- **结论: 客体不构成 reject 理由**

#### 新颖性 (§102 / 22 条 2 款): **PASS**

我搜了 5 个 hour, 找到的最近 X 类候选:
- **US12556533 (Gen Digital)** — 中央代理隐藏身份模式. **不是 client-side 全保留 + 中央 metadata only**. 单一文献**不构成 X 类 anticipation**
- **Salesforce Einstein Trust Layer (patent family)** — 仍是中央化 SaaS, 平台留 audit trail (内容也在). 跟 catfish "对话不过任何中央" 有本质区别. **不 X**
- **US12388799 (Split inference)** — 模型 split, 跟"对话本地化"不是一回事. **不 X**

**结论: 新颖性可过 (假设 N1+N3 narrow 后)**

#### 创造性 (§103 / 22 条 3 款): **REJECT (第一回合)**

这是我会下手的关卡. **OA1 我会写**:

> 权利要求 1 相对于以下对比文件的组合, 对本领域技术人员是显而易见的, 不具备专利法第 22 条 3 款规定的创造性:
>
> **对比文件 1**: US12556533 (Gen Digital) — 公开了 LLM 调用代理系统, 包括客户端转发 + 中央代理身份隐藏机制
>
> **对比文件 2**: US20250238616A1 — 公开了 LLM token 用量上报作为计费依据的方法
>
> **对比文件 3**: Salesforce Einstein Trust Layer 公开技术 — 公开了 LLM 调用的脱敏 + audit log 上报模式
>
> 本领域技术人员有动机将对比文件 1 的中央代理改为客户端持久化 (节省中央服务器存储), 并结合对比文件 2 的 token 计费 + 对比文件 3 的脱敏 audit, 得到权利要求 1 的方案. 该组合**没有产生超出三件文件预期之外的技术效果**, 因此不具备创造性.

**申请人答复策略 (如果你是申请人)**:
- 强调 N1 — "工具调用参数 + 工具结果"全链路本地化, **现有 prior art 都在 chat 层 (prompt/response), 没在 agent 层** → 这是技术效果上的区别
- 强调 N3 — "服务器密码学不可重建", 量化 metering payload 字段 → "**仅含 token count + hash, 缺失重建对话的必要信息**" — 这是可证明的技术特征, 不是抽象主张

**第二回合: 修改后 50/50**.

我作为审查员会**继续找 prior art**, 比如:
- IBM 在 agentic AI 已有多件 application, 是否覆盖 "tool-call argument local persistence"?
- Anthropic Claude Desktop 用户协议是否描述 "chat history local"?
- 但**如果申请人 N1+N3 修改成功 + 量化技术效果**, 我大概率在第三回合放行

**最终授权概率: 40-55%** (修 1-2 轮 OA 后)

#### 充分公开 (§112 / 26 条 3 款): **WATCH**

- catfish 的 SQLite schema + metering payload 字段需要具体写到说明书
- "密码学上不可重建" 这种 negative limitation 必须给**具体证明方法** (e.g. metering payload 完整字段列表 + 缺失字段证明 + 已知重建算法的反证)
- 写不清楚 → 26 条 3 款 reject

**结论: 申请书写好就行**

#### 清楚 (§112(b) / 26 条 4 款): **WATCH**

- "脱敏 metering" 这种功能性表述要有具体内容支持
- 必要技术特征: 客户端设备 / SQLite / 网络接口 / metering 字段集合 / hash 算法 — 必须都在 claim 1

### 审查员总评

| 维度 | 评分 | 备注 |
|---|---|---|
| 客体 | ✓ 易过 | 含硬件 + 技术效果 |
| 新颖性 | ✓ 易过 | 没有强 X 类 |
| 创造性 | ◑ 拉锯 | 第 1 轮我会 reject, 修 1-2 轮后大概率过 |
| 充分公开 | ✓ 写好就行 | metering 字段要列尽 |
| 清楚 | ✓ 写好就行 | 功能性限定要具体化 |

**最终授权概率: 40-55%**, 修 1-2 次 OA, **6-12 月** 国内授权.

如果我是审查员, 这件件**值得申请人投入** — 是 5 件里我最难驳的.

---

## 方向 2: SSE inline approval gate

### 审查员决策树

#### 客体适格: **WATCH (重要)**

- 中国第二部分第九章: SSE event injection + closure capture 涉及 "数据传输方法 + 软件实现", 客体过.
- **但 USPTO §101 Alice/Mayo 是高风险**:
  - Alice step 1: "abstract idea of human approving software action mid-execution" → 高度可能被认定 abstract idea
  - Alice step 2: 必须证明 "additional inventive concept" — SSE inline 是 specific technical implementation, 应可过, 但 PTAB 现在严格, 估计 §101 reject 后申请人需要 amend 加更多 technical detail
- EPO 52(2)(c): software-only method 可能 excluded, 但有"具体 technical effect" 可救 (不断流是 UX technical effect)

**结论: CN/EPO 过, USPTO §101 风险中-高**

#### 新颖性 (§102 / 22 条 2 款): **PASS**

我找到的最近候选:
- **AeneasSoft USPTO Provisional 2025** — pause/resume state machine, **不是同一 stream inline 注入**. 不 X
- **Anthropic Claude Code auto-mode** — model-based classifier, **不是 stream 内 inline event**. 不 X (而且 Anthropic 是 blog 公开 9 月, 不是 patent)
- **LangChain `interrupt()`** — checkpoint resume, **不是同一流 inline**. 不 X
- **Salesforce Agentforce Trust Layer** — policy mediator 拦截, **非 SSE inline**. 不 X
- **Seek AI 2 件 grant** — query 审批, scope 完全不同. 不 X

**结论: 新颖性可过**

#### 创造性 (§103 / 22 条 3 款): **REJECT (第一回合)**

我作为审查员, **OA1 会用 KSR 标准三组合**:

> 权利要求 1 相对于以下对比文件组合, 对本领域技术人员显而易见:
>
> **对比文件 1**: AeneasSoft 公开技术 — 公开了 in-process LLM tool-call 暂停-恢复机制
>
> **对比文件 2**: HTML5 SSE Standard (W3C, 2009) — 公开 SSE 流可携带任意 event 数据
>
> **对比文件 3**: Python contextvars PEP 567 (2018 内置库) — 公开 contextvar 跨协程数据传递机制
>
> 本领域技术人员有动机将对比文件 1 的暂停机制用对比文件 2 的 SSE event 实现, 并用对比文件 3 的 contextvar 路由 session_key, 得到权利要求 1 的方案. **是常规组合**, 不构成创造性.

**这是个标准 KSR three-way combination reject**.

**申请人答复策略**:
- 强调"**不打断 stream**" — 这是 user 感知层的 technical effect (审批不断对话连续性), 跟 AeneasSoft pause/resume "断流再恢复" 有本质 UX 区别
- 强调 **server-side 60s timeout fallback + session_key 多并发独立审批栈** 这三件套组合的 specific technical features

**第二回合 50/50** — 取决于申请人是否能说服我"stream 连续性"是不可预料的技术效果. 如果只说"用户体验更好", 我会以"用户体验是商业方法, 不是技术效果"再 reject. 如果给具体技术数据 (e.g. 比 pause/resume 少 X% 网络往返, 减少 Y ms 延迟), 我可能放.

**最终授权概率: 30-45%**, 修 2-3 轮 OA.

#### 充分公开 (§112): **PASS**

P15 / P15.2 实现完整, 实施例够.

#### 清楚: **WATCH**

- "闭包反射" 是不规范术语, 中国审查员可能要求改为 "通过捕获 stream queue 引用的回调函数 + 修改原函数 attribute 的方式"
- session_key 路由 — 多并发场景实施例必须给出

### 审查员总评

| 维度 | 评分 | 备注 |
|---|---|---|
| 客体 | ◑ CN/EPO 过 / **USPTO 高风险** | Alice step 2 必须 amend |
| 新颖性 | ✓ 过 | 没有 X 类 |
| 创造性 | ✗ → ◑ 拉锯 | OA1 必 reject. 修 2-3 轮 50/50 |
| 充分公开 | ✓ | — |
| 清楚 | ◑ | 术语需规范 |

**最终授权概率: 30-45%**, 修 2-3 次 OA, 国内 **12-18 月** 授权 (比方向 1 慢).

US provisional → non-provisional 路径, USPTO §101 第一次 OA 几乎肯定 reject, 申请人需要 amend 加 technical specificity → 35-45% 最终授权.

**这件件我作为审查员的态度**: 我相信申请人**最终能 narrow 出可授权 claim**, 但会比方向 1 难, 也比方向 1 窄. **claim 保护范围实际收益打 6 折**.

---

## 方向 3: 录屏 → skill freeze (RecMode)

### 审查员决策树

#### 客体适格: **PASS** (但勉强)

- 录屏 (硬件 capture) + LLM 推理 + 文件生成 — 客体过
- 但**如果 narrow 到 "frozen marker (version 字符串 + description 签名)"** — 这是 file format / data structure choice, 中国第二部分第九章 §6.2 "纯信息表示" 可能 reject
- USPTO §101: workflow generation method 已有大量 grant (UiPath / Microsoft), 客体不会 reject
- 结论: 客体勉强过

#### 新颖性 (§102 / 22 条 2 款): **REJECT (硬伤)**

**SkillForge** (skillforge.expert, 2025 公开产品 + HN 公开披露 HN id=47066593) 跟 catfish RecMode **几乎 1:1**:

> SkillForge 公开技术方案:
> 1. 录屏员工操作
> 2. 多 pass LLM (coarse → fine → metadata merge → intent grouping)
> 3. 输出 **SKILL.md** (跟 catfish 完全相同的产物格式)
> 4. 兼容 Claude Code / Codex

如果 SkillForge 公开日期早于鸿波 catfish RecMode 第一次 implement (这个要核实, 但 SkillForge 2025-04 公开, 鸿波 catfish RecMode 5/12 老 freeze 记录显示 5/12 在用), 则:

**OA1 我会写**:

> 权利要求 1 的全部技术特征已被对比文件 1 (SkillForge 公开技术方案, 2025-04 HN 披露) 完整公开. 权利要求 1 不具备专利法第 22 条 2 款规定的新颖性, 全部驳回.

**这是 X 类全覆盖驳回 — 申请人几乎无法答辩**.

**唯一可能救活**:
- catfish 的 "frozen marker + 双轨审定 dashboard" — SkillForge 没这层
- 但这部分独立看就是 "file format + business workflow", 创造性极弱

**第二回合**:
- 申请人 amend 加 "frozen marker (version-frozen suffix + description signature)" 限定
- 我 (审查员) 会引 **Microsoft SkillOpt (arXiv 2605.23904)** — 已用 "frozen agent + skill 文档" 概念. **C2PA 标准** (内容来源 / AI 生成标识) 已成熟规范
- 即使 narrow 也以 §103 reject "frozen marker 是显而易见的 file format 选择"

**最终授权概率: 15-25%** (即便 amend 也很难过 §103)

#### 创造性: 跟 §102 强相关. 即便修过 §102, §103 几乎必 reject.

#### 充分公开: 不是问题. 实施例够.

#### 清楚: "frozen marker" 必须具体定义.

### 审查员总评

| 维度 | 评分 |
|---|---|
| 客体 | ◑ 勉强过 |
| 新颖性 | ✗ **SkillForge 硬伤** |
| 创造性 | ✗ Microsoft SkillOpt + UiPath US11372380 + Power Automate Record with Copilot 三连击 |
| 充分公开 | ✓ |

**最终授权概率: 15-25%**, 即使授权 claim 也极窄, **没有商业 enforce 价值**.

**作为审查员我的态度**: 这件件**建议申请人撤回**, 浪费 OA 答复时间和官费. 如果坚持要 file, **预期被 reject 后转复审失败**.

---

## 方向 4: xcatfish-user runtime patch

### 审查员决策树

#### 客体适格: **REJECT (§25 / Alice §101)**

- 中国第二部分第九章 §6.2.2: "对一段计算机程序的优化方法, 如果它解决的问题是程序运行效率而不是技术问题, 不属于专利法保护客体"
- monkey-patch 是 "改变程序运行时行为" — 这是程序逻辑问题, 不是技术问题
- 锚点验证 (anchor verification) 是数学/逻辑判断 — 落入第 25 条 "智力活动规则"
- USPTO Alice step 1: "abstract idea of patching software at runtime" — 明显 abstract idea
- Alice step 2: 申请人需要证明 "additional inventive concept beyond conventional patching" — gorilla 库 / kpatch / AspectJ 都已经做了, 不构成 inventive concept

**OA1 我会写**:

> 权利要求 1 的方案实质上是对计算机程序内部逻辑的调整方法, 属于专利法第 25 条第 1 款第 (二) 项规定的"智力活动的规则和方法", 不属于专利保护客体, 全部驳回.

**这是客体驳回 — 没有 amend 空间 (除非完全重写 claim)**.

#### 新颖性: 即便客体过了, 也是硬伤

- **US5581697A (Sun 1996)** "patch sites in load objects" — **patch site = catfish 锚点**. 完整 X 类引证
- **gorilla 库 (PyPI, 2021)** `allow_hit=False` + `store_hit` — Python monkey-patching 的 anchor verification 完整开源实现
- 多个 X 类引证组合, 即便客体放行, §102 全死

#### 创造性: 0%

monkey-patching 是 Python 文化 30 年 + AOP 1997 + Dyninst 1990s + LangChain Middleware 2024 全覆盖. **任何 narrow 都救不了**.

### 审查员总评

| 维度 | 评分 |
|---|---|
| 客体 | ✗ **§25 + Alice 双重 reject** |
| 新颖性 | ✗ Sun 1996 + gorilla |
| 创造性 | ✗ 30 年 prior art 海 |

**最终授权概率: < 5%**.

**作为审查员我的强烈建议**: 撤回. 你提到的"anchor verification on upgrade" 不是发明, 是工程实践的 best practice.

---

## 方向 5: wiki 4 信号推荐

### 审查员决策树

#### 客体适格: **REJECT (§25 / Alice §101 / EPC 52(2)(c))**

- 中国第二部分第九章 §6.2.1: "如果方法权利要求中所涉及的方法仅仅是抽象算法或纯数学计算的, 不属于专利保护客体"
- 4 信号加权求和 = 数学算法
- "解释性符号 ↔ ◇ ∗ ≈" — EPC 52(2)(d) "schemes for presenting information" excluded
- USPTO Alice step 1: "abstract idea of weighted recommendation" — 明显 abstract
- Alice step 2: "additional inventive concept" — 没有

**OA1 我会写**:

> 权利要求 1 的方案实质上是对节点关联度的数学计算方法 + 信息呈现方式, 不涉及解决具体技术问题, 不构成专利法第 2 条第 2 款规定的技术方案, 属于第 25 条第 1 款第 (二) 项 "智力活动的规则和方法", 全部驳回.

#### 新颖性: 即便客体过, 也是硬伤

- **US8260787B2** "multiple recommenders 加权组合" 完整上位 = X 类
- **US12566536 (Atlassian)** Confluence Related Pages = X 类近场
- **Adamic-Adar 2003 paper** = X 类期刊文献

**3 个 X 类引证轮流上**.

#### 创造性: 0%

- "权重 3/4/1.5/1" 是常规数值优化, 中国审查指南明确不构成创造性
- "frontmatter sources 字段" 是 data input 选择
- Liben-Nowell 2007 综述 + Lü-Zhou 2011 综述 — 加权组合是 textbook standard practice

### 审查员总评

| 维度 | 评分 |
|---|---|
| 客体 | ✗ §25 / Alice / EPC 全 reject |
| 新颖性 | ✗ US8260787 + Atlassian + AA paper |
| 创造性 | ✗ 60 年学术 prior art 海 |

**最终授权概率: < 5%**.

**强烈建议撤回**. 这是典型的 "申请人在凑数 file" 案例, 审查员一眼能看穿.

---

## 综合 Summary 表 (审查员视角)

| # | 方向 | 客体 | 新颖性 | 创造性 | 充分公开 | 授权概率 | OA 轮数预期 | 我的建议 |
|---|---|---|---|---|---|---|---|---|
| 1 | 客户端零外泄 + metering 双盲 | ✓ | ✓ | ◑ | ✓ | **40-55%** | 1-2 | **值得 file** — 是 5 件最难驳的 |
| 2 | SSE inline approval gate | ◑ (US §101) | ✓ | ✗→◑ | ✓ | **30-45%** | 2-3 | **可 file** — 但 claim 比预期窄, 实际保护范围打 6 折 |
| 3 | 录屏 → skill freeze | ◑ | ✗ **SkillForge** | ✗ | ✓ | **15-25%** | 2+ | **建议撤回** — 浪费官费 + 答复时间 |
| 4 | xcatfish-user runtime patch | ✗ §25 / Alice | ✗ Sun 1996 + gorilla | ✗ | — | **< 5%** | 1 (直接 §25) | **不要 file** — 客体硬伤 |
| 5 | wiki 4 信号推荐 | ✗ §25 / Alice / EPC | ✗ US8260787 + Atlassian | ✗ | — | **< 5%** | 1 (直接 §25) | **不要 file** — 客体硬伤 |

---

## 跟 advisor 视角的对比 (诚实的 reconciliation)

advisor 视角 (前一份报告) 给方向 1+2 都 2.5/5 剩余空间, 建议 file. **审查员视角更悲观但更真实**:

| 方向 | advisor 视角 (剩余空间) | 审查员视角 (授权概率) | 差距分析 |
|---|---|---|---|
| 1 | 2.5/5 (说 N1+N3 可 file) | 40-55% (修 1-2 OA 后过) | 一致, 值得投 |
| 2 | 2.5/5 (说三件套可 narrow) | 30-45% (修 2-3 OA 后过, USPTO §101 高风险) | **审查员更悲观** — claim 实际收益打折 |
| 3 | 1.5/5 (说 narrow 到 frozen marker 可一试) | 15-25% (SkillForge 硬伤) | 审查员更悲观 — **建议直接撤回** |
| 4 | 0.5/5 (advisor 也说不要 file) | < 5% | 一致 |
| 5 | 0.5/5 (advisor 也说不要 file) | < 5% | 一致 |

**关键 reconciliation**:
- 方向 3 advisor 说"narrow 到 frozen marker + 双轨审定 1.5/5 可一试", **审查员视角直接撤回** — file 也是 §102 硬伤撞死, 浪费钱
- 方向 2 USPTO §101 风险被 advisor 视角低估 — 实际 Alice/Mayo 框架下软件方法 patent 现在 PTAB 严, 至少要 amend 1 次加 technical specificity
- 方向 1 两视角一致, 是真值得 file 的件

**修正后的最终建议**:
1. **方向 1 file** — CNIPA 主战场, 6 个月内完成
2. **方向 2 file US provisional** 占位 — 但**预期 claim 实际保护范围窄于设计**, 商业 license 议价能力有限
3. **方向 3 撤回** — advisor 视角太乐观, 撤回不申
4. **方向 4 / 5** — defensive publication

**修正后预算**:
- 方向 1 CNIPA: 1.5-2.5 万 RMB (含 1-2 轮 OA 答复)
- 方向 2 US provisional: 1-2 千 USD (8-15 千 RMB)
- (取消方向 3 一件 narrow 的预算 1.5-2.5 万)
- **6 个月内总投入: 2.5-4 万 RMB** (比 advisor 视角的 4-7 万省 1.5-3 万)

---

## OA1 草稿 (帮申请人提前 mental rehearsal)

我作为审查员, 收到方向 1 + 2 申请后第一次审查意见会写:

### 方向 1 OA1 (中国 CNIPA)

```
审查意见通知书 (第一次)

申请号: CN2026XXXXXXXXX.X
发明名称: 一种本地优先 LLM 调用代理系统及方法

一、 实质审查意见

权利要求 1 不具备专利法第 22 条 3 款规定的创造性, 理由如下:

对比文件 1: US 12556533 B1 (Gen Digital, 公开日 2026-02-17)
公开了一种 LLM 调用代理系统, 包括客户端转发模块, 中央代理服务器,
代理服务器持其自身 LLM 凭据访问外部 LLM, 隐藏员工身份. (见
说明书第 [0023]-[0045] 段及附图 1).

对比文件 2: US 2025/0238616 A1 (公开日 2025)
公开了 LLM 调用按 token 计数计费, 服务器仅记录 user_id, timestamp,
model, token_count, cost 五项 metering 元数据. (见权利要求 7).

权利要求 1 与对比文件 1 的区别技术特征是:
(1) 客户端持有完整对话本地持久化, 不上传服务器
(2) 服务器接收的 metering payload 不包含可重建对话内容的字段

针对区别技术特征 (1): 对比文件 1 的中央代理改为客户端持久化, 是
为减轻服务器存储压力的常规设计选择, 本领域技术人员无需创造性劳动
即可想到.

针对区别技术特征 (2): 对比文件 2 已公开 metering 元数据集合
(user_id/timestamp/model/token/cost), 本特征即对比文件 2 的直接应用.

综上, 权利要求 1 相对于对比文件 1 + 2 的组合是显而易见的,
不具备创造性, 应予驳回.

二、 申请人答复期限: 收文日起 4 个月内.
```

**申请人答复要点**:
1. amend claim 1 加 N1 "tool-call argument + tool-result 也本地化" (现有 prior art 没在 agent 层)
2. amend claim 1 加 N3 量化技术特征 ("metering payload 字段限于 {user_id, ts, model, token_count, cost}, 不含 prompt / response / tool_args / tool_result, 故服务器无法重建对话内容")
3. 提交一份**对比实验数据** — catfish 实测的 metering payload 用对比文件 1+2 的方法无法重建对话 (信息论证明)

**第二回合 50/50**.

### 方向 2 OA1 (USPTO §101)

```
United States Patent and Trademark Office
Office Action

Application No.: 18/XXX,XXX
Title: Method and System for Inline Approval Gate in LLM Token Stream

REJECTION UNDER 35 USC §101

Claim 1 is rejected under 35 USC §101 as directed to non-statutory
subject matter (abstract idea).

Step 2A Prong One: Claim 1 recites an abstract idea of "obtaining human
approval for a software action during execution" — this is a mental
process / certain methods of organizing human activity.

Step 2A Prong Two: Additional elements (SSE stream, contextvar, closure
reflection) are recited at high level of generality. They are
generic computing components used in conventional manner to implement
the abstract idea.

Step 2B: No additional elements that, individually or in combination,
provide an inventive concept beyond conventional computer activity.

Claim 1 is rejected.

Applicant may amend to add specific technical features that transform
the abstract idea into a patent-eligible application.
```

**申请人答复要点**:
1. amend claim 加 "specific SSE protocol-level event injection without breaking HTTP response stream connection" — 这是 network protocol level technical effect
2. amend claim 加 "Python contextvar PEP 567 cross-coroutine session_key routing" — 具体到 PEP 567 标准引用
3. amend claim 加 "60-second server-side timeout fallback with concurrent session_key multiplexing" — quantified parameters
4. 提交 declaration (35 USC §1.132) 说明 "stream-continuous approval" vs "pause-resume" 的 measurable technical difference (e.g. 网络 RTT 节省, 延迟测试数据)

**第二回合 §101 答辩后可能转向 §103 reject** — 是 USPTO software patent 标准走法.

---

## 我作为审查员的"职业建议" (站在你这边的 honest take)

如果我是鸿波你的资深审查员朋友, 私下跟你说:

**"老弟, 别 file 方向 3-5. 真的别. 我审过太多类似的申请, 都是 §25 / §101 / SkillForge 一类硬伤撞死. 你那点 freeze marker / monkey-patch anchor / 4 信号加权, 哥们当年读博时候就玩过, 不算发明."**

**"方向 1 和 2 你 file, 但别预期保护范围多大. 中国审查实践现在对 software patent 严格, 我见过 30% 申请到授权时 claim 已经 narrow 到自己都嫌窄. 你商业 license 议价的话别指望 claim 直接卡死大厂 — 真打官司他们的法务团队能找 100 个理由 invalidate. patent 真正的价值在 dual licensing 时给企业客户一个'我们有 IP, 你必须 license'的法律抓手, 让企业 PR + procurement 流程能走通."**

**"真正的护城河, 还是 catfish 的开源社区 + 国企本土化合规 + 客户关系. patent 是辅料, 不是主菜."**

---

## 报告结尾

本份 audit 用 5 个法条关卡 (客体 / 新颖性 / 创造性 / 充分公开 / 清楚) 跑了一遍, 给每个方向都模拟了 OA1 + 答复轮数预期 + 最终授权概率.

跟 advisor 视角的主要差距:
- **方向 2 USPTO §101 风险被低估** — Alice/Mayo 框架下要 amend 1-2 次加 technical specificity
- **方向 3 不该 file** — SkillForge 硬伤 + advisor 视角太乐观
- **方向 1 一致** — 真值得投

**修正后建议**: file 方向 1 (CNIPA) + 方向 2 (US provisional), 撤回方向 3, 方向 4/5 走 defensive publication. **6 个月内总投入 2.5-4 万 RMB**.

— 审查员 audit 完
