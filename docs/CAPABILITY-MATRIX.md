# 鲶鱼 · 能力矩阵 (Capability Matrix)

> **版本**: v1, 2026-04-30 创建
> **维护人**: 鸿波
> **用途**: 现状能力快照 — 鲶鱼**当前**有哪些功能 / 在哪段代码 / 测试覆盖如何 / Demo 怎么演.
>
> **跟其他 doc 关系**:
> - `BACKLOG.md` = 完整积压 (做过的 + 待做的 + 想法), **意图视角**
> - `CHANGELOG.md` = 每天 ship 的事 (日记格式), **时间视角**
> - `ROADMAP.md` = 4 Phase 演化 (客户视角), **战略视角**
> - **本文 = 现状快照** (✅ 已 ship 的功能), **能力视角**
>
> 4 份配合: BACKLOG 看意图 / CHANGELOG 看节奏 / ROADMAP 看演化 / **本文看能力**.
>
> **更新节奏**: 每个 sprint 末 (1-2 周一次) review 一次, ship 的回写到对应章节. 每月跟 ROADMAP / BACKLOG 同步.

---

## 一句话总结

鲶鱼 Phase 1 ship 中, 当前能力覆盖 **8 大模块 + 35+ 项功能**:

```
✅ 三层架构 (Companion / tool-bridge / central gateway) 跑稳
✅ SSO 全链路 (catfish-identity OIDC + 飞书 + dev_token fallback)
✅ 凭据安全 (secret_ref + Keychain + prompt regex 检测)
✅ 中央审计 (gateway audit JSONL, 看 metadata 不看内容)
✅ 跨厂商 LLM 路由 + 容灾 fallback (qwen / gemini / 私有)
✅ ★ 跨 session 上下文 (档1 session 索引 + 档2 employee_journal LLM 总结)
✅ Skill 系统 (catfish_run_skill + skill_guard + 自动注入 + 仪表盘扫描)
✅ 业务 skill 2 个 (leadership-briefing 4 段公文 + 双 backend 错别字 / weekly-report 周报 .xlsx)
✅ Companion 仪表盘 (AuditCard / 模型分布 / token 用量 / catfish skills 显示)
✅ 5 月 demo 文档全套 (DECK 32 张 / ELEVATOR V1-V5 / Q&A 13 题 / PREP 检查清单 / ROADMAP / POSITIONING)
```

---

## 模块 1 · Central Gateway (中央 LLM 网关)

> 路径: `central/llm-gateway/`. FastAPI + LiteLLM. Phase 1 主战场之一.

| 能力 | 状态 | 代码位置 | 测试 | Demo 场景 |
|---|---|---|---|---|
| 多 provider 路由 (qwen / gemini / 私有) | ✅ | `routes/chat.py` + `routes/catalog.py` | gateway 380+ | Slide 6 架构图 + 实测切模型 |
| LiteLLM 标准协议 (接新厂商 1 周) | ✅ | `provider_loader.py` | ✓ | Q&A #4 跨厂商 |
| Fallback 链 (主力慢 / 错 / 不可达 → 公网备用) | ✅ | `models.yaml` + `provider_loader.py` | ✓ | Q&A #10 容错 |
| Per-model timeout + param_overrides | ✅ | `models.yaml` | ✓ | — |
| 元数据审计日志 (JSONL, 不含对话内容) | ✅ | `audit_writer.py` | ✓ | Slide 10 AuditCard |
| 安全事件检测 (prompt 含明文密码 → security_concern 标记) | ✅ | `prompt_security.py` | ✓ | Slide 10 + Slide 16 |
| 数据统计强制 execute_code 注入 (stats_guard) | ✅ | `stats_guard.py` | ✓ | Q&A #10 |
| Multimodal 自动 route (截图 → vision 模型) | ✅ | `multimodal_guard.py` | ✓ | Slide 8 邮件 demo |
| Tool capability 检测 (ModelConfig.supports_tool_use 配置驱动) | ✅ | `tool_capability_guard.py` | ✓ | — |
| Skill 自动注入到 system prompt (catfish skill 列表) | ✅ | `inject_skill_catalog.py` | ✓ | 反幻觉前置 |
| Skill_guard REQUIRED block (防模型乱跑假命令) | ✅ | `skill_guard.py` | ✓ | — |
| ★ 档1 inject_session_history (注入最近 7 天 session 元数据) | ✅ 4-30 | `inject_session_history.py` | gateway 15 新 | Slide 11-12 场景 4 |
| ★ 档2 employee_journal 注入 (~50KB tail-truncate) | ✅ 4-30 | `employee_journal.py` | ✓ | 同上 |
| ★ 档2 session_summarizer 后台异步 LLM 总结 | ✅ 4-30 | `session_summarizer.py` | ✓ | 同上 |
| OIDC token 验证 + dev_token fallback | ✅ 4-28 | `auth/middleware.py` | ✓ | Slide 27 SSO 备用 |
| Watchdog respawn (5-10s 内自起) | ✅ 4-22 | install 脚本 + LaunchAgent | 实测 | Q&A #11 |

