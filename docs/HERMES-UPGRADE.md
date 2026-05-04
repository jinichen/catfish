# Hermes 升级方案 (5/4 起草, 5/8 后启动议程, demo 后执行)

> **背景**: 鸿波 5/4 看到 NousResearch/hermes-agent 升到 0.12.0, 我们当前固定在 0.10.0.
> demo (5/14) 前不动. 这份文档是"研究清楚再动"的产物 — 含 0.10 → 0.12 变更分析 +
> 我们补丁层风险评估 + **未来 hermes 升级预留方案** (核心思路: 字符串 patch → runtime hook).
>
> **状态 (5/4 23:30)**: 已完成 Curator 接口分析 + 集成方案设计 (见 § 8).
> **暂停原因**: 5/4-5/8 demo 准备期. **5/8 后** 鸿波统一启动议程, 决定 verify 工作 + 升级时间. 不动代码, 不抓更多源码.

---

## 1. 现状盘点

我们当前对 hermes 0.10.0 打了 **3 层** 共 **~735 行** 补丁:

| 层 | 文件 | 行 | 干啥 | 脆性 |
|----|------|---|------|------|
| 1 | `edge/hermes-fork/apply_brand_patch.py` | 468 | Python AST 级 patch + 字符串替换 (banner / version 标题 / Welcome / Goodbye / 状态栏 ⚕ → 🐟) | **极高** — 依赖 hermes 源码具体 AST 节点 + 字符串字面量位置 |
| 2 | `edge/hermes-fork/rebrand.sh` | 183 | sed 字符串替换 (UI 文案 / ASCII art / banner) | **高** — 依赖文件 glob 命中 + 字面量匹配 |
| 3 | `edge/hermes-fork/string-map.yaml` | 84 | 声明式替换规则 | **高** — 同上 |
| 4 (运行时) | `edge/tool-bridge/.../adapter.py:scrub_brand_in_result` | ~50 | dispatch 末尾对 `memory_*` 工具响应做 regex 脱敏 (`~/.hermes` / `hermes` 词) | **低** — 不动 hermes 源码, 只读响应 |

**关键观察:** 第 4 层 (BL-D9 那次做的 dispatch scrub) 是**唯一不依赖 hermes 内部结构**的, 升级 hermes 不会断. 这是未来要扩展的方向.

---

## 2. Hermes 0.10 → 0.12 变更摘要

### 0.11.0 (2026-04-23, "The Interface Release") — **大重写**

> 1,096 commit / 550 PR / 1,270 文件改 / +217k 行

**对我们影响大的变更:**

