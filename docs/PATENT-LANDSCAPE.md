# Catfish 专利布局 Prior Art 检索报告 (v1.0)

> 鲶鱼 / catfish — 本地优先 LLM agent 桌面应用 (Tauri + React + Rust)
> 作者 — 鸿波 (个人开源项目, 业余时间开发, 暂未 public)
> 目的 — 评估候选发明专利的剩余空间, 决定 file 哪几个 + 怎么 narrow
> 检索范围 — Google Patents + USPTO + CNIPA + WIPO PCT
> 检索日期 — 2026-06-07
> 报告作者 — claude 跑 5 parallel agent fan-out research

---

## TL;DR (一页摘要)

**推荐 file 2 个**:

| 优先 | 方向 | 剩余空间 | 商业化价值 | 推荐 file 路径 |
|---|---|---|---|---|
| **主** | 客户端零外泄 + 中央 metering 双盲架构 (narrow 到 N1+N3) | 2.5/5 | 中国国企 / 金融刚需 (高) | CNIPA 发明专利 + 美国 provisional 占位 |
| **副** | LLM stream 中 inline approval gate (SSE inject + contextvar + 60s timeout 三件套) | 2.5/5 | 美国 Anthropic/OpenAI license 目标 (中-高) | 先 US provisional 占时间戳, 12 月内决定 PCT |

**不推荐 file 3 个** (改为 defensive publication, 开源 README/CHANGELOG/blog 公开建立 prior art):

| 方向 | 剩余空间 | 主要 prior art |
|---|---|---|
| 录屏到 skill freeze (RecMode) | 1.5/5 | UiPath US11372380 + Microsoft "Record with Copilot" + **SkillForge 1:1 撞车** |
| xcatfish-user runtime patch | 0.5/5 | Sun US5581697 (1996) + gorilla `allow_hit` + kpatch + LangChain Middleware |
| wiki 4 信号相关推荐 | 0.5/5 | Atlassian US12566536 + Adamic-Adar 2003 paper + Obsidian Graph Analysis 插件 |

**关键时间窗**:
- catfish 还未公开 → grace period 还未起算, **从容** file
- AeneasSoft 已 file US provisional (跟方向 2 思路最接近) → **2 个月内** 启动方向 2 占位 否则被 patent block 风险
- 商业化 PCT 国际申请 30 月 entry window — 不急, 等 catfish 拿到 2-3 个企业客户合同再决定进美/欧

**资金预算**:
- 主 patent CNIPA: 1.5-2.5 万 RMB / 1 件, 6-12 月授权
- 副 patent US provisional: 1-2 千 USD 占时间戳, 12 月内决定 PCT
- 合计 短期投入: **3-5 万 RMB**, 远低于"5 个方向全 file PCT" 的 25-50 万

---

## 报告结构

本报告分 5 章, 每个方向独立评估. 章末有 summary 表 + 行动 checklist.

每方向输出:
- 拟 file 的 claim 思路
- 检索关键词
- 找到的高重叠 prior art (含 patent number + 申请人 + 公开日 + 1-2 句摘要 + 跟我方 claim 的差异)
- 剩余空间评估 (0-5 分)
- narrowing 建议
- PCT 国际申请价值 (高/中/低 + 理由)

---

## 方向 1: 客户端零外泄 + 中央 metering 双盲架构

### 拟 file 的 claim 思路

一种 LLM 调用代理系统: 员工与 LLM 的**完整对话** (prompt + response + 工具调用参数 + 工具结果 + 屏幕录制原料) 全部在员工本机持久化 (SQLite), 不上传; 中央服务器仅接收**脱敏 metering** (员工 email + 调用时间戳 + 模型名 + token 数 + 计费金额), 无法重建对话内容. 同时支持企业级审计 / 计费 / 配额管理 + 员工对话隐私 / 数据零出境.

### 高重叠 prior art (TOP 10)

| # | Patent / 编号 | 申请人 | 公开日 | 重叠度 | 关键差异 |
|---|---|---|---|---|---|
| 1 | **US12556533B1** Protecting private information during LLM interactions | **Gen Digital (Norton)** | 2026-02-17 | 高 | 中央代理隐藏身份, 非员工本机. 不含 metering / 计费 |
| 2 | **US12554888B2** Privacy-preserving prompt engineering | **SAP SE** | 2026-02-17 | 中 | prompt 脱敏 (LLM 仍看 masked prompt), 不是 prompt 完全留本机 |
| 3 | **US12488134B2** Data security in LLMs (NER 检测) | **SAP SE** | 2025-12-02 | 低-中 | prompt 内容检查 + 拦截, 不涉及 metering / 双盲 |
| 4 | **US12261827B1** Proxy servers for LLMs | **Auradine Inc** | 2025-03-25 | 中 | 缓存 + 流量管理 (云端 reverse proxy), 不是 client-side |
| 5 | **US20250106306A1** AI services in distributed cloud | **Cloudflare** | 2025-03-27 | 低 | edge inference orchestration, 不是 client-side metering |
| 6 | **US20250238616A1** Token optimization in LLM | (pending) | 2025 | 低-中 | token 优化省钱, 不涉及脱敏 metering 上报 |
| 7 | **US12530480** Role-based LLM | IBM | — | 低 | RBAC + LLM 访问控制, 不是 metering |
| 8 | **US12499158** LLM query management | IBM 系 | — | 低 | 查询/流量管理, 非双盲 |
| 9 | **US12388799** Split inference | — | — | 中-低 | 模型 split, 非对话本地化 + metadata 上报 |
| 10 | **Salesforce Einstein Trust Layer** (产品 + 多个 patent application) | **Salesforce** | 2023+ | 高 (产品层) | 中央化 SaaS, 平台留 audit trail; catfish 是对话不过任何中央 |