**测试覆盖**: gateway 380+ 用例 (含 4-30 新增 15 跨 session 测).

---

## 模块 2 · Edge / Tool-Bridge (本地工具调用 + 隔离)

> 路径: `edge/tool-bridge/`. Python, unix socket IPC, hermes venv 跑.

| 能力 | 状态 | 代码位置 | 测试 | Demo 场景 |
|---|---|---|---|---|
| Unix socket IPC (Companion ↔ tool-bridge) | ✅ | `ipc_server.py` | tool-bridge 165+ | — |
| Tool dispatch + audit (每次工具调用进 .audit.jsonl) | ✅ | `dispatch.py` | ✓ | Slide 6 架构图 |
| catfish_browser_goto (Catfish Chrome 隔离 profile) | ✅ | `browser/playwright_*.py` | ✓ | Slide 5 EIS 抓资质 |
| catfish_screenshot | ✅ | `screen/screenshot.py` | ✓ | Slide 8 邮件 demo |
| secret_ref + Keychain 解析 (跨平台 abstraction) | ✅ 4-28 | `secrets/keychain.py` | ✓ | Slide 16 凭据安全 |
| element-ui-pagination-helper skill (翻页) | ✅ | `skills/web/element-ui-pagination/` | ✓ | Slide 5 翻 15 页 |
| ★ catfish_run_skill 工具 (importlib 反射 load script.py) | ✅ 4-28 | `catfish_tools.py` | ✓ | Slide 5 4 段公文生成 |
| Chrome respawn (cdp_url mtime 监听) | 🔵 部分 | `chrome.rs` (Tauri 触发) | — | (BL-C11) |
| dispatch.py audit JSONL 事件流 | ⬜ | (BL-C14) | — | (Skill lifecycle 阶段 4) |

**测试覆盖**: 165+ 用例.

---

## 模块 3 · Central Identity (catfish-identity OIDC server)

> 路径: `central/identity-server/`. ~600 行 Python. **客户 IT 可读可审**.

| 能力 | 状态 | 代码位置 | 测试 | Demo 场景 |
|---|---|---|---|---|
| OIDC issuer (token / authorize / userinfo / jwks) | ✅ 4-28 | `routes/oidc.py` | identity 31+ | Slide 27 SSO 详细 |
| 飞书 adapter (扫码登录 → catfish JWT) | ✅ 4-28 | `adapters/feishu.py` | ✓ | Q&A #7 |
| 自建 OIDC adapter (客户 OIDC → catfish JWT) | ✅ 4-28 | `adapters/oidc.py` | ✓ | Q&A #7 |
| dev_token 兜底 + warning banner | ✅ 4-28 | `auth/dev_token.py` | ✓ | demo 翻车 fallback |
| token TTL + refresh | ✅ 4-28 | `tokens.py` | ✓ | — |
| User store (yaml 短期, PG 待做) | 🔵 yaml | `userstore_yaml.py` | ✓ | — |
| 钉钉 / 企微 adapter | ⬜ | (BL-D14/D15) | — | Phase 2 |
| RBAC 系统 | ⬜ | (BL-D8) | — | Phase 2 |
| Quota 系统 | ⬜ | (BL-D9) | — | Phase 2 |

**测试覆盖**: 31+ 用例.

---

## 模块 4 · Skill 系统

