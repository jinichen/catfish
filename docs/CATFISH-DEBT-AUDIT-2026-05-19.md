# Catfish 减负 audit — Post-Hermes-Cutover

> **日期**: 2026-05-19 晚 (40+ 小时清 sprint 收尾时刻)
> **作者**: 鸿波 + Claude
> **目的**: 5/19 hermes cutover + Week 1-2 gateway 减债之后, 全栈还有哪些可清的债
> **使用方式**: 明天清醒头脑挑 1-2 项排进 Week 3-4 sprint, **不在本文件落地后立刻动手**
> **来源**: 基于实际 grep + 架构对照, 非空谈

## 一句话现状

5/19 之后 catfish 的本质变了 — 不再是"全栈 AI agent 框架", 而是"hermes 的中国企业发行版 + 边缘集成层". 老的"全栈"代码很多变成"重复造轮子".

减负原则 (CATFISH-POSITIONING-2026-05-19.md 第 3 条宪法):

> 任何新功能 / 新需求, 过三问:
> 1. 这件事 hermes 已经做了吗? → 是 → 用它的, 不要重复
> 2. 客户企业会因为这功能买单吗? → 否 → 砍掉, 资源转移
> 3. 这是 AI 能力还是企业能力? → AI 能力 → 借 hermes; 企业能力 → 我们做

本文件就是过这三问的产物.

## 已完成 / 已规划

| 项 | 状态 | LOC | Ref |
|---|---|---|---|
| Week 1: compound_intent + self_critique 删 | ✓ commit `f191753` | -1330 | GATEWAY-CLEANUP-WEEK1-DELETE-PLAN |
| Week 2 Step A-C: catfish-memory 写路径 + gateway env gate | ✓ commit + Step B `+419` plugin / Step C `+14` gate | +433 / 待删 -1515 | GATEWAY-CLEANUP-WEEK2-MIGRATION-PLAN |
| Week 2 Step D: 部署 + 24-72h 观察 | ⏳ 5/20 起 | n/a | STEP-D-DEPLOY |
| Week 2 Step E: 硬删 gateway 旧 module | ⏳ ≥ 10 天后 | -1515 | STEP-D 通过后 |

## 减负候选 — 按 ROI 排序

### 优先级 1 — gateway Week 3-4 剩余冗余

**范围**: gateway 还有几层 hermes cutover 后冗余的 prompt 注入 / 行为塑造模块.

| 模块 | 调研问题 | LOC 估 |
|---|---|---|
| `tool_retry_hint` | hermes agent loop max_iterations=90 兜底, 这层 hint 还有必要? | ~200 |
| `inject_session_goal` (/goal 命令) | Companion 是不是改用 hermes 原生 /goal 了? | ~100 |
| `proactive` (主动 starter) | hermes proactive plugin 存在? | ~300 |
| `a2a_journal_hook` | a2a 协议是不是已经 hermes 接管? | ~150 |

**总估**: 200-750 LOC
**风险**: 中 — 每个都是改 chat 主路径
**前置**: 跟 Week 1-2 同套路, prep test + delete plan + env gate + observe
**时机**: 本 sprint 后续 (Week 3-4)

### 优先级 2 — patches/ 投上游 PR

**范围**: hermes upstream PR 投 3 个补丁, 合并后 catfish 这边 patches/ 删.

- `0001-cors-tauri-origin`: Tauri 客户端 CORS 白名单
- `0002-auth-decouple-service-token`: service token + X-Catfish-User
- `0003-companion-proxy`: api server proxy 转发 Companion 请求

**收益**: 长期省 patch 维护成本 (hermes 每次更新都要 reapply); catfish 这边代码删 patch 文件 + apply_brand_patch.py 简化
**风险**: 低 (走 NousResearch PR 流程, 不是删本地代码)
**前置**: 投 PR 前先确认 NousResearch 是不是接受这种 generic 化变更
**时机**: Week 3-4 投, 合并周期可能 1-2 个月

### 优先级 3 — Companion 前端 A5 残留 (Task #25 已建)

**范围**: A5 全 route 走 hermes 之后, 灰度回退路径 + 直连 gateway 死代码.

| 冗余 | 位置 | LOC 估 |
|---|---|---|
| `fetchWithOAuth` legacy path | `lib/me.ts:163` + 测试 | 50-80 |
| `useHermes` flag 6 处分支 | chat.ts / Dashboard 2 card / 测试 | 80-120 |
| `lib/env.ts` 直连 gateway fallback | DEFAULT_GATEWAY_PORT 推断逻辑 | 20-40 |
| SkillsHubCard / McpRegistryCard 直连 gateway | 需先调研走 hermes proxy 与否 | TBD |

**总估**: 150-240 LOC
**风险**: 中 — prod-facing UI, 错了员工立即可见
**前置**: A5 实盘观察 ≥ 7 天零回退 (最早 5/26)
**时机**: ≥ 5/26

### 优先级 4 — catfish-tool-bridge 退役调研

**范围**: 这是 hermes 还没原生 MCP 时建的 stdio bridge. hermes 现在原生支持 MCP, bridge 存在意义存疑.

