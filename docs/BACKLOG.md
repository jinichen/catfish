# 鲶鱼 · 全量 Backlog

> **版本**: v1, 2026-04-27 创建
> **维护人**: 鸿波
> **跟 Task Tracker 的关系**: Task tracker (Cowork tasks #1-#64) 只装 sprint 内 (~1-2 周内做) 的事;
> **本 backlog 装全量** — 战略 / 商业 / 工程 P1-P3 / 运营 / 文档 / 法律 / 多模态扩展 全部.
>
> **维护规则**:
> 1. 每周一上午 review 一次, 决定下周提哪些进 task tracker
> 2. 完成的不删, 改 status = ✅ 留作历史 (审计 / 软著申报用)
> 3. 新想法直接加, 不需要立刻拆细 — 按 section 归类即可
> 4. 状态: ⬜ 未开始 / 🔵 进行中 / ✅ 已完成 / 🚧 阻塞 / ❄️ 暂不做 / 💀 已废弃

---

## 阻塞 / 等鸿波 (今天才能解锁)

| ID | 项 | 状态 | 阻塞 |
|---|---|---|---|
| BL-X1 | `ls -la ~/Library/Mail/` 给我看路径结构 | 🚧 | Mail.app adapter 开工前提 |
| BL-X2 | Companion Tauri 本机 `cargo build --release` 出新 .app | 🚧 | 验证 #50 / #58 / #62 真机生效 |
| BL-X3 | `grep provider ~/.hermes/config.yaml` 检查 LLM 之前自作主张加的 `provider:auto` 是否还在 | 🚧 | 在的话改回 `''` |
| BL-X4 | `catfish_browser_goto` Companion 真机验证 (新对话发"打开 https://www.sohu.com") | 🚧 | — |
| BL-X5 | SOUL 三件套真机生效验证 (反幻觉 / 系统操作 / Memory 三层触发) | 🚧 | — |
| BL-X6 | `open -e ~/.hermes/USER.md` 加"喜欢历史哲学"段 | 🚧 | — |

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
| BL-A12 | Phase 1: 测试覆盖到 70%+ (当前估 30-40%) | ⬜ | 1-2 周 | — |
| BL-A13 | Phase 1: docs.catfish.ai 域名 + 文档站 (mkdocs / docusaurus) | ⬜ | 1 周 | — |
| BL-A14 | Phase 2: 改 5 个开源 repo visibility public + HN/知乎/X 公告 | ⬜ | 0.5 周 | A9-A13 |
| BL-A15 | Phase 2 公告前法务咨询: 开源合规 / hermes-fork license / 算法备案 | ⬜ | 决策 + 1 周 | A14 |

---

## B · 业务 / GTM / 商业化

| ID | 项 | 状态 | 估时 |
|---|---|---|---|
| BL-B1 | catfish-cloud beta 内测平台搭建 (SaaS 多租户) | ⬜ | 4-6 周 |
| BL-B2 | 私有部署报价单: <50 人 / 50-500 / 500+ 三档 | ⬜ | 决策 1 天 |
| BL-B3 | 至少 3 个客户 demo 场景准备 (POC) | ⬜ | 1 周 |
| BL-B4 | GTM materials: 销售一页纸 / Demo 视频 / Sales deck | ⬜ | 1 周 |
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

| ID | Task # | 项 | 估时 | 阻塞 |
|---|---|---|---|---|
| BL-C1 | #35 | macOS Mail.app adapter | 2 天 | BL-X1 |
| BL-C6 | #41 | Companion macOS LaunchAgent 自启动 | 0.5 天 | — |
| BL-C7 | #32 | Plan C Week 4 polish (quota / RBAC / 自启动) | 1.5 天 | C6 |
| BL-C8 | (新) | Gateway 错误人话化 (429/timeout/401 → 员工友好文案) | 0.5 天 | — |
| BL-C9 | (新) | Companion 仪表盘加 tool-bridge 状态卡片 (autostart 起来了但 UI 没显示) | 0.3 天 | — |
| BL-C10 | (新) | catfish-public-qwen-flash upstream model 名字修正 (写 deepseek-v4-flash 实际是 Qwen3.6) | 0.1 天 | — |
| BL-C11 | (新) | tool-bridge config_watcher (cdp_url mtime 监听 → respawn, 比 chrome.rs 触发更全面) | 0.5 天 | — |
| BL-C12 | (新) | Skill lifecycle 阶段 3 Review: 创建/更新后立即 dry-run 验证, 失败回滚 | 0.5 天 | — |
| BL-C13 | (新) | Skill lifecycle 阶段 3 Review: 创建前重复检查 (skill_view/skill_list 看相似 skill) | 0.3 天 | — |
| BL-C14 | (新) | Skill lifecycle 阶段 4 Use: tool-bridge 加 .audit.jsonl 事件流 (每次 dispatch_tool 命中 skill 写一行) | 0.5 天 | — |
| BL-C15 | (新) | Skill lifecycle 阶段 4 Use: catfish_today_summary 加 skill_invocations/failures/unused_30d 字段 + LearningCard UI | 1 天 | C14 |
| BL-C16 | (新) | Skill lifecycle 阶段 4 Use: 30 天未用 skill 主动建议员工删/留 | 0.5 天 | C14 |

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
| BL-D4 | 中央 Telemetry — 业务数据收集 (token / 延迟 metadata, **不含对话内容**) | ⬜ | 2 周 |
| BL-D5 | catfish-distribution — SaaS 多租户分发管理 | ⬜ | 3-4 周 |

### D.2 Auth / Audit / Quota / RBAC

| ID | 项 | 状态 | 估时 |
|---|---|---|---|
| BL-D6 | SSO 真接入 (替换 dev token, 4-22 已列遗留) | ⬜ | 1 周 |
| BL-D7 | 中央审计日志 (token 数 / 延迟 / 不含对话内容) | ⬜ | 1 周 |
| BL-D8 | RBAC 系统 (#32 提到一部分) | ⬜ | 1 周 |
| BL-D9 | Quota 系统 (#32 提到一部分) | ⬜ | 1 周 |

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
| BL-E24 | #22 | 网关 Fallback 链 | ✅ | 已 #22 完成 (4-26) |
| BL-E25 | #23 | 三层统一搜索 (公网 / 公司内 / 本地) — 智能路由 | 1-2 周 | Browser Agent + MCP 连接器 |

### E.9 🔧 平台

| ID | IDEAS# | 项 | 估时 | 备注 |
|---|---|---|---|---|
| BL-E26 | #24 | Hermes 自定义 skill namespace (catfish 独立 namespace 不混 productivity) | 0.5-3 天 | 调研 hermes 加载逻辑, 等 catfish skill ≥ 3 时做 |

---

## F · 运营 / 部署 / 安装

| ID | 项 | 状态 | 估时 |
|---|---|---|---|
| BL-F1 | macOS .pkg 安装包 (替代 install-catfish.sh dev script) | ⬜ | 1 周 |
| BL-F2 | Windows .msi 安装包 (Win 路径完成后) | ⬜ | 1 周 |
| BL-F3 | 全图形化 onboarding (员工首次启动引导) | ⬜ | 1 周 |
| BL-F4 | 数据迁移工具: 员工换电脑迁 memory/skill/state.db | ⬜ | 0.5 周 |
| BL-F5 | 备份方案: memory + state.db 自动备份到指定位置 | ⬜ | 0.5 周 |
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
| BL-G5 | 测试覆盖率提升: 30-40% → 70%+ | ⬜ | 1-2 周 |
| BL-G6 | 端到端集成测试 (现在只有单元) | ⬜ | 1 周 |
| BL-G7 | CI/CD pipeline (GitHub Actions / 内部 CI) | ⬜ | 0.5 周 |

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

## 总览统计

```
A  战略 / 开闭         15 项 ⬜  · 1-3 个月集中做 (Phase 0→1→2)
B  GTM / 商业化        11 项 ⬜  · Phase 2 启动前 + 长期
C  工程 P1 近期        11 项 ⬜  · ~2 周内
D  工程 P2 中期        16 项 ⬜  · 1-3 个月
E  工程 P3 远期 (IDEAS) 26 项 ⬜  · 3-6 个月按需挑做
F  运营 / 部署         11 项 ⬜  · 持续
G  内部建设 / Docs     7 项  ⬜  · 持续
H  法律 / 合规         8 项  ⬜  · Phase 1 / Phase 2 前必做
I  多模态扩展          6 项  ⬜  · P1.5-P2 之间
J  品牌 / VI           4 项  ⬜  · Phase 2 前

阻塞 / 等鸿波           6 项 🚧  · 今天才能解锁

合计 ~121 项
```

---

## 维护规则

1. **每周一早上**: 看一眼这份 backlog, 挑 5-10 项进 task tracker 做下周 sprint
2. **完成的不删**: 改 status = ✅ + 加 task # / commit hash, 留作历史 (软著申报 + 项目 review)
3. **新想法加到末尾**: 不需要立刻拆细, 按 section 归类即可, 后续 review 时拆
4. **状态约定**:
   - ⬜ 未开始
   - 🔵 进行中 (在 task tracker 里)
   - ✅ 已完成
   - 🚧 阻塞 (等外部 / 等决策)
   - ❄️ 暂不做 (有意识 deprioritize, 不忘记)
   - 💀 已废弃 (踩坑后决定不做了, 保留历史)
5. **每月**: 整体 review 一次, 重新评估优先级 — 战略可能变, backlog 也跟着调
6. **修这份文档**: 改完 commit message 写 `docs(backlog): <一句话原因>`, 在 git log 留下变更轨迹

---

## 跟其他文档的关系

```
STRATEGY.md            ─→  战略拍板 (3 个月一动)        ─→  填充 BACKLOG.md A 段
POSITIONING.md         ─→  产品定位 one-pager           ─→  填充 BACKLOG.md B 段 (GTM)
COMPETITIVE-DIFF.md    ─→  应对"跟 Hermes / OpenClaw 同质化" ─→  对外口径统一
SKILL-LIFECYCLE.md     ─→  Skill 5 阶段框架 (元文档)         ─→  指导 SOUL/policy/tool 设计
AUTH-DESIGN.md         ─→  SSO 落地路径 (BL-D6)              ─→  实施前 6 决策点必须拍板
IDEAS.md               ─→  长尾创意池 (随时加)           ─→  填充 BACKLOG.md E 段
TOMORROW.md            ─→  当周 sprint 计划 (每周写)     ─→  从 BACKLOG.md C/D 段挑出来
CHANGELOG.md           ─→  完成的事 (每天补)             ←─  BACKLOG.md ✅ 项的归宿
Cowork tasks           ─→  当前 sprint 跟踪 (实时)       ←─  从 BACKLOG.md 挑出来
BACKLOG.md (本)        ─→  全量积压 (每周 review)        →   汇总以上所有
```

---

## 决策签名

> 此文档代表 2026-04-27 的全量 backlog 快照. 后续按"维护规则"持续更新.
>
> 重大优先级调整 (例如 P3 提到 P1 / 整段砍掉) 在 git log commit message 里写明原因.