| 变更 | 影响 catfish |
|------|------------|
| **React/Ink CLI 全重写** | banner / 状态栏 / Welcome / Goodbye 字符串**位置全变**. apply_brand_patch.py 大概率全断. |
| **Profile 系统** (#3623) | `~/.hermes` 路径迁到 `display_hermes_home()`, 多 profile 隔离. dispatch scrub 的 regex `~/.hermes/...` 需扩展覆盖新路径形态. |
| **Transport ABC 层** (#13347) | provider 字符串从 `run_agent.py` 迁到 `agent/transports/`. AST patch 节点路径变. |
| **Shell Hooks** (#13296) | **对我们好**: hermes 加了 `pre_tool_call` / `post_tool_call` / `on_session_start` 等 lifecycle 钩子. **未来可以用钩子注入 banner / Welcome 替换, 不再用源码 patch**. |
| **Pluggable Context Engine** (#7464) | 上下文管理变 plugin slot. 我们没用, 不影响. |
| **Secret Redaction default off** (#16794) | hermes 自己的 secret 过滤默认关. 我们 prompt_security 模块独立, 不依赖. |
| **GPT-5.5 over Codex OAuth** (#14720) | 模型新增. 我们 model_catalog 自动跟. |
| **Webhook Direct-Delivery** (#12473) | 0-LLM 推送. 不用. |
| **17 个消息平台 (QQBot 加入)** | 不用. |
| **AWS Bedrock 原生支持** (#10549) | provider 加. 不影响. |

### 0.12.0 (2026-04-30, "The Curator Release")

> 0.11.0 后 7 天

| 变更 | 影响 catfish |
|------|------------|
| **后台 Curator daemon** | 自主对 skill library 评分/裁/合并, 跟我们 Skills Hub 概念**重叠**. 需评估是否冲突 — 可能要禁用 Curator (HERMES_CURATOR_ENABLED=false?) 防双方都管. |
| **Pluggable Memory Provider ABC** (#4623) | memory tool 后端可换. 我们 dispatch scrub 的 `_HERMES_TOOLS_NEEDS_BRAND_SCRUB = {memory, memory_save, memory_load, memory_search}` 可能要加新工具名. |
| **57% 冷启动优化** (#12995) | 用 lazy import. **副作用: 文件 line offset 全变**, 我们 sed-based rebrand.sh 大概率断. |
| **4 个新推理 provider** | 不影响我们. |
| **Spotify / Google Meet 原生** | 不用. |
| **ComfyUI / TouchDesigner-MCP bundled** | 不用. |
| **Teams 插件 (19 个平台)** | 不用. |

### 没有的东西 (重要)

- **❌ 没加 i18n / theming / branding hook 框架** — 字符串补丁仍只能字面量匹配
- **❌ 没暴露 banner/Welcome/Goodbye 替换接口** — 只能继续 source patch (或用 Shell Hooks 间接覆盖一些 lifecycle)

---

## 3. 升级风险等级 (catfish 视角)

| 模块 | 等级 | 原因 |
|------|------|------|
| `apply_brand_patch.py` (468 行 AST patch) | 🔴 极高 | 0.11 React/Ink 重写, AST 节点全变 |
| `rebrand.sh` (sed) | 🔴 高 | 0.12 lazy import 让 line offset 漂 |
| `string-map.yaml` | 🔴 高 | 同上, 字面量匹配 |
| `adapter.py: tool registry 调用` | 🟢 低 | agent 看 changelog 没动 dispatch 接口 (`r.dispatch / get_all_tool_names / get_schema / get_emoji / get_toolset_for_tool / is_toolset_available / get_max_result_size`) |
| `adapter.py: scrub_brand_in_result` | 🟡 中 | regex `~/.hermes/...` 要扩展覆盖 `display_hermes_home()` 输出 |
| `identity_inject.py` | 🟢 低 | SOUL.md 路径 `~/.hermes/SOUL.md` 在 profile 系统下可能位置变, 加 `HERMES_HOME` env fallback 即可 |
| `employee_journal.py` | 🟢 低 | 路径可调, 不依赖 hermes 内部 |
| **Skills Hub vs Curator** | 🟡 中 | 0.12 的后台 Curator 跟我们 Skills Hub 模型重叠. 评估是否禁用 Curator 或集成 |

---

## 4. demo 前 (5/4 ~ 5/14): 短期加固 (不升级)

**目标**: 不升级 hermes, 但把容易出问题的 catfish 侧加固, 升级时受影响面更小.

- [ ] **加固 `scrub_brand_in_result` 的 regex** 覆盖 `display_hermes_home()` 可能输出
  - 现:  `~/.hermes` / `/Users/x/.hermes` / `/home/x/.hermes`
  - 加:  `(?:[/\w.-]+)/.hermes(?:_<profile>)?` (覆盖未来 profile 子目录)
  - 影响面: 1 个函数, 加 5 个测试用例, 半小时
- [ ] **测试覆盖** 把 BL-E11/E15/E16 加进 hermes 升级回归清单 (现没 list, 升级后没 checklist 容易漏)
  - 写 `docs/HERMES-UPGRADE-CHECKLIST.md`, 列每个升级要重测的 catfish 功能 + 期望行为
- [ ] **DO NOT** 现在跑 dry-run apply_brand_patch.py 在 0.12 上 — 失败堆栈会让你紧张, demo 前不需要

**估时**: 2-3 小时

---

## 5. demo 后 (5/15 起): 中期升级到 0.12

**目标**: 实际升级到 hermes 0.12.0, 同时**重构 brand patch 架构**减少未来脆性.

### 阶段 A (5/15 ~ 5/17): 评估 + 备份
- [ ] git tag `pre-hermes-upgrade-0.10`
- [ ] dry-run apply_brand_patch.py 在 0.12 上, 记录每条规则 (468 行) 的命中状态
- [ ] 评估 Curator daemon 跟 Skills Hub 是否冲突, 决定: 禁用 Curator / 集成 / 共存

### 阶段 B (5/18 ~ 5/22): 重写 brand patch (核心改造)

**架构方向: 字符串补丁 → 三层运行时拦截**

```
旧 (脆性):                 新 (跟 hermes 解耦):
                                                                
源码 patch 改 banner    →   Shell Hook (on_session_start) 打 banner
                                                                
源码 patch 改 Welcome   →   Shell Hook 替换 print 输出
                                                                
源码 patch 改 ASCII art →   配置文件 + hermes plugin 渲染层
                                                                
sed 改 emoji ⚕ → 🐟      →   stdout filter wrapper (catfish 命令行入口包一层)
                                                                
dispatch 响应过滤        →   adapter.py:scrub_brand_in_result (已有, 扩展)
```

**具体三层 (从无脆到有脆):**

1. **Wrapper subprocess** (跟 hermes 完全解耦): catfish CLI 入口 (`edge/branding/catfish` shell 脚本) 包一层, stdout 经过 sed/awk filter 替换 `Hermes` → `鲶鱼` / `⚕` → `🐟` 等. **优点**: hermes 升级随便升, 不用 patch 源码. **缺点**: 只能替换文字字面量, 改不了 ASCII art 结构 / 不能改 React/Ink TUI 输出 (二者用不同 stdout 协议).
2. **Shell Hook** (用 0.11+ 提供的钩子): 注册 `on_session_start` → 打鲶鱼 banner; `pre_exit` → 打"再见 🐟". **优点**: 半官方接口, 跟 hermes 升级解耦. **缺点**: 0.11+ 才有, 不能用于历史 banner.
3. **Source patch** (兜底): 如果 wrapper + shell hook 还覆盖不到的 banner / 字符串, 才用源码 patch. 估计能从 468 行裁到 50-100 行.

**写新 brand 包: `edge/hermes-fork/v2/`**
  - `wrapper.sh` (~30 行): stdout filter
  - `hooks/` (目录): hermes shell hooks 注册
  - `legacy_patch.py` (~100 行): 仅兜底源码 patch
  - `tests/` 自动验证每层不破

### 阶段 C (5/23 ~ 5/27): 完整回归
- 跑 `HERMES-UPGRADE-CHECKLIST.md` 里所有 catfish 功能
- BL-E11 / E15 / E16 / 主动闲聊 / Quota / RBAC / Skills Hub / SSO / brand 全验
- 真机 hermes 0.12 vs catfish 全栈跑 1 整天

### 阶段 D (5/28 ~ 5/30): 文档 + 提 PR

- 整理 `HERMES-UPGRADE-PLAYBOOK.md` (下次升级流程 SOP)
- 给 NousResearch 提 issue 请求 i18n / branding hook (引用我们的用例 — 中国市场 SOE 客户需要本地化)

---

## 6. 长期 (Q3+): 上游 i18n hook + 完全解耦

**目标**: 让 hermes 升级跟 catfish brand 完全解耦, 每次升级 30 分钟搞定.

- 给 NousResearch 提 PR 加 `branding.toml` / `i18n.toml` 配置: 用户提供一个文件, hermes 加载时读, 替换所有 banner / lifecycle string. 类似 gettext.
- 我们的 catfish brand 变成 `branding.toml` 一个文件, **0 行 source patch**.
- hermes 升级 → catfish brand 不动, 只可能要补几个新加的 string key.

**估投入**: 给 hermes 提 PR 1-2 周, upstream merge 周期 2-4 周, 总 1.5 月. 但**一劳永逸**.

---

## 7. 风险 mitigation 表

| 风险 | mitigation |
|------|-----------|
| 升级时 brand patch 全断, 员工看到 hermes 字眼 | 阶段 B 做完 wrapper subprocess (无脆), 即使 source patch 失败员工看到的 stdout 也已经过滤 |
| Curator 跟 Skills Hub 冲突 | 阶段 A 评估时拍板: 禁用 Curator (env 变量) 或重写 Skills Hub 适配 |
| 升级中 demo 客户来电 | 升级在 demo 后 (5/15+), 给自己 30 天窗口 |
| upstream 提 PR 不被采纳 | wrapper subprocess 已经够稳, upstream PR 是 nice-to-have |

---

## 附: 升级回归 checklist (写到 `HERMES-UPGRADE-CHECKLIST.md`)

升级 hermes 后必验:

- [ ] catfish 命令启动, banner 显示鲶鱼 (不显 hermes)
- [ ] 退出语显鲶鱼 (不显 Goodbye)
- [ ] tool 列表能拿全 (`adapter.list_tools()` 返 60+ 个)
- [ ] catfish 原生 tool 能 dispatch (catfish_today_summary)
- [ ] hermes builtin tool 能 dispatch (memory_save / file_read 等)
- [ ] memory_save 响应里 `~/.hermes` 已脱敏成"鲶鱼本机存储"
- [ ] BL-E11 命名权: 改名后 LLM 自我介绍用新名
- [ ] BL-E15 专注模式: Cmd+Shift+F 切伪 IDE
- [ ] BL-E16 关系建立: session_meta 注入正确
- [ ] 主动闲聊: 9:30 / 14:00 / 17:30 macOS 通知触发
- [ ] Quota: chat 超限正确返 429 + 友好话术
- [ ] RBAC: manager / admin Dashboard 卡显示正确
- [ ] Skills Hub: publish / install / dry-run 跑通
- [ ] SOUL.md 注入到 system prompt (含品牌铁律 + 情绪规约 + 命名权 preamble)
- [ ] brand favicon / app icon / mascot 等无变化

---

## 8. Curator daemon (0.12 加) vs 我们 Skills Hub — 集成方案 (5/4 晚研究)

> **5/4 鸿波问**: 0.12 加的后台 Curator 跟我们 Skills Hub 是不是会冲突? 是不是应该集成?
> **结论**: **不冲突, 应该集成 (而非禁用). 5/8 后启动 verify + 实施.**

### 8.1 Curator 是什么 (verified, 引自 hermes 0.12 `agent/curator.py`)

> *"Curator — background skill maintenance orchestrator. The curator is an auxiliary-model task that periodically reviews **agent-created skills** and maintains the collection. It runs **inactivity-triggered** (no cron daemon): when the agent is idle and the last curator run was longer than `interval_hours` ago."*
>
> **Strict invariants:**
>   - *Only touches **agent-created** skills (see `tools/skill_usage.is_agent_created`)*
>   - ***Never auto-deletes — only archives.** Archive is recoverable.*
>   - ***Pinned skills bypass all auto-transitions***
>   - *Uses the auxiliary client; never touches the main session's prompt cache*

不是真"daemon", 是 lazy 触发: agent idle 时检查"距上次 N 小时", 满了才跑 1 次.

### 8.2 Curator 接口清单 (5/4 verified, 行号引自 v2026.4.30 `agent/curator.py`)

| 维度 | 答案 | 引用 / 怎么用 |
|------|------|--------------|
| **能 disable 吗?** | ✅ 能 | `~/.hermes/config.yaml` 加 `curator.enabled: false` (lines 115-125, `is_enabled()` 默认 True) |
| **能 tune 吗?** | ✅ 4 个参数都能 tune | yaml `curator.{interval_hours,min_idle_hours,stale_after_days,archive_after_days}` (lines 140-167, `get_*()` 函数读 config) |
| **会动 hub-installed skill 吗?** | ⚠️ 大概率不会 (需 verify) | docstring line 19: *"Only touches agent-created skills"*. 通过 `tools.skill_usage.agent_created_report()` 拿过滤后的列表 (line 221, 977). **`is_agent_created()` 实际判定逻辑没读 — 需 5/8 后抓 `tools/skill_usage.py` verify** |
| **能显式排除某 skill 吗?** | ✅ 能, pin 机制 | docstring line 21 + line 233: `if row.get("pinned"): continue`. **`skill_manage` 怎么 pin 没读 — 需 5/8 后 verify** |
| **会真删吗?** | ❌ 永不真删 | docstring line 20: *"Never auto-deletes — only archives. Archive is recoverable."* |
| **能从外部 pause 吗?** | ✅ 能 | 写 `~/.hermes/skills/.curator_state` JSON `{"paused": true}` (lines 102-109, atomic 写文件即接管) |
| **运行模式默认值** | 7 天间隔 / 2 小时 idle / 30 天 stale / 90 天 archive | lines 42-45 `DEFAULT_*` 常量 |
| **状态文件 schema** | `last_run_at` / `last_run_duration_seconds` / `last_run_summary` / `paused` / `run_count` (JSON, 顶层 `_` 前缀字段保留) | lines 52-61 `_default_state()` + line 71 |

### 8.3 推翻之前的"6 个冲突场景"分析

5/4 上一轮列了 6 个 Curator vs Hub 冲突场景, 看完源码后**5 个 hermes 自己已经解决**:

| 之前担心 | 实际情况 |
|---------|---------|
| Curator 误砍 hub skill (装→删→装→删 死循环) | ✅ `is_agent_created` 过滤 + pin 双保险 |
| Curator 合并 hub + 自写 skill | ✅ 同上 |
| 撞名: Curator 自动抽 vs Hub 已有 | ⚠️ 仍存在但范围局限 (只在 employee 提交到 hub 时), 走 BL-C13 dedup 拦 |
| Hub 推更新 vs Curator 已合并版本 | ✅ hub install 重新装直接覆盖 |
| 反向数据流 (Curator 评分上 Hub) | ✨ 可做 (Phase 2.5 ROI) |
| 员工不想要 Curator | ✅ yaml 一行 disable |

### 8.4 推荐方案: **集成 (而非禁用), 5 步实施**

升级到 hermes 0.12 时按这 5 步走:

#### 步骤 1: 默认启用, 但调到保守参数 (catfish 装 hermes 时自动写 config)

`~/.hermes/config.yaml`:

```yaml
curator:
  enabled: true            # 让它管员工自写脚本 (housekeeping)
  interval_hours: 168      # 1 周一次, 默认 7 天 OK
  min_idle_hours: 4        # idle 4 小时才跑 (默认 2 太激进, 容易在午休触发)
  stale_after_days: 60     # 60 天没用算 stale (默认 30 太激进, 季度性脚本会被冤)
  archive_after_days: 180  # 180 天才 archive (默认 90, 半年安全)
```

理由: 央企/SOE 场景下季度性脚本多 (季报/年报/审计周期), 默认 30 天 stale 会冤打很多. 调宽防员工困惑.

#### 步骤 2: catfish_skill_install 装完, 自动 pin (双保险)

`edge/companion-app/src-tauri/src/commands/skills.rs` 的 `catfish_skill_install` 末尾加一步: 调 hermes `skill_manage` API pin 住刚装的 skill. 即使 `is_agent_created()` 误判, pin 也能挡住 Curator.

成本: ~10 行 Rust + 1 个 hermes 命令 / API 调用. 等 5/8 后抓 `skill_manage` 源码确认调用方式.

#### 步骤 3: Onboarding 加 explicit consent (防员工困惑)

`OnboardingWizard.tsx` 的某个 step 加 toggle:

```
☑ 让小鲶定期帮我整理工作脚本 (推荐)
   180 天没用的脚本会归档 (可恢复 · 不真删) ·
   长得像的脚本会合并 · 高质量的会自动标记
```

不打勾 → catfish 写 `curator.enabled: false`. 默认勾.

理由: 央企员工对"自动删东西"敏感, explicit consent 防"我的东西不见了" 困惑.

#### 步骤 4 (Phase 2.5, demo 后第 2-3 周): Dashboard 加"小鲶整理记录"卡

读 `~/.hermes/skills/.curator_state` 的 `last_run_at` + `last_run_summary`, 显示:

> *"小鲶上次整理: 3 天前. 归档了 2 个 90 天没用的脚本 (可恢复). 合并了 3 个相似的工资条解析脚本."*

让员工知道发生了啥. 跟 BL-E16 RelationCard 的"鲶鱼对你的印象" 同思路 — 透明可控防 creepy.

#### 步骤 5 (Phase 3, Q3): 反向数据流 — Curator 评分 → Hub 健康度

Curator 跑完输出员工本机 skill 健康度 → 上传 catfish Hub `/api/skill_health` (新端点) → admin Dashboard 看 **公司级 skill 健康热图** (`feishu-expense-submit` 在 87 个员工那里 grade A 占 60% / D 占 5% — 明确告诉 admin 哪个 hub-skill 真好用).

**这是真正的协同 ROI** — "一个员工本机的使用反馈, 全公司受益". 是 Phase 3 federation 的雏形.

不紧急, demo 后 1-2 月再做.

### 8.5 5/8 后要 verify 的 2 件事 (启动议程时拍板再做)

虽然 docstring 写了"only touches agent-created", 升级前必须读源码确认:

1. **`tools/skill_usage.is_agent_created()` 判定逻辑** — 是按文件位置? metadata 字段 `created_by`? 目录所有权?
   - 我们 catfish_skill_install 装的会被误判吗?
   - **抓**: `https://raw.githubusercontent.com/NousResearch/hermes-agent/v2026.4.30/tools/skill_usage.py`

2. **`skill_manage` 的 pin API** — 怎么从 catfish 端调?
   - 命令行子命令? Python API? 直接改 metadata 文件?
   - 这决定步骤 2 的实现方式
   - **抓**: 找 `skill_manage` 模块路径 (可能在 `agent/` 或 `tools/` 或 `hermes_cli/`), 搜 GitHub repo

预计 verify 时间: 30-60 分钟. 0 风险, 不动机器.

### 8.6 时间表

| 时间 | 动作 |
|------|------|
| **5/4 (今晚)** | ✅ 写完本文档. 不动代码. 不抓更多源码. |
| **5/5 ~ 5/8 (demo 准备期)** | 0 动作. 专心 demo 准备 (BL-E14 PPT 吐槽 + 真机彩排 + PPT 内页 + 录视频) |
| **5/8 后 (鸿波启动议程)** | (a) 抓 `skill_usage.py` + `skill_manage` verify 上面 2 件事 (b) 拍板升级时间 |
| **5/14 demo 当天** | hermes 0.10.0 不动. demo. |
| **5/15 ~ 5/22 (demo 后第 1 周)** | 真升级 hermes 0.12 + 实施步骤 1+2+3. 同时走 § 5 阶段 B brand patch 重构 |
| **5/23 ~ 6/5** | 完整回归 (`HERMES-UPGRADE-CHECKLIST.md`). 全栈跑 1 整天 |
| **6 月** | 步骤 4: Dashboard "整理记录"卡 |
| **Q3** | 步骤 5: 反向数据流 + 上游 PR i18n hook |

### 8.7 风险 mitigation 表 (含 Curator)

| 风险 | mitigation |
|------|-----------|
| Curator 升级后误删 hub skill | 步骤 1 (保守参数) + 步骤 2 (pin 双保险), 步骤 3 (员工知情) |
| Curator behavior 不可预测员工恐慌 | 步骤 4 透明记录, 步骤 3 explicit consent |
| `is_agent_created()` 把 hub skill 当 agent-created 误判 | 5/8 后 verify; pin 双保险即使误判也挡 |
| `skill_manage` API 不存在 / 不能 pin | 5/8 后 verify; 兜底: 直接写 `~/.hermes/skills/.curator_state` 把 hub skill 名字加 paused list (取决于 hermes 是否支持) |
| Curator + 我们 BL-C12/C13 dedup 重复检查冲突 | 物理隔离: catfish_skill_install 装的 skill 加 metadata `catfish_managed: true`, BL-C13 跳过这些 (它们由 hub 中央管, 不需本机 dedup) |

---

## 9. 5/4 真机诊断 — brand patch 现状 + skin 选择

> 鸿波 5/4 在 hermes 终端发现"黄字白底看不清 + 'Hermes' 字样还在", 真机 grep 确认了 brand patch 当前不完整状态. 这一节是**5/8 后 § 5 阶段 B 实施**前的事实备忘.

### 9.1 brand patch 真机状态 (5/4 诊断)

```bash
# .before-catfish 备份数 (有 = 装过 install.sh)
$ find ~/.hermes/hermes-agent -name "*.before-catfish*" 2>/dev/null | wc -l
8

# ui-tui 是不是预编译 dist
$ ls ~/.hermes/hermes-agent/ui-tui/dist/ | head -3
app
app.js
banner.js

# src 还有哪几个文件含 "Hermes" / "Nous Research"
$ grep -rl "Hermes\|Nous Research" ~/.hermes/hermes-agent/ui-tui/src/ 2>/dev/null
ui-tui/src/bootBanner.ts.before-catfish     # backup file
ui-tui/src/app/slash/commands/setup.ts      # ⚠ patch 没覆盖
ui-tui/src/app/useMainApp.ts                # ⚠ patch 没覆盖
ui-tui/src/theme.ts.before-catfish          # backup file
ui-tui/src/content/setup.ts                 # ⚠ patch 没覆盖
```

**结论 3 件事:**
1. ✅ install.sh **装过** (8 个 .before-catfish 备份)
2. ⚠️ **ui-tui 跑的是预编译 dist (app.js/banner.js)** — 即使 ts 源码 patch 了, 屏幕显示的还是 dist 里没 patch 过的旧 banner. 这是 5/4 鸿波看到 "Hermes" 字样的根因
3. ⚠️ **3 个文件 brand patch 没覆盖**: `app/slash/commands/setup.ts` / `app/useMainApp.ts` / `content/setup.ts`

### 9.2 demo 前不动 (5/4 拍板) 的理由

走 § 5 阶段 B 的 wrapper subprocess + Shell Hooks 一次解决:
- 改源码 + rebuild dist 是死路 (升级 hermes 0.12 后又要重做)
- wrapper subprocess 跟 hermes 源码完全解耦, 升级随便升

5/4 demo 倒计时 10 天 + BL-E14 PPT 吐槽 (3-5 天) + 彩排 ×2 + 录视频 — 时间紧, 不开新方向.

### 9.3 demo 现场缓兵之计 (不动代码)

如果 demo 时打开 hermes 终端给客户看, 用以下命令把"看不清" 解决, "Hermes" 字样的暴露面降到最低:

```bash
# 1. 切到对比度 OK 的 skin (5/4 鸿波验证 warm-lightmode 可用)
/skin warm-lightmode

# 可用列表 (5/4 hermes 0.10 实测):
# ares / charizard / daylight / default / mono / poseidon / sisyphus / slate / warm-lightmode

# 2. 不主动滚到 banner / Goodbye, 演示尽量在 Companion (Tauri 桌面 app), 不要 catfish CLI
```

⚠️ skin 选择**不会持久化** (hermes 0.10), 每次开新 session 要重输. 这是 § 5 阶段 B 要解决的之一: wrapper subprocess 加 `on_session_start` hook 自动 `/skin warm-lightmode`.

### 9.4 5/8 后启动议程时的实施清单 (走 § 5 阶段 B)

#### 阶段 B.1 (~0.5 天) wrapper subprocess + 默认 skin

- 改 `edge/branding/catfish` 入口脚本 (现 ~50 行 shell), 把 hermes stdout 经过 awk filter:
  - `Hermes Agent v0.10.0` → `Catfish v0.1.0 · rt: hermes-0.10.0`
  - `Hermes` (独立词) → `鲶鱼`
  - `Nous Research` → `鲶鱼平台`
  - `Welcome to Hermes Agent!` → `欢迎使用鲶鱼 · 直接说事, 不用客气.`
  - `Goodbye! ⚕` → `再见 🐟`
  - `⚕` → `🐟`
- 启动时 wrapper 第一帧自动发 `/skin warm-lightmode\n` 给 hermes stdin (持久化 skin 直到 hermes 0.11+ 加 config.skin)

#### 阶段 B.2 (~0.3 天) Shell Hooks (hermes 0.11+ 升级后)

- 注册 `on_session_start` 钩子打鲶鱼 banner (替代 wrapper 第一帧 hack)
- 注册 `pre_exit` 钩子打"再见 🐟"

#### 阶段 B.3 (~0.5 天) 兜底 source patch (从 468 行裁到 50-100 行)

- 改 `apply_brand_patch.py` 加规则覆盖 5/4 漏的 3 个文件 (`app/slash/commands/setup.ts` / `app/useMainApp.ts` / `content/setup.ts`)
- 但**不依赖** dist rebuild — wrapper subprocess 已经过滤了, source patch 仅作"二道防线"
- 把 dist rebuild 的依赖去掉 (改 install.sh 不再要求 npm run build)

#### 阶段 B.4 (~0.5 天) 测试 + 写自动化检查

- `tests/hermes_brand_check.sh` 启动 catfish, capture 头 50 行 stdout, grep 不含 "Hermes" / "Nous Research" / "⚕"
- 升级 hermes 后自动跑这个测试, 防 banner 字符串变化

**预算**: 阶段 B 合计 ~1.8 天, 5/15-5/17 完成.

---

最后更新: 2026-05-04 (周一夜) — Curator 接口分析 + 集成方案完整归档 + 9.x 真机诊断 (brand patch 装了但 dist 预编译 + 3 个文件漏 + warm-lightmode skin 验证可用), 等 5/8 后启动议程