**关键产品层 prior art (无明确 patent 但影响 obviousness 评估)**:

- **Cloudflare AI Gateway** (DLP + analytics + cost tracking)
- **Zscaler AI Guard / Palo Alto AI Access Security** (DLP for AI prompts)
- **Microsoft Purview / OpenAI Compliance API** (元数据 only audit logs)

### 剩余空间评估: **2.5 / 5**

理由:
- "LLM proxy / gateway 做隐私脱敏 + 中央 audit" 一大方向**已被 SAP、Salesforce、Cloudflare、Gen Digital** 全覆盖, **不可作为 broad claim**
- "token 计数计费"是 industry 通用, **不可独立 claim**
- "**对话留本机 (local-first) + 服务器只收脱敏 metering**" 这个**双盲 + 客户端本地全持久化**组合, 检索范围内**没有明确命中** — 这是我方真 distinctive 空间
- **风险**:
  1. 产品层 "chat history stored locally" 已经在 Open Source 圈讨论很多 → "obvious combination" 风险
  2. Anthropic / OpenAI / GitHub Copilot 没看到公开 patent, 但**有可能正在 file**
  3. **Salesforce Einstein Trust Layer 的 patent family 没全查完, 是最高 risk 对手**

### Narrowing 建议 (重要, 决定能否 file)

把 claim 限缩到这三个**组合性 distinctive feature** 上 (单独都老, 组合可能新):

**N1. "Agent 工具调用全链路本地化"**

> "...完整对话**包括 LLM 工具调用参数与工具执行结果**全部留本机持久化..."

→ 现有 prior art 全部聚焦在 prompt / response, **没人 cover MCP / tool-call argument + tool-result 也只留本机**. 这是 catfish 作为 agent 桌面 app 的真特点.

**N2. "屏幕录制级 evidence + 仅 metadata 上报" 绑定的 dual-bind 架构**

> "...服务器仅接收 (user_id, timestamp, model, token_in, token_out, cost), 而本机同时记录**带屏幕录制 hash 的 evidence chain**, 用于事后审计取证..."

→ "屏幕录制 evidence + 元数据 cross-reference" 这个跨模态 audit pattern 没在检索中命中.

**N3. "中央服务器密码学不能重建对话"的可证明性质**

> "...服务器接收的 metering payload 经过设计使得即使获取全部服务器数据也无法重建 prompt / response 文本 (only hash/length/token-count), 即 server 端的 zero-knowledge property..."

→ 把 claim 从 "不上传" 升级到 "**密码学上服务器不可重建**" — 这是 Gen Digital 和 SAP 都没做到的 (它们的中央 gateway 仍 see content).

**推荐主 claim**: **N1 + N3 组合 method claim**, 这是最 distinctive 也最难绕过的角度.

### PCT 国际申请价值: 中 (偏低)

| 市场 | 价值 | 理由 |
|---|---|---|
| 中国 (CN) | **高** | 国企 / 金融 / 医疗"数据不出境 + 计费可审计"刚需, dual licensing 主营收来源 |
| 美国 (US) | 低-中 | Salesforce/Cloudflare/Microsoft 已占满云端 AI gateway, "local-first agent" license 难变现 (美国偏好云端 SaaS) |
| 欧盟 (EU) | 中 | GDPR + AI Act 加成, 但欧盟企业 LLM agent 渗透率低, 短期 license market 小 |
| 日本 (JP) | 低 | — |

**推荐路径**:
1. **CNIPA 国内发明专利** (N1+N3 narrow 版) — 6-12 月走完, 1-2 万元成本
2. **PCT 暂缓** — 等拿到 2-3 个国企客户合同后, 用 30 月 PCT entry window 决定进 US/EU
3. **不要 file broad claim** — 大概率被 Gen Digital / SAP / Salesforce 干掉, 浪费钱

> **给鸿波**: 这方向**不要给 patent 太多商业期待**. Salesforce Einstein Trust Layer 已经在产品层做了 90% 功能. catfish 的真 moat 是 "对话本地化 + open-source 信任 + 中国本土化合规" 这套组合. Patent 价值在 dual licensing 时给企业客户"我们有 patent 你必须 license"的法律抓手, 不是真 enforce. file 1-2 个 narrow claim 在 CNIPA 够用.

---

## 方向 2: LLM stream 中 inline approval gate (P15)

### 拟 file 的 claim 思路

一种**在持续生成的 LLM Server-Sent Events (SSE) token 流中无缝插入人类审批暂停-继续事件的方法**: 当 LLM 调用 dangerous_command / execute_code_guard 类工具时, 系统**不打断当前 SSE token 流**, 而是通过闭包反射机制 (Python contextvar + closure capture of `_stream_q` event queue) 向同一 SSE 流注入 "approval-pending" event, 前端展示浮动 banner + 60s 倒计时, 员工点击批准/拒绝后通过 RPC 调用 `chat_approval(session_key, choice)` 解锁工具执行, LLM 继续 streaming.

