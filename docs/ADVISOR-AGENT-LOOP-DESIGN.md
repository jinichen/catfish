# ADVISOR-AGENT-LOOP-DESIGN — advisor 改 multi-turn agent loop + 外部 agent 分发

> **状态**: 设计稿 backlog. 鸿波 6/18 提出, 回头再拍是否启动 / 怎么分 phase.
> **背景**: 鸿波 6/18 问 "把 Cowork / Codex 当 agent 调有没可能 + 怎么告诉 advisor 走哪条". 两轮 audit 后落这份 plan.
> **不动**: 现有 advisor (briefing_advisor.ts) 不立即改, 这只是路径规划.
> **关联**:
> - `CATFISH-ADVISOR-DESIGN.md` (Phase 7 智能参谋定位, 强约束源头)
> - `CATFISH-HERMES-BOUNDARY.md` (catfish 只做 hermes 没的事, 不重写)
> - `CENTRAL-EDGE-DATA-BOUNDARY.md` (数据零出端)
> - `~/.hermes/skills/autonomous-ai-agents/{codex,claude-code,kanban-codex-lane}/SKILL.md` (hermes 现有 agent 集成 SKILL.md)

---

## 0. 这份 plan 解决什么问题

鸿波 6/18 在 catfish 仪表盘看到 hermes 已经装了 `autonomous-ai-agents/codex` (v1.0.0) / `claude-code` (v2.2.0) / `hermes-agent` (v2.1.0) / `kanban-codex-lane` (v1.0.0) / `opencode` (v1.2.0) 五个 skill, 提出:

> "把 Cowork / Codex 作为另一个 AGENT 来交互的可能性?"

> "怎么告诉 advisor 是走我们现有的通道, 还是 codex?"

audit 后发现两个事实:

1. **catfish advisor 当前路径**: `briefing_advisor.ts` 单轮 POST `/v1/chat/completions` + `response_format: json_object`, 拿完整 JSON 走人. `tools` 字段只在 Call 2 `transformToStructured` 用一次, **仍是单轮**, tool_call 一次就 parse 出场. 不走 agent loop, codex/claude-code skill **完全不会被触发**, 装了等于没装.
2. **hermes 调外部 agent 是两层不同的事**:
   - **Provider adapter 层** (`codex_runtime.py` / `anthropic_adapter.py`): hermes 当 LLM 客户端用. `api_mode == "codex_app_server"` 时 hermes spawn `codex app-server` 当 model server. 这是 hermes 用谁推理, 不是 hermes 把 codex 当 sub-agent.
   - **Tool 层** (`tools/terminal_tool.py` + SKILL.md): hermes agent loop 自主决策要不要 `terminal(command="codex exec ...")` spawn codex 当 sub-agent. SKILL.md 是给 LLM 当用法指导, 不是 hardcoded 路径.

要做"hermes 当 master 把 codex 当 sub-agent 干代码 / 文件类任务", 路径已通; 但 catfish advisor 单轮 chat completion 走的是 LLM gateway 路径, 不进 agent loop, **现状 codex 在 advisor 链路 0 触发**.

这份 plan 把 advisor 改 multi-turn 的两条路径 + 决策机制 + phase 演进图 落下来.

---

## 1. 现状 audit

### 1.1 advisor 调用形态

`edge/companion-app/src/lib/briefing_advisor.ts`:

```ts
// Call 1: fetchBriefingAdvisor (line ~1040)
POST {backendUrl}/v1/chat/completions {
  model, messages: [system, user],
  max_tokens: 6000, temperature: 0.4,
  stream: false,
  response_format: { type: 'json_object' }
}
// 单轮, 拿完整 JSON 出场

// Call 2: transformToStructured (line ~1370)
POST {gatewayUrl}/v1/chat/completions {
  model, messages,
  tools: [{ function: submit_advisor_result, parameters: ADVISOR_JSON_SCHEMA }],
  tool_choice: { type: 'function', function: { name: 'submit_advisor_result' } }
}
// 也是单轮, 强制调 tool 一次, 不走 agent loop
```

### 1.2 hermes 端 agent loop 暴露状态

audit `~/.hermes/hermes-agent/` 得:

- hermes 主 agent loop 在 `agent/conversation_loop.py` 跑, 由 CLI (`hermes chat`) / gateway platforms (telegram/slack/...) 触发.
- hermes API server (`APIServerAdapter` 在 hermes 内, catfish-xcatfish-user/plugin.py 有 monkey-patch precedent) 暴露 `/v1/chat/completions` + `/api/sessions/*` + (P18 加的) `/api/sessions/{id}/compress/stream`. **未暴露** "运行 agent loop 直到 final answer" 这种 endpoint.
- `autonomous-ai-agents/codex/SKILL.md` 写明用法: `terminal(command="codex exec '...'", pty=true, workdir=...)`. 路径是 agent 自主决策 + tool calling.
- `autonomous-ai-agents/kanban-codex-lane/SKILL.md` 是 **kanban 工作流专用 convention** (ownership / safety / worktree 隔离), 不是技术 bridge. 主 loop 调 codex 不强制走 kanban-lane convention.

### 1.3 关键约束 (来自 CATFISH-ADVISOR-DESIGN.md / CATFISH-HERMES-BOUNDARY.md)

- **任何级别都不代行** (5/21 鸿波 3 拍): advisor 推荐 + 准备草稿, 不替员工执行
- **草稿不直接发**: 邮件 / 公告 / 文档草稿存 `~/.catfish/outputs/`, 员工自己审 + 发
- **catfish 只做 hermes 没的事**: hermes 有的不重写
- **数据零出端**: 员工原始数据不出端, catfish-private-main 内网模型走
- **决策留痕**: 员工选了啥写 `~/.catfish/decisions.jsonl`

这些约束 + 鸿波 6/17 "AI never sends email autonomously" → 直接结论: **codex/claude-code dispatch 不能让 LLM 自由触发, 必须员工拍板才执行**.

---

## 2. 两条路径选 (Option α vs Option β)

### 2.1 Option α — catfish 这边跑 loop, hermes 当 LLM gateway (推荐)

```
catfish briefing_advisor.ts
  ↓ POST /v1/chat/completions (turn 1)
hermes (纯 LLM gateway)
  ↓ tool_calls 回包
catfish 自己调 tool (Tauri 命令: dispatch_codex / compliance_scan / journal_write / ...)
  ↓ tool result 加 messages
catfish POST /v1/chat/completions (turn 2)
  ↓
... 直到 finish_reason = "stop"
catfish parse final 输出 → AdvisorResult
```

**优点**:
- 红线在 catfish 这层守 (Rust + TS), 跟现有架构和谐
- 不动 hermes (0 monkey-patch)
- tools 实现复用现有 Tauri 命令栈 (compliance_scan / journal_write / email_draft 都现成)
- 现有 Call 2 `transformToStructured` 已经是 tool calling 范式, plumbing 一半都有了
- catfish 完全控制每个 tool 的 sandbox / 红线过滤 / budget

**缺点**:
- catfish 自己写 loop 控制器 + tool registry, 维护成本 (但不大, ~200 行)
- 跟 hermes 主 loop 的功能有一定重叠

### 2.2 Option β — hermes 这边跑 loop, 加 `/v1/agent/run` endpoint

```
catfish briefing_advisor.ts
  ↓ POST /v1/agent/run { messages, max_turns: 10, allowed_skills: [...] }
hermes APIServerAdapter (P20 monkey-patch 加新 endpoint)
  ↓ hermes 内部 spawn agent runtime, 跑完整 agent loop, 调装好的 skill
  ↓ return final result
catfish 一次拿结果
```

**优点**:
- catfish 改动小 (只换 url)
- 复用 hermes 现成 agent loop + skills (codex / claude-code / opencode 都在装)
- 跟 `kanban-codex-lane` convention 直接对齐

**缺点**:
- 需要 hermes 内部跑 agent loop 实例化 (上下文 / session / streaming), 改动比 P18 大
- 红线下移到 hermes 那层, catfish 失去精细控制 — 违反"catfish 只做 hermes 没的事"原则, 同时违反"数据零出端" (hermes 那层能看到所有 tool 执行结果, 比 catfish 控制弱)
- HTTP timeout 复杂 — 5-10 min long task, 需要走 SSE 流或 polling
- monkey-patch 大改 hermes 内部状态, 破坏 catfish-hermes 边界

**结论**: **走 Option α**. 跟 `CATFISH-HERMES-BOUNDARY.md` 的"catfish 只包 hermes 给员工"原则一致 — agent loop 控制是 catfish 边端逻辑, 不需要 hermes 帮跑.

---

## 3. 三层决策机制 — 怎么告诉 advisor 走哪条

鸿波 6/18 catch: "怎么告诉 advisor 走我们现有通道还是 codex?"

audit 后**核心结论**: 别让 LLM 自由 dispatch. 决策分三层:

### 3.1 层 1 — LLM 自由判断"归类", 不触发执行

`ADVISOR_JSON_SCHEMA` 给每个 mainTask 加 `recommended_lane` 字段, LLM 自由填:

```ts
recommended_lane:
  | "self"                  // 员工自己看 / 思考 / 对话类
  | "current_tools"         // 走现有 catfish 自动化 (合规扫 / 邮件草稿 / journal 写)
  | "codex_dispatch"        // 建议 spawn codex 实施 (代码 / 文件 / git / lint / 测试)
  | "claude_code_dispatch"  // 建议 spawn claude-code (写作 / 多语言代码 / 文档)
```

SYSTEM_PROMPT 加归类规则:

```
推荐 lane 规则:
- 看 todo title + tags + 目标产物
- 涉及 .ts/.py/.rs/.md 文件 / git 操作 / lint / 测试 / 重构 → codex_dispatch
- 涉及邮件 reply / 公告 / 周报正文 写作 → claude_code_dispatch
- 涉及合规 / 抄送审 / catfish 通道能干的 → current_tools
- 不确定 → self (员工自己判断)
```

LLM **只标 lane, 不触发执行**.

### 3.2 层 2 — UI 显示, 员工点哪条走哪条

主菜 ActionCard 旁边显 lane badge:

```
[fix lint in src/auth.py]            [📦 codex 实施 ~3min]    ← 橙色按钮 (员工点才 spawn)
[reply 老板邮件 (财报问题)]            [✍️ AI 起草 →]           ← 蓝色 (现有 current_tools 路径)
[读 Q2 战略文档]                       [👤 你自己来]             ← 灰色不可点
```

**实际触发 dispatch 必须员工点 badge**. LLM 不自主触发. 跟现有 "AI 起草 →" 按钮一样的体感 — advisor 推荐, 员工拍板. 跟 `CATFISH-ADVISOR-DESIGN.md` 强约束 "任何级别都不代行" 一致.

### 3.3 层 3 — Rust 层 hardcode 排他规则 (LLM 推荐被覆盖)

某些场景直接拒, UI 显"建议被覆盖":

```rust
// codex_dispatch 红线
if task.touches_secrets() || task.touches_env_files() {
  override_lane = "self";
  reason = "涉及凭证, codex 不让碰";
}

if task.is_email_send() {
  override_lane = "current_tools";   // 强走 email_draft (草稿不发)
  reason = "邮件外发 走 catfish 草稿 + 员工审, 不让 codex 跑";
}

if task.touches_knowledge_base_write() {
  override_lane = "current_tools";
  reason = "知识库写 走 catfish 通道, 不让 codex 改";
}

if task.involves_personal_data_export() {
  override_lane = "self";
  reason = "员工原始数据外联, 强制员工手动";
}
```

UI 显: ⚠️ "原建议 codex_dispatch, 被红线覆盖 → 你自己来 (涉及凭证)".

### 3.4 完整流程图

```
advisor multi-turn agent loop
  ↓
turn 1: LLM 决策 — 每条主菜标 recommended_lane
  ↓
Rust 红线规则: 检查每条 lane, 不合规则 override
  ↓
turn 2..N: LLM 调 tool (compliance_scan / journal_write / ...) 准备草稿/上下文
  ↓
LLM finish → AdvisorResult (含 recommended_lane + override_reason)
  ↓
UI 渲染: 主菜旁显 badge ("📦 codex" / "✍️ AI 起草" / "👤 自己")
  ↓
员工点 badge   ← 这里才**实际**触发 dispatch
  ↓
Tauri 命令: codex_dispatch_task / draft_email / ...
  ↓
Rust 端: worktree 隔离 + 红线过滤 + budget 兜底 + log
  ↓
跑完返 diff / 草稿 / 结果
  ↓
UI 显 review pane: 员工 accept / reject / partial
  ↓
决策留痕 ~/.catfish/decisions.jsonl
```

---

## 4. Phase 演进图

Phase 化引入, 每个 phase 都可单独 ship + 可单独回滚.

### Phase 1 — advisor 单轮 → multi-turn loop plumbing

**改动**: 局限 `briefing_advisor.ts` + 新 `agent_loop.ts` helper. 不动 Rust, 不动 hermes.

**工作**:
- 抽 `runAdvisorAgentLoop(messages, tools, maxTurns)` helper, 复用 `fetchWithAuth`
- `ADVISOR_JSON_SCHEMA` 加 `recommended_lane` 字段
- `SYSTEM_PROMPT` 加 lane 归类规则
- tools 暴露给 LLM 但只包**现有自动化** (compliance_scan / journal_write / email_draft 已有 Tauri 命令)
- `max_turns = 5`, `max_budget_usd = 0.1` (deepseek), `0.3` (catfish-private-main)
- fallback: max_turns 到 → 走 `transformToStructured` 兜底 / tool 挂 → 单条砍重试 / 全挂 → stale cache