**调研问题**:
- catfish tool (catfish_run_skill / catfish_email_search / catfish_memory_compress 等) 能否直接用 hermes 原生 MCP server 跑?
- 性能 / 调用链 / debugability 三个维度比较 bridge vs 原生 MCP

**LOC 估**: 500-1500 (整个 plugin 退役)
**风险**: 高 — 直接断 catfish tool 调用链
**前置**: 实证 hermes MCP 等价能力, 至少 2 周双跑
**时机**: Q3 (不本 sprint)

### 优先级 5 — catfish-memory 5 源 audit

charter 那天你自己提过的问题: `session_meta / employee_journal / skills_catalog / feedback / skill_guard` 这 5 个 catfish 边缘源, 哪些 hermes 自带 memory 已经覆盖?

**初步判断**:
- 砍候选: `session_meta` (时间感, hermes 自带 session 元), `skill_guard` (静态铁律, 应该走 system prompt)
- 留: `employee_journal` (写路径 Week 2 刚做完), `feedback` (员工 thumbs, hermes 没等价), `skills_catalog` (catfish 独有)

**LOC 估**: 砍 2 个源 100-200 LOC
**风险**: 低
**时机**: Q3

### 优先级 6 — catfish-identity 死 flow 清理

A5 切完后 Companion → gateway 不再走 user JWT, catfish-identity 的某些 flow 可能死代码.

**调研问题**:
- 哪些 grant_type 还在用? (client_credentials 仍要, refresh_token 看 Companion 是否还会刷)
- catfish-web 管理后台还在用 OIDC, 不能整个删

**LOC 估**: 100-300
**风险**: 中
**时机**: 跟 Task #25 前端一起做 (≥ 5/26)

### 优先级 7 — catfish-skills 迁到 hermes skill 格式

charter 写明的"skills 整合", 不算减负算形态转换. 迁完 catfish-skills 目录可能整个去掉, 共享 hermes registry.

**LOC 估**: 看 catfish 现有 skill 数量
**风险**: 中 — 测试覆盖要重做
**前置**: hermes 那边 skill registry 进展 (跟 NousResearch 对齐)
**时机**: Q2-Q3

### 优先级 8 — 注释清理 / 文档归档

代码里几百条 `BL-FIX23 / BL-MM7 / BL-F12 ...` 老 marker, 大部分事件过期. 清理是可读性, 不减 LOC.

老 SPRINT-LOG / SOUL / POSITIONING.md 老版应该归档到 `docs/archive/`.

**时机**: Week 4 / 哪天闲了

## 不动清单

| 项 | 为什么不动 |
|---|---|
| `launchd wrapper.sh` | 刚做完 (BL-HERMES-LAUNCHD-ENV-LOAD), 工作良好 |
| `dev_users.yaml` | 测试基础, 改了影响 1000+ 测试 |
| `config.yaml` 主配置 | 生产依赖, RBAC / quota / catalog 全在这里 |
| `catfish-policy` plugin | 跟 charter 第 4 条护城河 (RBAC/审计) 强相关, 是核心而非债 |

## 跟 charter 对齐核对

按 5/19 charter 三问过滤上面 8 项:

| 项 | hermes 已做? | 客户买单? | AI/企业能力? | 结论 |
|---|---|---|---|---|
| gateway 剩余 prompt 注入 | 是 | 否 | AI | **删** |
| upstream PR | (赋能 hermes) | 间接 | AI | **PR** |
| Companion A5 残留 | 否 | 否 | 企业 | **删冗余** |
| catfish-tool-bridge | hermes MCP 替代 | 否 | AI | **退役 (调研后)** |
| catfish-memory 5 源 | 部分 | 部分 | 混合 | **audit 砍 2 源** |
| catfish-identity 死 flow | 否 | 否 | 企业 | **删冗余** |
| skills 迁移 | 是 | 是 | 混合 | **迁不删, 共享** |
| 注释 / 文档 | n/a | n/a | n/a | **清理** |

## 行动建议 (5/20 起手)

**今晚不动手** — 这份 audit 是导航图, 不是动手清单.

明天 (5/20) 跟我一起挑:
- A. 优先级 1 (gateway Week 3 剩余, 高 ROI 但风险中) — 这是本 sprint 自然延续
- B. 优先级 2 (patches PR) — 低风险但收益滞后
- C. 不挑减负, 转 backlog 别的事 (Task #14/#15 修复 / Companion UX 重做 / skills 迁移启动 / ...)

**强烈建议**: 至少先 Week 2 Step D 实盘观察通过 (24-72h), 再开新减负战线. 不要 cutover 没稳就追下一个 cutover.

---

*Ref*:
- *CATFISH-POSITIONING-2026-05-19.md (charter 三问)*
- *SPRINT-LOG-20260519.md (今天 40+ 小时清单)*
- *GATEWAY-CLEANUP-WEEK1-DELETE-PLAN.md*
- *GATEWAY-CLEANUP-WEEK2-MIGRATION-PLAN.md*
- *GATEWAY-CLEANUP-WEEK2-STEP-D-DEPLOY.md*
- *Task #25 BL-COMPANION-CLEANUP-POST-A5*