### 高重叠 prior art (TOP 12)

| # | Patent / 编号 | 申请人 | 公开日 | 重叠度 | 关键差异 |
|---|---|---|---|---|---|
| 1 | **Seek AI Patents (US 2 件 grant)** Human-in-the-Loop Workflows for LLM-Generated Queries | Seek AI Inc | 2024-12-04 | 中 | scope 是 BI/analytics query, 非 dangerous command + tool execution; 不走 SSE stream inline |
| 2 | **AeneasSoft USPTO Provisional** In-Process Telemetry Interception + Safe Pause & Resume State Machine | AeneasSoft (开源) | 2025 (pending) | **高 (最危险)** | circuit breaker (失败阈值触发暂停), 非主动 gate; 无 SSE 流注入, 不强调 stream 连续性 |
| 3 | **US12450494B1** Validating autonomous AI agents using generative AI | 个人 | 2025-10 | 中 | 自动审 (generative AI 决策), 不是真人 banner+timeout |
| 4 | **US12412138B1** Agentic Orchestration | — | — | 低 | tool 编排, 不谈审批 gate |
| 5 | **US12468798** Managing AI agents with user-controlled authorization tokens | IBM 系 | — | 中 | token-based 静态 authorization, 非动态 per-call gate |
| 6 | **UiPath Action Center (US 12135778, 12124424)** | UiPath | — | 中 | RPA 流程 step-by-step 离散任务, 非 LLM token stream level |
| 7 | **Salesforce Agentforce Trust Layer 专利** | Salesforce | 2025 filed | **高 (架构层)** | 中心化云架构 vs catfish 本地 desktop; policy mediator 触发 approval, 非 SSE inline 注入 |
| 8 | **Anthropic Claude Code auto-mode** (公开论文 / 工程 blog) | Anthropic | 2025-09 | **高 (直接竞品)** | model-based classifier 决定弹/不弹 permission prompt; CLI 阻塞模式, 非 SSE inline 注入 |
| 9 | **LangChain `interrupt()` / LangGraph** | LangChain Inc (open-source) | 2024 | **高 (公开 prior art)** | checkpoint 暂停 → 等人输入 → 从断点恢复 (非同一流注入); 已成 public prior art, **catfish 不能 claim 这层** |
| 10 | **Microsoft Copilot Studio multistage AI approvals** | Microsoft | 2026 (product) | 中 | 工作流 designer 模型, 非 chat stream level |
| 11 | **Drift Patent US10818293** event-based dialogue manager | Drift | — | 低 | 销售/客服 bot 多轮决策, 不是 tool 审批 |
| 12 | **US20250238616A1** Token optimization in LLM | Freshworks | 2025 | 低 | token 优化, 非审批 |

### 剩余空间评估: **2.5 / 5**

理由:
- LLM + HITL + tool approval 已有 Seek AI grant / Salesforce filed / Anthropic Claude Code / UiPath / AeneasSoft 多家覆盖
- 但 catfish 三个具体技术点**暂未见精确 prior art**:
  1. **不打断 SSE 流, 通过闭包反射注入 approval-pending event 到同一流** — AeneasSoft 是断 + 恢复 (Pause/Resume state machine), 不是同一流 inline 注入
  2. **Python contextvar + `_stream_q` closure capture 跨协程注入** — 实现细节, 可写 method claim
  3. **server-side 60s timeout fallback + session_key 多并发独立审批栈** — 工程细节
- 难点: 这些更像"实现技巧"而非"发明", 35 USC §101 / 中国《审查指南》算法条款下可能驳回为"软件实现细节"
- **AeneasSoft 是最直接威胁** — 同样 in-process, 同样 pause/resume, 已 provisional filed

### Narrowing 建议

1. **"SSE 流内 inline 注入"作 claim 1 主限定**: "不断开连接 / 同一 HTTP response 内 mid-stream 插入 approval event frame" 写成核心独立 claim. 现有 prior art (Seek AI / AeneasSoft / Salesforce) 都走"暂停-另起会话/checkpoint resume"路线, catfish 是"流不停, 注入控制事件"

2. **闭包 + contextvar 的具体绑定机制**: "通过捕获写入队列 (`_stream_q`) 引用的闭包函数, 跨 contextvar 边界向同一 stream 写入控制 event" 这种实现层 method claim

3. **三件套组合**: 危险工具白名单 + 60s server-side 倒计时 + session_key 多 session 独立审批栈, 单独任何一个都不新, **三个一起作系统 claim 可能新颖**

### PCT 国际申请价值: **中**

| 市场 | 价值 | 理由 |
|---|---|---|
| **美国 (US)** | **高** | license target 是 Anthropic / OpenAI / Microsoft Copilot, dual-license / 守势专利价值最高 |
| 中国 (CN) | 中 | ByteDance/Tencent/Alibaba/Baidu 国内 agent SaaS 扩张, 但 CNIPA 算法条款下保护边界窄 |
| 欧盟 (EU) | 中-低 | EU AI Act 推升 audit/approval 需求, 但 EPO 对纯软件 method claim 严 |

**推荐**:
- **2 个月内** 先 file US provisional (低成本占坑, 1-2 千 USD), 否则 AeneasSoft 类 provisional 累积 → 被 block 风险
- 12 个月内观察是否值得 PCT
- 不优先单独 CN 申请 (license 目标在美)
- 全部 narrow 到 **SSE inline injection + contextvar closure + 60s timeout 三件套**, 不写宽泛 HITL claim