**工期**: 1-2 天

**验**:
- advisor 行为**不变** (因为 tools 没引入新能力, LLM 还会走单轮直接出 JSON)
- 但已经是 multi-turn 架构, 后续 phase 增量
- recommended_lane 字段已填入, UI 暂只 console.log 看分布, 不接渲染

**红线**: 0 引入 dispatch tool, 数据出端约束完全不变.

### Phase 2 — codex_dispatch Tauri 命令 + UI 接 codex badge

**改动**: 加 Rust 端 `codex_dispatch_task` Tauri 命令, advisor tools 加 `codex_dispatch` (但**仍然只标 lane, 不触发**), UI ActionCard 加 badge 显示.

**工作**:
- Rust: `codex_dispatch_task(task_id, prompt, workdir, max_budget)` Tauri 命令
  - spawn codex CLI 在新 worktree (`/tmp/catfish-codex/<task_id>-<ts>/`)
  - 红线过滤: 不 mount secrets / .env / ~/.catfish/profile.json
  - budget 兜底: `--max-budget-usd` flag
  - log: `~/.catfish/dispatch-log/<date>.jsonl`
- TS: ActionCard 旁加 codex badge, 点击 → 调 Tauri 命令 → 弹 review pane
- review pane: 员工看 codex 给的 diff, accept / reject / partial / 跳回 self
- `SYSTEM_PROMPT` 加 codex 归类规则细化 (eg. "看 todo tag 含 'code' 'lint' 'refactor' 走 codex_dispatch")

**工期**: 3-5 天

**验**:
- 给一个 "fix lint in src/auth.py" 类 todo, advisor 标 lane = codex_dispatch, UI 显 badge
- 员工点 badge → 弹 review pane → 看 codex diff → accept → 应用到工作目录
- 不应用情况下 worktree 自动清理

**红线**:
- codex 跑 OpenAI 云 / 本地 codex 二选一, 默认走本地 (零出端). 没本地 fallback OpenAI 时**必须**显黄条提示 "本任务数据将出端到 OpenAI, 继续? 否 / 是".
- worktree 隔离, 不让 codex 读 `~/.catfish/` 原始数据.

### Phase 3 — claude_code_dispatch Tauri 命令 + UI badge

**改动**: 同 Phase 2 模式, 不重复.

**特殊**: claude-code 文字类任务更多, 跟邮件 draft / 公告写作冲突 — 决策表得画清楚:
- 邮件 reply → **强走 current_tools** (email_draft Tauri 命令存草稿), 不让 claude-code 直接生成
- 公告 / 周报 / 文档**正文初稿** → 可走 claude_code_dispatch
- 给员工 ghostwriter 风格的文档 → claude_code_dispatch

**工期**: 3-5 天

### Phase 4 — kanban-codex-lane convention 落实 + ownership 约束

**改动**: 复用 hermes 的 `kanban-codex-lane` SKILL.md 思路, 在 catfish 这层做相同 ownership 约束.

**工作**:
- catfish advisor 必须**保有 todo lifecycle 所有权**: codex 跑完不能直接 mark Done, 必须 advisor (LLM) review + catfish 自家测试通过才 close
- worktree 自动隔离: codex 永远在 sibling worktree 跑, accept 时 cherry-pick 回主分支
- 跑 catfish 自家测试: codex 完后必须跑 `npm test` / `cargo test`, 失败强制 reject

**工期**: 2-3 天

**验**:
- codex 跑完跑了 100 % 测试通过 → review pane 显绿
- 测试挂 → review pane 显红 + "测试挂, 不推荐 accept"
- 强 accept → 决策留痕 `decisions.jsonl` (员工拍了反正)

---

## 5. 红线 + 跟现有约束的关系

### 5.1 跟 CATFISH-ADVISOR-DESIGN.md 强约束的关系

| 强约束 | 这份 plan 的处理 |
|---|---|
| 任何级别都不代行 | dispatch 必须员工点 badge 触发, LLM 只标 lane, 跟现有"AI 起草不发"一致 |
| 草稿存 outputs/ 不直接发 | claude_code 写完邮件草稿存 `~/.catfish/outputs/<date>/`, 跟现有路径一致 |
| 决策留痕 | 员工每次 dispatch 选择 + accept/reject/partial 都写 `~/.catfish/decisions.jsonl` |
| 判断透明 | review pane 显 diff + codex 跑了什么命令 + 改了什么文件 |
| 央国企特殊层保守 | 政治敏感 / 合规事项的 lane 永远是 `self` 或 `current_tools`, 不让 codex/claude-code 碰 |