> 现 catfish skill = 工程审定 skill, 走 catfish_run_skill 工具调用. 跟 hermes skill 是两套独立系统 (SOUL.md 4-30 加铁律说明).

### 4.1 Skill 基础设施

| 能力 | 状态 | 代码位置 | 测试 | Demo 场景 |
|---|---|---|---|---|
| catfish_run_skill 工具 (动态 load script.py) | ✅ 4-28 | `edge/tool-bridge/.../catfish_tools.py` | ✓ | Slide 5 |
| Skill 自动注入 catalog (gateway → system prompt) | ✅ 4-28 | `inject_skill_catalog.py` | ✓ | — |
| Skill_guard REQUIRED block (反幻觉) | ✅ 4-29 | `skill_guard.py` | ✓ | 4-30 加铁律 5/6 |
| Companion 仪表盘扫描显示 (Tauri Rust, 同时扫 hermes + catfish) | ✅ 4-29 | `commands/skills.rs` | — | Slide 10 仪表盘 |
| Skill 字体目录 (legal-restricted / opensource / system-fallback) | ✅ 4-29 | `skills/_shared/fonts/` | — | 法律红线 |
| Skill 版本管理 / 兼容性 | ⬜ | (BL-L14) | — | Phase 1 末 |
| Skill 下线 / deprecation | ⬜ | (BL-L15) | — | Phase 1 末 |
| Skill 完整审计 + dispatch_tool 关联 | ⬜ | (BL-L17) | — | Phase 1 末 |
| Skill 分享 / 安装 (Skills Hub) | ⬜ | (BL-D1, BL-L18) | — | Phase 2 |

### 4.2 业务 Skill (catfish 工程审定)

| 能力 | 状态 | 代码位置 | 测试 | Demo 场景 |
|---|---|---|---|---|
| **leadership-briefing** (公文汇报材料 .docx) | ✅ 4-29 | `skills/department/leadership-briefing/` | 25/25 | Slide 5 场景 1 |
| ↳ 4 段公文结构 (概况/分项/问题/下一步), heading 文本 LLM 自由 | ✅ 4-30 | SKILL.md | regex 检 | — |
| ↳ blocks 模式 (paragraph / kv_table / table / ordered_list) | ✅ 4-29 | script.py | ✓ | — |
| ↳ 表格全转 CSV 附件 (主文档干净, 仅 1 KPI 总览 ≤5 行例外) | ✅ 4-30 | script.py | ✓ | — |
| ↳ 内容质量铁律 (论点+数据+推论+承上启下) | ✅ 4-30 | SKILL.md | — | — |
| ↳ 双 backend 错别字检查 (typo_check 公文字典 50+ + pycorrector Kenlm) | ✅ 4-30 | typo_check.py + script.py _maybe_audit | ✓ | — |
| ↳ 字体 (方正小标宋 + 仿宋_GB2312, 合规字体) | ✅ 4-29 | `_shared/fonts/legal-restricted/` | ✓ | — |
| ↳ 页眉页脚 + 页码 + 公文红线 | ✅ 4-29 | script.py | ✓ | — |
| **weekly-report** (员工周报 .xlsx) | ✅ 4-29 | `skills/department/weekly-report/` | 17/17 | (周报场景, demo 备用) |
| ↳ 单 Sheet 6 列模板 (序号/项目/本周/下周/截止/备注) | ✅ 4-29 | script.py | ✓ | — |
| ↳ 多行单元格自动高度 + 浅蓝表头 | ✅ 4-29 | script.py | ✓ | — |
| ↳ Phase 2 简化版 (方式 A+ — LLM 用 session_search 抽 7 天历史) | ✅ 4-30 | SKILL.md prompt 工程 | — | — |
| ↳ Phase 2 完整版 (接 hermes audit log 自动判断本周 ship 拼草稿) | ⬜ | (BL-L7) | — | 5-6 月 |

### 4.3 通用 Skill (Hermes 已有, catfish 复用)

| 能力 | 状态 | 来源 |
|---|---|---|
| docx (Word 文档创建/编辑) | ✅ | hermes |
| pptx (PPT 创建/编辑) | ✅ | hermes |
| xlsx (Excel 创建/编辑) | ✅ | hermes |
| pdf (PDF 处理) | ✅ | hermes |
| skill-creator (创建新 skill) | ✅ | hermes |
| schedule (定时任务) | ✅ | hermes |

