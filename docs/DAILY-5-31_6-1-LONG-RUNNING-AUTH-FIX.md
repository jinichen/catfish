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

**871c6dd BL-HERMES-PROXY-AUTH-ME 治本**:
- `me.ts:fetchWithAuth` 加 path 感知:
  - `isApiPath()`: URL.pathname.startsWith("/api/")
  - `rewriteToGateway()`: backendUrl (hermes 8642) → gatewayUrl (8999)
  - /api/* → fetchWithOAuth(rewrite) — 绕 hermes
  - /v1/* → fetchWithHermes() — 保 BL-A5 设计
- `setup-catfish-edge.sh` 加详细注释 (enabled=true 保留, path 感知补救)
- **11/11 vitest 全过** (3 hermes /v1/* + 4 /api/* 新 + 2 灰度 + 2 切换)

验证: Companion 重启 console **0 个 401**, /api/me /api/audit /api/quota /api/proactive 全 200 OK, chat 仍走 hermes service token.

---

## 7 个 commit (按时间顺序)

| commit | 内容 |
|---|---|
| `f4ae9c9` | BL-LONG-RUNNING-V1 主线 (tasks_history.rs + TasksCard + 通知) |
| `4e16d01` | BL-LONG-RUNNING-V1-FIX (sync caller daemon thread, +2 单测) |
| `77d8358` | BL-LONG-RUNNING-V1-SCROLL + FOLLOWUP (TasksCard 滚 + Privacy + Proactive 友好) |
| `f185463` | BL-SESSIONS-FILTER-PROACTIVE (sidebar regex 隐藏自动 trigger) |
| `5143aa1` | .gitignore: 内部讲稿不入仓 |
| `871c6dd` | **BL-HERMES-PROXY-AUTH-ME** (path 感知治本, 11 测试) |
| `ee8e686` | BL-LONG-RUNNING-V1-PHASE-E (sidebar ⌛ 推断标识) |

---

## 真正学到的 3 个教训

### 1. P0 先看代码, 不猜

5/31 22:00 撞 401 时, 我反应是猜 "长期 bug 不修" + 让鸿波 kill 错进程. 浪费 2 小时.

6/1 早 audit `scripts/setup-catfish-edge.sh` 找到元凶 line 172 — **5 分钟**.

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

## 后续 (留 backlog)

| ID | 工程量 | 价值 | 何时做 |
|---|---|---|---|
| **BL-HERMES-UPSTREAM-FIX** | 等 hermes 0.15 | 治根 (path 感知可砍) | 上游升级时 |
| **BL-LONG-RUNNING-V1 Phase C 检查点** | ~2 小时 | 任务中断续接 | 下周 |
| **BL-LONG-RUNNING-V1 Phase D 失败重试** | ~1 小时 | model fallback / 指数退避 | 下周 |
| **BL-AUTH-DECOUPLE-A6 audit** | ~1 小时 | gateway 内部 self-call 安全 | 本周 |
| **Phase 7 智能参谋** | 3-5 天 | 质变 KPI | 等鸿波 review 设计稿 |
| **BL-WECHAT-V2** | 半天 | 多用户 openid → email | 微信用户增长前 |

---

## 个人感受

5/29 plugin 战 1 天, 5/30 文件索引战 1 天, 5/31 long running 战 1 晚 + 6/1 早 治 hermes auth — **连续 4 天主线 ship**, 累但都是真东西.

**最有收获的是 6/1 早那次 audit**. 5/31 晚我猜 "长期 bug", 鸿波两次纠正 + 一次火 ("不修明天怎么工作"). 我才老实看代码 — `scripts/setup-catfish-edge.sh` line 172 元凶 5 分钟定位.

**P0 真原则**: 不猜源头. 不假设"早就坏的". 先 grep 代码.

---

*作者: 鸿波 + Claude (Cowork)*
*配套: BL-HERMES-PROXY-AUTH-ME, BL-LONG-RUNNING-V1, BL-SESSIONS-FILTER-PROACTIVE*
*生成时间: 2026-06-01 06:30*