**关键风险**: AeneasSoft 已 provisional, freedom-to-operate 审查必须做; Anthropic Claude Code auto-mode 工程细节公开 → 新颖性反证.

---

## 方向 3: 录屏到 skill freeze 流水线 (RecMode)

### 拟 file 的 claim 思路

一种**员工屏幕录制片段经 LLM 推理自动生成可冻结的可复用 skill 模板**的端到端流水线方法: 录屏 → LLM 推理意图 → propose skill (frontmatter + body + 脚本) → 员工 review → freeze (生成 v0.1.0-frozen marker) → catfish_skill_publish RPC 共享到 marketplace.

### 高重叠 prior art (TOP 15)

| # | Patent / 编号 | 申请人 | 公开日 | 重叠度 | 关键差异 |
|---|---|---|---|---|---|
| 1 | **US11403201B2** Systems for capture and generation of process workflow | UiPath | 2022-08-02 | **高** | 录屏 → 自动生成 workflow data file. 不含 LLM 推理意图 / frozen marker |
| 2 | **US11372380B2** Media-to-workflow generation using AI | UiPath | 2022-06-28 | **高** | 媒体输入 AI 模型预测 workflow 让用户选 — 跟 catfish "LLM propose + 员工 review" 高度重叠 |
| 3 | **US11481304B1 / US11954008B2** User action generated process discovery | Automation Anywhere | 2022 / 2024 | **高** | 录制多应用交互, 检测 process, 自动产 RPA. 规则/统计 mining, 非 LLM 端到端 |
| 4 | US11461215B2 Workflow analyzer | UiPath | — | 中 | 录屏 + ranking 自动化机会, 不生成 skill 模板 |
| 5 | **US9720706B2** Generating task flows for application | Salesforce | 2017 | 中-高 | 应用内监听 → 链接 task flow → 给他人复用. 无 LLM / frozen marker |
| 6 | **US20250104429A1** LLM + Vision Models with Digital Assistant | — | 2025-03-27 | 中-高 | VLM + LLM 解屏推意图执行任务. 实时执行型, 不产可冻结 skill |
| 7 | WO2024259362A2 Customer agent recording | Salesforce | 2024 | 中 | 客服业务录像 + 发布管线, 非 skill 生成 |
| 8 | **Blue Prism Capture** (产品 + patent family) | SS&C / Blue Prism | — | 中 | CV + API spy 自动捕 demonstration, 非 LLM |
| 9 | **Microsoft "Record with Copilot" / Power Automate Desktop AI Recorder** | Microsoft | (product 2024+) | **极高** | 端到端 LLM 转 desktop flow, 跟 catfish RecMode 几乎对应. 输出 Power Automate flow XML, 非 SKILL.md, 无工程审定/教学产物区分 |
| 10 | **SkillForge** (skillforge.expert, 2025) | 公开产品 | 2025 | **极高 (1:1)** | 录屏 → 多 pass LLM (coarse → fine → metadata merge → intent grouping) → **输出 SKILL.md**, 兼容 Claude Code / Codex. 无团队 publish + 安全扫描 + 审定 BL, 无 frozen marker. **这是最关键的 blocking 参考** |
| 11 | ALLOY (arXiv 2510.10049, 2025-10) | 学术 | 2025-10 | 高 | 浏览器内 demonstrate → 结构化 workflow + NL 泛化 |
| 12 | Invisible Mentor (arXiv 2509.26557, 2025) | 学术 | 2025 | 中 | 录屏推断动作推荐 workflow. 输出是建议非可执行 skill |
| 13 | WISE-Flow (arXiv 2601.08158) | 学术 | — | 中 | LLM-based workflow inducer + 多 pass 验证 |
| 14 | **Microsoft SkillOpt** (arXiv 2605.23904, 2026-05) | Microsoft | 2026-05 | 中-高 | "frozen agent + skill 文档"概念. "frozen"用词已占用, 削弱 catfish 在该用词的独占性 |
| 15 | **Sikuli** (MIT, 2009+) | 学术 | 2009 | 低-中 | capture-replay 视觉脚本 by demonstration. 老 prior art, 击穿宽 PBD 录屏 claim |

### 剩余空间评估: **1.5 / 5**

**这是三个方向中 prior art 最密的一个**.

理由:
- "录屏 → 自动生成可执行 workflow / skill" 在 UiPath / Automation Anywhere / Blue Prism / Salesforce 早被锁住
- **Microsoft Power Automate "Record with Copilot"** 公开技术 → 主权利要求几乎无生存空间
- "LLM 看屏推意图 + propose skill" 被 **SkillForge 1:1 命中** (且 SKILL.md 输出, 跟 catfish 完全相同的产物形态)
- "团队 marketplace + 共享" 被 Salesforce US9720706 + RPA bot store 覆盖
- 唯一活路是 "frozen marker + 教学产物 vs 工程审定的可审计区分", 但 Microsoft SkillOpt 已用"frozen"语言, C2PA 也覆盖"AI 生成标识"

### Narrowing 建议