---

## 模块 5 · Edge / Companion App (员工桌面副手)

> 路径: `edge/companion-app/`. Tauri (Rust) + React + TypeScript.

| 能力 | 状态 | 代码位置 | 测试 | Demo 场景 |
|---|---|---|---|---|
| Tauri 跨平台壳 (Mac ship, Win Phase 2) | ✅ Mac | `src-tauri/` | — | — |
| Companion 主界面对话 UI | ✅ | `src/App.tsx` + `hooks/useChat.ts` | ✓ | Slide 5 输入框 |
| useChat MAX_TOOL_ROUNDS 20 (鸿波"10 轮总踩上限") | ✅ 4-30 | `useChat.ts` | — | — |
| useChat parse_error 早停 (3 连续) + 友好提示 | ✅ 4-30 | `useChat.ts` | — | — |
| useChat cache 60s TTL + 关键工具缺失自动重拉 | ✅ 4-30 | `useChat.ts` | — | — |
| AuditCard 仪表盘 (今日 / 模型分布 / TTFT / security_concern) | ✅ 4-29 | `components/AuditCard.tsx` | — | Slide 10 |
| catfish skills 显示 (Tauri Rust 扫 + UI 渲染, 🐟 catfish: 前缀) | ✅ 4-29 | `commands/skills.rs` + `SkillsCard.tsx` | — | Slide 10 |
| 文件下载 UI 优雅化 (FilePill 组件) | ✅ 4-28 | `components/FilePill.tsx` | — | Slide 5 .docx 链接 |
| Squircle icon (基础) | ✅ | `src-tauri/icons/` | — | — |
| 浮窗模式 (Cmd+Shift+Space 召唤) | ⬜ | (BL-D10) | — | Phase 2 |
| 菜单栏 / 托盘常驻 | ⬜ | (BL-D11) | — | Phase 2 |
| LaunchAgent 自启动 | ⬜ | (BL-C6) | — | — |
| 邮件 tab GUI | ⬜ | (BL-D13) | — | Phase 2 |
| Win 跨平台 | ❄️ | (BL-C2~C5) | — | Phase 1 末 Week 7 |

---

## 模块 6 · ★ 跨 session 上下文 / 持久个体 (4-30 ship, 5 月 demo 致命卖点)

> Phase 1 已 ship, **Phase 1 跟竞品锁层差核心**. 解决"跨对话信息割裂, 不像真实个体"硬伤.

| 能力 | 状态 | 代码位置 | 测试 | Demo 场景 |
|---|---|---|---|---|
| 档1 · session 索引注入 | ✅ 4-30 | `inject_session_history.py` | ✓ | Slide 11-12 |
| ↳ 读 ~/.hermes/state.db 取最近 7 天 sessions | ✅ | 同上 | ✓ | — |
| ↳ 字段: id / 时间 / 消息数 / title / 首条 user msg | ✅ | 同上 | ✓ | — |
| ↳ read-only sqlite + 1s timeout + 异常吞掉 | ✅ | 同上 | ✓ | — |
| ↳ 注入到 system prompt 末尾 (找最后 system msg 追加, 幂等) | ✅ | 同上 | ✓ | — |
| 档2 · employee_journal 长期日记 | ✅ 4-30 | `employee_journal.py` | ✓ | Slide 12 cat 给客户看 |
| ↳ 文件位置 ~/.catfish/employee_journal.md (全本地, 中央看不到) | ✅ | 同上 | ✓ | Q&A #13 隐私 |
| ↳ 50KB tail-truncate 注入 (~12K token, 保留最新) | ✅ | 同上 | ✓ | — |
| ↳ append-only markdown (易客户审计, 易员工删) | ✅ | 同上 | ✓ | — |
| 档2 · session_summarizer 后台异步 LLM 总结 | ✅ 4-30 | `session_summarizer.py` | ✓ | — |
| ↳ asyncio.create_task fire-and-forget (不阻塞主流程) | ✅ | 同上 | ✓ | — |
| ↳ qwen-flash 总结 1-2 段 100-300 字 | ✅ | 同上 | ✓ | — |
| ↳ mark 时机修正 (写入成功才 mark, 失败不 mark 留重试) | ✅ | 同上 | ✓ | (4-30 踩坑修复) |
| ↳ 日期前缀由 gateway 加 (防 LLM 脑补错时间) | ✅ | 同上 | ✓ | — |
| 真实验证 (鸿波本机 9 session 全部成功总结, journal ~6KB) | ✅ | — | 实测 | — |
| journal 向量召回升级 | ⬜ | (BL-L8) | — | 5-6 月 (长期员工) |
| journal 备份 / 同步 | ⬜ | (BL-L9) | — | — |