### 5.2 跟 CATFISH-HERMES-BOUNDARY.md 的关系

- ✅ **不重写 hermes** — Option α 走 catfish 自己 loop, 不动 hermes 主 loop. agent loop 控制是 catfish 边端的事 (UI / 红线 / 员工拍板都是 catfish 范围), hermes 不该接.
- ✅ **包装 hermes 给员工** — hermes 的 codex/claude-code skill 是技术能力, catfish 给它包**员工主权 + 红线 + UI**.
- ⚠️ **审一下边界**: catfish 写 advisor loop 控制器**有没有重写 hermes 已有功能**? hermes 主 loop 跑在 hermes CLI 上, catfish 这层是给 advisor UI 用, 二者用例不同. 可以接受.

### 5.3 跟 CENTRAL-EDGE-DATA-BOUNDARY.md 的关系

- 全部新代码在 `edge/companion-app/` (TS + Rust). 不进 `central/`.
- codex/claude-code 跑本地 (员工本机), 数据零出端.
- 走云 (OpenAI Codex / Anthropic) 时必须显黄条 + 员工确认.

### 5.4 跟 PRIVACY-PRINCIPLES.md / 数据零出端 的关系

- codex worktree mount 时**白名单**: 只 mount 项目代码 + 当前 todo 上下文, 不 mount `~/.catfish/profile.json` / `~/.catfish/journal_todos.json` / `~/.catfish/email/*` 等原始员工数据.
- claude-code 写邮件草稿时, 输入的"原邮件正文"必须先过 catfish 红线过滤 (去敏感字段) 才喂给 claude-code.

---

## 6. 未决问题 (鸿波回头拍)

1. **Phase 1 立刻启动还是 backlog?**
   - Phase 1 不引入 dispatch tool, 但已经把 advisor 改 multi-turn, 是个**架构性**改动. 如果 P3.5.32 系列 (UI 优化) 还在打磨期, Phase 1 可能不急.

2. **codex 默认走本地还是云?**
   - 本地 codex CLI 需要装 `npm install -g @openai/codex` + 配 auth. 员工没装时是黄条引导员工装还是直接 fallback 云?
   - 本地有 open-source 的 `opencode` (catfish 仪表盘也看到了 v1.2.0), 完全本地 inference 一条路, 但能力可能不如 codex.

3. **claude-code 跟现有 email_draft 怎么分边界?**
   - 现有 catfish email_draft 已经能起草邮件, claude_code_dispatch 加入后是替代还是并存?
   - 提议: 邮件类**强走 email_draft** (跟现有一致), claude_code_dispatch 只用于公告 / 周报 / 文档正文.

4. **agent loop 的 max_turns 怎么定?**
   - catfish-private-main 慢 (~100s/call), 5 轮 = 500s. UI LoadingProgress 已经做了 phase, 5 轮内可控.
   - deepseek-flash 快 (~5s/call), 10 轮也才 50s, 可以放宽.
   - 提议: model-aware, private-main = 5, deepseek = 10.

5. **kanban-codex-lane convention 在 catfish 这边怎么落?**
   - hermes 那边 SKILL.md 是给 hermes agent loop 看的. catfish 自家 loop 也要不要写一份 catfish-side convention md?
   - 提议: 写 `CATFISH-CODEX-LANE.md`, 复刻 hermes 那份的 ownership / safety / worktree 约束, 但适配 catfish UI 流程 (员工点 badge 触发 / review pane 必经).

6. **Cowork 当 agent 调要不要列**?
   - audit 后 Cowork 没 headless CLI 入口, 现状不通. 等 Anthropic 开放再考虑.
   - 不进 phase 1-4, 留 future backlog.

---

## 7. 不在这份 plan 范围

- Cowork 当 sub-agent 调 (Cowork 无 CLI 入口, 等 Anthropic)
- hermes 主 agent loop 改造 (catfish 不该碰)
- LLM 路由 / model role 抽象改造 (P3.5.29 已 ship, 不重复)
- Insight Reports 三维新功能 (P3.5.32 已 ship)

---

## 8. 路径决策记录

| 日期 | 决策 | 拍板人 |
|---|---|---|
| 6/18 | 选 Option α (catfish 这边跑 loop), 不动 hermes | 待鸿波拍 |
| 6/18 | 三层决策 (LLM 归类 + UI 拍板 + Rust 红线) | 待鸿波拍 |
| 6/18 | Phase 1-4 顺序 | 待鸿波拍 |

---

**END**
