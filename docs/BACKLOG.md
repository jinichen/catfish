# 鲶鱼 · 全量 Backlog

> ⚠️ **看进度 / 下一步去 [`docs/FEATURE-TRACKS.md`](FEATURE-TRACKS.md)** (2026-05-03 加).
> 本 BACKLOG.md 留作工程颗粒度 (BL-ID 编号) 历史归档, 不再加新 section.
> 新任务直接进 FEATURE-TRACKS 对应 track 的 ⬜ 列表.
>
> ---
>
> **版本**: v2, 2026-04-30 升级 (v1 = 2026-04-27 创建)
> **维护人**: 鸿波
> **跟 Task Tracker 的关系**: Task tracker (Cowork tasks) 只装 sprint 内 (~1-2 周内做) 的事;
> **本 backlog 装全量** — 战略 / 商业 / 工程 P1-P3 / 运营 / 文档 / 法律 / 多模态扩展 全部.
>
> **维护规则**:
> 1. 每周一上午 review 一次, 决定下周提哪些进 task tracker
> 2. 完成的不删, 改 status = ✅ 留作历史 (审计 / 软著申报用)
> 3. 新想法直接加, 不需要立刻拆细 — 按 section 归类即可
> 4. 状态: ⬜ 未开始 / 🔵 进行中 / ✅ 已完成 / 🚧 阻塞 / ❄️ 暂不做 / 💀 已废弃

---

## v2 升级说明 (2026-04-30)

v1 写于 4-27, 之后 3 天 (4-28 / 4-29 / 4-30) ship 了 23+ 项, 但没回写 — backlog 漂移. v2 修这事:

1. **回写已完成项**: BL-X1~X6 阻塞重新 review; 原 backlog 里被 ship 的项改 ⬜ → ✅ + 标 commit 日期
2. **新增 §K · 4-28~30 三天 ship 完成项快照**: 23+ 项不在 v1 backlog 里的新功能 (跨 session 上下文 / leadership-briefing 4 段 / weekly-report / 双 backend 错别字 / SSO 全链路 / 演讲稿 etc.)
3. **新增 §L · 4-30 当前缺口 (新发现的待办)**: skill 全生命周期残缺 / journal 向量召回 / IDP UserStore PG 化 / weekly P2 完整版 / 真机彩排 / hermes venv 总结脚本路径 / fonts 二进制移出 git 等
4. **总览统计**更新: ~121 项 → ~150 项, 已 ship 30+ 项

后续每周一 review 时**必须**回写, 严禁再漂.

---

## 5/14 - 6 月 sprint plan (5/13 真收尾 23:50 修订 — ACP /steer + Kanban scope 1 已提前 ship)

> **背景**: 5/13 一日 ship 6 件大事 — hermes 0.13 升级 + ACP /queue + ACP /steer + macOS Reminders + Multi-Agent Kanban scope 1 + BACKLOG 修订. 鸿波当晚 4 次推翻我"图省事" 判断 (升级估时 / i18n / hermes B 方案 / ACP 砍掉) + 1 次推 reality check 改方案 (Kanban scope 全功能 → scope 1 单员工本地). 副线 5/15-5/19 大幅腾出空间, 主线 RBAC sprint 可前推.
>
> **修订原则**: 一天最多 2 件中等并行, 不要超载. 后端 RBAC sprint (8 天主线) 不变, 副线 (前端/桌面端) 因 5/13 提前 ship 大量缩减, 部分 5/15-5/18 时间还给主线.

### 5/13 真收尾 (23:50, 已 ship) — 4 commit

| commit | 项 | 状态 |
|---|---|---|
| `4084bf9` | ACP /queue 等价 (RED-1A) + BACKLOG 5/14-6月 sprint plan | ✅ |
| `a95c190` | macOS Reminders.app 集成 (BL-REMINDER) | ✅ |
| `4d2795c` | ACP /steer 等价 (RED-1B) — 路径 A 断流+续接 | ✅ |
| `dead4f7` | Multi-Agent Kanban scope 1 (RED-2 单员工本地) | ✅ |

### 5/14 (周四) — 升级尾巴 + 启动 3 件并行

| 时段 | 项 | 工作量 |
|---|---|---|
| 上午 (4h) | BL-HERMES013-FIX-1: ui-tui rebuild (`npm install && npm run build`) — 状态栏 ⚕→🐟 | 5 分钟 |
| 上午 | 验证 Post-write delta lint (0 改动, 验 hermes 0.13 自带) | 5 分钟 |
| 上午 | **8 项资质合并 → catfish_run_skill** (5/13 反复卡的痛点根治) | 3.5 小时 |
| 上午 (碎片) | ACP /steer + Kanban scope 1 真机验证: Companion build + 三按钮 / Reminders 权限 / Kanban 5 列加 fake data | 30 分钟 |
| 下午 (4h) | **BL-RBAC P0 + hermes B sprint Day 1** — 启动: catfish-identity OAuth client credentials grant 设计 + 第一段代码 | 4 小时 (设计 1h + 代码 3h) |

### 5/15-5/16 (周五 + 周六) — RBAC sprint 全速 (副线腾空)

| 日期 | 主线 (RBAC sprint, 后端) | 副线 |
|---|---|---|
| 5/15 | RBAC Day 2: catfish-identity OAuth grant 实现完 + hermes-cli client 注册测试 + 单测 | (5/13 ACP /steer 已 ship, 副线放空给主线 — 多 4h) |
| 5/16 | RBAC Day 3: `allowed_models` per-dept/user 数据模型 + DB schema + alembic migration | RED-2-PG 设计调研: 看 hermes 0.13 自带 Multi-Agent Kanban 是不是 PG-based + 字段约定 (供 5/23 真做时参考) |

### 5/17-5/19 (周日 + 下周一二) — RBAC 中段

| 日期 | 主线 RBAC | 副线 |
|---|---|---|
| 5/17 | RBAC Day 4: `allowed_tools` per-dept (gateway sanitize_tools 接) | (空 — RED-2-PG 必须等 5/22 RBAC P0 表 ready 后才能落 FK, 不能 5/17 并行) |
| 5/18 | RBAC Day 5: `allowed_skills` per-dept (Skills Hub filter) | (空 / 主线缓冲) |
| 5/19 | RBAC Day 6: `allowed_channels/chats/rooms` (跟 hermes 0.13 命名对齐) + catfish-web `/admin/access` UI 启动 | (空) |

### 5/20-5/22 (周三-五) — RBAC 收尾

| 日期 | RBAC sprint |
|---|---|
| 5/20 | Day 7: `/admin/access` UI 完整 |
| 5/21 | Day 8: 测试 + 文档 |
| 5/22 | 缓冲 / 漏项补 (尤其 quota_users / departments 表 ready 给 5/23 RED-2-PG 用 FK) |

### 5/23-5/25 — RED-2-PG 真做 (任务 #57, jsonl → PG)

> **背景**: 5/13 ship 的 RED-2 scope 1 (jsonl 单员工本地) 是过渡品. 5/14 0:15 鸿波拍板 "PG 上线后 jsonl 删", 单一 source of truth. RBAC P0 完后才能落 FK 引用.

| 日期 | 项 | 工作量 |
|---|---|---|
| 5/23 | **Day 1**: PG schema (`catfish_tasks` 表 — sub FK→quota_users, department FK→departments, kind/label/status/started_at/finished_at/error/result_preview/created_at) + alembic migration `005_catfish_tasks` + index `(sub, started_at)` `(department, started_at)` | 1 天 |
| 5/24 | **Day 2**: edge `task_manager._run_wrapper` finally 改 push 到 `POST /api/tasks` (替代 jsonl 写入) + gateway 加 endpoint 接收 + RBAC 校验 sub | 1 天 |
| 5/25 | **Day 3**: `tasks_browse.list_my_tasks` 切读 PG (不读 jsonl) + 加 `list_dept_tasks` (manager 看本部门, admin 全公司) + KanbanPage 加 toggle "我的 / 本部门 / 全公司" (按 role 显示) + 删 jsonl 相关注释 + 老 `~/.catfish/tasks.jsonl` 不删 (历史归档) | 1 天 |

**已知 trade-off** (5/14 0:15 拍板接受): 中央 PG 起不来 / 没 VPN → 单机 KanbanPage 看不到任务. 单一 source of truth 优于双写一致性问题.

### 5/26-5/30 — BL-LEARN-RECMODE MVP (任务 #59, 5/14 22:00 v0 框架已 ship)

> **背景**: 5/14 ship v0 框架后 (CDP listener / 3 endpoints / aggregator 核心 / 25 单测), 真盘点剩余 5-9 天工作量, 比原设计估的 3-4 天多 50%. 5/14 鸿波拍板 A 方案: 按真实估调排 5/26-5/30 共 5 天 MVP, V2 优化 6/9+ 单独排.

| 日期 | 项 | 任务 | 工作量 |
|---|---|---|---|
| 5/26 (Day 1) | cdp_listener 真接 websockets + Page.captureScreenshot + aggregator.call_llm httpx → catfish-gateway | #63 | 0.5-1 天 |
| 5/27 (Day 2) | Companion RecMode UI 上半 — 🎙 按钮 + zustand store + 启停 + ffmpeg 录音 (复用 BL-VOICE3) | #64 | 1 天 |
| 5/28 (Day 3) | Companion UI 下半 — preview 模态 + 错误状态机 + Skill preview ("跑一次试" / "保存" / "重录" 三按钮) | #65 | 1 天 |
| 5/29 (Day 4) | 端到端联调 + 跑通第一个真 skill (eis-qualification-check, 鸿波录种子数据) + few-shot 例子加 SYSTEM_PROMPT | #66 | 1 天 |
| 5/30 (Day 5) | 隐私 14 天自动删 + 漏项补 + RecMode v1 ship (CHANGELOG/BACKLOG 升级状态) | #67 | 0.5-1 天 |

### 6/3-6/6 — i18n 接入 (任务 #51, RecMode 插队后再推 1 周)

| 项 | 工作量 |
|---|---|
| catfish brand patch RULES 改 i18n 兼容 (字符串走 `_("...")`) | 1 天 |
| 补 catfish 品牌字符串 zh-CN / en-US 翻译表 | 半天 |
| Companion 加语言 toggle (zh/en) | 半天 |
| hermes 0.13 i18n + catfish brand 端到端测试 | 1 天 |

### 6/9+ — RecMode V2 + 其他优化 (任务 #68, #62 等)

| 项 | 任务 | 工作量 |
|---|---|---|
| RecMode V2 — selector 漂移自动修复 (find_by_text 跑时 + main vision fallback) + DOM mutation summary 算法 | #68 | 1.5-2 天 |
| BL-MULTITURN-WINDOW — catfish_run_skill 跑超 5 轮截断 messages history (减 40-60% 大任务 token) | #62 | 1 天 |

### 待评估 (5/14 1:50 加, 鸿波 "教学方式很不人性、也很难复制" 反思后)

| ID | 项 | 状态 | 触发 |
|---|---|---|---|
| BL-LEARN-RECMODE | 录屏 + 语音教学引擎 — 用户正常操作 + 顺嘴说意图, 后端综合自动生成 skill (不让用户写 catfish API) | ⬜ 待评估 / 设计 ✅ | 设计文档 5/14 2:15 落 `docs/LEARN-RECMODE-DESIGN.md` (468 行, 12 章 + 2 附录). 5/19 V1-V4 验证通过后排 5/26-5/29 sprint (3-4 天). 详见 task #59. |

种子数据: 鸿波 5/19 (周一) 手动检查 EIS 资质过期时录全程屏 + 🎤 语音 "为啥这么点", 那段录像作录屏教学引擎第一个 ground truth.

### 6 月+ — hermes 0.13 剩下红利按需

- Checkpoints v2 (跟我们 inflight_streams 整合, 自动 pruning) — 1-2 天
- SSE MCP transport + OAuth forwarding (跟 mcp_registry_proxy 整合) — 1 天
- X-Hermes-Session-Key (memory provider 相关) — 0.5 天
- Post-write delta lint (已 0 工作量享受)
- 100 CLI tips (已享)

### 明确不接 (5/13 拍板)