---

## 模块 7 · SOUL.md / Identity (员工身份 + 鲶鱼人格层)

> 路径: `edge/identity/SOUL.md`. 鲶鱼的"做事原则" + 员工档案 + 反幻觉铁律.

| 能力 | 状态 | 代码位置 | Demo 场景 |
|---|---|---|---|
| SOUL.md 三层 (反幻觉 / 系统操作 / Memory 三层触发) | ✅ | `edge/identity/SOUL.md` | (后台, 不直接演) |
| USER.md 员工档案 (姓名 / 偏好 / 工作内容) | ✅ | `~/.hermes/USER.md` | — |
| MEMORY (本地, 跨 session) | ✅ | hermes 自带 | — |
| ★ catfish_run_skill 反幻觉铁律 (4-30 加, 踩过坑 195 条对话) | ✅ 4-30 | SOUL.md § "catfish_run_skill 工具不要去 skills_list 验证" | — |
| 双 skill 系统说明 (hermes vs catfish 完全独立) | ✅ 4-30 | 同上 | — |
| catfish skill 永远不要跑假命令 (skills_install/pull/browse) | ✅ 4-30 | 同上 | — |

---

## 模块 8 · 5 月 demo 销售物料 (4-28 + 4-30 主)

> 路径: `docs/MAY-DEMO-*.md`, `docs/ELEVATOR-PITCH.md` 等. **Phase 1 demo 必备**.

| 能力 | 状态 | 文件 | Demo 场景 |
|---|---|---|---|
| **MAY-DEMO-DECK.md** (PPT 大纲 32 张, 每张含口语稿) | ✅ 4-30 | `docs/MAY-DEMO-DECK.md` | 现场全程 |
| ↳ 4 demo 场景 (含 ★ 场景 4 跨 session 记忆) | ✅ 4-30 | Slide 4-15 | — |
| ↳ 4 锁层差 (三层架构 / 数据本地 / 跨 session / Federation) | ✅ 4-30 | Slide 16-21 | — |
| **ELEVATOR-PITCH.md** (5 个 30 秒电梯演讲, V1-V5 适配 CTO/部门/员工/CISO/财务) | ✅ 4-30 | `docs/ELEVATOR-PITCH.md` | 客户偶遇 / 简介 |
| **MAY-DEMO-Q-AND-A.md** (13 题详细备背) | ✅ 4-30 | `docs/MAY-DEMO-Q-AND-A.md` | 现场 Q&A |
| ↳ 含 ★ 第 13 题"跨 session 记忆怎么实现, 隐私怎么保证" | ✅ 4-30 | 同上 | — |
| **MAY-DEMO-PREP.md** (个人检查清单) | ✅ 4-30 | `docs/MAY-DEMO-PREP.md` | demo 前 1 周对照 |
| **ROADMAP.md** (4 Phase 客户视角) | ✅ 4-26 | `docs/ROADMAP.md` | Slide 22 |
| **POSITIONING.md** (产品定位 one-pager) | ✅ 4-26 | `docs/POSITIONING.md` | 客户问"差异化" |
| **COMPARE-1PAGER.md** (vs 星辰 / Hermes / OpenClaw) | ✅ 4-26 | `docs/COMPARE-1PAGER.md` | 客户问竞品 |
| **POC-PLAN.md** (PoC 1-2-3 周时间表) | ✅ 4-26 | `docs/POC-PLAN.md` | demo 后客户说"想试" |
| **COMPETITIVE-DIFFERENTIATION.md** | ✅ | 同上 | — |
| **AUTH-DESIGN.md** (SSO 6 决策点, 客户 IT 自助接入参考) | ✅ 4-25 | `docs/AUTH-DESIGN.md` | Q&A #7 |
| **SKILL-LIFECYCLE.md** (Skill 5 阶段框架) | ✅ | `docs/SKILL-LIFECYCLE.md` | — |
| **STRATEGY.md** (开源 + 商业化战略) | ✅ 4-25 | `docs/STRATEGY.md` | (内部, 不给客户) |
| **BACKLOG.md v2** (全量积压, 4-30 升级) | ✅ 4-30 | `docs/BACKLOG.md` | (内部) |
| **CAPABILITY-MATRIX.md** (本文, 现状能力快照) | ✅ 4-30 | `docs/CAPABILITY-MATRIX.md` | — |
| 实录 demo 视频 (3 个 1 分钟真实 case) | ⬜ | (BL-L19) | demo 嵌入 |
| 实际 PPT (按 DECK 大纲填 Keynote / 飞书) | ⬜ | (BL-L20) | — |