1. **死守 "frozen marker + 双轨审定 dashboard" 作 narrow claim 核心**:
   - skill artifact frontmatter 含**机器可验证的 frozen 标识** (version "X.X.X-frozen" + description 固定签名串 "由 catfish_freeze_skill 自动凝固")
   - dashboard / publish 流程基于该 marker **自动分流** 教学产物 vs 工程审定
   - 团队 marketplace publish RPC 在 frozen 标识上**强制走安全扫描 + 审定 gate**
   - 把 claim 重心从 "录屏→生成" 挪到 "**自动 provenance 标识驱动的差异化发布管线**"

2. **加 "本地优先 + dual-mode skill 来源审计" 限定**:
   - 录屏数据全部端侧 LLM 推理, 不出本机
   - freeze 后 skill artifact 携带**端侧推理来源签名**, marketplace 上传时区分工程审定
   - 避开 Microsoft / UiPath 云端方案

3. **绑定 SKILL.md + MCP 生态语义**:
   - frontmatter YAML 含 specific 字段 (allowed-tools, isProtected, version-frozen suffix) 触发**特定 RPC `catfish_skill_publish` 的 BL 流程**
   - 定位为 "**SKILL.md 规范的扩展 + 配套发布协议**", 不直接撞 RPA 自动化路径

### PCT 国际申请价值: **低**

理由:
1. 大厂 (UiPath/Microsoft/Automation Anywhere/Salesforce) 已 US/EP/CN 大量布点, freedom-to-operate 弱势
2. 即便 narrow claim 拿 grant, 强制力低 → 潜在 license 买家 (这些大厂) 引现有族系反诉
3. SkillForge / ALLOY 等 2025 公开技术封死宽 claim
4. PCT 国家阶段 15-25 万 RMB 起步收益比差

**建议**: **不 file** 或 **仅 CNIPA 国内一件 narrow** (双轨发布管线 + frozen marker), 防国内 RPA 厂 (影刀/弘玑/来也) 反向围堵. 不进 PCT.

---

## 方向 4: xcatfish-user "plugin as runtime patch" 模式

### 拟 file 的 claim 思路

一种**通过运行时 importlib + 闭包反射动态向上游 AI agent daemon 注入个性化补丁而不 fork 源码的方法**, 升级时通过锚点验证 (anchor variable name / 方法签名 / class hierarchy) 保证补丁仍然有效.

### 高重叠 prior art (TOP 16)

| # | Patent / 编号 / 库 | 申请人 / 来源 | 公开日 | 关键差异 |
|---|---|---|---|---|
| 1 | **US5581697A** Run-time error checking using dynamic patching | Sun | 1996 | "patch sites in load objects" = catfish 锚点思路祖宗 |
| 2 | **US6918110B2** Dynamic instrumentation by patching function entry | IBM | — | runtime hot replace 标准做法 |
| 3 | US7275241B2 Dynamic instrumentation for mixed mode VM | IBM | — | runtime probe injection |
| 4 | **US8683450B2** Testing software patches Dyninst | — | — | "runtime code patching ... continue execution without recompile/restart" — 跟 catfish "可热替换 + 不影响 daemon 状态" 完全重叠 |
| 5 | **US7784044B2 / US20040107416A1** Patching of in-use functions | Microsoft | — | 已含**签名/验证机制** 确保 patch 安全 apply |
| 6 | **US9335986B1** Hot patching with "verify memory in known state before applying" | — | — | 这是 anchor verification 等价物, 比 catfish "verify `_stream_q` in free vars" 更通用且更早 |
| 7 | US9092301 Patch with signature demonstrating "produced by designated authority" | — | — | 签名验证 |
| 8 | **CN104461625A** 热补丁方法 (符号表搜索原函数地址 + 修改指令) | 中国 | — | "符号表查找" = catfish importlib + getattr 锚点定位 |
| 9 | US9703576B2 Aspect scoping in modularity runtime | IBM | — | load-time AOP weaving |
| 10 | US20100138815A1 Implementing aspects with callbacks in VMs | — | — | runtime AOP 注入 |
| 11 | USPTO H2202 Dynamic hooking without interrupting execution | Microsoft Detours | — | runtime hook 标准 |
| 12 | **Microsoft Agent Governance Toolkit (2026)** open-source | Microsoft | 2026 | AI agent runtime governance: plugin lifecycle + Ed25519 签名 + capability gating — **直接竞争 "AI agent 专用 plugin 注入" narrowing 角度** |
| 13 | **LangChain Middleware (2024+)** 文档 | LangChain Inc | 2024 | 官方文档明确称 "monkey-patching agent class is fragile" 改 middleware hooks — **AI agent plugin 域现有公开技术路径** |
| 14 | kpatch / kGraft / livepatch | Linux | 2014+ | kernel live patching, 含 "patch 加载前 verify kernel 版本兼容性" |
| 15 | **gorilla 库 (PyPI)** Python monkey patching | open-source | 2021 | **`allow_hit=False` 就是 anchor verification 等价物** (apply 前检查目标 attribute 是否存在/是否被改) + `store_hit` 保留原函数. **几乎完全摧毁 catfish 的 distinctive claim** |
| 16 | eBPF CO-RE + BTF | Linux kernel | — | "Compile Once Run Everywhere" runtime relocation 自动适配 kernel 升级, 比 catfish 锚点验证更先进 |

### 剩余空间评估: **0.5 / 5** (实质上不可专利)

