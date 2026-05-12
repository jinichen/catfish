# Hermes 0.13 对齐 — catfish 借鉴清单 + 升级路线

**来源**: 2026-05-12 凌晨对比 Hermes Agent v0.13.0 "The Tenacity Release" (上游 5/7 ship) 后给鸿波的建议. 完整原文在 transcript `323b9217-3f73-4437-a7a9-cf180172675c.jsonl` (line 958/961/984/1070).

主题: Hermes 0.13 主线 **"agent finishes what it starts"** — 跟 5/11 末 BL-FIX23-L7/L8 / BL-FIX46 / BL-MM9-FREEZE 我们做的 turn 控制完美撞车. catfish 跟 Hermes 同源但走企业向, **短期借鉴设计, 中长期升级 hermes 本体**.

---

## 1. 直接撞车 (Hermes 怎么做的, 我们要不要改)

| 我们做的 | Hermes 0.13 做的 | 建议 / catfish 状态 |
|---|---|---|
| BL-FIX45 (UX 层错误恢复) | **Atomic session persistence + 重启 auto-resume + Preserve pending update prompts across restarts** (基建层) | gateway 应补 "atomic 写 + 重启续 session". 现在 gateway 崩一次员工对话就丢. **5/18 升级 sprint Day 3-4** 一并做 |
| LLM gateway fallback chain | **ProviderProfile ABC + `plugins/model-providers/` + `transform_llm_output` plugin hook** | 把 fallback chain 重构成 ABC + plugin 目录. `transform_llm_output` hook 正对应我们 audit/脱敏/quota 标签注入的三个横切关注 — 命名直接抄齐 |
| BL-MM Skills Hub | **`hermes curator archive / prune / list-archived` + frontmatter slug 保护 bundled/hub skills 不被覆盖** | 教学一次→凝固 skill 一定会遇重名/旧版本残留. 抄 archive/prune 三件套 |
| BL-Q3-WEBSKILL (browser tools) | **Browser 默认拦 cloud-metadata SSRF (169.254.169.254) + `allow explicit CDP override` + `--no-sandbox` AppArmor 限制** | ✅ **已 ship** BL-HERMES013-2 (Playwright SSRF deny ~80 行) |
| catfish audit | **Default-on secret redaction + skill 凝固时扫 prompt-injection** | ✅ **已 ship** BL-HERMES013-1 (audit credential scrub ~10 行); skill 凝固 prompt-injection 扫**未做**, BL-MM9-FREEZE-FU 待办 |

## 2. 短期借鉴 (5/14 demo 前, 半天-2 天能做)

| 项 | 工作量 | 状态 |
|---|---|---|
| **`/goal` Ralph loop** — agent 锁定目标多轮不偏 | 2 天 | ✅ **已 ship** BL-HERMES013-3 (`session_goals.py` ~250 行 + SOUL.md 铁律段) |
| **状态栏 context compression counter + 启动 banner 折叠** (Companion UI) | 半天 | ⬜ 未做, 5/13 后 |
| **`transform_llm_output` 插件钩子** — 接入即统一 audit/quota/脱敏 三条横切 | 半天 | ⬜ 未做, **5/18 升级 sprint Day 3** 优先 |
| **`allowed_channels / allowed_chats / allowed_rooms` allowlist 命名** — 企业按部门限制副手, 配置 schema 跟生态一致 | 1 天 | ⬜ 未做, 跟 BL-RBAC P2 合并 |

## 3. Q3/Q4 中长期借鉴

- **Multi-Agent Kanban** (durable multi-profile + heartbeat + reclaim + zombie detection + hallucination gate) — 跟 catfish 中央 hub 分发任务给多员工副手完美对位, **Q3 加 "catfish kanban"** 模块. 0.13 主功能, 客户期望以后会看到.
- **Checkpoints v2** (single-store + real pruning + 磁盘 guardrail) — skill session-renewal 需要 checkpoint, 抄三要素别自己造.
- **SSE MCP transport + OAuth forwarding + stale-pipe retries + keepalive** — MCP registry 支持长连 OAuth 透传.
- **`no_agent` cron watchdog 模式** — "纯脚本拉数据, 非空 verbatim 投递, 空 stdout 静默" — 企业大量"定时拉 + 异常才告警"场景直接对位.
- **Post-write delta lint** — write_file + patch 后自动跑 py/json/yaml/toml 语法检查. skill 自动写代码场景必备.
- **`X-Hermes-Session-Key` header** — memory scope 用 header 不用 cookie, 多副手共存设计干净.
- **ACP `/steer` + `/queue`** — 不打断的 in-flight 指令注入 + 排队. 比硬中断好.