---

## 测试覆盖快照 (2026-04-30)

| 模块 | 用例数 | 状态 |
|---|---|---|
| central/llm-gateway | **380+** | ✅ 全过 (含跨 session 15 新) |
| edge/tool-bridge | **165+** | ✅ 全过 |
| central/identity-server | **31+** | ✅ 全过 |
| skills/department/leadership-briefing | **25/25** | ✅ 全过 (含双 backend) |
| skills/department/weekly-report | **17/17** | ✅ 全过 |
| edge/companion-app (单元) | 部分 | 🔵 待补 |
| 端到端集成测试 | ⬜ | (BL-G6) |
| 总覆盖率估算 | ~50% | (目标 70%+, BL-A12) |

---

## 跟 Phase 的对应关系

| Phase | 状态 | 本文覆盖 |
|---|---|---|
| **Phase 1 · 单员工 AI 副手** (现在 ship 中) | 🔵 90% | 模块 1-8 大部分 ✅ |
| **Phase 2 · 团队版** (Q3 2026) | ⬜ | RBAC / Quota / Win 跨平台 / 钉钉企微 / Skills Hub |
| **Phase 3 · ★ Catfish Federation** (Q4 2026) | ⬜ | BL-E17/E18, 设计已定 (BACKLOG.md E.5) |
| **Phase 4 · 集团级网格** (2027 Q2+) | ⬜ | — |

---

## 演讲时怎么用这份矩阵

**给 CTO**: 翻到模块 1 / 模块 3, 让 IT 看到"具体每个能力 + 代码位置 + 测试覆盖", 不是空话.

**给 CISO**: 翻到模块 6 跨 session 上下文 (隐私设计) + 模块 1 prompt_security + 凭据 secret_ref + 模块 3 catfish-identity (~600 行可审).

**给部门领导**: 翻到模块 4.2 业务 skill (leadership-briefing + weekly-report 跑通了什么真实场景), 模块 6 跨 session 记忆 (员工真同事不是聊天机).

**给员工**: 翻到模块 5 Companion (装了能干啥) + 模块 4.2 业务 skill (能解决你哪些工作).

**给软著申报 / 法务**: 整页, 含代码位置 + 测试覆盖 + ship 日期, 是法律意义上"已实现功能"清单.

---

## 维护规则

1. **每个 sprint 末** (1-2 周): review 一次, 把 ✅ 的回写, ⬜ 的去掉无关项
2. **每月**: 跟 ROADMAP / BACKLOG 对齐, 调整 Phase 标签
3. **大版本 release 前**: 整页 review + 给客户的版本 (砍掉内部细节)
4. **修这份文档**: commit message 写 `docs(matrix): <一句话原因>`, 在 git log 留下变更轨迹

---

## 决策签名

> v1 = 2026-04-30 创建. 解决 v1 BACKLOG 漂移之后"鲶鱼到底有哪些功能没有一个权威源" 的问题.
>
> 跟 BACKLOG v2 同步落地, 二者互补:
> - BACKLOG = 意图 (做过的 + 待做的 + 想法)
> - 本文 = 能力 (现在能干什么, 在哪段代码, 测试如何, 怎么演)
