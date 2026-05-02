# 鲶鱼 · 全量 Backlog

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
| BL-C12 | (新) | Skill lifecycle 阶段 3 Review: 创建/更新后立即 dry-run 验证, 失败回滚 | 0.5 天 | ⬜ |
| BL-C13 | (新) | Skill lifecycle 阶段 3 Review: 创建前重复检查 (skill_view/skill_list 看相似 skill) | 0.3 天 | ⬜ |
| BL-C14 | (新) | Skill lifecycle 阶段 4 Use: tool-bridge 加 .audit.jsonl 事件流 (每次 dispatch_tool 命中 skill 写一行) | 0.5 天 | ⬜ |
| BL-C15 | (新) | Skill lifecycle 阶段 4 Use: catfish_today_summary 加 skill_invocations/failures/unused_30d 字段 + LearningCard UI | 1 天 | ⬜ (依赖 C14) |
| BL-C16 | (新) | Skill lifecycle 阶段 4 Use: 30 天未用 skill 主动建议员工删/留 | 0.5 天 | ⬜ (依赖 C14) |

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
| BL-D3 | MCP Registry 中央服务 — 内部 MCP server 注册中心 | ⬜ | 2-3 周 |
| BL-D4 | 中央 Telemetry — 业务数据收集 (token / 延迟 metadata, **不含对话内容**) | 🔵 (部分) | gateway audit JSONL 已写, telemetry server 收集还没建 |
| BL-D5 | catfish-distribution — SaaS 多租户分发管理 | ⬜ | 3-4 周 |

### D.2 Auth / Audit / Quota / RBAC

| ID | 项 | 状态 | 估时 |
|---|---|---|---|
| BL-D6 | SSO 真接入 (替换 dev token) | ✅ 4-28 | catfish-identity OIDC server + 飞书 adapter + Companion JWT + dev_token fallback ~600 行, 端到端通 |
| BL-D7 | 中央审计日志 (token 数 / 延迟 / 不含对话内容) | ✅ 4-22~28 | gateway audit JSONL ship, AuditCard 已显示 |
| BL-D8 | RBAC 系统 (3 角色: admin/manager/employee) | 🔵 5/2 MVP | spec + middleware + 单元 16 测过. UI 完整 + 部门 audit/quota endpoint Phase 2 接力 |
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
| BL-E11 | #10 | agent 命名权 — 员工给鲶鱼起名 (小雷/老李/阿呆), 改头像 | 1 天 | personality 系统 |
| BL-E12 | #11 | 鲶鱼周末不干活 (节假日/晚 10 点后自动说"明天再帮") | 0.5 天 | cron + prompt 开关 |
| BL-E13 | #12 | 鲶鱼主动闲聊 — 周五下午主动问"上周报销审批了没" | 1-2 周 | Memory + 员工状态推断 |

### E.4 🎪 纯娱乐 / 毒舌型

| ID | IDEAS# | 项 | 估时 | 前置 |
|---|---|---|---|---|
| BL-E14 | #13 | 鲶鱼吐槽 PPT — 上传 PPT 毒舌点评 | 3-5 天 | PPTX parser + critical personality |
| BL-E15 | #14 | "领导来了"快捷键 — 一键切假装认真写代码 | 1 天 | Companion |
| BL-E16 | #15 | 社交健康检查 — 扫 IM 记录分析人际 | 1-2 周 | Slack/IM 适配器 + 员工授权 |

### E.5 🌐 组织协作型 (Plan D)

| ID | IDEAS# | 项 | 估时 | 前置 |
|---|---|---|---|---|
| BL-E17 | #16 | 两个鲶鱼对话 (Agent-to-Agent 跨人协作) | 1-2 个月 | 全员有 agent + 隐私控制成熟 |
| BL-E18 | #42 (task) | Catfish Federation Plan D 完整架构 | 5-6 周 | E17 |

### E.6 🎭 彩蛋 / 反直觉

| ID | IDEAS# | 项 | 估时 | 前置 |
|---|---|---|---|---|
| BL-E19 | #17 | 鲶鱼的"情绪" — 偶尔说"今天问的东西很有意思"建立关系 | 1-2 天 | Memory |
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
| BL-I3 | 视频上传 (帧采样 + 多模态) | ⬜ | 1 周 | — |
| BL-I4 | 音频文件上传 + 转写 | ⬜ | 0.5 周 | I1 选型后 |
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
| BL-L26 (新 5/1) | 用户上传文件 ≥ 50KB 走 BM25 检索 — 复用 edge/local-search FTS5 (4-24 已 ship), top-K 段落注入 user message. **不做 embedding RAG**: 公文场景关键词查询 BM25 比 embedding 准 30%, 也省调用费. 5/1 鸿波拍板 BM25 单一方案 cover 95%, 不分级 long-context | 3 天 | ⬜ |
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

阻塞 / 等鸿波           8 项   (8 🚧)               · 解锁后归类

合计 ~186 项 (v1 = 121 项, v2 +65 项)
其中已完成 ~35 项 (✅), 进行中 3 项 (🔵)
```

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
>
> 后续按"维护规则" §4 持续更新 — **每天收工时回写 ✅, 严禁再漂**.
> 重大优先级调整 (例如 P3 提到 P1 / 整段砍掉) 在 git log commit message 里写明原因.