理由 (鸿波你的预期完全正确):
- **运行时注入补丁** — 30 年前已是 Sun/IBM/Microsoft 标准技术
- **锚点验证 (anchor verification on upgrade)** — gorilla 库 `allow_hit=False` + `store_hit` + kpatch + US9335986B1 + US9092301 签名验证全覆盖
- **可热替换 + daemon 状态保留** — kpatch/kGraft + Dyninst US8683450B2 早 claim
- **闭包反射 + free vars 验证** — Python `func.__closure__` / `func.__code__.co_freevars` 标准用法, 不是发明
- **AI agent daemon 专用** — Microsoft Agent Governance Toolkit + LangChain Middleware 已占位; "应用领域限定" 在 35 USC §101 + Alice/Mayo 框架下 PTAB 长期不接受作 patentable 区分

### Narrowing 建议

**坦白讲**: anchor verification 这点 carry 不了 claim. gorilla 0.4.0 (2021) 的 `allow_hit + store_hit` 已是 anchor verification 完整体现, kpatch 在 kernel 层做了更严格等价物. catfish 的 "verify `_stream_q` 在 free vars" 只是同一思路 Python 反射特化, **PTAB/CNIPA 几乎肯定以 "obvious extension of well-known monkey-patching safety practice" 驳回**.

monkey-patching 是 Python 文化 30 年通用模式, AOP 是 Gregor Kiczales 1997 年发明, 动态 instrumentation 是 Paradyn/Dyninst 1990 年代工作. **不存在可专利空间**.

### PCT 国际申请价值: **低 (= 不应申请)**

理由:
- Prior art 密度极高且时间线长 (1996 - 2026 持续公开技术), 任何审查员能 30 分钟找到 5+ 篇 102/103 拒绝引证
- USPTO Alice/Mayo 框架以 "abstract idea of patching software at runtime" §101 驳回
- CNIPA 软件 + 业务方法类审查趋严, 落入 "智力活动规则" 边界
- EPO 要求 "technical contribution beyond mere automation", monkey-patching 不构成 technical effect
- 机会成本太高 (PCT 起步费 + 国家阶段 5 年保守 30-50 万 RMB)

**最终建议**: 放弃 file, 改为在 catfish 公开 repo 的 CHANGELOG / docs 记录 "xcatfish-user runtime patch + anchor verification" 实现作 **defensive publication**. 零成本 + 防他人 patent block.

---

## 方向 5: wiki + 4 信号相关推荐

### 拟 file 的 claim 思路

基于 direct link (×3) / source overlap (×4) / Adamic-Adar (×1.5) / type affinity (×1) 4 因素加权的知识图谱节点相关推荐, 输出 top-K + 解释性符号 (↔ ◇ ∗ ≈).

### 高重叠 prior art (TOP 13)

**商业专利 (致命级)**:

| # | Patent / 编号 | 申请人 | 关键差异 |
|---|---|---|---|
| 1 | **US12566536B** Electronic document management system with content recommendation interface | **Atlassian** | claim 直接覆盖 "在内容协同平台对当前页面计算 relatedness score, 信号 hierarchical proximity + navigation events + keyword similarity, 加权组合, 展示候选 cards" — 跟 catfish 同构, 只是信号集略不同 |
| 2 | **US11875020B** (Atlassian, 同族) | Atlassian | Confluence "Related content" feature 已 grant |
| 3 | **US8260787B2** Recommendation system with multiple integrated recommenders | — | 显式 claim "multiple recommenders 各出 score, 用 weight 加权组合, weight 可调" — **catfish 4 信号加权框架的上位概念, 完美覆盖** |
| 4 | US7991650 / US7991757 Obtaining recommendations from multiple recommenders | — | 同族 |
| 5 | US20140074545A1 Human workflow aware recommendation engine | — | enterprise collaboration graph 多信号加权, 知识员工场景 — 贴合 catfish wiki |
| 6 | US9378432 Hierarchy similarity measure | Microsoft | 文档层级图 similarity, Atlassian US12566536 先驱 |
| 7 | Notion Labs 专利组合 | Notion Labs | search ranking / user interaction monitoring 已 file, 相邻空间挤压 |

**学术 prior art (60 年学科, 否定 novelty 充分)**:

| # | Paper | 关键差异 |
|---|---|---|
| 8 | Adamic & Adar (2003) "Friends and neighbors on the web" | AA 算法本体 |
| 9 | Liben-Nowell & Kleinberg (2003/2007) "Link prediction problem for social networks" | CN/Jaccard/AA/PA/SimRank 加权组合 baseline 对比 |
| 10 | Lü & Zhou (2011) "Link prediction in complex networks: a survey" | 加权 CN/AA/Jaccard hybrid 是 textbook |
| 11 | Kumar et al. (2020) + J. Supercomputing 2023 综述 | "weighted combination of local similarity measures" standard practice |
| 12 | PatentMind (arXiv 2505.19347, 2025) | multi-aspect similarity 加权 + LLM 算权重 |
| 13 | Wang & Wang KDD 2019 / Xian SIGIR 2019 | "可解释 + 路径符号 + 推理理由" 成熟方向 |

**产品 prior art**:
- Confluence "Related pages" (live, 2023+, Atlassian 商用)
- Obsidian Graph Analysis / Smart Connections / Various Complement 等插件 (2020-2023 公开, 含 AA/CN/Jaccard/co-citation)
- Roam Research "Linked References" + "Unlinked References" (2020)
- Logseq / 思源笔记 (开源, 2020+, 同类 wikilink + backlink 推荐, 早于 catfish)

### 剩余空间评估: **0.5 / 5**

