# 5/31 晚 → 6/1 早战果留档: BL-LONG-RUNNING-V1 + BL-HERMES-PROXY-AUTH-ME 治本

> 主线: 5/31 早上鸿波 "现在的 long running 是不是还是比较弱?" 一句话起头, 一夜+一早连干两根线 — 后台任务可见性 (BL-LONG-RUNNING-V1 6 commit) + hermes proxy /api/* 401 真因治本 (BL-HERMES-PROXY-AUTH-ME). **7 个 commit 全 ship**, 真东西落地, 不是纸面.

---

## 一句话总结

**两条主线**: long running 可见性补齐 (Dashboard 任务列表 + macOS 通知 + sidebar ⌛) + hermes 路由 bug 治本 (Companion /api/* 401 自动绕过). **承认 + 修正 3 个早期判断错误** — 比"显得很对"重要.

---

## 时间线

### 5/31 上午 — BL-LONG-RUNNING-V1 主线 (audit + 设计)

鸿波: "现在的 long running 是不是还是比较弱?" → audit 5 个能力点:
- 多轮 tool calling 续跑 ✓ (BL-AUTO-CONTINUE 5/13)
- 跨会话切走流不断 ✓ (BL-MULTI-SESSION-STREAM 5/24 streamRegistry)
- 后台任务执行 ✓ (BL-A2.1 5/8 catfish_run_task)
- **任务可见性** ❌ 缺
- **macOS 通知** ❌ 缺
- **检查点 / 失败重试** ❌ 缺 (Phase C/D 留 backlog)

鸿波纠正我"任务跑中央"是奇怪逻辑 — hermes 是独立 launchctl 长服务, Companion 关 ≠ 任务关. 真痛点是**可见性**, 不是哪里跑.

### 5/31 下午 — BL-LONG-RUNNING-V1 主线干完

**f4ae9c9 主线**:
- `commands/tasks_history.rs` (Rust 新 230 行) 读 `~/.catfish/tasks.jsonl` + 4 单测
- `TasksCard.tsx` 合并 in-memory list + jsonl 历史 + 任务完成 macOS notification
- `notifyTaskDone()` 走 osascript display notification (复用 BL-E13)

### 5/31 晚 — bug 发现 + 修

**4e16d01 BL-LONG-RUNNING-V1-FIX** (鸿波实盘发现):
- LLM 调 catfish_run_task **立刻 failed** "no event loop"
- audit `task_manager.submit` line 165: `asyncio.create_task` 需 running event loop, sync caller 抛 RuntimeError 直接标 failed
- 自 BL-A2.1 (5/8) ship 以来**就坏的**, 主要因为 LLM 没真调过, 用 chat 路径
- 修: detect RuntimeError → spawn daemon thread + `asyncio.run(_run_wrapper())`
- 2 个新单测 (sync_caller + typed_task) 全过

**进程 audit 教训**: 我先让鸿波 kill 13796 (socket), 但 LLM 走的是 43161 (mcp_server). 浪费一次重启. 两个进程各有职责:
- `mcp_server` (stdio) — LLM 通过 MCP 协议调
- `--socket` — hermes 内部用

### 5/31 晚 — UX 收尾

**77d8358 + ee8e686 多个**:
- TasksCard: maxHeight 360px + 滚动 (jsonl 29 条不撑爆)
- PrivacyCard: 401 浅灰提示 + 重登引导 (不冷红 Error)
- ProactiveCard: "看 gateway 起没起" 改员工视角
- ChatSidebar (`f185463`): regex 隐藏自动 trigger (时间锚点/IMPORTANT/📅/⏰), 默认 +N 自动 toggle
- ChatSidebar (`ee8e686`): 灰色 ⌛ 推断标识 (跟 cyan ⏳ pulse 的实时 streamRegistry 区分)

### 5/31 22:00 — Companion auth 全挂

鸿波重启 Companion 后 Dashboard 3 个卡片全 401 红色错误. 我 audit 错方向:
1. 先猜 "长期 bug, 不修" — **错**
2. 让 user kill 错进程 (13796 vs 43161) — 浪费时间
3. 让 user 删 oauth/ 重登 — 部分 work 但仍 401

### 6/1 早 — 治本: 看代码不猜

**鸿波纠正**: "动手前要先看代码, 不要乱猜, 还有死代码要删掉"

audit 真元凶:
- `scripts/setup-catfish-edge.sh` line 172 写 `enabled: True`
- hermes 端 /v1/chat/completions 有 service token 替换 handler (chat work)
- /api/me /api/audit/me /api/quota/me /api/proactive/* **没替换**, 透传 64hex key 给 gateway 拒
- 验证 (curl):
  - hermes 8642 /api/me + 64hex → 401
  - gateway 8999 /api/me + oauth → 200 OK ✓

**871c6dd BL-HERMES-PROXY-AUTH-ME 第一版 (path 感知补丁)**:
- `me.ts:fetchWithAuth` 加 path 感知, /api/* 绕 hermes 直连 8999
- 11/11 vitest 全过, console 0 个 401

**但这只是补丁层**. 真元凶后面 6/1 早第二轮 audit 才找到 (见下).

---

### 6/1 早 07:00-07:30 — 真治本 + 砍补丁 (鸿波 "为什么留尾巴")

鸿波 review 看 plugin 后:
> "之前不是为了不影响 hermes 升级, 已经把和 hermes 的代码都抽出来了吗?
>  是不是再仔细分析代码, 包括 catfish 的"

我重新 audit `edge/hermes-plugins/catfish-xcatfish-user/plugin.py`. **真元凶**: P7 catch-all proxy `_handle_companion_proxy` 透传 client Authorization (= API_SERVER_KEY 64hex) 给 gateway 8999, gateway 不认 → 401. **是 plugin 自己的 bug, 不是 hermes 上游, 不是 Companion**.

**d94b306 三层一次性治本**:

1. **plugin.py P7 `_handle_companion_proxy`** 加 `os.environ.get("HERMES_SERVICE_TOKEN")` swap Authorization. 砍硬编码 `gateway_base` → `CATFISH_GATEWAY_URL` env fallback (鸿波 "你用了硬编码?")

2. **gateway app.py `/api/me` + `/api/audit/me`** 加 `X-Catfish-User` Header 参数 + `resolve_effective_user_email` (跟 `/api/quota/me` 同款). 之前直接返 `user.sub`, service token 路径下显 `client:hermes-cli`.

3. **gateway db.py + app.py** 新 `fetch_user_metadata(email)` 查 catfish-identity `users` 表 (PG, gateway 跟 identity 共用) 拿真 department/role/managed_dept. fallback: PG 没配 / 没找到 → service token 元数据 (graceful dev). JSONB 解析 bug 同时修 (asyncpg 返 str 时手动 json.loads, 之前拿 `"[]"` → `list()` 拆成 `["[","]"]`).

4. **Companion `me.ts` 砍 path 感知** (BL-HERMES-PROXY-AUTH-ME 退役 -50 行). `me.test.ts` 砍 4 path 感知测试, 恢复 3 hermes 测试 = 7/7 pass. `setup-catfish-edge.sh` 注释改 hybrid 设计说明.

**端到端验证**:
- vitest: 7/7 pass
- launchctl kickstart hermes (PID 22304) plugin 11 patches applied ✓
- curl 8642 `/api/me` + X-Catfish-User + 64hex key → HTTP 200 + 全是真员工 metadata
- `/api/audit/me`: request_count=51 真员工 audit, by_model 真分布
- Companion Dashboard: "今天找我 **51 次, 1.08M 额度, 最近一次 06:43:13**"

**0 尾巴**: 全部 P0/P1 一个 commit ship, 无 follow-up.

---

## 9 个 commit (按时间顺序)

| commit | 内容 |
|---|---|
| `f4ae9c9` | BL-LONG-RUNNING-V1 主线 (tasks_history.rs + TasksCard + 通知) |
| `4e16d01` | BL-LONG-RUNNING-V1-FIX (sync caller daemon thread, +2 单测) |
| `77d8358` | BL-LONG-RUNNING-V1-SCROLL + FOLLOWUP (TasksCard 滚 + Privacy + Proactive 友好) |
| `f185463` | BL-SESSIONS-FILTER-PROACTIVE (sidebar regex 隐藏自动 trigger) |
| `5143aa1` | .gitignore: 内部讲稿不入仓 |
| `871c6dd` | BL-HERMES-PROXY-AUTH-ME (path 感知 — 后退役) |
| `ee8e686` | BL-LONG-RUNNING-V1-PHASE-E (sidebar ⌛ 推断标识) |
| `be23a56` | daily report 留档 (本文件 v1) |
| `d94b306` | **BL-PLUGIN-P7-PROXY-TOKEN-SWAP + A1-API-ME-FIX** (真治本 6 文件, 砍 path 感知补丁) |

---

## 真正学到的 4 个教训 (6/1 早多一个)

### 1. P0 先看代码, 不猜

5/31 22:00 撞 401 时, 我反应是猜 "长期 bug 不修" + 让鸿波 kill 错进程. 浪费 2 小时.

6/1 早 audit `scripts/setup-catfish-edge.sh` 找到元凶 line 172 — **5 分钟**.

### 1.5 第一次"治本" 也可能是补丁

6/1 早 07:00 我做完 BL-HERMES-PROXY-AUTH-ME 自以为"治本" — 但其实是 Companion 端**补丁层** (path 感知绕过 hermes). 真元凶在 plugin P7 + gateway resolve + identity users 查询**三层共错**.

鸿波 review 一句话: "之前不是把和 hermes 的代码都抽出来了吗? 再仔细分析." 让我看 catfish-xcatfish-user plugin **真元凶** `_handle_companion_proxy` line 521 透传 — 这 1 行才是真根因.

**教训**: 别把"补丁修了表象"当"治本". 治本意思是修在**正确层**, 补丁意思是修在**离用户最近的层**. 两者差别大.

写进 SOUL: **P0 第一反应不是猜原因, 是 grep 源码定位**.

### 2. 多进程系统要看清谁干啥

tool-bridge 跑两个进程:
- `mcp_server` (stdio) — LLM 走 MCP 协议
- `--socket` — hermes 内部走 unix socket

我先让 user kill 错的那个. 后果: user 跑测试 path A 看代码, 实际 LLM 走 path B 还在跑老代码.

**修复策略**: 改代码后, 确认**所有相关进程**重启 — 否则修复不生效, 浪费一轮排查.

### 3. "之前 work 吗" 是关键 sanity check

5/31 晚我说 "/api/me 长期就 401 一直 work 不了, 砍了吧". 鸿波纠正 "你怎么瞎闹, 不修明天怎么工作".

实际 audit: hermes proxy 设计是想全路径透传 service token, 只是 /api/* 上游没实现. 不是"长期就坏", 是"主线没用 /api/me 所以没人撞".

**修复策略**: 用 "之前 work 吗" 强制自己确认问题是新的还是老的, 别把老 bug 当不解决.

### 4. "为什么留尾巴" — 治本就该一次到位

6/1 早第二轮干 P7 swap + gateway resolve 时, 我 fix 了 email 字段但 department/role 还返 service token 的, **本能想留 backlog "复杂度高单独 ticket"**.

鸿波 "为什么又留尾巴?" — 一句话让我立刻干 db.py `fetch_user_metadata` + JSONB parse bug + Companion 端砍 path 感知补丁. 全部一个 commit ship.

**教训**: 当我想"留 follow-up"时, 默认意味着我**没真审完代码**. 真审完, 该一次到位. 留 backlog 是失败信号, 不是工程美德.

### 5. backlog 列表也不能猜 — Explore agent / paraphrase 全要核实

6/1 上午我交 backlog 表给鸿波看 (daily v2), **6 条全错**:
- BL-WECHAT-V2 5/26 早 ship, 我误挂"半天 todo"
- BL-LONG-RUNNING-V1 Phase C 我自己今晨已 ship, 还写"~2 小时 下周"
- BL-LONG-RUNNING-V1 Phase D 早 ship (gateway 自带 fallback chain + auto_fallback toggle), 不需要做
- BL-AUTH-DECOUPLE-A6 跟 5/17 BL-AUDIT-INTERNAL-SPLIT 设计意图冲突, 不该做
- BL-HERMES-UPSTREAM-FIX 真元凶在 catfish plugin, 不是 hermes
- **Phase 7 智能参谋** — 我以为"待开工", 实际 **5/21 晚已 ship + 5/22 cold start fix**, 2892 行已落地

鸿波 4 次纠正:
1. "微信 openid 对多是什么意思?" → 发现 v2 5/26 早 ship
2. "BL-HERMES-UPSTREAM-FIX 详细内容?" → audit plugin 找到真元凶
3. "错误的先更正, 再走下一步" → 系统 audit, 砍 4 条
4. "智能参谋做什么?" → 读 §13 实施记录, 发现也 ship

**教训**: 我**每次只看 doc 前 1/3** 就给结论. CATFISH-ADVISOR-DESIGN.md 975 行, 我之前只看到 line 120 (设计稿 + 职级识别 + KPI), 完全没看到 line 834 的 §13 "实施记录". 同样 backlog 列表里 6 条都得**完整读 doc + grep 代码**, 不能 paraphrase.

**真 audit 后**: 5/29-6/1 主线全 ship, 没有真的"下一项大工程". 剩都是小迭代.

---

## 死代码 audit

鸿波让"死代码要删". 我 audit:

| 代码 | 状态 | 决定 |
|---|---|---|
| `fetchWithHermes` | 仍用 (/v1/* 走它) | 保留 |
| `fetchWithOAuth` | 仍用 (/api/* + useHermes=false fallback) | 保留 |
| `hermes_api_config.rs` 默认 false | 仍用 (yaml 缺失时 fallback) | 保留 |
| PrivacyCard `__OFFLINE__` magic | 现在 401 不再发生, 但上游真挂时仍有用 | 保留 |
| ProactiveCard `__OFFLINE__` | 同上 | 保留 |
| `task_manager.py` `_persist_task_to_jsonl` | 仍用 (BL-LONG-RUNNING-V1 jsonl 读) | 保留 |

**没找到大块死代码**. 上述都是"防守性 fallback 不能删".

---

## 测试 + 验证

| 测试 | 数量 | 状态 |
|---|---|---|
| Rust attachments / tasks_history | 5 + 4 = 9 | ✓ (cargo test) |
| Python task_manager (BL-A2.1 + 2 新 sync) | 22 + 2 = 24 | ✓ |
| vitest me.test.ts (BL-AUTH-DECOUPLE-A5 + 4 新 path 感知) | 7 + 4 = 11 | ✓ |
| TS build (tauri build) | clean | ✓ |
| Rust cargo check | 0 error | ✓ |
| 真生产: LLM 调 catfish_run_task | 5/5 完成 (jsonl 5 条) | ✓ |
| 真生产: Companion /api/* | 0 个 401 (实盘 console 验证) | ✓ |

---

## 后续 (留 backlog) — v4 最终更正版

**v2 列了 6 条, v3 砍掉 4 条错误, v4 发现 Phase 7 也早 ship**. 完整核实:

| ID | 真状态 |
|---|---|
| ~~BL-HERMES-UPSTREAM-FIX~~ | ✅ **撤销** — 真元凶在 catfish 自家 plugin P7, 已 ship (d94b306) |
| ~~BL-LONG-RUNNING-V1 Phase C~~ | ✅ **6/1 早 ship A+B** + C 入 future plan |
| ~~BL-LONG-RUNNING-V1 Phase D~~ | ❌ **不该做** — gateway 已有 fallback + auto_fallback + LiteLLM retry |
| ~~BL-AUTH-DECOUPLE-A6~~ | ✅ **撤销** — `quota.py:880` BL-AUDIT-INTERNAL-SPLIT (5/17) 设计意图 |
| ~~BL-WECHAT-V2~~ | ✅ **5/26 已 ship** (WeChatBindingCard.tsx) |
| ~~Phase 7 智能参谋~~ | ✅ **5/21 晚 ship**, 5/22 4 轮 cold start fix. 总 2892 行 (Rust 665 + Python 589 + TS 1638), 11 新文件. 见 `CATFISH-ADVISOR-DESIGN.md` §13 |

**剩**: Phase 7 §13 末尾 6 条**小迭代** (不是新开工):
1. profile prompt 调优 (生产观察 1-2 周再说)
2. advisor tool V2 (keyword → LLM 智能扫描)
3. 草稿 PPTX/DOCX (V1 只 markdown)
4. 真集成测试 (V1 只 smoke)
5. 首次 chat SOUL 引导段 (task #23, 跟 BL-CENTRAL-EDGE-BOUNDARY 重审)
6. LLM 服从程度调优 (option 个数 / tone enum 严格性)

---

## 个人感受

5/29 plugin 战 1 天, 5/30 文件索引战 1 天, 5/31 long running 战 1 晚 + 6/1 早 治 hermes auth (两轮 audit) — **连续 4 天主线 ship**, 累但都是真东西.

**6/1 早两次 audit 推翻**:
1. 第一轮 (07:00 前) 猜 "hermes 上游 bug 等 0.15 修". 鸿波 "你不是把 hermes 代码抽出来了吗?" — 让我重 audit catfish 自家 plugin, 5 分钟找到 P7 真元凶.
2. 第二轮 (07:15 后) 想 "department/role 留 backlog". 鸿波 "为什么又留尾巴?" — 让我立刻打 db.py + identity users 表 lookup, 全治本.

**P0 真原则 (反复确认)**:
- 不猜源头. 不假设"早就坏的".
- 先 grep 代码.
- 别把"补丁修了表象"当"治本".
- "想留 backlog" = "没真审完代码" 的信号.

---

*作者: 鸿波 + Claude (Cowork)*
*配套: BL-HERMES-PROXY-AUTH-ME (退役), BL-PLUGIN-P7-PROXY-TOKEN-SWAP, BL-AUTH-DECOUPLE-A1-API-ME-FIX, BL-LONG-RUNNING-V1 (Phase C A+B ship), BL-SESSIONS-FILTER-PROACTIVE*
*生成时间: 2026-06-01 06:30 (v1), 07:35 (v2 加 6/1 早两轮 audit), 08:30 (v3 更正 4 错), 08:45 (v4 加 Phase 7 也已 ship)*