- ❌ hermes 0.13 ACP client 改造 (catfish-Companion 改成 ACP IDE 集成) — IDE 集成 ≠ 桌面端, 场景不同
- ❌ no_agent cron mode — hermes CLI 内部模式, catfish 不用
- ❌ A 持久 dev token / C hermes 直连 OpenRouter — 都跟产品定位反着走 (任务 #50 拍板必须 B OAuth)

### 5/13 总教训 (落档备忘)

1. **gateway 不该猜 LLM 心思** — BL-FIX23 反复 7 轮全删, 净 -330 行
2. **看到红利别急接, 先 audit 跟产品定位一致吗** — ACP / i18n / B-vs-A-vs-C 4 次重审让结论更精准
3. **docs 跟代码同步是隐性 bug** — 5/4 起草的 HERMES-UPGRADE.md 标"暂停 0.10", 5/7 实际 ship 升级但 docs 没更新, 5/13 我误读差点重做
4. **web_fetch 不可全信** — GitHub releases 用 lazy load, 顶部最新可能拿不到. 凭不完整 fetch 结果否定自己 docs 是更大的错
5. **判断错误及时承认** — 5/13 我 4 次"想当然" 都被鸿波推翻. 倾向"避免新工作"的判断比"先 audit 再说"差很多

---

## 阻塞 / 等鸿波 (今天才能解锁)

| ID | 项 | 状态 | 阻塞 |
|---|---|---|---|
| BL-X1 | `ls -la ~/Library/Mail/` 给我看路径结构 | 🚧 | Mail.app adapter 开工前提 |
| BL-X2 | Companion Tauri 本机 `cargo build --release` 出新 .app | 🚧 | 验证仪表盘 catfish skills + tool-bridge 卡片真机生效 (4-29 加了功能, 真机没 build) |
| BL-X3 | `grep provider ~/.hermes/config.yaml` 检查 LLM 之前自作主张加的 `provider:auto` 是否还在 | 🚧 | 在的话改回 `''` |
| BL-X4 | `catfish_browser_goto` Companion 真机验证 (新对话发"打开 https://www.sohu.com") | 🚧 | — |
| BL-X5 | SOUL 三件套真机生效验证 (反幻觉 / 系统操作 / Memory 三层触发) | 🚧 | 4-30 SOUL 加了 catfish_run_skill 反幻觉铁律, 真机一起验 |
| BL-X6 | `open -e ~/.hermes/USER.md` 加"喜欢历史哲学"段 | 🚧 | — |
| BL-X7 (新) | 公司机器测 catfish-private-main (qwen 122b) 真实 tool 调用能力 (撤回 4-29 / 4-30 两次误判) | 🚧 | 5-1 起进公司 |
| BL-X8 (新) | 5 月 demo 真机彩排 (4 场景 17 分钟, 含场景 4 跨 session 记忆) — 计时 + 录屏复盘 | 🚧 | demo 前 3 天 + 前 1 天各 1 次 |

---

## A · 战略 / 开闭分界 (来自 STRATEGY.md)

> Phase 0 → 1 切换准备, 1 个月内.

| ID | 项 | 状态 | 估时 | 依赖 |
|---|---|---|---|---|
| BL-A1 | 给每个**计划开源**的组件加独立 README/CHANGELOG (不依赖 monorepo) | ⬜ | 1 周 | — |
| BL-A2 | 关键 hard-code env var 化 — 飞书 endpoint / 公司 SSO url (内网 IP 已 #15 做完) | ⬜ | 1-2 天 | — |
| BL-A3 | `central/llm-gateway/.env.example` 文档化每个 env var | ⬜ | 0.5 天 | — |
| BL-A4 | `docs/contributors/CODE_OF_CONDUCT.md` 草案 | ⬜ | 0.5 天 | — |
| BL-A5 | `docs/SECURITY.md` 漏洞披露流程 | ⬜ | 0.5 天 | — |
| BL-A6 | 给所有源代码加 SPDX header `// SPDX-License-Identifier: Apache-2.0` | ⬜ | 0.5 天 | — |
| BL-A7 | 第三方 attribution: hermes-agent (Nous Research, MIT) 在每个开源 repo README "Built on:" 段 | ⬜ | 0.5 天 | — |
| BL-A8 | 灰区拍板: catfish-gateway-auth / catfish-telemetry / browser-agent 开闭 | ⬜ | 决策, Phase 1 启动前 | — |
| BL-A9 | Phase 1: `git subtree split` 抽取开源组件到独立 repo (保留 history) | ⬜ | 1 周 | A1-A8 |
| BL-A10 | Phase 1: 主 repo 改成 monorepo + git submodule 引用 open 部分 | ⬜ | 0.5 周 | A9 |
| BL-A11 | Phase 1: 给开源 repo 加 CONTRIBUTING / .github/ISSUE_TEMPLATE | ⬜ | 0.5 周 | A9 |
| BL-A12 | Phase 1: 测试覆盖到 70%+ (4-30 估 50%+, gateway 380 / tool-bridge 165 / leadership-briefing 25 / weekly-report 17) | ⬜ | 1-2 周 | — |
| BL-A13 | Phase 1: docs.catfish.ai 域名 + 文档站 (mkdocs / docusaurus) | ⬜ | 1 周 | — |
| BL-A14 | Phase 2: 改 5 个开源 repo visibility public + HN/知乎/X 公告 | ⬜ | 0.5 周 | A9-A13 |
| BL-A15 | Phase 2 公告前法务咨询: 开源合规 / hermes-fork license / 算法备案 | ⬜ | 决策 + 1 周 | A14 |

---

## B · 业务 / GTM / 商业化

| ID | 项 | 状态 | 估时 |
|---|---|---|---|
| BL-B1 | catfish-cloud beta 内测平台搭建 (SaaS 多租户) | ⬜ | 4-6 周 |
| BL-B2 | 私有部署报价单: <50 人 / 50-500 / 500+ 三档 | ⬜ | 决策 1 天 |
| BL-B3 | 至少 3 个客户 demo 场景准备 (POC) | ✅ 4-30 | (4 场景: EIS+汇报 / 屏幕邮件 / IT 仪表盘 / 跨 session 记忆) |
| BL-B4 | GTM materials: 销售一页纸 / Demo 视频 / Sales deck | 🔵 (部分) | DECK 32 张 + ELEVATOR 5 版本 + Q&A 13 题已写, 视频待录 |
| BL-B5 | 营销网站 (catfish.ai 落地页) | ⬜ | 1 周 |
| BL-B6 | 销售路径决策: 自销 vs 渠道 (中国 to-B 特殊路径) | ⬜ | 决策 |
| BL-B7 | 价格策略决策: per-seat / per-org / 用量计费 | ⬜ | 决策 |
| BL-B8 | HN / 知乎 / X 公告文案 ("Why we're open-sourcing catfish") | ⬜ | 0.5 周 |
| BL-B9 | 第一个付费客户 (Phase 2 KPI) | ⬜ | 看商务 |
| BL-B10 | Phase 2 KPI: GitHub stars 500+ / HN 上首页 / 第一个合同 | ⬜ | KPI |
| BL-B11 | Phase 3 KPI: ARR 100-500 万 / 月活 50+ / 私部 10+ | ⬜ | KPI |

---

## C · 工程 P1 近期 (sprint 内, 已在 task tracker 的也列出来)

> **2026-04-28 节奏调整**: Phase 1 先把 Mac 全栈 + 中央服务 P0 闭环 (Week 1-6),
> Win 跨平台 (BL-C2~C5) 推到 **Phase 1 末尾 (Week 7)** 一鼓作气批量做.
> 详见 `STRATEGY.md` Phase 1 范围拍板段.

### C.1 Mac + Skill lifecycle (Week 1-2 — 当前 sprint)

| ID | Task # | 项 | 估时 | 状态 |
|---|---|---|---|---|
| BL-C1 | #35 | macOS Mail.app adapter | 2 天 | ⬜ (依赖 BL-X1) |
| BL-C6 | #41 | Companion macOS LaunchAgent 自启动 | 0.5 天 | ⬜ |
| BL-C7 | #32 | Plan C Week 4 polish (quota / RBAC / 自启动) | 1.5 天 | ⬜ (依赖 C6) |
| BL-C8 | (新) | Gateway 错误人话化 (429/timeout/401 → 员工友好文案) | 0.5 天 | ⬜ |
| BL-C9 | (新) | Companion 仪表盘加 tool-bridge 状态卡片 (autostart 起来了但 UI 没显示) | 0.3 天 | ⬜ |
| BL-C10 | (新) | catfish-public-qwen-flash upstream model 名字修正 (写 deepseek-v4-flash 实际是 Qwen3.6) | 0.1 天 | ⬜ |
| BL-C11 | (新) | tool-bridge config_watcher (cdp_url mtime 监听 → respawn, 比 chrome.rs 触发更全面) | 0.5 天 | ⬜ |
| BL-C12 | (新) | Skill lifecycle 阶段 3 Review: 创建/更新后立即 dry-run 验证, 失败回滚 | 0.5 天 | ✅ 5/2 (catfish_tools.py: _dry_run_skill + skill_install 调用) |
| BL-C13 | (新) | Skill lifecycle 阶段 3 Review: 创建前重复检查 (skill_view/skill_list 看相似 skill) | 0.3 天 | ✅ 5/2 (_check_skill_dedup) |
| BL-C14 | (新) | Skill lifecycle 阶段 4 Use: tool-bridge 加 .audit.jsonl 事件流 (每次 dispatch_tool 命中 skill 写一行) | 0.5 天 | ✅ 5/2 (tool-bridge/audit.py 整模块) |
| BL-C15 | (新) | Skill lifecycle 阶段 4 Use: catfish_today_summary 加 skill_invocations/failures/unused_30d 字段 + LearningCard UI | 1 天 | ✅ 5/2 (catfish_tools.py:979-982 + LearningCard) |
| BL-C16 | (新) | Skill lifecycle 阶段 4 Use: 30 天未用 skill 主动建议员工删/留 | 0.5 天 | ✅ 5/2 (skill_unused_30d 字段) |

### C.2 Win 跨平台 ❄️ 暂缓到 Phase 1 末尾 (Week 7) 批量做

> 不在当前 sprint, 不在 task tracker 优先列. 等 Mac 全栈 + 中央服务 P0 完成后一鼓作气 4-5 天搞定.
> **现在做 Mac 时**: IPC 用 abstraction 包一层 (Trait `Transport` / `BaseTransport`), 路径用 `pathlib.Path` 不 hardcode, 平台分支预留 `if sys.platform == "win32"` 占位, 防到 Week 7 改造代价大.

| ID | Task # | 项 | 估时 | 状态 |
|---|---|---|---|---|
| BL-C2 | #38 | Companion IPC 跨平台 (Unix Socket → TCP localhost) | 0.5 天 | ❄️ 暂缓 |
| BL-C3 | #39 | Companion Win 路径 + 端到端验证 | 1 天 | ❄️ 暂缓 (依赖 C2) |
| BL-C4 | #36 | Outlook for Windows adapter (pywin32 COM) | 1 天 | ❄️ 暂缓 (依赖 C2-C3) |
| BL-C5 | #37 | Foxmail for Windows adapter | 2 天 | ❄️ 暂缓 (依赖 C2-C3) |

---

## D · 工程 P2 中期 (1-3 个月内)

### D.1 中央闭源服务 (商业差异化, STRATEGY 列的)

| ID | 项 | 状态 | 估时 |
|---|---|---|---|
| BL-D1 | Skills Hub 中央服务 — 员工贡献 skill 回中央, 类比 GitHub Marketplace | ⬜ | 3-4 周 |
| BL-D2 | Secret Broker 中央服务 — 凭据中转, 让员工不暴露 token | ⬜ | 2-3 周 |
| BL-D3 Phase 1 | **MCP Registry 服务** — FastAPI :8996 + 4 manifest yaml (jira/gitlab/filesystem/time) + gateway 反代 /v1/mcp/* + Companion Dashboard 卡只读 + 部门权限过滤 | ✅ **5/9 ship** (鸿波 '现在就开始做不要拖') | 33 单测. 跳静态 MVP 直接 ship 真服务 |
| BL-D3 Phase 2 | **订阅 + OAuth + secret-broker** — POST/DELETE/GET subscribe + OAuth start/callback (mock 模式 demo / real 模式 token exchange) + Companion 订阅按钮真接通 + ✓已订阅徽章. 数据库 PG 主存储 (跟 gateway/identity 同套, 鸿波 '为什么不统一 PG' 后改) + sqlite fallback + alembic migration | ✅ **5/9 ship** (鸿波 '剩下的一点做完') | 39 单测. 跟 BL-G6 secret-broker 同期 ship |
| BL-D3 Phase 2.1 | **真 OAuth token exchange** — 框架已就位 (env CATFISH_MCP_OAUTH_MODE=real 启), 接 Atlassian/GitLab 真 client_id/secret 验证待 demo 后做 | 🔵 5/22+ | 代码已 ship, 缺真 OAuth app 注册 |
| BL-D3 Phase 3 | **Agent 动态加载 + pod-per-user** — gateway 调 /v1/mcp/subscribed → 注入 LLM tool 列表 / docker pod-per-user 拉 mcp-server-* / tool call 路由到 pod | ⬜ 5/26-29 (demo 后) | 1-2 周, 真 demo "员工说看 Jira" 返真数据 |
| BL-G6 | **Secret Broker** — keyring 后端 (mac Keychain / Win wincred / Linux libsecret) + 5 endpoint + 内存兜底. KMS / Vault prod 留后续. 给 BL-D3 Phase 2 提供 OAuth token 存储 | ✅ **5/9 ship** Phase 2 dev MVP | 16 单测. 端口 8995. prod KMS / Vault 升级留 5/22+ |
| BL-D4 | 中央 Telemetry — 业务数据收集 (token / 延迟 metadata, **不含对话内容**) | 🔵 (部分) | gateway audit JSONL 已写, telemetry server 收集还没建 |
| BL-D5 | catfish-distribution — SaaS 多租户分发管理 | ⬜ | 3-4 周 |

### D.2 Auth / Audit / Quota / RBAC

| ID | 项 | 状态 | 估时 |
|---|---|---|---|
| BL-D6 | SSO 真接入 (替换 dev token) | ✅ 4-28 | catfish-identity OIDC server + 飞书 adapter + Companion JWT + dev_token fallback ~600 行, 端到端通 |
| BL-D7 | 中央审计日志 (token 数 / 延迟 / 不含对话内容) | ✅ 4-22~28 | gateway audit JSONL ship, AuditCard 已显示 |
| BL-D8 | RBAC 系统 (3 角色: admin/manager/employee) | 🔵 5/2 MVP + 5/10 sysadmin (BL-ARCH1 P1) | spec + middleware + 单元 16 测过. **Phase 2 接力清单 (5/13 鸿波拍板, 待 5/15-5/19 sprint)**: P0 (5 天) — `allowed_models` per-dept/user (普通员工只能内网, manager+ 才能 gemini-pro 付费) + `allowed_tools` per-dept (财务部不能 catfish_browser_*) + `allowed_skills` per-dept (eis-login 只给运维) + `allowed_channels/chats/rooms` 命名跟 Hermes 0.13 对齐 (HERMES-013-ALIGN §2) + catfish-web `/admin/access` UI; P1 (3 天) — 拒绝事件审计 (`denial_reason` 字段) + RBAC config 变更日志 (`rbac_audit`) + per-user override (TTL 自动失效) + `/admin/access/events` 审计页; P2 (4 天) — 客户独立 RBAC config (跟 SOUL_FFCS 同精神) + MCP connection access control (OAuth 哪些部门可见) + BL-F11 RBAC 渗透测试 |
| BL-D9 | Quota 系统 (三维: per-user/model/department, sliding window sqlite) | 🔵 5/3 MVP | spec + quota.py 核心 + 单元 17 测过. /v1/quota/me endpoint + manager UI Phase 2 接力 |
| BL-D17 (新) | IDP UserStore 抽象 + PG/LDAP backend (现 yaml 短期够用) | 🔵 5/4 PG MVP | users + registry → PG (asyncpg + auto seed yaml). schema migration init_schema. quota / audit JSONL 留 sqlite Phase 2 接力 |

### D.3 Companion UI 二期 (来自 IDEAS § Companion UI 形态)

| ID | 项 | 状态 | 估时 |
|---|---|---|---|
| BL-D10 | 浮窗模式 (主入口) — 全局快捷键 `Cmd+Shift+Space` 召唤 | ⬜ | 1-2 周 |
| BL-D11 | 菜单栏/托盘常驻 (右上角图标 + 状态指示) | ⬜ | 0.5 周 |
| BL-D12 | 上下文压缩 catfish-autocompress (SOUL 提到, 自动触发) | ⬜ | 1 周 |
| BL-D13 | #40 Companion 邮件 tab GUI (4 个 adapter 都齐后再做更划算) | ⬜ | 1.5-2 天 |

### D.4 飞书集成深化

| ID | 项 | 状态 | 估时 |
|---|---|---|---|
| BL-D14 | 飞书集成: 现 feishu-monitor 是基础, 深化成完整 IM 适配 | ⬜ | 2 周 |
| BL-D15 | 企业微信适配器 (中国 to-B 必需) | ⬜ | 2 周 |
| BL-D16 | 钉钉适配器 (部分客户用) | ⬜ | 2 周 |

---

## E · 工程 P3 远期 — IDEAS.md 24 项

按 IDEAS 原分类列, 每项标 IDEAS.md 里的 # 编号 + 估时 + 前置:

### E.1 🎯 日常刚需型 (高优, 员工天天用)

| ID | IDEAS# | 项 | 估时 | 前置 |
|---|---|---|---|---|
| BL-E1 | #1 | 鲶鱼晨报 (Daily Brief) — 每天 9 点推送"今天 3 件事" | 1-2 周 | Email Agent + Jira/GitLab MCP + cron |
| BL-E2 | #2 | 对话式回顾周报 — 周五聊聊整理成正式周报 | 3-5 天 | Memory 体检 + Skill 系统稳定 |
| BL-E3 | #3 | 会议纪要机器人 — 实时语音转写 + 会后纪要 | 2-3 周 | Companion v1 + STT/Whisper |
| BL-E4 | #4 | 哄客户草稿生成器 — 三种回复风格 (专业/共情/义正) | 3-5 天 | Companion v1 + Email Agent |
| BL-E5 | #5 | 选中即翻译 — `Cmd+Shift+T` 浮窗翻译 (员工母语自然表达, 不是逐字) | 1-2 天 | Companion v1 |

### E.2 🔧 工作流自动化型 (中优)

| ID | IDEAS# | 项 | 估时 | 前置 |
|---|---|---|---|---|
| BL-E6 | #6 | 内部系统懒人代理 (帮我报销 X) — OA / ERP 自动操作 | 1-2 周/流程 | Browser Agent + 公司 OA MCP |
| BL-E7 | #7 | 代码改完自动更新文档 — git hook + diff 检测 | 1 周 | GitLab MCP + 文档定位 |
| BL-E8 | #8 | 鲶鱼的"学习清单" — 月底主动总结成长机会 | 1 周 | Memory + FTS5 |
| BL-E9 | #9 | "今天不想跟人说话"模式 — 鲶鱼帮过滤所有 IM @ | 1-2 周/平台 | 企业 IM 适配器 |
| BL-E10 | (新) | 智能日程协助 (从邮件 / 会议自动安排) | 1 周 | Email Agent + Calendar |

### E.3 💬 人机关系型 (高价值, 长期复利)

| ID | IDEAS# | 项 | 估时 | 前置 |
|---|---|---|---|---|
| BL-E11 | #10 | agent 命名权 — 员工给鲶鱼起名 + 3 档人设 (gentle/direct/roast) | ✅ 5/3 | (Onboarding StepName + AgentPrefsCard + gateway preamble + 11 处 UI 自指换 dynamic name) |
| BL-E12 | #11 | 鲶鱼周末不干活 (节假日/晚 10 点后自动说"明天再帮") | 0.5 天 | cron + prompt 开关 |
| BL-E13 | #12 | 鲶鱼主动闲聊 — 周五下午主动问"上周报销审批了没" | 🟡 5/2 MVP | (9:30/14:00/17:30 macOS 通知 ship; 见 BL-E13-FIX 修复待办) |
| BL-E13-FIX | (新, 5/4) | proactive 三件套修复: catch-up 补发 + 加宽时段窗口 + 通知权限引导 | 0.5 天 | demo 前必做; 现实测概率 3 个 slot 全 miss (员工不在前台) |

### E.4 🎪 纯娱乐 / 毒舌型

| ID | IDEAS# | 项 | 估时 | 前置 |
|---|---|---|---|---|
| BL-E14 | #13 | 鲶鱼吐槽 PPT — 上传 PPT 毒舌点评 | 3-5 天 | PPTX parser + critical personality. **demo 后下周开** (5/15+) |
| BL-E15 | #14 | 专注模式快捷键 (前 "领导来了" — 央企语境改名) — Cmd+Shift+F 全屏伪 IDE | ✅ 5/3 | (FocusModeView + Tauri 全局快捷键 + TabBar 入口) |
| BL-E16 | #15 | 社交健康检查 — 扫 IM 记录分析人际 | 1-2 周 | Slack/IM 适配器 + 员工授权 |

### E.5 🌐 组织协作型 (Plan D)

| ID | IDEAS# | 项 | 估时 | 前置 |
|---|---|---|---|---|
| BL-E17 | #16 | 两个鲶鱼对话 (Agent-to-Agent 跨人协作) | 1-2 个月 | 全员有 agent + 隐私控制成熟 |
| BL-E18 | #42 (task) | Catfish Federation Plan D 完整架构 | 5-6 周 | E17 |

### E.6 🎭 彩蛋 / 反直觉

| ID | IDEAS# | 项 | 估时 | 前置 |
|---|---|---|---|---|
| BL-E19 | #17 | 鲶鱼的"情绪" / 关系建立 | ✅ 5/3 | (SOUL "情绪铁律" 5✅+6❌+ 频率纪律 / session_meta 时间感 / Dashboard RelationCard 透明可删) |
| BL-E20 | #18 | "昨晚喝多了"模式 — 从本地 IM 缓存读出昨晚发了啥 | 3-5 天 | Companion + IM 本地访问 |

### E.7 🎨 Evolver 启发的

| ID | IDEAS# | 项 | 估时 | 前置 |
|---|---|---|---|---|
| BL-E21 | #19 | 鲶鱼的"今天模式" — 探索/稳健/修复 一键切 | 2-3 天 | prompt + slash command |
| BL-E22 | #20 | Skill 审计事件流 (EvolutionEvent JSONL) — 软著合规 + 组织学习 | 1 周 | Skills Hub MVP |
| BL-E23 | #21 | Skill 分布式验证 — 员工夜间 agent 帮验证别人 skill | 2 周 | Skills Hub 有体量 |

### E.8 🌐 网关 / 搜索

| ID | IDEAS# | 项 | 估时 | 备注 |
|---|---|---|---|---|
| BL-E24 | #22 | 网关 Fallback 链 | ✅ 4-26 | 已 ship, 自动 fallback 到公网模型 |
| BL-E25 | #23 | 三层统一搜索 (公网 / 公司内 / 本地) — 智能路由 | 1-2 周 | Browser Agent + MCP 连接器 |

### E.9 🔧 平台

| ID | IDEAS# | 项 | 估时 | 备注 |
|---|---|---|---|---|
| BL-E26 | #24 | Hermes 自定义 skill namespace (catfish 独立 namespace 不混 productivity) | ✅ 4-29 (绕路) | catfish skill 走独立 catfish_run_skill 工具 + skills/<namespace>/<skill>/ 目录, 不混入 hermes namespace. 实质等价 |

### E.10 🐟 桌面状态浮宠 (Codex pet 同思路, 鸿波 5/4 拍板归档)

> **背景**: 鸿波 5/4 看到 OpenAI Codex 加了"电子宠物" (桌面悬浮动画显 AI 状态), 问"我们要不要做". 评估: **应该做, demo 后 ship**.
> **跟 brand kit 完美契合**: 我们 5/3 做的 mascot SVG (圆胖鲶鱼 + 双须) 物化到桌面.
> **跟 BL-E11/E15/E19 (人格 sprint) 同思路**: AI 从"工具" → "在场的存在".

| ID | 阶段 | 估时 | 何时 | 范围 |
|---|---|---|---|---|
| BL-E27.1 | MVP — 4 状态浮窗 + 双击唤起 | 2-3 天 | 5/22-5/24 (demo 后第 1 周) | Tauri secondary window 80×80 透明 + always on top + 不抢 focus; mascot SVG + CSS 游动动画; 4 状态 (idle/thinking/running/done); 单击 popover, 双击唤 Companion; 右键菜单 (隐藏/切位置/设置) |
| BL-E27.2 | Polish — 拖拽 + persona 联动 | 3-5 天 | 5/25-5/30 | 拖拽 4 屏角 + localStorage 持久化; 鱼眼跟随鼠标 ("它在看你"); 跟 BL-E11 3 档 personality 联动 (gentle/direct/roast 不同游动节奏); idle 时呼吸感 |
| BL-E27.3 | 联动 — 主动闲聊 + 专注模式 + 全屏检测 | 2-3 天 | 6 月初 | BL-E13 触发时桌宠**游到屏幕中央** + 弹起话题; BL-E15 专注模式时淡出; 检测全屏/视频会议自动隐藏; 隐私模式一键 invisible |

**MVP 4 状态视觉:**
- **idle**: 静止 + 慢飘 (30 秒一次轻微扭尾)
- **thinking**: 头顶冒小气泡 (BL-E16 关系建立时也用)
- **running tool**: 旋转图标 + 浮 tool 名 tooltip
- **done**: 闪 ✓ + 1 秒回 idle

**demo 现场怎么用** (彩排彩排时演练, 不是 demo 当天即兴):
- 上半场 (15 min, 硬实力 RBAC/Quota/审计) → **桌宠隐藏** (太轻佻减分)
- 下半场 (5 min, 同事感 BL-E11/E15/E19) → **召唤桌宠**
  - 起名"小老李" → 桌宠出现 + 名牌
  - 让它写汇报 → thinking 气泡
  - Cmd+Tab 切走 → 持续在场
  - 30s 后 done 闪 ✓ → 切回看文档
  - 主动闲聊触发 → 桌宠**游到屏幕中央** + 弹话题

**风险点 (5/8 议程时拍板):**
- macOS Tauri 多窗口实测有坑 (always-on-top vs Mission Control 行为, 多屏幕)
- 央企严肃场景的"轻佻"风险 → MVP 默认开 + Onboarding 加 toggle "我喜欢桌宠 / 不喜欢"
- 视频会议屏幕共享时**必须自动隐藏** (BL-E27.3 重要), 防客户开会时桌宠飘进屏幕被领导看到

---

## M · 记忆纪律 / 记忆是资产 / 越用越懂 (新, 2026-05-04 鸿波 explicit 拍板)

> **产品哲学** (鸿波 5/4 跟小鲶共识): 记忆是资产, 记错了**更新 > 删除**, 保留版本历史作为成长痕迹.
> **2026-05-04 晚扩**: 加"越用越懂员工偏好" 维度 — feedback / 性格 / 工作模式 / 文书风格 4 个层次.

### M.1 记忆覆盖 (改不删, 留版本)

| ID | 项 | 估时 / 状态 | 备注 |
|---|---|---|---|
| BL-MM1 | 记忆覆盖纪律 — SOUL 加章节"覆盖前先 read 旧值, 写时把旧值塞进新值的 inline 备注里, 跟员工说话必 quote'旧 X → 新 Y'" | ✅ 5/4 | 0 后端代码改动. 工具底层不动也能模拟版本感 (read-then-write + inline annotation pattern) |
| BL-MM2 | catfish_remember 后端版本化 — `session_facts.json` schema 从 `{key: value}` 改 `{key: [{value, ts, prev_value}, ...]}` + 工具返回加 `previous_value`/`revision_count` + gateway inject 时显式列上次值 | ✅ 5/5 凌晨 | 13 条 tool-bridge 测试 + 3 条 gateway 测试. 旧 schema 自动迁移. 单 key 最多保留 5 版. SOUL § "工具底层暂不存版本数组" 删除, 换成 BL-MM2 纪律 |
| BL-MM3 | hermes memory_save 包一层版本化 (在 adapter.py, BL-D9 思路扩展) — read-modify-write 双调用模拟版本数组, 跟 hermes 0.10/0.12 兼容 | ✅ 5/7 (跟 BL-D14.5 hermes 0.12 升级提前一并完成) | adapter.py `_memory_save_versioned` wrapper, 28 单测; SOUL § 561 已更新; `CATFISH_DISABLE_MM3=1` 可回退 |
| BL-MM4 | Dashboard "记忆版本历史"卡 — 列所有 key, 点开看时间线 + 两版本 diff (像 git log) | ⬜ demo 后, 1-2 天 | 真客户演示卖点: "鲶鱼对你的认知怎么演化" |

### M.2 主动学习 / feedback / 越用越懂员工

> 鸿波 5/4 问: "小鯰能不能不断的越来越了解用户的性格、工作模式、生活模式、文书性格?"
> 现状: 没有显式 feedback 机制, 学习只能靠 LLM 自觉. 4 维全部 partial 或 ❌.
> 4 个工作量等级方案 — A 已 ship, B/C/D 排 5/8 后启动.

| ID | 项 | 估时 / 状态 | 备注 |
|---|---|---|---|
| BL-MM5 | 主动学习员工偏好 (SOUL 章节) — 4 类信号 + 频率纪律 (3+ 次同 pattern 才主动问) + 落盘格式 + ❌ 禁止 (不评论生活/情绪) | ✅ 5/4 | 0 后端代码. **方案 A**. 跟 BL-MM1 区分: MM1 被动 correction, MM5 主动学 preference |
| BL-MM6 | 显式 feedback UI — ChatBubble 加 👍 / 👎 / "改一下" 按钮 + `/api/feedback` + `~/.catfish/feedback.jsonl` + Dashboard "你给我的反馈" 卡 | ⬜ 5/8 后启动, ~2-3 天 | **方案 B**. 客户演示加分 ("能给反馈"), 跟 BL-MM5 配合: 显式信号补 LLM 自觉的不足 |
| BL-MM7 | 结构化用户画像 — `~/.catfish/user_profile.json` (writing_style / work_pattern / personality_traits + evidence_count) + 满 N 次主动问 + Dashboard 卡可调可锁 | ✅ 5/6 (鸿波"BL-MM7、MM8 直接开始"拍板当天 ship) | edge/tool-bridge/src/catfish_tool_bridge/user_profile.py (317 行) + UserProfileCard.tsx + 19 单测 PASS. 3 次 evidence 才 propose / 红线字段 (健康/财务/感情) 严禁 LLM propose / 员工 lock 防 LLM 改 / Dashboard 一键清空 |
| BL-MM8 | 文书风格 fingerprint — 员工历史文档抽风格指纹, 写新文档前调 fingerprint 调整生成参数, "本次按你 5 月 XX 那篇汇报风格写" | ✅ 5/6 (跟 MM7 同日 ship) | edge/tool-bridge/src/catfish_tool_bridge/style_fingerprint.py (454 行) + StyleFingerprintCard.tsx + 20 单测 PASS. 抽: 句长 / 段落数 / 词频 (jieba 中文分词 + char-level n-gram 兜底) / 标点偏好 / 列表-散文比例 / 3-5 样本句. 时间衰减 |
| BL-MM9 | **agent 自动抽 skill** — 鲶鱼监测员工反复做的事 (3+ 次同 pattern), 主动 propose "这个流程我帮你存成 skill 吧?", 员工确认后写 catfish/skills/. 不让 LLM 静默自决, 必员工确认 | ✅ **5/8 凌晨 ship** (鸿波"一次性别再分批"提前) | catfish_tools.py `propose_skill` + `~/.catfish/skill_proposals.jsonl` + 20 单测 PASS + SOUL § BL-MM9 纪律 (3 次门槛 / 红线 / 24h 限流 / session ≤5). 跟 hermes 对标但加员工 confirm 门槛 |
| BL-MM10 | **memory 自精炼 loop** — 老 employee_journal 跨 chunk LLM 总结 → 提炼 user_profile traits (level 1 具体事实 → level 2 性格模型) + 老 user_profile evidence 累 100+ 后浓缩, 释放 attention. 时间衰减权重 + lock 字段不动 | ✅ **5/8 凌晨 MVP ship** (规则版, 6/15 PoC 时接 LLM) | central/llm-gateway/.../memory_distill.py + 24 单测 PASS. 抽 work_pattern.peak_hours (时间戳分布) + writing_style.bullet_pref (列表 vs 散文). 红线过滤 + 24h 限流. LLM 真抽 hook 留 |
| BL-MM11 | **skill 级 👍/👎/改 评分** — 一条 assistant 消息含 catfish_run_skill 时, 给每个 skill_path 加 feedback 按钮. 写 ~/.catfish/skill_quality.jsonl. 跟 BL-MM6 共存, 一个给消息一个给 skill | ✅ **5/8 ship** (鸿波"一起做") | edge/companion-app/src-tauri/.../skill_feedback.rs (4 commands) + SkillFeedbackButtons.tsx (复用 BL-MM6 UI 模式) + ChatMessage.tsx 集成 + lib.rs 注册 |
| BL-MM12 | **综合质量分数 0-100** — Dashboard SkillAuditCard 显示每个 skill 的 quality_score (优/良/中/差 4 档颜色), 按分降序. 公式 50×success_rate + 30×log-normalized_freq + 20×explicit_feedback_ratio (BL-MM11 数据源) | ✅ **5/8 ship** | skill_audit.rs `compute_quality_scores` (~80 行) + 6 单测 + SkillAuditCard 加 quality_scores 渲染区 + 鼠标悬停看公式明细 |
| BL-MM13 | **catfish_propose_skill_revision 工具** — 鲶鱼监测老 skill 的低分 / 失败模式, 主动建议 "这个 skill 改一下吧?". SemVer 版本递进 + red-line 字段冻 + 24h ≤3/session 限频. 不直接改 skill 文件, 写 ~/.catfish/skill_revisions.jsonl 等员工 accept. 跟 MM9 propose_new_skill 对称 | ✅ **5/8 深夜 ship** (鸿波"13/14/15 一起完成不留尾巴") | tool-bridge catfish_tools.py (~150 行) + 14 单测 + SOUL § BL-MM13 纪律 |
| BL-MM14 | **Dashboard SkillRevisionCard** — pending (待处理) / effectiveness_due (14 天到期) / recent_resolved 三段 UI. 30s polling. accept 时自动 fetch 当前 quality_score 存为 baseline | ✅ **5/8 深夜 ship** | edge/companion-app/src-tauri/.../skill_revision.rs (~280 行, 4 commands) + SkillRevisionCard.tsx (~455 行) + Dashboard 集成 |
| BL-MM15 | **改进有效性跟踪** — accept 后 14 天 daemon 比当前 quality_score vs baseline. ≥+10 improved (✅ 真改善) / ≤-10 regressed (❌ 反而坏了, 建议回退) / 中间 neutral. **闭环成立**: 鲶鱼自己看到回退建议会主动 propose_skill_revision 反向 patch | ✅ **5/8 深夜 ship** | check_effectiveness Tauri command + SkillRevisionCard effectiveness_due 段 + 14 天阈值 hardcode (后续可调) |
| BL-FIX23 L1 | SOUL ★ "做完才说铁律" — 反馈 = 立刻 emit tool_call, 不发 plan-only "明白我要修 X". assistant message 必须含 ≥1: tool_call / 具体可验证结果 / 真问澄清 | ✅ 5/9 (4ccea2b) | edge/identity/SOUL.md (41 行段落). 软纪律层, 工程兜底是 L4/L5/FIX24 |
| BL-FIX23 L2 | (a) Companion lib/chat.ts SSE parser 加 delta.reasoning_content 处理 (Qwen / DS thinking 模式不丢内容); (b) gateway streaming chunk_stats 采样日志 (content/reasoning/tool_calls/empty 分布 + finish_reason 跟踪, debug 工具) | ✅ 5/9 (6a0b459) | tsc 0 error. 跟 BL-FE3 配套, demo 后升级原生折叠 UI |
| BL-FIX23 L4 | gateway _build_litellm_params 强制 max_tokens=4096 默认 (client 没传时). 修 Qwen vLLM 默认 max_tokens 太小 (~600 token) 长 docx 被上游截 finish_reason=length. **真根因 #1** | ✅ 5/9 (780670a) | 5 单测. auto_continue.py 注释里写明 streaming 5/8 后续做, 这次顶上, BL-A1.2 streaming auto-continue 真做掉留 demo 后 |
| BL-FIX23 L5 | gateway 强制 plan-only retry — 4 条 AND 触发 (finish_reason=stop + 0 tool_call + plan-only content + user 反馈) → deepcopy body + 加 assistant 已输出 + 加 user 硬 hint, litellm.acompletion 起新一轮 (同 used_model 不再 fallback), 新 stream chunks 接到原 SSE. 上限 2 次. **真根因 #2**, 鸿波拍板"方案 C, 不要考虑别的" | ✅ 5/9 (04ed80f, 修 follow-up 37a5ae2) | 13 单测. 客户端无感, 看着像鲶鱼自己续写 |
| BL-FIX24 | 重复 tool_call 检测 — 鸿波诊断 "**任务完成度评估缺位**". duplicate_tool_call_guard.py 扫近 12 条 messages, 抓 productive tool_calls, 算 arguments sha256 (normalized JSON), 同 (tool, hash) ≥ 2 次 → 注入 user hint "你重复 N 次, 不要再调, 等新指令. 不要主动问 '需要再做吗'". 跟 self_critique 互补 — 治"做了又做". 同时 SOUL 加 ★ "做完不再问铁律" (跟"做完才说"配套, 一进一出) | ✅ 5/9 (staged 4 文件待 mac 端 commit) | 15 单测. **真根因 #3** |
| BL-FIX24-config | catfish-private-main timeout 60s → 120s (yaml 覆盖). 防长 context 推理慢撞 fallback 误切 deepseek (公网, 保密性破坏 + ttft 187 秒比 private 慢) | ⬜ 5/10 ship | yaml 改 config + 重启 gateway |
| BL-A1.2 streaming auto-continue | finish_reason=length 跨次拼 SSE [DONE]. auto_continue.py 已实现 non-streaming 版, streaming 版 5/8 注释说"后续做", 现在 L4 max_tokens=4096 兜底, 真做留 demo 后 | ⬜ 5/15+ | 30+ 行 |
| BL-FE3 完整版 | Companion 原生 reasoning_content 折叠 UI ("点击展开思考过程") — L2 已合并 onDelta 兜底, 这版区分 thinking vs content, 跟 deepseek-flash / Qwen3.5 thinking 模式联动 | ⬜ 5/15+ | 1-2 天 |
| BL-E27.4 | **桌宠状态颜色 + 单击重置 + macOS 通知降级** — 桌宠头加 indicator (红 failed > 绿 completed > 蓝 running > 默认), 单击桌宠 = 已看 → 清 unseen 标记. macOS 通知收紧 (失败始终发, 成功 ≥30s + 同 label 1h 去重 + 测试任务过滤). 解决鸿波 5/8 抱怨"通知中心一周累积" | ✅ **5/8 ship** | services/pet_status.rs (8 单测 mac 跑) + commands/pet.rs 加 2 commands + pet.tsx 加 5s polling + status dot SVG + 数字徽章. task_manager.py 通知规则重写 + 11 新单测 |

---

## F · 运营 / 部署 / 安装

| ID | 项 | 状态 | 估时 |
|---|---|---|---|
| BL-F1 | macOS .pkg 安装包 (替代 install-catfish.sh dev script) | ⬜ | 1 周 |
| BL-F2 | Windows .msi 安装包 (Win 路径完成后) | ⬜ | 1 周 |
| BL-F3 | 全图形化 onboarding (员工首次启动引导) | ⬜ | 1 周 |
| BL-F4 | 数据迁移工具: 员工换电脑迁 memory/skill/state.db | ⬜ | 0.5 周 |
| BL-F5 | 备份方案: memory + state.db + employee_journal 自动备份到指定位置 | ⬜ | 0.5 周 |
| BL-F6 | hermes state.db schema 升级机制 (未来 schema 变了怎么 migrate) | ⬜ | 0.5 周 |
| BL-F7 | Production gateway 部署 (现 dev mode) | ⬜ | 1 周 |
| BL-F8 | catfish-cloud SaaS 多租户运营手册 | ⬜ | 1 周 |
| BL-F9 | 监控告警: gateway 错误率 / tool-bridge 健康 / hermes 进程崩溃 | ⬜ | 1 周 |
| BL-F10 | 性能基准测试 (gateway 多 user 并发) | ⬜ | 0.5 周 |
| BL-F11 | 安全测试 (SSO / RBAC / 审计日志渗透测试) | ⬜ | 1 周 |
| BL-F12 | session_summarizer 改成走本机 gateway HTTP loopback (替代直接 import litellm 绕过 fallback). catalog fallback chain 自动接管 (qwen 挂时 gemini-flash 接). 复用 quota / metrics / brand scrub | ✅ 5/4 | 30 分钟 (httpx.AsyncClient + skip-identity header + 8 单测) |
| BL-F13 | 修 aiohttp Unclosed client session 警告 (gateway shutdown 时 LiteLLM 内部 client 没 close, asyncio 报 ERROR). lifespan shutdown 加 best-effort 清理 (兼容 LiteLLM 多版本 attr 名) | ✅ 5/4 | 15 分钟 |
| BL-F14 | 内部 LLM 用例选模型 (`pick_internal_model`): catalog 加 use_case tag (summarizer/proactive_starter/a2a_aux), private 优先 public 兜底, env 可强制 override. summarizer/proactive/a2a_server 三处不再写死模型. **拆 gateway 部署 / catalog 改名 / 删模型 都不用动 .py 代码** | ✅ 5/4 | 1 小时 (新模块 internal_models.py + 改 3 caller + 14 单测) |
| BL-F15 | 修 quota_exceeded 死循环: gateway quota check 在 with_fallback 之前抛 429, fallback chain 不接, summarizer 死循环 hammer. 治标: picker 返候选列表 + summarizer 收 429 切下一个候选 + session 5 分钟冷却防 hammer | ✅ 5/5 凌晨 | 1 小时 (pick_internal_models_ordered + summarizer 候选 try + cool down + 10 单测) |
| BL-F16 | **治本** quota check 移进 with_fallback: 让主对话也享受"模型级 quota fallback" — 主模型 quota 满时自动切 chain 下一个, 不直接 429 给员工. 影响 chat_completions 主路径, 风险中, 排 demo 后 | ⬜ 5/15+ | 2-3 小时 (改 app.py 的 quota check 位置 + with_fallback 加 quota-aware 跳过 + 测试) |
| BL-F17 | internal use case 跳 quota check (X-Catfish-Internal header): summarizer/proactive/a2a 是后台 housekeeping, 不该消耗员工 user_day quota. gateway 看到 header 跳 quota check + 跳 record_usage. audit log 仍写 (透明). 鸿波 5/5: "summarizer 不要去限制用户的 quota 这才是合理的" | ✅ 5/5 凌晨 | 30 分钟 (3 处 record_usage 跳过 + summarizer/proactive 加 header + 1 单测) |

---

## FE · Companion 前端待办

> 5/4 鸿波加 deepseek-v4-flash 时发现的前端 reasoning_content 处理缺失. 单独立 FE 章节, 跟 D 工程后端 / E P3 远期 区分.

| ID | 项 | 状态 | 估时 |
|---|---|---|---|
| BL-FE1 | 真主动桌宠 (跟 BL-E27 联动, 看 BL-E27.3) | 见 BL-E27 | — |
| BL-FE2 | Dashboard 记忆版本卡 (BL-MM4) | 见 BL-MM4 | — |
| BL-FE3 | **前端原生支持 reasoning_content** — Claude Sonnet 风格 collapsible "深度思考" 段; ChatBubble 加可折叠 reasoning 显示; useChat 加 onReasoning callback; lib/chat.ts 解析 SSE chunk 时 dispatch reasoning_content 跟 content 分开累积 | ⬜ 5/15+ | 1-2 天 |
| BL-FE4 | Dashboard "你给我的反馈" 卡 (BL-MM6 显式 feedback UI 配套) | 见 BL-MM6 | — |
| BL-FE5 | Dashboard "鲶鱼对你的认知"画像卡 (BL-MM7 user_profile.json 配套) | 见 BL-MM7 | — |

**BL-FE3 详情**: deepseek-v4-flash thinking 默认开, stream 时 `delta.content=null, delta.reasoning_content="..."`. Companion 当前 `chat.ts:376` 只看 `delta.content` (null 跳过), reasoning 全丢. 演示 thinking 模型时聊天框一直空 / 撞 timeout. 修法: 加 `onReasoning(text)` callback + ChatMessage 折叠 UI ("点击展开思考过程"). 跟 BL-MM6 BL-FE4 同期做最佳.

**对其他模型影响**: 0 副作用. 千问 / 内网 qwen 默认无 reasoning_content, if-check 走原路径; Gemini 3 / OpenAI o1+ / Claude Sonnet 4.5+ 也有 reasoning_content, 改后能正确显. **demo 后 5/15 做, 配合 deepseek-flash 重新打开 thinking.**

---

## G · 内部建设 / Docs / Tests

| ID | 项 | 状态 | 估时 |
|---|---|---|---|
| BL-G1 | 员工使用文档 (catfish 怎么装怎么用) | ⬜ | 1 周 |
| BL-G2 | 管理员部署文档 (private 部署版) | ⬜ | 1 周 |
| BL-G3 | 开发者 API 文档 (gateway / tool-bridge IPC 协议) | ⬜ | 1 周 |
| BL-G4 | Tutorial / onboarding 视频 (3-5 分钟一个) | ⬜ | 1 周 |
| BL-G5 | 测试覆盖率提升: 30-40% → 70%+ (4-30 已升到 ~50%, 还需 G6/G7) | 🔵 | 1-2 周 |
| BL-G6 | 端到端集成测试 (现在只有单元) | ⬜ | 1 周 |
| BL-G7 | CI/CD pipeline (GitHub Actions / 内部 CI) | ⬜ | 0.5 周 |
| BL-G8 (新) | docs/CAPABILITY-MATRIX.md (现状能力快照) — 跟 BACKLOG 互补 | ✅ 4-30 | 跟 v2 同步落地 |

---

## H · 法律 / 合规

| ID | 项 | 状态 | 优先级 |
|---|---|---|---|
| BL-H1 | 软著申报 (CHANGELOG 提到这是写日志的目的之一) | ⬜ | Phase 1 内 |
| BL-H2 | 商标注册 (鲶鱼 / Catfish) | ⬜ | Phase 1 内 |
| BL-H3 | 公司注册 / 工商登记 (如未做) | ⬜ | Phase 1 前 |
| BL-H4 | 数据合规: 边缘数据主权法律层面背书 (一个律师 review) | ⬜ | Phase 2 前 |
| BL-H5 | 算法备案 (catfish-cloud 在国内运营 + ICP/EDI) | ⬜ | catfish-cloud 上线前 |
| BL-H6 | 隐私政策 / 用户协议 / 服务条款 起草 | ⬜ | catfish-cloud 上线前 |
| BL-H7 | 第三方依赖 license attribution 全审 (hermes / Tauri / litellm 等) | ⬜ | 开源前 |
| BL-H8 | Phase 2 公告前法务咨询 (开源合规风险) | ⬜ | Phase 2 前 |
| BL-H9 (新) | fonts/opensource/二进制从 git 移出 (现 git 历史含字体文件, license 风险) | ⬜ | 开源前必做 |

---

## I · 多模态 / Agent 能力扩展

| ID | 项 | 状态 | 估时 | 备注 |
|---|---|---|---|---|
| BL-I1 | 语音输入 (Cowork 风格), ASR 选型 (Whisper.cpp / qwen-audio / 阿里云 / macOS 原生听写) | ⬜ | 决策 + 1.5-2 天 | 4 种方案待拍板 |
| BL-I2 | 文件上传 — PDF/Excel/Word 提取后 inject (P1) | ⬜ | 1-1.5 天 | — |
| BL-I3.1 | 视频抽音轨转写 (会议录像 → 要点) | ✅ 5/8 ship | 2 小时 | 复用 BL-I4 链路, ffmpeg `-vn -ar 16000` |
| BL-I3.2 | 视频帧抽取 + vision 描述 | ⬜ 推后 | 1 天 | vision 调用费 + "全本地"故事冲突, demo 后看 |
| BL-I4 | 音频文件上传 + 转写 | ✅ 5/8 ship | 半天 | parse_audio_preview + whisper.cpp + ggml-small.bin (5/1 已装) + BM25 sidecar |
| BL-I5 | catfish-roleplay 之外更多角色化 skill (面试官 / 客户 / 投资人) | ⬜ | 0.5 周/角色 | catfish-roleplay 模板 |
| BL-I6 | catfish-search 增强 (FTS5 + 向量, 现是 FTS) | ⬜ | 1 周 | embedding 模型可用 |

---

## J · 品牌 / Visual Identity (来自 IDEAS § 品牌方向)

| ID | 项 | 状态 | 估时 | 备注 |
|---|---|---|---|---|
| BL-J1 | Icon 设计 (AI 生 + 调整) — 极简几何, 橙/墨绿, 鲶鱼轮廓 + 圆角方块 | ⬜ | 0.5 天 | 不急 |
| BL-J2 | 3 尺寸 + Companion 集成 | ✅ 部分 | — | #23 squircle 已做基础 |
| BL-J3 | 公司 Logo / 名片 / 邮件签名模板 | ⬜ | 0.5 天 | — |
| BL-J4 | Press kit (Logo 各版本 / 品牌色 / 字体) | ⬜ | 0.5 天 | Phase 2 公告前 |

---

## ★ K · 4-28~30 三天 ship 完成项快照 (v1 backlog 没列, v2 补回写)

> 这一节专门装 v1 backlog 漂移期间 (4-28 ~ 4-30) 真实 ship 的功能.
> 每项标 commit 日期 + 主要文件位置 (代码位置在 `docs/CAPABILITY-MATRIX.md` 进一步细化).
> Phase 标签: 全部 **Phase 1** (现在 ship 中).

### K.1 SSO + 身份层 (4-28 主)

| ID | 项 | Ship 日期 | 主文件 |
|---|---|---|---|
| BL-K1 | catfish-identity OIDC server (~600 行) — 自建轻量 issuer + JWKS + token endpoint | ✅ 4-28 | `central/identity-server/` |
| BL-K2 | 飞书 OIDC adapter — 用户用飞书账号扫码登录 | ✅ 4-28 | `central/identity-server/adapters/feishu.py` |
| BL-K3 | Companion JWT 客户端 — 自动刷 token + 持久化 | ✅ 4-28 | `edge/companion-app/src/auth/` |
| BL-K4 | dev_token 兜底机制 + warning banner — SSO 配错时救急 | ✅ 4-28 | gateway `auth/middleware.py` |
| BL-K5 | gateway auth=oidc / dev 切换 — env 一行换 | ✅ 4-28 | `central/llm-gateway/.env` |
| BL-K6 | SSO 6 决策点文档化 — 为客户对接做基础 | ✅ 4-28 | `docs/AUTH-DESIGN.md` |

### K.2 Skill 系统 (4-28 + 4-29 主)

| ID | 项 | Ship 日期 | 主文件 |
|---|---|---|---|
| BL-K7 | catfish_run_skill 工具 — importlib 反射 load `skills/<ns>/<skill>/script.py` | ✅ 4-28 | `edge/tool-bridge/.../catfish_tools.py` |
| BL-K8 | skill 自动注入 — gateway inject 当前可调 catfish skill 列表到 system prompt | ✅ 4-28 | `central/llm-gateway/.../inject_skill_catalog.py` |
| BL-K9 | skill_guard 工程级保护 — REQUIRED block, 防模型胡乱跑 skills_install/browse 假命令 | ✅ 4-29 | `central/llm-gateway/.../skill_guard.py` |
| BL-K10 | tool_capability_guard 配置驱动 — 用 ModelConfig.supports_tool_use, 不硬编码黑名单 | ✅ 4-29 | `central/llm-gateway/.../tool_capability_guard.py` |
| BL-K11 | Companion 仪表盘显示 catfish skills (Tauri Rust 同时扫 hermes + catfish 目录) | ✅ 4-29 | `edge/companion-app/src-tauri/src/commands/skills.rs` |
| BL-K12 | 字体目录三层结构 (legal-restricted / opensource / system-fallback, 法律红线) | ✅ 4-29 | `skills/_shared/fonts/` |

### K.3 业务 skill (4-29 + 4-30 主)

| ID | 项 | Ship 日期 | 主文件 |
|---|---|---|---|
| BL-K13 | leadership-briefing skill (V1 真复刻 PDF) | ✅ 4-29 | `skills/department/leadership-briefing/` |
| BL-K14 | leadership-briefing 改 4 段公文 (概况/分项/问题/下一步), heading 文本 LLM 自由 | ✅ 4-30 | `skills/department/leadership-briefing/SKILL.md` |
| BL-K15 | leadership-briefing 内容质量铁律 (论点+数据+推论+承上启下), 表格全转附件 CSV | ✅ 4-30 | `skills/department/leadership-briefing/script.py` |
| BL-K16 | leadership-briefing 双 backend 错别字检查 (typo_check 公文字典 50+ 条 + pycorrector Kenlm 补充, 按 (old, pos) 去重) | ✅ 4-30 | `skills/department/leadership-briefing/typo_check.py` |
| BL-K17 | weekly-report skill (员工周报 .xlsx 复刻员工模板) | ✅ 4-29 | `skills/department/weekly-report/` |
| BL-K18 | weekly-report Phase 2 简化版 (方式 A+ — LLM 用 session_search 抽近 7 天历史, 员工 review/补充) | ✅ 4-30 | `skills/department/weekly-report/SKILL.md` |

### K.4 ★ 跨 session 上下文 / 持久个体 (4-30 主, 5 月 demo 致命卖点)

| ID | 项 | Ship 日期 | 主文件 |
|---|---|---|---|
| BL-K19 | 档1 inject_session_history — 注入最近 7 天 session 元数据 (id/时间/消息数/title/首条 user msg) | ✅ 4-30 | `central/llm-gateway/.../inject_session_history.py` |
| BL-K20 | 档2 employee_journal — 追加式日记 markdown, 50KB tail-truncate, 注入 system prompt | ✅ 4-30 | `central/llm-gateway/.../employee_journal.py` |
| BL-K21 | 档2 session_summarizer — 后台异步 LLM 总结结束的 session, append 到 journal (qwen-flash, 失败重试) | ✅ 4-30 | `central/llm-gateway/.../session_summarizer.py` |
| BL-K22 | hermes venv 装 litellm (用清华镜像) — session_summarizer 后台总结链路打通 | ✅ 4-30 | hermes venv |

### K.5 SOUL.md / 反幻觉 (4-30 主)

| ID | 项 | Ship 日期 | 主文件 |
|---|---|---|---|
| BL-K23 | SOUL.md 加 § "catfish_run_skill 工具不要去 skills_list 验证" 铁律 (踩过坑) | ✅ 4-30 | `edge/identity/SOUL.md` |
| BL-K24 | skill_guard REQUIRED block 同步加铁律 5/6: 双层防御 | ✅ 4-30 | `central/llm-gateway/.../skill_guard.py` |
| BL-K25 | 删除 hermes 自创 skill (data-analysis/qualification-management-report 等抢占的) | ✅ 4-30 | `~/.hermes/skills/data-analysis/` rm -rf |

### K.6 Companion 工程改善 (4-30)

| ID | 项 | Ship 日期 | 主文件 |
|---|---|---|---|
| BL-K26 | useChat.ts MAX_TOOL_ROUNDS 10 → 20 ("10 轮总踩上限"反馈) | ✅ 4-30 | `edge/companion-app/src/hooks/useChat.ts` |
| BL-K27 | useChat.ts 连续 3 轮 parse_error 早停 + 友好提示 | ✅ 4-30 | 同上 |
| BL-K28 | useChat.ts cache 60s TTL + 关键工具缺失自动重拉 (不用 Cmd+R 刷新) | ✅ 4-30 | 同上 |

### K.7 5 月 demo 准备 (4-28 + 4-30)

| ID | 项 | Ship 日期 | 主文件 |
|---|---|---|---|
| BL-K29 | 5 月 demo 文档 6 件套基础版 (~1100 行) | ✅ 4-28 | `docs/MAY-DEMO-PREP.md` etc. |
| BL-K30 | 演讲稿 4 份同步今日新增功能: DECK 32 张 (含场景 4 跨 session) + ELEVATOR V1-V5 + Q&A 13 题 + PREP 检查清单 | ✅ 4-30 | `docs/MAY-DEMO-DECK.md` etc. |

---

## ★ L · 4-30 当前缺口 (新发现的待办, v1 backlog 漏的)

> 这一节装 4-30 当天讨论 / 实测中发现的待办, v1 没列. 按优先级排.

### L.1 demo 前必做 (5 月中旬前)

| ID | 项 | 估时 | 状态 |
|---|---|---|---|
| BL-L1 | 真机彩排 (4 场景 17 分钟) — demo 前 3 天 + 前 1 天各 1 次, 计时 + 录屏复盘 | 2 × 1 小时 | ⬜ (= BL-X8) |
| BL-L2 | demo 前 7 天积累 employee_journal.md — 真实用 catfish 工作攒至少 5-10 段 session 摘要 (没内容客户看不到效果) | 持续 | ⬜ |
| BL-L3 | 真实部门汇报模板 (.docx) 拿回来 — 让 leadership-briefing 学风格 (用词 / 结构 / 数据展示偏好) | 5 分钟 | 🚧 (公司带回来) |
| BL-L4 | EIS 资质列表页面截图 + URL/API 探查 (确认有"导出"按钮 = 方案 D 直接成立) | 5 分钟 | 🚧 (公司带回来) |
| BL-L5 | 30 秒真实使用录屏 (cmd+shift+5) — 5 月 demo PPT 嵌入 | 30 秒 | ⬜ |
| BL-L6 | 第 3 个 catfish skill (annual-summary 或 project-approval) — 给客户看 skill 不止 2 个 | 1-2 天 | ⬜ |

### L.2 PoC 阶段 (5-6 月)

| ID | 项 | 估时 | 状态 |
|---|---|---|---|
| BL-L7 | weekly-report Phase 2 完整版 — 接 hermes audit log + tool 调用历史自动判断本周做了哪些事拼草稿 (现在简化版方式 A+) | 1 周 | ⬜ |
| BL-L8 | journal 向量召回升级 — 现在 50KB tail-truncate 半年员工正常用够, 长期 (2-3 年用户) 应升级为 embed journal 段落按相关性 retrieve top-K (语义模糊查询) | 1-2 周 | ⬜ |
| BL-L26 (新 5/1) | 用户上传文件 ≥ 50KB 走 BM25 检索 — top-K 段落注入 user message. **不做 embedding RAG**. | ✅ 5/7 (3 天压 1 天 ship) | parse_file.py 写 sidecar `<keptPath>.parsed.txt` (PDF/Word/Text), commands/file_parse.rs `attachment_bm25_search` 调 attachment_bm25.py (TF + 长度归一化, 中文 2-char window — trigram FTS5 不行央企 2 字词如"合同"/"解除"). 24 单测 PASS. useChat.send 自动 enrich, formatFileAttachment 用 BM25 段落代替 preview |
| BL-L9 | journal append-only 备份 / 同步策略 (员工换机 / 误删保护) | 0.5 周 | ⬜ |
| BL-L10 | session_summarizer 不依赖 DASHSCOPE_API_KEY — 改用 catfish 自己 gateway 路由的任意可用模型 | 0.5 天 | ⬜ |
| BL-L11 | catfish_run_skill 加载失败友好错误引导 (现在 stack trace 不友好) | 0.5 天 | ⬜ |
| BL-L12 | hermes venv 也能跑 session_summarizer (现在只能从 gateway venv 跑) | 0.5 天 | ⬜ |
| BL-L13 | IDP UserStore 抽象 + PG/LDAP backend (= BL-D17, 短期 yaml 够) | 3-5 天 | ⬜ |

### L.3 Skill 全生命周期补齐 (5/10 步未做)

> 现 catfish skill 系统 ship 了 5 步: 设计 / 加载 / 调用 / inject_catalog / guard. 还差 5 步:

| ID | 项 | 估时 | 状态 |
|---|---|---|---|
| BL-L14 | Skill 版本管理 (skill 改 SKILL.md, 旧 session 是否兼容) | 0.5 周 | ⬜ |
| BL-L15 | Skill 下线 / deprecation 流程 (warning banner) | 0.3 天 | ⬜ |
| BL-L16 | Skill 删除 (清掉 audit + 仪表盘 + 提示员工) | 0.3 天 | ⬜ |
| BL-L17 | Skill 完整审计 (catfish_run_skill 调用全 audit, 跟 gateway audit 关联) | 0.5 周 | ⬜ |
| BL-L18 | Skill 分享 / 安装 (从其他员工 / 中央 hub 拿 skill, 关联 BL-D1 Skills Hub) | 1 周 | ⬜ |

### L.4 演讲稿 / 销售物料补缺 (demo 前)

| ID | 项 | 估时 | 状态 |
|---|---|---|---|
| BL-L19 | demo 实录 3 个 1 分钟视频 (真实 case 嵌入 PPT) | 1 天 | ⬜ |
| BL-L20 | PPT 实际制作 (按 MAY-DEMO-DECK.md 32 张大纲填) — Keynote / 飞书 | 1 天 | ⬜ |
| BL-L21 | SSO 接入文档收尾 (给客户 IT 自助接入) | 1 天 | ⬜ |

### L.5 工程债清理

| ID | 项 | 估时 | 状态 |
|---|---|---|---|
| BL-L22 | fonts/opensource/二进制从 git 历史移出 (= BL-H9, license 风险) | 0.5 天 | ⬜ |
| BL-L23 | install_to_hermes.sh / setup_b_plan.sh (B 方案撤回, 留着无害但 cleanup) | 0.3 天 | ❄️ |
| BL-L24 | gateway audit JSONL 字段稳定化 + 文档 (Phase 2 IT 审计前) | 0.5 周 | ⬜ |
| BL-L25 | tool-bridge / gateway / catfish-identity 中央服务 7×24 watchdog respawn 实测 | 0.5 天 | ⬜ |
| BL-L27 (新 5/2) | datetime.utcnow() 全替换 datetime.now(timezone.utc) — Python 3.12+ deprecated, 五一 sprint 加的代码 (a2a_audit / a2a_jwt / catfish_tools / registry) 都用了, ~10 处 | 0.5 天 | ⬜ |
| BL-L28 (新 5/2) | ALLOW.md 关键词匹配从子串改为 token-overlap (中文 jieba / lac 分词后比 token 集合) — 现在 "项目 X 进展" 不命中 "项目 X 上周进展" (中间插字符就 fail), 实际员工写 ALLOW 不可能列全所有变体 | 1 天 (Phase 2) | ⬜ |

---

## ★★ M · 五一 5 天 sprint (5/1 ~ 5/5) — Phase 2 核心功能跃迁

> **创建**: 2026-04-30 晚, 五一假期 5 天 (5/1 周五 ~ 5/5 周二) 全力做 Phase 2 5 大核心功能.
> **目标**: 5 项中 4 项完整 ship + 1 项 (Skills Hub) MVP, **Plan D 协议 B 真实现 (单机 mock 验证)**.
> **不在 5 天内的**: Journal 向量召回 (5/6+ 用公司 bge-m3 验), Plan D 跨 2 台真机测试 (5/6+ 公司多账号), RBAC/Quota/Win 跨平台 (Phase 1 末).

### M.0 关键设计决策 (4-30 鸿波拍板)

| 项 | 选项 | 备注 |
|---|---|---|
| Plan D 协议风格 | **B** MCP-style streaming (JSON-RPC 2.0 over SSE) | FastAPI 原生 SSE 不复杂, 跟 MCP 生态对接长期价值大 |
| 隐私 spec 格式 | **A** ALLOW.md (默认 DENY) | 国央企合规默认显式授权 |
| Registry 存哪 | **A** catfish-identity yaml 表 | 复用现有 SSO 基础, 1 天能完成 |

### M.1 Day 1 · 5/1 周五 — 多模态 (9h, ship 完整)

| ID | 项 | 时长 | 状态 |
|---|---|---|---|
| BL-M1.1 | macOS 原生听写接入 (Companion 🎤 → Tauri Rust → NSSpeechRecognizer) | 4h | ⬜ |
| BL-M1.2 | 文件上传 UI (Companion 拖拽 / 选择 → FilePill chip) | 1h | ⬜ |
| BL-M1.3 | 文件解析后端 (PDF pypdf / Excel openpyxl / Word python-docx → inject system prompt 顶部) | 4h | ⬜ |

### M.2 Day 2 · 5/2 周六 — Skill 全生命周期 4 步 (9h, ship 完整)

| ID | 项 | 时长 | 状态 |
|---|---|---|---|
| BL-M2.1 | Skill 版本管理 (SKILL.md frontmatter `version` 字段 + catfish_run_skill 加载兼容性检查) | 2h | ⬜ (= BL-L14) |
| BL-M2.2 | Skill 下线 / deprecation (`deprecated: true` + 调用时 warning banner) | 2h | ⬜ (= BL-L15) |
| BL-M2.3 | Skill 删除 (`catfish_skill_delete` 工具 + 仪表盘清理 + 提示员工) | 2h | ⬜ (= BL-L16) |
| BL-M2.4 | Skill 完整审计 (`~/.catfish/skill_audit.jsonl` + Companion SkillAuditCard) | 3h | ⬜ (= BL-L17 + BL-C15) |

### M.3 Day 3 · 5/3 周日 — Skills Hub MVP + Plan D 协议设计 (9h)

| ID | 项 | 时长 | 状态 |
|---|---|---|---|
| BL-M3.1 | Skills Hub MVP 本机版 (`catfish_skill_install` 工具 + 仪表盘"已装/可装" 列表) | 5h | ⬜ (= BL-D1 简化, BL-L18) |
| BL-M3.2 | Plan D 协议 spec (`docs/PLAN-D-PROTOCOL.md`): MCP JSON-RPC 2.0 over SSE + JWT 互信 + ALLOW.md format + audit spec | 4h | ⬜ |

### M.4 Day 4 · 5/4 周一 — Plan D registry + A 端 + B 端 (10h)

| ID | 项 | 时长 | 状态 |
|---|---|---|---|
| BL-M4.1 | Registry (catfish-identity `registry.yaml` + 路由查询 endpoint `/registry/lookup`) | 3h | ⬜ |
| BL-M4.2 | A 端 (Companion 生成 A2A JWT + 调 SSE + UI 流式显示 B 鲶鱼回答) | 3h | ⬜ |
| BL-M4.3 | B 端 (gateway `POST /a2a/ask` SSE endpoint + JWT 验签 + 隐私拦截 + 转 LLM + 流式返流) | 4h | ⬜ |

### M.5 Day 5 · 5/5 周二 — Plan D 隐私 + audit + mock + demo (9-10h, ship 完整)

| ID | 项 | 时长 | 状态 |
|---|---|---|---|
| BL-M5.1 | ALLOW.md spec + 关键词/正则匹配拦截器 (默认 DENY, 不用 LLM 判断) | 2h | ⬜ |
| BL-M5.2 | A2A audit 双方记录 (A 调用记 A audit, B 应答记 B audit, 中央 gateway metadata) | 2h | ⬜ |
| BL-M5.3 | 单机 mock 2 员工 (env `CATFISH_HOME=~/.catfish-alice` / `~/.catfish-bob`, 2 端口 / 2 SOUL/USER) | 4h | ⬜ |
| BL-M5.4 | 真 A2A demo (Alice 鲶鱼问 Bob 鲶鱼"项目 X 上周进展", Bob 鲶鱼 ALLOW 项目 X 状态, 流式答) + 测试 + 文档 | 5h | ⬜ |
| BL-M5.5 | commit + push + CHANGELOG 五一 sprint 总结 + BACKLOG ✅ 同步 | 1h | ⬜ |

### M.6 5 天总账 (预期 ship)

```
完整 ship (代码 + 测试 + 文档):
  ✅ 多模态语音 (macOS 原生听写)               BL-I1
  ✅ 多模态文件上传 (PDF/Excel/Word)           BL-I2
  ✅ Skill 版本管理                           BL-L14
  ✅ Skill 下线 / deprecation                  BL-L15
  ✅ Skill 删除                                BL-L16
  ✅ Skill 完整审计                            BL-L17
  ✅ Skills Hub 本机版 MVP                     BL-D1 (简化), BL-L18
  ✅ ★ Plan D B 协议真实现 (单机 mock)         BL-E17 雏形 (单机), BL-E18 协议层

5/6 上班后做:
  ⬜ Journal 向量召回 (用公司 bge-m3)          BL-L8
  ⬜ Plan D 跨 2 台真机 federation 测试        BL-E18 完整
```

### M.7 关键风险

🟥 **macOS 听写 Tauri Rust 调用没集成过** — Day 1 上午 4h 调不通就 fallback Whisper.cpp (whisper-node)
🟥 **SSE + JWT 跨实例验证调试容易踩坑** — Day 4 留 1h 余裕做 wireshark / curl 抓包
🟥 **单机 mock 端口/路径冲突** — Day 4 末测一遍, 不要拖到 Day 5 才发现
🟧 **5 月 demo 不演 Plan D** — Phase 3 Q4 ship 承诺别打脸, 这次 ship 是技术 ready 给 demo 后客户技术对接看
🟧 **5 天连续 9-10h 高强度** — 每天早 9 晚 7 + 中午 1h 午休, 不熬夜

---

## 总览统计 (v2)

```
A  战略 / 开闭          15 项  (15 ⬜)              · 1-3 个月集中做 (Phase 0→1→2)
B  GTM / 商业化         11 项  (9 ⬜ + 1 🔵 + 1 ✅)  · Phase 2 启动前 + 长期
C  工程 P1 近期         16 项  (12 ⬜ + 4 ❄️)        · ~2 周内
D  工程 P2 中期         17 项  (14 ⬜ + 1 🔵 + 2 ✅) · 1-3 个月
E  工程 P3 远期 (IDEAS) 26 项  (24 ⬜ + 2 ✅)        · 3-6 个月按需挑做
F  运营 / 部署          11 项  (11 ⬜)               · 持续
G  内部建设 / Docs      8 项   (6 ⬜ + 1 🔵 + 1 ✅)  · 持续
H  法律 / 合规          9 项   (9 ⬜)                · Phase 1 / Phase 2 前必做
I  多模态扩展           6 项   (6 ⬜)                · P1.5-P2 之间
J  品牌 / VI            4 项   (3 ⬜ + 1 ✅)         · Phase 2 前

K  ★ 4-28~30 ship 快照  30 项  (30 ✅)               · 已完成 (回写)
L  ★ 4-30 当前缺口      25 项  (24 ⬜ + 1 ❄️)        · 5 月 demo 前 / PoC / 后续

M  ★ 5/10 架构反思     3 项   (2 ⬜ + 1 ❄️)         · 5/15 起做 (BL-ARCH1/2/3)

阻塞 / 等鸿波           8 项   (8 🚧)               · 解锁后归类

合计 ~189 项 (v1 = 121 项, v2 +65 项, v3 +3 项)
其中已完成 ~80 项 (✅), 5/10 凌晨一夜 +18 项 (FIX27~38 + 35.1 + D2 + D2 Phase 2 + D3 fix5 + 架构决策)
```

### §M · 5/10 凌晨架构反思 (新)

> 5/10 凌晨 4:00 鸿波 challenge 触发. **客户端 = "我"的体验, web = "组织/管理"的体验** (业界 VSCode + GitHub / Cursor + cursor.sh / 1Password + 1password.com / Slack + admin.slack.com 标杆).

| ID | 项 | 状态 | 估时 | 依赖 |
|---|---|---|---|---|
| BL-CENTRAL-BILLING | **成本月报** — 按月统计 token 用量 / 请求次数 / 模型分布; 按部门 / 项目分摊成本; 导出 PDF / Excel; 同期对比 (本月 vs 上月)。7/30 从 /admin/billing 挪来 —— 那一页当时把这份设计清单直接渲染给客户看, 已从侧栏摘掉 (见 central/web/src/routes/admin/navConfig.ts) | 未排期 | - | 数据源已具备: gateway_audit + 模型单价 (7/30 起模型配置里可填 price_per_1k_tokens) |
| BL-ARCH1 | **catfish-web 中央门户 (新项目)** — Skills Hub 全广场 + publish UI / MCP 市场 + IT 配 OAuth credentials / Manager 视图 (本部门 quota/audit/team) / Admin 后台 (用户/部门/全公司 audit/billing/dev_users 编辑/identity users.yaml 编辑) / 历史 audit 大查询 | ✅ 5/10 | 3 周 → 1 天 | P0+P1 一夜 ship |
| BL-ARCH1 P1 | **完整用户管理 + sysadmin 超级管理员** — identity-server `users` 加 8 字段 (locked / deleted_at / created_by / last_login_at / must_change_password 等) + `users_audit` 表 + 9 个 admin endpoints (CRUD / 锁 / 重置密码 / 审计) + RBAC (sysadmin > admin > manager > employee) + gateway `/api/admin/*` 反代 + catfish-web `/admin/users` & `/admin/system` 两套页面. 防自锁 / 软删除 / bcrypt 12. | ✅ 5/10 | 1 天 | BL-ARCH1 |
| BL-ARCH2 | **Companion 瘦身** (BL-ARCH1 配套) — 砍 7 张管理类卡 (DepartmentQuota / DepartmentAudit / Audit / SkillAudit / SkillsHub 浏览 / McpRegistry 浏览 / AdminGlobal), 留 14 张 "我的" 视角卡 (Identity / AgentPrefs / Quota / Catalog / SkillsMcp(我装的) / Curator / Services / Proactive / Tasks / Relation / Memory / UserProfile / StyleFingerprint / Feedback / Learning / SkillRevision). 顶部加 WebPortalLink banner 按 role 显示 web 锚点 (/me /skills /mcp /manager /audit /admin /admin/system) | ✅ 5/10 | 2 天 → 30 分钟 | BL-ARCH1 |
| BL-ARCH3 | **catfish-edge-daemon (可选, 性能评估后定)** — Tauri Companion 瘦身后 watchdog/OAuth/文件 IO 拆出来成本机系统服务 (launchd/systemd), Tauri ↔ daemon localhost:8994 HTTP. ❄️ 暂不做, 先看 BL-ARCH1 落地后 Companion 还多重 | ❄️ | 1-2 周 | BL-ARCH1 完成 + 性能评估 |
| BL-VOICE2 P0 | **Piper local TTS — 让鲶鱼说话** (mac, 5/10 鸿波 "这么好玩的没理由不现在做"). subprocess 调 piper 二进制 (跟 whisper.cpp 同模板), 中文 voice ~30MB, 100% 本地数据不出公司. 文件: `commands/tts.rs` + `lib/tts.ts` + `TTSButton.tsx` + ChatMessage 集成 + Tauri assetProtocol scope. 部署: `brew install piper-tts` + curl voice. | ✅ 5/10 | 1 天 | BL-FIX23 (chat 流式稳定) |
| BL-VOICE2-WIN | Piper Windows 打包 — piper.exe 内置 .exe bundle resource, find_executable 走 Tauri resource path (跟 BL-WIN9 同模板) | ⬜ | 1 天 | BL-VOICE2 P0 |
| BL-VOICE2-PET | 桌宠"说话"动画 — catfish-pet.svg 加嘴巴帧 + 跟音频时长同步 cycle (员工看到桌宠真在说话, 央企演示加分明显) | ⬜ | 1-2 天 | BL-VOICE2 P0 |
| BL-VOICE2-PRO | Proactive 闲聊触发 → 自动播 (员工 opt-in, 默认关). 配合 BL-VOICE2-PET 桌宠动画做"会主动找你说话的鲶鱼" | ⬜ | 半天 | BL-VOICE2-PET |
| BL-VOICE2-AGENT | AgentPrefsCard 加 voice 选择器 (huayan/bizhao) + 试听按钮 + tts.enabled toggle | ⬜ | 半天 | BL-VOICE2 P0 |

**为啥不做"全中央 web 化" (rejected)**: 跟 catfish 初衷冲突 (离线 / 员工感 / 央企心理 / 数据归属感 / Hermes 集成本机). BL-ARCH1 拆"管理类" 去 web (本来就该 web — 跨员工 / 跨部门 / IT 操作), Companion 留"我的" 功能 (本来就该本机 — 桌宠 / 快捷键 / 我的画像 / 我装的 skill).

**Phase 时间线** (实际 vs. 原计划):
```
原计划:
  5/14 demo:                     不动现状, Companion 重也演得动 (主线已通)
  5/15 起 (3 周, BL-ARCH1+ARCH2):
    W1: catfish-web 搭 + OIDC + 复用 SkillsHub / McpRegistry / Audit 三卡
    W2: admin 后台 (用户/配额/审计大查询)
    W3: manager 视图 + billing 月报 + nginx 部署
    Companion 同步瘦身 2 天 (砍 5 卡 + 加链接)
  6 月+: BL-ARCH3 评估 (Companion 瘦身后还多重决定要不要拆 daemon)

实际 (5/10 鸿波 "现在就做, 不用管 demo"):
  5/10 凌晨/午:  BL-D6/BL-D2/D3 PG 统一 + 4 service _load_dotenv 修
  5/10 下午:     BL-ARCH1 P0 catfish-web 搭 (~2.5K 行 TS, NavBar/RoleGate/8 路由)
  5/10 傍晚:     BL-ARCH1 P1 完整用户管理 + sysadmin (~1.7K 行, 9 endpoints + 2 页面)
  5/10 夜:       BL-ARCH2 Companion 瘦身 (砍 7 卡 + WebPortalLink banner)
  → ARCH1+ARCH2 一天合计 ~4.5K 行 ship, 提前 21 天

5/14 demo: 中央 web + Companion 瘦身版双线展示 (sysadmin 用户管理 + 我的桌宠).
6 月+: BL-ARCH3 评估 (Companion 瘦身后还多重决定要不要拆 daemon)
```

### §N · 5/10 夜 a16z continual learning 启发 (H2 评估)

> 鸿波 5/10 夜读 a16z《Why We Need Continual Learning》(2026-04-22) 后定调:
> **"常变是这类企业、政府的常态"** — catfish 卖点不应停在"接 LLM",
> 而是"跟得上你们公司变化的伙伴". 三个延伸方向, 5/14 demo + 6 月数据反馈
> 后再评估启动顺序. 当前 ❄️ deferred, 不开 task 不分散收尾精力.

| ID | 项 | 状态 | 估时 | 客户痛感 | 优先级 |
|---|---|---|---|---|---|
| BL-Q3-FACT | **事实补丁系统** — 政策/标准变更 → diff → 标记受影响 skill → LLM 重写 → 走 SkillRevision 审批. 5/10 夜鸿波认可"真卖点", 设计文档 ship 见 [`docs/CATFISH-FACT-PATCH-DESIGN.md`](./CATFISH-FACT-PATCH-DESIGN.md) | ✏️ 设计完成 | 1-2 周 | ✅ 高 (季度更新) | **P0 候选** |
| BL-Q3-SWARM | skill 协调器 — 长 agent loop 拆多 skill 子 context, 互不污染 | ❄️ 观察 | 3-4 周 | ❌ 低 | 等数据 |
| BL-Q4-GRAPH | skill 之间 RAG — skill 引用其他 skill 输出 | ❄️ 观察 | 2-3 周 | ❌ 低 | 锦上添花 |

#### BL-Q3-FACT 设计草稿 (鸿波 5/10 夜认可的"真卖点")

**问题域**:
- 央企/政府每季度有政策/标准/流程变更 (ISO27001 新版本 / 国资委新规 / 财务制度调整)
- skill 是某时间点的"上次做法"固化 → 政策一变 skill 输出建议过时 → 客户信任崩
- 通用 LLM 也帮不上忙 (训练 cutoff + 不知道客户内部新规)
- catfish 当前没解, **是 BL-MM13/14/15 skill_revision 体系最自然的下一步**

**解题思路** (复用 BL-MM13/14/15 已有基础设施):
1. **触发**: IT / 合规人员上传"变更说明"文件 (PDF / Word / 飞书纪要), 或者
   定期扫公司知识库 / 政策门户 (BL-D3 mcp 接公司内部系统)
2. **diff**: LLM 比对新文件 vs 已有 skill 中的"假设" (e.g. skill_run_briefing
   假设"周报按 X 模板", 新规改成 Y 模板)
3. **标记受影响 skill**: 用 SkillsHub 元数据 (subscribe_count + 内容 grep + LLM 语义匹配)
   找出引用了旧假设的 skill, 标 "可能受新规影响"
4. **LLM 重写候选**: 对每个受影响 skill, 自动生成改进 patch
   (catfish_propose_skill_revision 现成工具 + 政策上下文 prompt)
5. **走审批流**: 进 SkillRevisionCard / catfish-web /admin 待审, 合规审查通过后落盘
6. **追踪有效性**: BL-MM15 14 天监测分数, 没提升回退

**P0 范围 (1-2 周, 复用现有)**:
- IT 手动上传"变更说明"触发 (不做自动扫)
- 单 skill diff (不做跨 skill 依赖)
- 走现有 SkillRevision 审批流, 不另起 UI

**P1 范围 (Q4 +)**:
- 定期扫公司知识库 (mcp)
- 跨 skill 依赖图 (BL-Q4-GRAPH 联动)
- 合规专属审批 workflow (跟 sysadmin 用户管理同款)

**5/14 demo 故事线**:
> 客户问 "那以后 ISO27001 改了 / 我们流程变了, skill 不就过时了吗?"
> 答 "对, 这是通用 LLM 永远解不了的问题 — 它的训练数据停在某个时间点.
>      catfish 不一样, 我们 Q3 启动事实补丁系统: 您的合规部门把变更说明
>      给鲶鱼一看, 它会标出所有可能受影响的 skill, 自动生成改进版给您审批.
>      这套机制其实今天的 skill_revision 已经在跑 (演示 LearningCard
>      pending → accept → 14 天追踪) — Q3 加上"政策触发"那个入口就完整了."

**为啥这是真护城河**:
- 文章 a16z 论点: "weights 不让自动学是因为合规边界 — 一旦自动学就丧失 auditability"
- catfish 不动 weights, 改 skill 文件 — **白盒 + 全程可审 + 走人工审批节点**
- 通用 LLM (ChatGPT / Claude / 国内闭源) 都做不到这个, 因为他们没"member 公司"概念
- 这是 catfish "白盒 continual learning" 在央企场景的杀手级落地

---

---

## 维护规则

1. **每周一早上**: 看一眼这份 backlog, 挑 5-10 项进 task tracker 做下周 sprint
2. **完成的不删**: 改 status = ✅ + 加 task # / commit hash, 留作历史 (软著申报 + 项目 review)
3. **新想法加到末尾**: 不需要立刻拆细, 按 section 归类即可, 后续 review 时拆
4. **每天收工写 CHANGELOG 时**: ★ 重要 — 同步把当天 ship 的事在 BACKLOG 里标 ✅ (防再漂)
5. **状态约定**:
   - ⬜ 未开始
   - 🔵 进行中 (在 task tracker 里)
   - ✅ 已完成
   - 🚧 阻塞 (等外部 / 等决策)
   - ❄️ 暂不做 (有意识 deprioritize, 不忘记)
   - 💀 已废弃 (踩坑后决定不做了, 保留历史)
6. **每月**: 整体 review 一次, 重新评估优先级 — 战略可能变, backlog 也跟着调
7. **修这份文档**: 改完 commit message 写 `docs(backlog): <一句话原因>`, 在 git log 留下变更轨迹

---

## 跟其他文档的关系

```
STRATEGY.md            ─→  战略拍板 (3 个月一动)        ─→  填充 BACKLOG.md A 段
POSITIONING.md         ─→  产品定位 one-pager           ─→  填充 BACKLOG.md B 段 (GTM)
COMPETITIVE-DIFF.md    ─→  应对"跟 Hermes / OpenClaw 同质化" ─→  对外口径统一
SKILL-LIFECYCLE.md     ─→  Skill 5 阶段框架 (元文档)         ─→  指导 SOUL/policy/tool 设计
AUTH-DESIGN.md         ─→  SSO 落地路径 (BL-D6)              ─→  实施前 6 决策点必须拍板
IDEAS.md               ─→  长尾创意池 (随时加)               ─→  填充 BACKLOG.md E 段
TOMORROW.md            ─→  当周 sprint 计划 (每周写)         ─→  从 BACKLOG.md C/D 段挑出来
ROADMAP.md             ─→  4 Phase 客户视角 (粗块)           ←─  BACKLOG.md K (已 ship) 决定 Phase 边界
CAPABILITY-MATRIX.md   ─→  现状能力快照 (✅ 项 + 代码位置)   ←─  BACKLOG.md K + Phase 1 完成项
CHANGELOG.md           ─→  完成的事 (每天补)                 ←─  BACKLOG.md ✅ 项的归宿
Cowork tasks           ─→  当前 sprint 跟踪 (实时)           ←─  从 BACKLOG.md 挑出来
BACKLOG.md (本)        ─→  全量积压 (每周 review)            →   汇总以上所有
```

---

## 决策签名

> v1 = 2026-04-27 全量 backlog 快照 (121 项, 121 ⬜).
> **v2 = 2026-04-30 升级**: 修 v1 漂移问题, 回写 4-28~30 ship 30 项, 新增 25 项缺口, 总计 186 项. 已 ship ~35 项 (✅).
> **v3 = 2026-05-10 凌晨**: §M 加 BL-ARCH1/2/3 架构反思 (鸿波 4:00 challenge, 客户端瘦身 + 中央门户 web 化). 5/10 凌晨一夜 ship 18 件 (BL-FIX27~38 + 35.1 + D2 + D2 Phase 2 + D3 fix5 + 架构决策). 已 ship ~80 项, 4 个中央 service PG 统一收尾.
>
> 后续按"维护规则" §4 持续更新 — **每天收工时回写 ✅, 严禁再漂**.
> 重大优先级调整 (例如 P3 提到 P1 / 整段砍掉) 在 git log commit message 里写明原因.
- BL-P10-GATEWAY-HOSTS-ENV (6/2 22:50 audit 发现): plugin.py P10 hard-code
       localhost:8999 + 127.0.0.1:8999, 但 5/29 直 patch hermes 时真用
       CATFISH_GATEWAY_HOSTS env 灵活配置. 真客户机房部署用内网 IP 时, P10
       不会识别 catfish-gateway → X-Catfish-User 不注入 → 撞 400. 加 env 回来.

## BL-MEMORY-OPTIMIZE-2026-06-02 (鸿波 6/2 23:30 audit → 6/3 真做完)

真 audit catfish 记忆系统真痛点 (扫真生产 USER.md/MEMORY.md 真数据):

### P0 真该 ship
- ~~A1 prefetch 5 数据源迁 catfish-xcatfish-user (激活 14 天 dormant)~~ **真 audit 6/3: 前提错** —
       catfish-memory plugin 真在 hermes 8642 真 load 真 register, prefetch_all 真注入 user message
       末尾 (conversation_loop.py:914-925 真`<memory-context>` fence), 真不 dormant. 6/2 凌晨
       "14 天 dormant" 真盲判断. 真生产 dump 真验 4/5 数据源真在跑 (discipline +
       session_meta + distilled_facts + feedback). 缺 skills_catalog (~/.catfish/skills/ 真无
       目录) — BL-MEMORY-SKILLS-CATALOG-EMPTY 后续修.
- **✓ A2 历史 entry LLM reclassify** (6/3 真做完) —
       真扫 MEMORY 16 entry / USER 10 entry. 真砍 MEMORY 91% (7551→688 chars, 16→2),
       12 条 skill spec 真 append 到 catfish-browser-task / catfish-project-approval /
       catfish-weekly-report 4 个 SKILL.md, 1 条 (contextual status update) 移 USER.md,
       4 条 meta/重复真删. 真 backup .bak.20260603-AUDIT.
- **✓ A3 catfish_memory_dedupe 改语义级 + Dashboard 按钮** (6/3 真做完) —
       memory_dedupe 真升级: Jaccard 0.4 prefilter → LLM gateway 真语义判定 (same/related/
       different + is_workflow_or_spec). _llm_dedupe_judge 真复用 _expertise_llm_call pattern.
       真接 ~/.catfish/memory_audit.jsonl audit log. propose_skill_hints 真返让 LLM 调
       catfish_propose_skill. CATFISH_DEDUPE_LLM_ENABLE 默认开, fallback Jaccard.
       HermesMemoryCard 真加"🧹 整理记忆" 按钮 + inline suggestions section + 应用按钮.

### P1
- ~~B1 砍老 memory 入口 (catfish_remember/memory_save), 单一 5 kind router~~ **真 audit 6/3**:
       6/2 23:29 catfish-memory plugin register_tool override=True 真已生效, hermes builtin
       memory tool 真被 5 kind router 取代. 老 catfish_remember 真 audit: 还在 tool 列表? 后续 audit.
- **✓ B2 Dashboard memory review 卡** (6/3 真做完) — 真升级 HermesMemoryCard 加 dedupe
       按钮 + suggestions UI, 不另起新卡 (真已有 MemoryHistoryCard/AuditCard 真展示历史).
- **✓ B3 LLM detect 细节冗长 → 自动 propose_skill** (6/3 真做完) — 真两路径:
       Reactive (A3 LLM dedupe 返 propose_skill_hints) +
       Proactive (memory_router add 真静态 heuristic _detect_workflow_spec_pattern 真检
       >= 500 chars 或 (>= 200 + 2 关键词) → inject propose_skill_hint, 0 LLM 调).

### P2
- ✓ **memory char limit 调高** (5/27 之前真已做) — ~/.hermes/config.yaml
       user_char_limit=3500 / memory_char_limit=5000 (default 1375/2200).
- ✓ **query 相关性筛 entry 注入** (6/3 真做完, BL-MEMORY-P2-2-APPLY) —
       catfish_memory.py _render_skills_catalog 加 query 参数 + char-level Jaccard
       (name_score*3 + head_score) 真排序. query 真有 budget cap 5000 chars (砍 75%).
       query 真空兼容旧行为 (全 67 skill 注入 20K). 不依赖 jieba (plugin light), 不调 LLM.
       smoke 真验: query='周报' 真 catfish-weekly-report 真排第 1.
- ~~周报 cron 摘要 journal~~ **真撤回 (鸿波 6/3 拍)** — 鸿波 verbatim
       "周报还是我每周手动提出, 然后调用周报SKILL 的方式". 真符合**员工主权红线**:
       周报真员工亲自触发, 真不自动. 真聚合 journal 真"本周周报草稿" 真也无意义
       (真只为周报 skill 服务, 周报手动后真聚合无价值). 真避免未来 sprint 真重提.
- ✓ **BL-MEMORY-SKILLS-CATALOG-EMPTY** (6/3 真做完, BL-MEMORY-P2-4-APPLY) —
       _render_skills_catalog dual-path: ~/.catfish/skills/ 优先 → ~/.hermes/skills/
       fallback. 支持两层结构 (skill 直接含 SKILL.md 或 category/skill). dedupe by
       skill 名. smoke 真验 fallback 真扫到 67 skill (29 hermes + RecMode + symlink).

### 24h 验证
- ~/.catfish/memory_audit.jsonl 真生效: source_tool=memory(catfish-router) + 新加
       source_tool=catfish_memory_dedupe 真有记录
- HermesMemoryCard "🧹 整理记忆" 按钮员工真用, LLM 真返 verdict=same/related 真比例
- LLM 真按 propose_skill_hints 真主动调 catfish_propose_skill (LearningCard 真有新增 proposed)
- prefetch user message 末尾 fence size 真 (~7K → ~3K chars, A2 真已生效)

## BL-CI-TOOLBRIDGE-TESTS-DEBT (6/3 留)

CI tool-bridge job 跑 844 tests, 760 pass / 18 fail. fail 真 3 类历史债:

1. **缺 python-docx**: test_run_skill (5 个) 真调 leadership-briefing skill script.py,
   script.py import docx (python-docx). CI install 没装. 修: ci.yml tool-bridge install
   加 'python-docx'.

2. **screenshot 测试真 macOS-only**: test_screenshot (5 个) 期望 macOS 路径,
   GitHub Actions ubuntu runner 真返 '不支持平台 Linux'. 修: 加 @pytest.mark.skipif(sys.platform != 'darwin').

3. **测试 mock 真过期**: test_screenshot 真 7 个 AttributeError — catfish_tools.py
   重构后 _MAX_SCREENSHOT_BYTES / _import_playwright / _flatten_a11y 改名或删. 修:
   更新测试 mock 用真当前 API.

3 类都是 pre-existing 测试维护债, 跟员工功能 0 关系. 真不阻塞 ship.

## BL-CI-TASK-MANAGER-SYNC-RACE (6/3 留, CI 唯一遗留 fail)

tests/test_task_manager.py::test_submit_typed_task_from_sync_caller — `'running' != 'completed'`.

5s deadline 等 `print('hello sync')` 完成, CI 上 5s 还 'running'. 真根因待审:
  - submit_typed_task sync 入口真 spawn 后台 thread 时机问题?
  - 或测试 deadline 真太短 (Linux runner 慢)?
  - 或 task_manager._manager singleton 真在 test 间没 reset?

不阻塞今晚 ship — 跟 14 commit 真 0 关系. 修法:
  A. 调 deadline 5s → 30s (粗糙)
  B. audit submit_typed_task sync 入口真 race
推荐 B.

## BL-CATFISH-WIKI-MODE (6/3 留, 长期 sprint, 借鉴 Karpathy LLM Wiki + nashsu/llm_wiki)

借鉴对象:
- **Karpathy gist** (https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — 原始抽象 pattern
- **nashsu/llm_wiki** (https://github.com/nashsu/llm_wiki) — Tauri+React+sigma.js 完整实现

核心思想: LLM 不要每次 RAG re-derive, 而是**增量建+维护持久 wiki** (结构化 markdown 文件 +
wikilinks + frontmatter). 知识编译一次, 持续 current, 不是每查再生.

跟 catfish 现状对照:

| Karpathy/llm_wiki | catfish 现状 | gap |
|---|---|---|
| 3 层: Raw / Wiki / Schema | journal+distilled = Wiki 雏形, USER/MEMORY 散页, 无 Raw | 缺 Raw + 缺正规 Wiki 结构 |
| Ingest 两步 (Analysis+Generation) | sync_turn → distill 单步 | 缺 Analysis 阶段 + 输出无 frontmatter |
| Query (答案 file 回 wiki) | chat 真消失除非 explicit memory_update | 缺自动沉淀 |
| Lint 定期 health check | hm 脚本反向治理 (员工手扫) | 缺 contradiction/stale/orphan 检测 |
| index.md content catalog | 无 | 缺 |
| log.md chronological parseable | journal 有但格式不严 | 改格式 `## [YYYY-MM-DD] kind \| title` |
| wikilinks + 图谱 | 无 cross-reference | 缺 |
| Obsidian 兼容 vault | 散在 ~/.catfish/ 各处 | catfish_home 改 vault 结构员工就能用 Obsidian 看 |
| Schema 注入 system prompt | _render_memory_discipline + _render_safety_redline 同精神 | 缺正规 schema + purpose |

catfish 领先 llm_wiki:
- 执行能力 (skill / 浏览器 / 邮件)
- 跨员工 skill 共享 + RecMode
- 审批 sandbox + 安全红线
- Companion Dashboard 多 tab
- 同 stack (Tauri 2.11 + React 19)

### P0 — 立即可做, 1 commit ~30 行, 立竿见影

**P0.1 _render_purpose 注入** (catfish-memory plugin)
- 加 _render_purpose 跟 _render_memory_discipline 同套路, 注入 user message 末尾
- 内容鸿波亲自写一遍 (员工是谁: 资质管理负责人 / 服务什么场景: 合规审核/ISO/客户调研)
- 让 LLM 每轮看到员工身份+目的, 减跑偏

**P0.2 _render_schema 注入** (catfish-memory plugin)
- 把 5 kind router 规则 + journal/distilled 分工 + memory cap 显式写出来
- 跟 P0.1 同位置注入
- 借鉴 llm_wiki "Project Schema and Routing (AUTHORITATIVE)" 标记

**P0.3 journal log 格式严格化**
- 改 journal append 格式: `## [YYYY-MM-DD] kind | title` 一行
- catfish-tool-bridge 真 journal_append 写时按这格式
- 改完后 `grep "^## \[" employee_journal.md | tail -5` 拉最近 5 条
- LLM prefetch journal 时也按这格式提取最近 N 条

### P1 — 下周 sprint, 3-5 天

**P1.1 拆 _call_distill_llm 为两步**
- Step 1 Analysis (entity/concept/contradiction/recommendations 结构化)
- Step 2 Generation (输出 frontmatter 真 wiki page)
- 借鉴 llm_wiki buildAnalysisPrompt + buildGenerationPrompt
- 输出位置: ~/.catfish/wiki/entities/ + ~/.catfish/wiki/concepts/

**P1.2 Query-as-Source (Karpathy 第 5 条)**
- 员工真在 chat 问完, Companion 加按钮 "存进 wiki"
- 点了, LLM 把这轮 Q&A 写成 wiki/queries/<日期>-<主题>.md (带 frontmatter sources: [chat])
- 自动 ingest 抽 entity/concept (走 P1.1 pipeline)
- 高价值: 员工真问过的不丢, 沉淀进知识体系

**P1.3 Obsidian 兼容**
- 改 ~/.catfish/ 真 vault 结构:
  - `.obsidian/` 自动生成 (推荐 settings + plugins)
  - `raw/sources/` — 员工导入文件 (PDF/邮件附件等), immutable
  - `wiki/` — LLM 生成 (entities/concepts/sources/queries/log.md/index.md)
  - 保留 `~/.catfish/output/` (skill 真自动生成文件)
- 员工真用 Obsidian 打开 ~/.catfish/ 就是个 vault

### P2 — 2-3 周

**P2.1 catfish_memory_lint tool**
- 借鉴 llm_wiki runSemanticLint
- LLM tool 输入: 全 MEMORY/USER/distilled, 输出 contradiction/stale/missing-page/suggestion
- Dashboard 加 "记忆体检" 卡, 员工每周点一次
- 触发: 手动 (Phase 1) → scheduled (Phase 2)

**P2.2 Structural lint** (员工自己跑, 不调 LLM)
- 借鉴 llm_wiki runStructuralLint
- 扫 wiki/**/*.md 抽 [[wikilinks]], 找 orphan/broken-link/no-outlinks
- 跟 hm 脚本风格一致 (员工跑 catfish-cli 命令), 0 LLM 调用

### P3 — 1-2 月

**P3.1 CJK bigram tokenization**
- 改 _query_token_set 从 char-level set 升 bigram (每个 → [每, 个, 每个])
- 1 行修, 立刻改善 skills_catalog 真 query 匹配

**P3.2 4 信号 relevance**
- direct link (×3) + source overlap (×4) + Adamic-Adar (×1.5) + type affinity (×1)
- 前置依赖: P1.1 出 frontmatter sources/type, P1.3 出 vault 结构
- 替换 _render_skills_catalog 真 char-Jaccard
- 也用作 query-time wiki 页排序 (P2 用)

**P3.3 Dashboard wiki tab + sigma.js 知识图谱**
- Companion 加 "🧠 知识体系" tab
- 左边 wiki 树, 中间页面 preview, 右边图谱 (sigma.js + graphology)
- 借鉴 llm_wiki 3 列布局
- 员工真看到自己资质管理领域真完整知识图

### P3.3 三级拆分 (6/4 鸿波拍 — 商用必须open 即用 不装 Obsidian)

6/4 实测 Obsidian 打开 ~/.catfish/ vault graph view 完美渲染, 真file explorer noise 真Obsidian 设计**没原生 hide. 商用不能让员工装 Obsidian + 配置 File Hider plugin, 必须 catfish Dashboard 自己 ship.

**P3.3a Level 1 — Dashboard summary card (1 天)**
- "仪表盘" tab 内加 `🧠 知识体系` card
- counts (N entities / M concepts / K queries) + 最近 3 个 distill timeline + Top 5 hub entity (按 related count)
- 点 card → open Finder 跳 ~/.catfish/wiki/
- read-only summary, 入门版

**P3.3b Level 2 — 独立 tab (2-3 天)**
- 顶部 tab bar 加 `🧠 知识体系` (并列 早安 / 工作台 / 邮件 / 仪表盘)
- 3 列布局:
  - 左: tree (entities / concepts / queries 3 group + 搜索 box)
  - 中: 选中 file 真 Markdown render (含 `[[wikilink]]` clickable 跳转)
  - 右: sigma.js + graphology graph view (跟 Obsidian 同 capability)
- 数据: 前端 Tauri fs API 直接 read `~/.catfish/wiki/*.md` + gray-matter parse frontmatter + regex 抽 wikilink edge
- 依赖已就绪: package.json Tauri 2.11 + React 19 + sigma + graphology 全 install (5/29 audit llm_wiki 时确认)

**P3.3c Level 3 — 商业 polish (1-2 周)**
- filter + 搜索 (tag / entity_type / concept_type / 最近 N 天)
- `+ 新建 entity / concept` button (员工手动加 — LLM 抽不全的补)
- dataview 类预制 query 模板: "本周新增 entity" / "孤立 concept" / "Top 10 trending tag"
- broken link 检测 (依赖 P2.2 structural lint) + 红色提示 + 修复建议
- (长期 P4) 团队协作 — wiki 多员工 share (本地 own + 可选 publish 团队)

### catfish 商业差异化 (vs Obsidian / Notion / ChatGPT)

| | catfish (商用版 P3.3 后) | Obsidian | Notion/feishu | ChatGPT UI |
|---|---|---|---|---|
| open 即用 | ✓ Companion 启动即 vault | ✗ 自己装 + 配置 | ✓ 但中心存 | ✓ 但无 vault |
| 全本地 | ✓ | ✓ | ✗ | ✗ |
| LLM 自动生 wiki | ✓ chat → ingest | ✗ 手动写 | ✗ | ✗ |
| 中央 0 红线 | ✓ | ✓ | ✗ | ✗ |
| 长期记忆累积 | ✓ entities + concepts + queries | ✗ | ✗ | ✗ |
| markdown 自由迁移 | ✓ Obsidian 兼容 vault | ✓ | ✗ proprietary | ✗ |
| 团队协作 (未来) | ✓ 本地 own + 可选 publish | 第三方 plugin | ✓ | ✗ |

### 风险 + 取舍

1. **LLM 调用真贵**: P1.1 两步 ingest 真每 source 多 1 次 LLM call, P2.1 lint 每周 1 次大 prompt.
   token 预算压力. 配 CATFISH_WIKI_ENABLE env 真默认关, 员工拍才开.

2. **vault 结构变更**: P1.3 改 ~/.catfish/ 真目录, 需 migration 脚本. backup + dry-run 必须.

3. **跟 catfish 现有 memory 体系并存**: MEMORY.md / USER.md / journal / distilled_facts 都保留,
   wiki 是 superset. memory_router 5 kind 真不动. 真长期看是 wiki/entities/ 真吃掉
   USER.md, wiki/concepts/ 真吃掉 MEMORY.md, 但**渐进迁**.

4. **员工主权红线**: 数据 0 出端 ✓ (wiki 都本地). 真 Obsidian 兼容真让员工直接用熟悉
   工具看, 真不强 lock 在 Companion. 真符合 catfish 价值观.

### 24h 验证 (P0 真 ship 后)

- prefetch user message 末尾真出现 "## 🎯 员工身份与目的 (purpose)" 段
- 也出现 "## 📐 catfish memory schema (规则)" 段
- LLM 真自我介绍时真说自己是 "鸿波真资质管理副手" 不是 generic "AI 助手"
- journal 真 grep "^## \[" 真拉得到最近 5 条 ingest/query/lint 记录

### P0 ship 真根因排查 (6/4 凌晨)

P0 commit 8075d2e ship 6/3 晚 + hermes 重启, 但 6/4 凌晨验真 4 marker 全 ✗.
逐层 audit 暴露根因 — **catfish-memory plugin 真 5/29 hermes 0.15.2 升级后没补回 install**:

1. `~/.hermes/plugins/` 真只有 catfish-policy + catfish-xcatfish-user, **没 catfish-memory symlink**
2. `~/.hermes/config.yaml` 真 `memory:` 块 **没 `provider: catfish-memory` 字段**
3. 真 hermes `~/.hermes/hermes-agent/plugins/memory/__init__.py:308 _get_active_memory_provider` 读 `cfg_get(config, "memory", "provider")` — 没真返 None, 不装外部 provider
4. 所以 6/3 整天写真 _render_purpose / _render_schema / journal 格式严格化代码全**没生效** — 有改没装

修法 (6/4 08:30~08:40, 2 步):
```bash
# 1. 补 symlink
ln -s ~/person_task/catfish/edge/hermes-plugins/catfish-memory ~/.hermes/plugins/catfish-memory

# 2. config.yaml memory 块加
#   provider: catfish-memory
```
重启 hermes + Companion 新 chat → log 真 5 条 register/activate/initialize/sync_turn 全出现 + LLM 真自我介绍"我是小鲶, 你的副手" (跟之前 "鸿波, 在." generic 真不同) — P0 真真生效.

教训: hermes 升级要带 **plugin install 真 audit step** — `ls ~/.hermes/plugins/` + `grep provider ~/.hermes/config.yaml`. 版本升级 reset 真配置真 silent.

---

## BL-HERMES-UPGRADE-PLUGIN-AUDIT (6/4 凌晨发现, P1)

hermes 真版本升级 (5/29 真 0.15.2) reset / wipe 真:
1. `~/.hermes/plugins/` 真 user-installed symlink (catfish-memory 真没了)
2. `~/.hermes/config.yaml` 真 memory.provider 真字段 (5/19 真加 → 5/29 升级没)

后果: 真 plugin silent 真不装载, 真 6/3 整天**改代码没生效**, 6/4 凌晨真才查出.

**修法**:
- catfish 真 hermes 升级 script (`scripts/upgrade-hermes.sh` 或类似) 加 **pre-upgrade snapshot** + **post-upgrade restore**:
  - 保存 `~/.hermes/plugins/` 真所有 symlink 真 target
  - 保存 `~/.hermes/config.yaml` 真 `memory.provider / plugins.enabled / mcp_servers` 字段
  - 升级完自动 restore + 真`hermes plugin verify` 真验
- 加 `catfish doctor` CLI 命令 — 每周一自动跑, 真扫 `~/.hermes/plugins/` 真 vs catfish 真期望 plugin 列表 (catfish-memory / catfish-xcatfish-user / catfish-policy), 不齐**红色提醒**

risk: catfish-policy 也在 `disabled` 列表里, 6/4 凌晨没动 — 真要追这个 plugin 真什么时候 真为什么真 disabled.

---

## BL-CATFISH-MEMORY-SUMMARIZE-UPSTREAM-502 (6/4 凌晨发现, P2)

log 第 1 次 chat (session=20260604_084022_0f4973, 真 "你好" pairs=10 触发 sync_turn) 真:
```
WARNING catfish.memory: catfish-memory summarize: HTTP 502
  litellm.InternalServerError: OpenAIException - Connection error
INFO catfish.memory.plugin: catfish-memory bg session=...: LLM 总结返空, 跳过 (不写 journal)
```

bg session 真总结调catfish gateway 8999 真上游 (litellm 真 OpenAI provider) connection error. 真直接后果: 这次 chat 真没进 journal.

**修法**:
- catfish-memory plugin sync_turn 重试 retry-with-backoff 真 (2 次 retry, 指数 backoff 2s / 8s)
- 重试都失败 — 真 buffer 真留到下次 sync_turn, 不silent drop.
- buffer 真有 cap (e.g. 50 pairs), 防内存涨

根因catfish gateway 上游 instability (litellm 接 OpenAI 失败 / rate limit) — 长期看 catfish-public-deepseek-flash 真 catfish gateway failover 真补救 (BL-CATFISH-GATEWAY-FAILOVER, 别真 BL 真已经有).

---

## BL-HERMES-SYSTEM-PROMPT-PERSIST-BROKEN (6/4 凌晨发现, P2)

log 真:
```
WARNING agent.conversation_loop: Stored system prompt for session 20260604_084022_0f4973 is null;
  rebuilding from scratch this turn. Prefix cache will miss until the rebuild persists.
  Investigate the previous turn's update_system_prompt write path.
```

每 chat session 第一 turn system_prompt persist 没写 → **下一 turn rebuild from scratch** → **prefix cache miss** → 每 turn 真重新 prefix encode 29K+ tokens.

后果:
- prompt caching 99% → 0% 第一 turn miss
- 真长期看 真 token cost 大涨, latency 慢
- 真 hermes 0.15.2 升级真新引入 bug 或catfish-xcatfish-user 真 monkey-patch 踩到 update_system_prompt 真写路径

**audit**:
- 真 hermes `agent.conversation_loop._update_system_prompt` (路径自己找) 真写 path
- 真 catfish-xcatfish-user 真 11 个 patch 里有没真 patch 真 system_prompt 写真
- reproduce — 真新开 session 第 2 turn log grep "Stored system prompt is null"

阻塞依赖: 5/29 升级真 11 patch 真完整 audit (BL-HERMES-0152-PATCH-AUDIT 有的话已有).

---

## BL-DUMP-FILE-NAMING-INCONSISTENT (6/4 凌晨发现, P3)

真 hermes session dump 真两种文件名 pattern, 鸿波 6/4 凌晨 grep 找错文件误判 P0 没生效:

1. `request_dump_<session_id>_<ts>.json` (e.g. `request_dump_20260604_071823_...json`) — skill_curator 后台 LLM call 真 dump, **5940 chars 固定** (curator prompt), **不含** catfish-memory marker
2. `request_dump_api-<hash>_<ts>.json` (e.g. `request_dump_api-3832a9e072a2f450_...json`) — 员工 chat 真 dump, **12K+ chars 含真 timeline / employee profile / marker

真问题**:
- naming 真不区分 真 caller (curator vs employee chat vs background task), grep 验证 真踩坑
- 真 6/4 08:40 chat session `20260604_084022_0f4973` 真 **api- dump 真没生成** — dump 真 sample / conditional 真写真, 不每次写

**改法**:
- dump filename 真加 source tag: `request_dump_<source>_<session>_<ts>.json` (source = `chat` / `curator` / `bg`)
- 或者目录分 `~/.hermes/sessions/dumps/chat/` / `dumps/curator/` / `dumps/bg/`
- 真 verification 专用 CLI 真 `hermes session dump latest --type chat` 直接拉最新员工 chat dump

risk 低 — hermes 真 upstream, catfish monkey-patch 要慎重. 优先catfish 自己写真验证 CLI, 真绕过 dump 文件命名问题.

---

## BL-CATFISH-WIKI-MODE P1.1 + P1.1.1 ship 记录 (6/4 下午)

P1.1 = 拆 distill 两步生 wiki/entities/ + wiki/concepts/
P1.1.1 = 6 fix prompt + max_tokens + timeout + unicode slug

### Ship 历程 (9 commit)

| commit | 内容 |
|---|---|
| c5059de | P1.1 wiki two-step ship — Analysis + Generation + ---FILE: sentinel parser + ~/.catfish/wiki/ 写盘 (327 行) |
| fe45ae0 | P1.1.1 prompt 3 fix: catfish skip rule / concepts 先 entities 后 / related YAML 双引号 |
| 12785f9 | debug: 加 raw LLM 输出 dump (last_wiki_analysis.txt + last_wiki_generation.txt) |
| cf19df8 | max_tokens 7000 → 4096 (catfish-private-main output cap) + 异常 log 加 `[Type]: repr` hint |
| b8c5b89 | _GENERATION_HTTP_TIMEOUT = 180s (60s ReadTimeout 实测, LLM 生 4096 tokens 真长) |
| 27b9639 | _WIKI_PATH_PATTERN 加 re.UNICODE — slug 接受中文 (`资质申报流程.md` / `中电.md`) |

### 实测 13:12 输出

```
✓ wiki ship 5 entities + 5 concepts
journal: ## [2026-06-04 13:12] distill | 5 entities, 5 concepts
```

- entities (5, 全工作业务, catfish skip ✓): FFCS数字鲶鱼 / ISO22301 / 北京福富高新 / 鸿波 / 中电
- concepts (5, P1.1.1 强化生效): 文档交付标准 / 周报生成规范 / 资质申报流程 / 资质通报模板 / 资质优先级管理
- related YAML: `["[[资质申报流程]]", "[[资质优先级管理]]", ...]` 双引号 string list 兼容 Obsidian + YAML
- unicode slug 写盘 OK (中文文件名)

### 教训 (P1.1.1 6 个 fix 顺序)

1. **prompt example 真 LLM 跟得太死** — `<slug>` placeholder 第一次跑 LLM 填真, 但 polish 后LLM 真保留 example literal** parse 0 file. → 加 raw dump 真 debug 真最快定位.
2. **`str(e)` 真空** catch 异常时 type hint 真必加 — `[%s]: %r` 直接显示 `ReadTimeout / RemoteProtocolError / etc` 根因 直接知.
3. **max_tokens 真模型 真 cap 真 model-specific** — catfish-private-main 4096 真cap, 7000 直接抛 `httpx.ReadTimeout` (gateway 等 generate 真超时). 未来 plugin 真真真真 model metadata 真 cap detection** 自动 cap_max_tokens 真 helpful.
4. **timeout 60s 真LLM 生 4096 tokens 真不够** (~90s+). generation 真单独 180s 真 robust.
5. **path 白名单 ASCII-only 真踩坑** — LLM 中文 slug 真自然用, 白名单真要 Unicode. `re.UNICODE` 真 `\w` 真简单**真.
6. **catfish/AI 工具 skip rule 真入 Analysis prompt 最早一段 — 优先级最高, LLM 真严守真.

### 24h 观察期 + cleanup

明天 8:40+ (P0 ship 24h 满):
- 删 plugin prefetch 末尾 diag log (line 328-345 真 `logger.info("catfish-memory prefetch: returning...")`)
- 删 `_call_analysis_llm` / `_call_generation_llm` 真 `last_wiki_*.txt` raw dump
- catfish-doctor.sh 追加 wiki health check — `ls wiki/concepts/ | wc -l` >= 3 `ls wiki/entities/ | wc -l` >= 3 真验

### 下一步 P1.2 Query-as-Source

- Companion chat UI 加按钮 "💾 存进 wiki"
- 点了, 真 LLM 把这轮 Q&A 写 `~/.catfish/wiki/queries/<日期>-<主题>.md`
- frontmatter: `sources: [chat]`, `related: [<auto-抽 entity/concept>]`
- 自动走 P1.1 真 Analysis + Generation pipeline 抽 entity/concept
- 估 3-5 天 (前端 button + 后端 plugin pipeline)

---

## BL-BRIEFING-TWO-COL (6/10 ship, P3.3.6-11) — 早安 tab 从 read-only 卡片到可操作工作区

**rationale**: P3.3.6 之前 BriefingTab 只显 advisor 卡片 (主菜 + 3 个建议口径 + draft 草稿 link), 员工没办法在这里"做事" — 想跟 AI 聊任务进度得切到工作台 chat tab. 改造目标: 让早安 tab 自身成为完整工作区, 选 task → 跟 AI 直接说 → AI 能调 tool (起草邮件 / 跑数 / 写报告) → 状态记录, 一站式.

### 子 phase ship 记录

| phase | ship | 说明 |
|---|---|---|
| **P3.3.6** | 6/10 | 左右两栏布局 — sidebar 按 urgency 分组, detail pane 独立滚动 |
| **P3.3.7 Phase 1** | 6/10 | Detail pane 砍静态段, 改 in-memory task chat (system prompt 注入 task 上下文) |
| **P3.3.7 Phase 2** | 6/10 | chat 持久化 ~/.catfish/task_chat/<key>.jsonl (append-only) |
| **P3.3.8** | 6/10 | 早安天气 wttr.in + IP 自动 + 多城市 + 6h cache (BriefingCard 头部) |
| **P3.3.9** | 6/10 | task_uid stable key — LLM 重写 title 也不丢 chat. advisor 注入 prev_tasks 让 LLM 复用旧 uid |
| **P3.3.10** | 6/10 | task chat 升到工作台同款 — useTaskChat hook + ensureTools + ChatToolCall + approval banner |
| **P3.3.11** | 6/10 | jsonl schema 扩 tool_calls / tool_call_id, 重启后 tool 卡片完整还原 |

### 关键文件

新建:
- `src/hooks/useTaskChat.ts` (~230 行) — per-task chat hook, 跟 useChat 解耦不绑 useChatStore
- `src-tauri/src/commands/weather.rs` — wttr.in 拉 + 6h cache + 多城市
- `src-tauri/src/commands/task_chat.rs` — jsonl 持久化 (P3.3.7 Phase 2 起)
- `src/lib/weather.ts` / `src/lib/task_chat.ts` — TS wrapper
- `src/tabs/Briefing/components/BriefingTwoColumnView.tsx` — 两栏 view + DetailPane

改:
- `src/tabs/Briefing/AdvisorView.tsx` — 接 BriefingTwoColumnView
- `src/tabs/Dashboard/BriefingCard.tsx` — 加天气头部
- `src/lib/briefing_advisor.ts` — MainTask 加 taskUid, SYSTEM_PROMPT 加 task_uid 复用段, parseMainTask 兼容 task_uid/taskUid + fallback gen, _fetchBriefingAdvisorImpl 自拉 cache 注入 previousTasks

### 真坑日记

1. **task chat 一刷新就丢** (用户实测撞): P3.3.7 Phase 2 chat 用 sanitize(title) 当文件名, 但 LLM 每次 advisor 刷新会重写 title ("CSMM-4 评估撰写" → "CSMM-4 正式评估准备"), 老 jsonl 失配. 真根因 fix = P3.3.9 task_uid (LLM 给 6 字符稳定 uid, advisor 复用注入)

2. **天气拉失败** (用户实测撞): reqwest 默认无 gzip feature, wttr.in 返 gzip body → `error decoding response body`. Cargo.toml 加 `["gzip", "deflate", "brotli"]` feature

3. **天气死锁 6h 不重拉** (用户实测撞 v2): 第一次失败 cache 写了 entries=[] + error, 6h 内被判 fresh, 刷新按钮也救不了 (`weatherGet(false)`). fix: cache 里 `entries.is_empty() || error.is_some()` 视同 stale 强制重拉

4. **task chat markdown 不渲染** (用户实测撞): DetailPane ChatMsg 直接显 `{msg.content}`, `**xxx**` 当字面量. fix: 复用 `lib/markdown.tsx` 的 Markdown 组件 (跟工作台 ChatMessage 同款)

5. **tool 历史重启丢**: P3.3.10 ship 时 jsonl schema 只存 user/assistant text, tool_calls / tool result 在内存. 重启后 chat 文本在但 tool 卡片消失. P3.3.11 fix = schema 加 tool_calls / tool_call_id, load 时 join 回 assistant.tool_calls[i].result

6. **SYSTEM_PROMPT template literal 内 backtick 没 escape**: 加 P3.3.9 task_uid 段时 `# 上次 advisor 输出` 内 backtick 让 ts 把整个 SYSTEM_PROMPT 解析炸. fix: 换 `"` 引号

### 设计取舍

- **useTaskChat 不复用 useChat**: useChat 928 行强耦合 useChatStore 单例 store, 多实例会跟工作台 chat 串. 选 fork 一个轻量 (~230 行) 而不是改 useChat 支持 multi-instance — 后者动太多, 风险大
- **不持久化 system prompt 也不持久化 sessionId**: 每次 send 重算 system (task 上下文可能变), 也不进 state.db (task chat 用自己的 jsonl, 不混进工作台 session 历史)
- **approval banner 全局 event listener**: BriefingTab 跟工作台 ChatTab 是 activeTab 条件 render, 不同时 mount, listener 自动 cleanup, 不会双触发
- **老 jsonl 不迁移**: P3.3.9 后老 title-命名 jsonl 留盘上, DetailPane load 时 fallback 兜底读. 不主动迁移 (减少破窗风险), 让员工新对话写 uid jsonl 自然过渡

### 待办 (留 P3.3.12+)

- BriefingTwoColumnView 改 `__msg` css 让 `.markdown-body` 跟气泡背景协调 (assistant 气泡可能 over-padding)
- task_chat jsonl 没 prune 机制 — 一年后单 file 几 MB. 加 archive 老条目 / 截断 N 轮前
- approval banner pattern_key / command 字段 chat completions path 没填, banner 显空白. 跟工作台一样的体验缺漏, 不阻塞
- task chat tool 调用历史不进 advisor cache, advisor 下次 refresh 看不到 "员工跟 AI 在这条 task 上聊过什么" — context drift 风险
- 多 task 并发 chat (员工开 task A chat 中, 切到 task B 再 chat) — 当前 useTaskChat 实例随 DetailPane unmount, A 的 stream 会被打断. 需 stream registry 化 (跟 useChat BL-MULTI-SESSION-STREAM 5/24 同款)