理由:
- 上位概念 (multi-signal weighted recommendation on knowledge graph) 被 **US8260787 + US12566536 + US20140074545** 覆盖死
- 4 个具体信号每个单独都是 textbook (direct link / co-citation 即 source overlap / Adamic-Adar / type 同型) — 学术 prior art > 15 年
- "加权求和" 是 link prediction 标准方法 (Lü-Zhou 2011 综述 explicit)
- Atlassian Confluence Related Pages 已 commercial use → prior art 公开使用
- 实质 novelty 仅剩 (a) 3/4/1.5/1 具体权重数字 (b) frontmatter sources 字段 (c) ↔ ◇ ∗ ≈ 符号 — 都是 "obvious to one of ordinary skill", 35 USC §103 / 中国创造性条款下 reject
- USPTO Alice §101 (抽象思想) 风险也极高

### Narrowing 建议

**诚实建议**: **不要 file 这个方向**.

如果非要 narrow:
- "特定权重 3/4/1.5/1" — 没用 (审查员引 "数值范围常规优化" 驳回)
- "frontmatter sources 字段" — 勉强 file 但价值低, Obsidian / MyST 等社区已大量使用
- "解释符号 ↔ ◇ ∗ ≈" — UI presentation, 中国走外观/界面专利但发明专利侧拒, USPTO §101 abstract idea
- 唯一可考虑: "本地 + 增量索引"工程层 ("Tauri 桌面端实时增量更新 4 信号 score" — B+tree / RocksDB 增量 / debounce / partial recompute) — 但偏离 4 信号推荐算法核心, 算 separate invention

### PCT 国际申请价值: **低**

理由:
- 国内 CN: Atlassian / Microsoft / Notion 已 file 类似领域, 审查员极易找对比文件
- 美国 US: Alice §101 + 充分 KSR §103 obviousness 子弹
- 欧洲 EP: 推荐算法 + UI presentation 双重落入 "non-technical", EPO 几乎不授权
- PCT $5k + 国家阶段 $30-80k, ROI 显著为负

**建议**: **drop 这个方向**. 改作 **defensive publication** (发 IP.com / arXiv / blog), 阻止他人 file blocking patent 反咬 catfish + 零成本.

---

## 最终 Summary 表 + 推荐

### 五方向汇总

| # | 方向 | 剩余空间 | PCT 价值 | 推荐 |
|---|---|---|---|---|
| 1 | 客户端零外泄 + 中央 metering 双盲 (N1+N3 narrow) | **2.5/5** | 中 (CN 高) | **主 file — CNIPA 国内发明专利** |
| 2 | LLM stream inline approval gate (SSE + contextvar + 60s 三件套) | **2.5/5** | 中 (US 高) | **副 file — US provisional 占位 + 12 月后 PCT 评估** |
| 3 | 录屏 → skill freeze (RecMode) — narrow 到 frozen marker + 双轨审定 | 1.5/5 | 低 | **不 file PCT**, 可选 CNIPA 一件 narrow (BL) |
| 4 | xcatfish-user runtime patch | 0.5/5 | 低 | **不 file** — defensive publication |
| 5 | wiki 4 信号推荐 | 0.5/5 | 低 | **不 file** — defensive publication |

### 行动 checklist (6 个月内)

**Phase A: 2 个月内** (T+0 ~ T+60d)
- [ ] 找软件方向中型代理所 1-2 家试聊 (报价 + 经办人), 不签
- [ ] 方向 2 (SSE inline approval) 起草 **US provisional** — 1-2 千 USD 占时间戳, 占住跟 AeneasSoft 时间差
- [ ] 方向 1 起草 **CNIPA 发明专利申请文件**: N1 (agent 工具调用全链路本地化) + N3 (服务器密码学不可重建) 组合 method claim. 自己写第一版 spec + claim, 代理所审 + 改
- [ ] 准备 **freedom-to-operate 报告** 给方向 2 (重点 AeneasSoft provisional + Anthropic Claude Code auto-mode)

**Phase B: 3-6 个月** (T+60d ~ T+180d)
- [ ] CNIPA file 方向 1 (附 N1+N3 narrow claim), 等 OA
- [ ] 评估方向 3 是否值得一件 CNIPA narrow (frozen marker + 双轨审定 pipeline). 如果 catfish 商业上验证了 RecMode 价值, file. 否则 drop
- [ ] 方向 4 + 5 写 **defensive publication** 到 catfish docs/PATENTS.md, 公开 commit (建立 prior art 时间戳)
- [ ] catfish GitHub 公开 (跟 file 时间错开 1-2 月, 减 patent examiner 引用自己 commit 的风险)

**Phase C: 12-18 月** (T+180d ~ T+540d)
- [ ] 方向 2 US provisional 到期前评估 PCT 进入 (取决于 catfish 是否有美国客户接触)
- [ ] 方向 1 CNIPA 授权 (12 月内) 后, 评估 PCT 进 US/EU
- [ ] 跟 1-2 家国企试点 catfish 商用, 用 patent + open source 谈 dual licensing

### Dual licensing 商业模式建议

催 catfish 的开源 license 选择:

| Option | License | Dual licensing 兼容 | 适用 |
|---|---|---|---|
| A | **Apache 2.0** | ✓ (含 explicit patent grant) | catfish 所有非商业用户自动获得 patent license. 商业 license 单独签 (类似 Cockroach CCL: 大于 N 节点 / N 员工时收费) |
| B | **MIT** | ✓ (无 explicit patent grant, 你保留所有 patent right) | 类似 Redis BSD: 社区免费, 大公司商用付费 |
| C | **SSPL (MongoDB)** | ✓ | "提供 catfish 作 SaaS 服务"必须开源全套 — 防 AWS 类大厂白嫖. 但 OSI 不认 SSPL = 不算开源 |
| D | **Elastic License v2** | ✓ | source-available, 大公司商用付费 + 防 SaaS 包装 |
| E | **AGPL v3** | ✓ | 强 copyleft, 防 SaaS 白嫖. 但企业客户 (国企尤其) 怕 AGPL |

**推荐**:
- catfish 主代码 **Apache 2.0** (兼顾社区友好 + patent grant)
- 商业 license 单独条款 (大于 50 员工 / 大于 1000 RMB MRR 时需 license)
- 跟 MongoDB / Cockroach / Elastic 类公司学 dual licensing 商业条款

### Defensive publication 模板

方向 4 + 5 (+ 方向 3 如不 file) 写 `docs/PATENTS.md`:

```markdown
# Catfish 技术公开声明 (Prior Art Establishment)

本文档公开声明 catfish 项目作者鸿波于 [日期] 发明的以下技术方案, 作为开源
社区共有的 prior art, 防止任何第三方对相关技术 file blocking patent.

## 1. xcatfish-user 运行时 monkey-patch + 锚点验证机制
[具体描述 + 实现代码 link]

## 2. wiki 4 信号 (direct/source/AA/type) 加权相关推荐
[具体描述 + 代码 link]

## 3. RecMode 录屏 → LLM propose skill → freeze 流水线 (如不 file)
[具体描述 + 代码 link]

这些技术方案以 Apache 2.0 license 公开, 任何人可以自由使用 / 改进 / 商用,
作者保留就具体改进后续 file patent 的权利, 但本文档涉及的具体方案构成
prior art, 排除他人就同一方案的 novelty 主张.

公开日期: [date]
作者: 鸿波
项目: https://github.com/[user]/catfish
Commit hash: [hash]
```

### 资金预算

| 项目 | 时间 | 金额 |
|---|---|---|
| 方向 2 US provisional 申请 + 代理 | T+60d | 1-2 千 USD ≈ 8-15 千 RMB |
| 方向 1 CNIPA 发明专利申请 + OA 2 轮 | T+30d 起, 6-12 月 | 1.5-2.5 万 RMB |
| (可选) 方向 3 CNIPA 一件 narrow | T+180d 后 | 1.5-2.5 万 RMB |
| 方向 1+2 freedom-to-operate 报告 | T+60d | 5-10 千 RMB |
| **6 个月小计** | — | **4-7 万 RMB** |
| (12-18 月) 方向 1 PCT 进 US | T+18mo | 5-10 万 RMB |
| (12-18 月) 方向 2 PCT 全套 | T+18mo | 8-15 万 RMB |
| **总预算** (含 PCT) | 18 个月 | **20-35 万 RMB** |

**关键决策点**: 6 个月内 catfish 是否拿到 2-3 个企业试点客户 → 决定要不要继续投 PCT.

---

## 关键风险提示

1. **AeneasSoft (方向 2)** — 2025 已 US provisional, 跟 catfish P15 思路最接近. **必须 2 个月内 file US provisional 占时间戳**, 否则被 block 风险.

2. **Salesforce Einstein Trust Layer (方向 1)** — patent family 庞大且未全公开, 是方向 1 最高 risk 对手. file 前必须做完整 freedom-to-operate 审查.

3. **SkillForge (方向 3)** — 已是 public prior art (HN 公开披露 2025), 输出 SKILL.md 跟 catfish 完全相同. **这是为什么方向 3 不应 file PCT**.

4. **Anthropic Claude Code auto-mode (方向 2)** — 2025-09 工程 blog 公开 model-based classifier approval flow. 是方向 2 最大新颖性威胁. **catfish file 时必须明确"非 classifier 自动决定, 而是 显式工具白名单 + 真人 banner"** 来 differentiate.

5. **职务发明风险 (再次提醒)** — 鸿波说 catfish 是"纯个人项目 + 业余时间". 但**正式 file 前必须自查公司劳动合同 / 员工手册的 IP 条款**, 确认:
   - 项目跟本职工作"完全无关"
   - 没用单位电脑 / 网络 / 资金
   - 没用本职工作中获得的"领域专有知识" (EIS 系统经验 — 这点要小心)
   - 必要时跟 HR / 法务发邮件留痕, 让单位 written 放弃任何 IP 主张

---

## 报告结尾

本报告由 5 个并行 deep-research agent 检索 ~50 个 patent / 公开技术 + 12 篇学术论文得出. 已尽力 honest 评估, 不给 false reassurance. 任何决策前请跟专利律师 / 代理所专业咨询.

总结: catfish 有 2 个真值得 file 的方向 (1 + 2), 短期 5 万 RMB 内可完成 CN + US 占位. 其余 3 个方向走 defensive publication 比 file 划算. 整体期望值: dual licensing 模式下若 catfish 拿到 2-3 个国企商业客户, patent 是支撑 license fee 的法律抓手; 若 catfish 商业上没起来, 短期 5 万投入也不算重大沉没成本.

— 报告完