## 4. 明确不做 (catfish 定位冲突)

- **20 个 messaging platforms** (Google Chat / Telegram / Discord / WhatsApp / Matrix / QQBot 等) — Hermes 走 to-C / 社区, catfish 走企业内. **只挑飞书 / 企业微信 / 邮件**.
- Voice cloning / video_analyze / Achievements 分享卡 — 跟企业副手定位无关.
- 100 CLI tips / 7 个 i18n locale / `default-large` 18px 主题 — 我们 Tauri 桌面端按客户需求选 zh-CN + en, 不全铺.
- SearXNG / OpenRouter 缓存 / shop-app 等 optional skill — 走 Skills Hub 让客户自装, **主仓不内置**.
- ACP adapter (VS Code / Zed / JetBrains 嵌入) — catfish Companion 是独立桌面端, 不进 IDE.

## 5. 最该立刻动手的 3 件 (按 ROI 排序, 早上拍板)

| # | 项 | 工作量 | 当前状态 |
|---|---|---|---|
| 1 | **gateway atomic session persistence + 重启 auto-resume** | 1 天 | ⬜ **未做**, demo 前是大风险点 (gateway 崩一次员工对话丢). 5/13 优先评估 |
| 2 | **`/goal` Ralph loop** | 2 天 | ✅ **已 ship** BL-HERMES013-3 |
| 3 | **`transform_llm_output` plugin hook 重构** | 半天 | ⬜ 未做, 5/18 升级 sprint 一并做 |

## 6. 升不升 hermes 本体的判断

**短期不升** (5/12-5/14 demo 前 freeze), 借鉴设计自实现.

| 节点 | 该不该升 |
|---|---|
| 5/11 (拍板时) | 不升, freeze |
| 5/12-5/14 | 不升, demo 准备 |
| 5/15-5/17 | 看 0.13.1 / 0.13.2 patch release 出没出, 等 1-2 个 patch 再升 |
| 5/18-5/22 | **升级 sprint** (跟 catfish-web 5/15+ 那波并行) |
| 5/22+ | 0.13 默认基线, 借鉴 multi-agent kanban / checkpoints v2 |

**升级风险表**:

| 风险 | 程度 |
|---|---|
| 0.13 release 5/7 → 现在才 4 天, 早期 bug 暴露窗口 | 高 |
| catfish-policy plugin 4 条 matcher 兼容性 (shell/file_read/network/tool_name 跟 0.13 新 plugin surface) | 中 |
| Default-on secret redaction 跟 catfish 自己 audit 脱敏**撞** | 中 |
| Hermes 重写 session persistence (Checkpoints v2) 跟 catfish sessions 表存储模型对接 | 中 |
| catfish-gateway custom endpoint 协议改动 | 低 |
| Companion 桌面端 (不依赖 hermes 版本) | 0 |

**收益对比 — 不升 hermes 也能拿到 90% 设计灵感**:

| Hermes 0.13 功能 | catfish 自己能不能做? |
|---|---|
| `/goal` Ralph loop | ✅ 已 ship BL-HERMES013-3 |
| `transform_llm_output` plugin hook | 能, gateway 自己写 ABC hook 重构, 半天就完事 |
| Atomic session persistence | 能, sessions 是自己 PG 表, 加 WAL 模式就够 |
| Cloud-metadata SSRF deny | ✅ 已 ship BL-HERMES013-2 |
| Default-on secret redaction | ✅ 已 ship BL-HERMES013-1 |
| Allowlist 配置 schema | 能, BL-RBAC 加 allowed_* 字段 |

真要升级才能用的: **Multi-agent kanban (Q3) / Checkpoints v2 / SSE MCP transport**, 都不是 5/14 demo 必备.

## 7. BL-HERMES-UPGRADE-013 升级 sprint 计划 (拟 5/18-5/22)

- **Day 1** — 兼容性验证 (装 0.13 独立 venv, catfish-policy 11 单测, custom endpoint 测连通, skill 系统路径)
- **Day 2** — 撞车点处理 (default-on redaction × catfish audit 合并 / 关一边, catfish-policy 接新 plugin surface, frontmatter slug 保护抄进 Skills Hub)
- **Day 3-4** — 借鉴改造 (`transform_llm_output` ABC hook 重构 audit/quota/脱敏, sessions atomic write + auto-resume, Playwright deny cloud-metadata 跟 0.13 内置版对齐, audit 默认开脱敏)
- **Day 5** — 集成测试 + 文档

跑前 prereq: `docs/HERMES-UPGRADE-PHASE-C-RUNBOOK.md` 的 brand patch 幂等流程 (11 step fixture 已验过 0.13 升级覆盖路径, 见 CHANGELOG 5/7 段).

## 8. 5/12 早上拍板已 ship 的 3 件 (BL-HERMES013-borrow 系列)

push 脚本: `bash scripts/sync-bl-hermes013-borrow.sh`

### [BL-HERMES013-1] Audit credential scrub (~10 行)

- `central/llm-gateway/src/catfish_gateway/prompt_security.py:107` — 加 `scrub_credentials_in_text()`
- `central/llm-gateway/src/catfish_gateway/metrics.py:209` — `log_request_metadata` 写 error 字段前 scrub

### [BL-HERMES013-2] Playwright SSRF deny (~80 行)

- `edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py:2488-2586` — 加 `_check_ssrf_safe(url)`
- 拦: `169.254.169.254` (AWS IMDS), `metadata.google.internal` (GCP), `100.100.100.200` (AliCloud), `169.254.170.2` (ECS task), `fd00:ec2::254` (IPv6 IMDS), `169.254.0.0/16` 段, `fe80::/10` 段
- 不拦: `10.x` / `192.168` / `172.16` / `127.0.0.1` / `localhost`
- 矩阵: 7 deny + 8 allow + 4 边界 全过

### [BL-HERMES013-3] `/goal` Ralph loop (~250 行)

- 新模块 `central/llm-gateway/src/catfish_gateway/session_goals.py` — read/write/clear/detect_goal_command/inject_session_goal
- 命令: `/goal xxx` 设, `/goal` 查, `/goal clear` / `/goal 清除` / `/goal off` 清
- `app.py` chat_completions 早期拦截命令 → `_fake_sse_response` 返假 SSE (不调 LLM, 不计 quota)
- inject pipeline 加 `inject_session_goal` (在 inject_feedback 之后, system 末尾)
- `edge/identity/SOUL.md:450` 加 "★ /goal 锁定目标铁律" 段

## 9. 反思

"agent 一气呵成不卡在中途" 是 LLM agent 2026 上半年共同议题. catfish 跟 Hermes 同源但走企业向, 在 "stop/retry 控制" 这块用 SOUL + Jaccard + task_complete 反判, 比 Hermes 偏基建的 `Checkpoints v2 / atomic persistence` 路线**更轻量**, 但**也更容易丢**. 短期我们的轻量方案先扛 demo, 中期应该抄 Hermes 基建路线把 turn 控制做扎实.

**客户不关心 "我们升到 0.13", 客户关心 "副手能不能帮我做事"**. demo 主轴还是 BL-Q3-FACT + BL-Q3-WEBSKILL + skill 教学故事, hermes 版本号不进 talking points.

---

**最后修改**: 2026-05-12 (鸿波回查时补落档, 早上原文拍板未落 docs)
**关联**: `CHANGELOG.md` 5/11 段 BL-HERMES013-borrow, `docs/HERMES-UPGRADE.md`, `docs/HERMES-UPGRADE-PHASE-C-RUNBOOK.md`
