# 鲶鱼 · Feature Tracks (主入口)

> **快照**: 2026-05-03 · **维护人**: 鸿波 · **更新**: 每周日晚 + 重大 ship 时
> **角色**: 这是**唯一**的"我们在做啥 / 还差啥 / 在哪个 phase"主入口.
> 其他 doc 角色见底部 § 文档地图.

---

## 🚦 Phase 进度 (一行看清)

```
Phase 1 · 单员工 AI 副手           [█████████░] 95% · 5 月 demo + 1 PoC 客户
Phase 2 · 团队版 (SSO/RBAC/Win)    [██████░░░░] 60% · 五一 sprint Phase 2 后端 100% + 阻断 + UI
Phase 3 · ★ Federation             [███░░░░░░░] 30% · 五一 sprint Plan D MVP, Q4 ship
Phase 4 · 集团级 mesh               [░░░░░░░░░░]  0% · 2027 Q2+
```

> 📈 5/2 收尾后大幅推进:
>   - Phase 1: 92% → 95% (+ project-approval skill / 主动闲聊 / Quota 真接 chat)
>   - Phase 2: 35% → 60% (+ Dashboard 角色化 / 多账号 / PG 完整双写 / alembic 双服务 / Quota 100% / Manager PUT UI / Admin 全局)

---

## 🚨 当前最大风险 / 缺位 (Top 5)

| # | 风险 / 缺位 | 影响 | 跟踪 track |
|---|---|---|---|
| 1 | **5 月 demo 真机彩排没做** (10 天倒计时) | 现场翻车 | #18 销售物料 |
| 2 | **employee_journal 真业务内容不够** | 跨 session 记忆演不出 | #14 + 主动闲聊 已部分缓解 |
| 3 | **Win 客户端 0% 没动** (5/2 拍板暂不动) | Q3 大客户阻塞 | #2 ★ |
| 4 | **Production 部署 0%** (现都跑鸿波本机 dev mode) | 给客户东西也跑不起 | #16 ★ |
| 5 | **团队 1 人** (Phase 2 一定带不动) | Q3 KPI 跳票 | #20 |

---

## 📋 全部 Tracks (按 phase + 重要性排)

### Phase 1 · 已 ship 主线

#### #1 Companion macOS [Phase 1, 92%]
> Tauri 桌面客户端, 鲶鱼员工每天打开的入口.
- ✅ 三 tab (对话/控制台/仪表盘) + 浮窗 Cmd+Shift+Space 召唤 (5/5)
- ✅ 多模态 (Whisper.cpp 语音 + PDF/Excel/Word 文件解析)
- ✅ 仪表盘 5 卡 (身份/服务/Catalog/Skills/审计/Quota 接通)
- ⬜ Onboarding 引导 (员工首次启动) · 1 周 (BL-F3)
- ⬜ 数据迁移工具 (换电脑搬 memory/skill/state) · 0.5 周 (BL-F4)
- ⬜ 备份方案 (auto backup) · 0.5 周 (BL-F5)

#### #3 tool-bridge (本机 IPC) [Phase 1, 90%]
> 把鲶鱼 native tools 暴露给 hermes / Companion 调.
- ✅ 12 个 catfish_* 工具 (run_skill / a2a_ask / skill_install/delete / today_summary / browser × 4 / screenshot / remember 等)
- ✅ tool_bridge_call_tool Tauri command
- ⬜ 端到端集成测试 (现 165 单测) · 1 周 (BL-G6)

#### #4 中央 gateway [Phase 1, 95%]
> LLM 路由 + auth + audit + quota 入口.
- ✅ OpenAI 兼容 /v1/chat/completions + /embeddings + /catalog
- ✅ litellm + fallback 链 + tool_capability_guard + multimodal_guard
- ✅ inject (skills_catalog / session_history / employee_journal / SOUL identity / stats_guard)
- ✅ /api/quota/me (5/3 接 Dashboard) + RBAC 矩阵 (5/2)
- ✅ A2A 端 (sign + verify + ALLOW.md 拦截) (5/4)
- ⬜ Production 部署 (现 dev mode) · 1 周 (BL-F7) — 见 #16
- ⬜ 多 user 并发性能基准 · 0.5 周 (BL-F10)

#### #5 SSO + identity-server [Phase 1, 95%]
> OIDC issuer + 飞书 adapter + dev_token fallback + Keychain.
- ✅ 自建 OIDC server (~600 行) + JWKS + token endpoint
- ✅ 飞书 OIDC adapter
- ✅ Companion JWT 客户端 (Keychain + 自动刷)
- ✅ dev_token + warning banner
- ✅ users 双 backend (PG / yaml fallback) (5/4)
- ⬜ 钉钉 / 企微 adapter (各 2 周) (BL-D15/D16)
- ⬜ 客户 IT 自助接入文档收尾 (BL-L21)

#### #8 Plan D Federation 协议层 [Phase 3 雏形, 30%]
> 跨员工 agent-to-agent, 鲶鱼真正的护城河.
- ✅ 协议 spec v0.1 (480 行 PLAN-D-PROTOCOL.md)
- ✅ Per-agent registry + JWKS (B 方案)
- ✅ A 端 (sign+send) + B 端 (verify+receive ALLOW.md 拦截) + audit jsonl
- ✅ 单机 mock 端到端通过 (Alice ↔ Bob)
- ✅ ALLOW.md token-overlap 匹配 (5/2 BL-L28)
- ⬜ 跨 2 台真机测试 · 1-2 周 (BL-E18)
- ⬜ mTLS / 自签 CA · 1 周
- ⬜ Federation registry HA · 1 周
- ⬜ 隐私边界严格化 (ALLOW 协商) · 2-3 周
- ⬜ 跨员工 demo (问下张三) · 1-2 个月 (BL-E17)

---

### Phase 2 · 五一 sprint 完整 ship Phase 2 后端

#### #6 RBAC [Phase 2, 75%]  ★ 5/2 大幅推进
> 三角色 (admin/manager/employee) + 部门隔离 + Dashboard 角色化.
- ✅ Permission Enum (8 维度) + ROLE_PERMISSIONS 矩阵
- ✅ require_permission FastAPI 依赖 (401/403)
- ✅ users.role + managed_departments + effective_role()
- ✅ User dataclass 加 is_admin / is_manager / can_manage_department
- ✅ Dashboard 按角色 conditional render (employee/manager/admin 看不同卡)
- ✅ DepartmentQuotaCard + DepartmentAuditCard (manager 看本部门聚合 + top 员工)
- ✅ /api/quota/department/{dept} + /api/audit/department/{dept} (RBAC 检查)
- ✅ /api/me 端点 + useMe hook
- ✅ dev_users.yaml 多账号 + DevUserSwitcher 黄条 (7 测试账号切换)
- ✅ 21 单测过 (rbac 16 + dev_token 5)
- ⬜ 实际接到所有路由 (现只 quota / audit / me) · 0.5 周
- ⬜ Manager 改本部门 quota PUT 端点 (现 read-only) · 1 周
- ⬜ Admin 全局聚合卡 · 0.5 周

#### #7 Quota [Phase 2, 100%]  ★ 5/2 完整 ship
> 三维滑动窗口 (用户/模型/部门) + 实时 chat 接通 + manager UI.
- ✅ sliding window sqlite + check_quota (4 维度)
- ✅ /api/quota/me 端点 + Dashboard QuotaCard 实时
- ✅ /api/quota/department/{dept} GET 部门聚合 + top 10 员工
- ✅ /api/quota/department/{dept} PUT manager 改限额 (写 quotas.yaml)
- ✅ /api/quota/global admin 全员 top departments
- ✅ quotas.yaml.example + path 修正
- ✅ 双 backend (PG / sqlite, 自动 fallback 不丢数据)
- ✅ chat completions 主流程接 record_usage (PG 双写)
- ✅ **chat 入口接 check_quota 阻断 (5/2 完整 ship): 超额返 429 + friendly 话术**
- ✅ **Companion 识别 429 显友好 banner ("你今日 X 用满了, 切到 Y")**
- ✅ **DepartmentQuotaCard 加 inline QuotaEditor (manager 直接 input + 保存)**
- ✅ 28 单测过 (含 5 部门聚合 + 3 update_department_quota + 2 全局聚合)

#### #9 PG 中央数据库 [Phase 2, 95%]  ★ 5/2 完整 ship
> users + registry + quota_events + gateway_audit 全走 PG, fallback 完整.
- ✅ identity-server db.py asyncpg 池 + 双 backend (PG / yaml)
- ✅ users.reload_from_pg + seed_pg_from_yaml
- ✅ registry 双 backend (load/save_async)
- ✅ gateway db.py 镜像 (asyncpg 懒 import)
- ✅ quota.py PG 双写 (psycopg sync, 自动 fallback sqlite 不丢)
- ✅ metrics.py PG 双写 (gateway_audit 表, fallback jsonl)
- ✅ database.yaml.example + .gitignore 真 yaml
- ✅ alembic 双服务 (gateway + identity-server) 各自 migration
- ✅ 各服务独立 alembic_version_gateway / _identity 防共享 PG 撞
- ✅ 3 PG integration 测 + 双 backend sqlite/jsonl 测全过
- ✅ 真机 Mac PG 端到端跑通 (5 张表 + chat → quota_events + gateway_audit 双写)
- ⬜ 多 gateway 实例共享 PG 测试 (BL-D17 完整) · 0.5 周
- ⬜ ALEMBIC CI 集成 (.github/workflows/) · 0.5 周

#### #10 Skill 系统 (load/run/inject/lifecycle) [Phase 1+2, 80%]
> 设计 / 加载 / 调用 / inject / guard / 版本 / 下线 / 删除 / 审计 全套.
- ✅ catfish_run_skill (importlib 反射) + skill_guard REQUIRED block
- ✅ inject_skills_catalog (注入到 system prompt)
- ✅ 版本 (SemVer) + deprecated 字段 + 审计 jsonl
- ✅ skill_install / skill_delete (30 天回收站) + Skills Hub MVP 本机版
- ✅ 17 lifecycle 测过
- ⬜ skill 创建后立即 dry-run 验证, 失败回滚 · 0.5 天 (BL-C12)
- ⬜ skill 创建前重复检查 · 0.3 天 (BL-C13)

#### #11 Skills Hub (中央托管) [Phase 2, 30%]
> 组织级技能市场, 让员工分享 skill, 部门集体学习.
- ✅ 本机版 MVP (skill_install 工具) (5/3)
- ✅ central/skills-hub 目录 (★ 但只有 README stub)
- ⬜ 真实中央 hub server (FastAPI + skill 仓库 + 审核流) · 3-4 周
- ⬜ 部门级 skill auto-推 · 1-2 周
- ⬜ Federation 化 (跟 #8 联动) · 2 周

#### #2 ★ Companion Windows [Phase 2, 0% · 5/2 决策: 暂不动]
> 大客户都用 Win, 没 Win 客户端 = Q3 大客户阻塞.
> **5/2 鸿波拍板**: 暂不开工, 等 PoC 客户出现 Win 需求再做 (避免 1 人团队分心).
> 凭据安全模型已设计好 (Rust `keyring` crate 跨平台 → Mac Keychain / Win Credential Manager).
- ⬜ Tauri Win build 验证 · 1 天 (BL-C2)
- ⬜ IPC TCP 替代 Unix socket (Win 没 unix socket) · 2 天 (BL-C3)
- ⬜ Win Credential Manager 实测 (keyring crate 抽象, 代码不改) · 1 天 (BL-C4)
- ⬜ msi 安装包 + 自动更新 · 1 周 (BL-F2)
- ⬜ 全部路径用 std::path::PathBuf, 测试在 Win 跑通 · 1 周
- **估时**: 4-5 天集中 + 1 周 msi/部署
- **触发**: PoC / demo 客户明确说"Win 必须" → 启动这条 track
- **风险**: hermes / Whisper.cpp / ffmpeg 在 Win 是否能跑全没验过

---

### Phase 1 · 完全没在主跟踪上的 ★

#### #12 ★ email-agent (邮件 agent) [Phase 1, ~50%]
> ⚠️ **PROJECT-STATUS 漏掉**, 但实际有 src/ + tests/ + DESIGN.md (~500 行).
> 走客户端集成 (Outlook + Foxmail), **不碰密码**, 不走 IMAP.
- ✅ DESIGN.md 完整 (P1-1 三大支柱之二)
- ✅ Foxmail Mac DB parser (catfish_email/foxmail_db.py)
- ✅ box_parser (mbox 格式)
- ✅ adapters/foxmail_mac.py + base.py
- ✅ inbox.py + cli (`__main__.py`)
- ✅ tests (test_box_parser, test_adapter_foxmail_mac)
- ⬜ 跟 Companion 接通 (现是独立 CLI) · 1 周
- ⬜ Outlook Mac adapter · 1 周
- ⬜ Win Outlook adapter · 1-2 周
- ⬜ 起草工具 (catfish_email_draft) 接到 tool-bridge · 0.5 周
- ⬜ Companion 邮件 tab GUI · 1 周 (BL-D13)
- **决策待**: demo 演不演这个? 真演就要彻底接通 + 1-2 周工作

#### #13 ★ feishu-monitor [Phase 1, ~60%]
> ⚠️ **PROJECT-STATUS 漏掉**, 实际有 src/ + tests/ + scripts/. 飞书消息 CDP 实时过滤 + 草稿.
- ✅ src/ (cdp_client / monitor / handlers / config / relevance / cli)
- ✅ tests (test_relevance)
- ✅ scripts/ + install.sh / uninstall.sh
- ✅ 工作原理: catfish Chrome → CDP 9222 → MutationObserver JS → Python daemon → 三处理器 (osascript 通知 / Hermes inbox / 草稿生成)
- ⬜ 跟 Companion 状态接通 (Dashboard 看监控状态) · 0.5 周
- ⬜ 草稿质量验证 (实测员工接受率) · 持续
- ⬜ 多账号 / 切换账号支持 · 1 周
- **决策待**: demo 演不演? 这个要演非常吸睛 (领导发消息员工不被拉进飞书)

#### #14 ★ 业务 skill 库 [Phase 1, 40%]  ★ 5/2 推前
> 客户 demo / PoC 看的"摸得着的能力", 现 3 个 (5/2 拍板 project-approval).
- ✅ leadership-briefing (4 段公文 + 表格附件 + 双 backend 错别字)
- ✅ weekly-report (.xlsx 周报)
- ✅ project-approval (项目立项, 复用 leadership-briefing 渲染层 + 4 段语义改) ★ 5/2
- ⬜ qualification-export (4 月停在 demo) · 0.5 周
- ⬜ meeting-minutes (会议纪要, 配合 #4 视频) · 1 周
- ⬜ annual-summary (年度总结) · 1 周
- ⬜ procurement (采购单) · 1 周
- **demo 阶段**: 3 个够初步多样性, 5 月后扩到 5+ 个

#### #15 多模态 [Phase 1, 50%]
> 语音 / 文件 / 视频 / 音频.
- ✅ 截图 + vision (4 月已通)
- ✅ 语音输入 (Whisper.cpp + ffmpeg avfoundation, 5/1)
- ✅ 文件上传 (PDF/Excel/Word/CSV/TXT/MD, 5/1)
- ⬜ 视频上传 (帧采样 + 多模态) · 1 周 (BL-I3)
- ⬜ 音频文件转写 (Whisper) · 0.5 周 (BL-I4)
- ⬜ 大文件 (>50KB) BM25 检索 · 3 天 (BL-L26)

#### #17 浮窗 / 全局 UX [Phase 1, 80%]  ★ 5/2 加系统通知
> 让员工随时召唤鲶鱼 (类 Spotlight).
- ✅ Cmd+Shift+Space 全局快捷键 (5/5)
- ✅ Esc 隐藏 + dock 单击恢复 (5/5)
- ✅ macOS 原生标题栏 (品牌不重复)
- ✅ macOS 通知 (osascript, BL-E13 用) ★ 5/2
- ✅ Companion 默认 prod, DEV 模式 opt-in (CATFISH_AUTOSTART_ENV=dev) ★ 5/2
- ⬜ 浮窗版 (无标题栏 / 居中悬浮) 单独 UX · 1 周
- ⬜ 选中即翻译 Cmd+Shift+T · 1-2 天 (BL-E5)
- ⬜ "领导来了" 一键切假装代码 · 1 天 (BL-E15)
- ⬜ menubar 状态指示 · 0.5 周

#### #25 ★ 主动闲聊 (BL-E13) [Phase 1, 30% C-MVP]  ★ 5/2 ship MVP
> 让小鲶按时段主动找员工聊, 不让 employee_journal 饿死 (demo 跨 session 记忆有内容).
- ✅ gateway proactive.py: 读 journal tail + 时段 + qwen-flash 生成 starter
- ✅ /api/proactive/starter 端点
- ✅ Companion ProactiveCard (Dashboard 第一卡, 30min 自动换)
- ✅ "跟小鲶聊聊 →" 按钮 → 切 chat tab + 输入框预填 starter (zustand)
- ✅ useProactiveScheduler hook: 9:30 / 14:00 / 17:30 自动 macOS 通知 (localStorage 防重)
- ✅ Tauri notify() 用 osascript display notification (无新依赖)
- ⬜ 节假日 / 晚 10 点不打扰 (推断状态) · 1 周
- ⬜ snooze / 拒绝机制 (现 localStorage 开关) · 0.5 周
- ⬜ 配置 UI (时段 / 频率 / 模板) · 0.5 周
- ⬜ 跨 session 深度情境关联 (journal 结构化解析) · 1-2 周
- **完整 BL-E13**: 1-2 周, demo 后做

---

### 缺位 area · 完全没动

#### #16 ★ Production 部署 + 运维 [Phase 1.5, 0%]
> 现都跑鸿波本机 dev mode, 给客户也跑不起.
- ⬜ Production gateway 部署方案 (k8s / docker-compose) · 1 周 (BL-F7)
- ⬜ catfish-cloud 多租户运营手册 · 1 周 (BL-F8)
- ⬜ 监控告警 (gateway 错误率 / hermes 崩溃) · 1 周 (BL-F9)
- ⬜ 性能基准 (多 user 并发) · 0.5 周 (BL-F10)
- ⬜ 安全测试 (SSO/RBAC/审计渗透) · 1 周 (BL-F11)
- ⬜ central/distribution (托管安装/PyPI/签名) · ★ 只 README stub
- ⬜ central/telemetry (匿名遥测) · ★ 只 README stub
- **风险**: 5 月 demo 后客户说"装一份给我们", 没产品形态可交付

#### #19 中央服务 stub (5 个未实现) [Phase 2, 0%]
> README 写了定位 + P0/P1, 全无代码.
- ⬜ central/distribution · 安装包托管 + 签名 · 1-2 周
- ⬜ central/mcp-registry · 企业 MCP 连接器仓库 · 2-3 周
- ⬜ central/secret-broker · 凭据短期令牌化 (员工不持长期 key) · 2-3 周
- ⬜ central/telemetry · 匿名遥测聚合 · 1 周
- ⬜ central/skills-hub · 见 #11 (跟它合并)
- **优先级**: secret-broker 是 SOE 客户合规硬要求, 应该先做

#### #18 销售物料 + demo 准备 [Phase 1, 60%]
> 5 月中旬 demo 倒计时.
- ✅ 演讲稿 4 份 (DECK 32 张 / ELEVATOR V1-V5 / Q&A 13 题 / PREP 检查清单)
- ✅ POC-PLAN.md (1-2-3 周 PoC 计划模板)
- ✅ POSITIONING / COMPARE-1PAGER / COMPETITIVE-DIFFERENTIATION
- 🔴 **真机彩排 ×2** (demo 前 3 天 + 前 1 天) · 各 1h (BL-X8)
- 🔴 **demo 前 7 天攒真实 employee_journal.md** · 持续 (BL-L2)
- 🔴 **实录 3 个 1 分钟 case 视频** · 1 天 (BL-L19)
- 🔴 **实际 PPT 制作 (按 DECK 大纲填 Keynote)** · 1 天 (BL-L20)
- 🟠 **公司机器测 catfish-private (qwen 122b) tool 能力** · 0.5 天 (BL-X7)
- 🟠 部门汇报模板 + EIS 截图 · 公司带回 (BL-L3/L4)
- 🟠 报价单 (50/200/1000+ 三档) · 鸿波决策 (BL-B2)
- 🟠 销售路径 (自销/渠道) · 鸿波决策 (BL-B6)

#### #20 团队建设 [跨 phase, 1人]
> Phase 2/3 一定带不动 1 人, 但扩到 3-5 人才能 ship.
- 当前: 鸿波 1 人 + 鲶鱼 dogfood
- ⬜ 1 后端工程师 (Phase 2 RBAC/Skills Hub/Win) · 5 月底前启动
- ⬜ 1 销售 / BD (5 月 demo 后必须考虑)
- ⬜ 1 客户成功 / 驻场 FAE (PoC 启动后)
- ⬜ 法务 / 合规 (软著 + 商标 + 法律 review)

#### #21 法律合规 [Phase 1, 0%]
> 软著 + 商标 + 公司注册 + 隐私政策, 拖会出大问题.
- ⬜ 公司注册 / 工商登记 · Phase 1 内 (BL-H3)
- ⬜ 软著申报 · Phase 1 内 (BL-H1, CHANGELOG 已为这个写)
- ⬜ 商标"鲶鱼/Catfish" · Phase 1 内 (BL-H2) — 容易被抢注
- ⬜ 数据合规 律师 review · Phase 2 前 (BL-H4)
- ⬜ 算法备案 (catfish-cloud 国内运营) · cloud 上线前 (BL-H5)
- ⬜ 隐私政策 / 用户协议 · cloud 上线前 (BL-H6)
- ⬜ 第三方依赖 license 全审 · 开源前 (BL-H7)
- ⬜ fonts/opensource 二进制从 git history 移出 · 开源前必做 (BL-H9)

---

### Phase 2/3 · 长期 backlog (不细列)

#### #22 三层统一搜索 [Phase 1.5, 0%]
> 公网 + 公司内 + 本地, 智能路由. (BL-E25, 1-2 周)
- ✅ 本地 FTS5 已 ship (edge/local-search)
- ⬜ 公司内 (Browser Agent + MCP)
- ⬜ 公网 (现走 Browser Agent)
- ⬜ 智能路由层

#### #23 长期人机关系 / 反直觉特性 [Phase 3+, 5%]  ★ 5/2 部分 (BL-E13 MVP)
> 鲶鱼晨报 / 周末不干活 / 主动闲聊 / 情绪 / 社交健康检查 / 学习清单等 (BL-E1~E20).
> ★ 主动闲聊 BL-E13 已 C-MVP ship, 见 #25.
> ~10-15 个 1-3 周的 idea, 都在 IDEAS.md 草稿里, 不在 demo 主线.

#### #24 web-ui [Phase 2 可选, 0%]
> 给不想装 Companion 的员工纯浏览器入口. README stub.

---

## 📁 文档地图 (老 doc 角色澄清)

| Doc | 角色 | 状态 |
|---|---|---|
| **`FEATURE-TRACKS.md`** (这份) | 主入口, 唯一 single source of truth | ★ 新 |
| `BACKLOG.md` | 工程颗粒度归档 (BL-XXX commit-level) | 仍维护, 不再加新 section, 入口移到这里 |
| `ROADMAP.md` | 客户视角 4 phase 一页 | 不动, 销售用 |
| `PROJECT-STATUS.md` | ⚠️ 4-30 stale, 替代为 FEATURE-TRACKS | 待标 deprecated |
| `CAPABILITY-MATRIX.md` | 现状能力快照 (跟 tracks 互补) | 不动 |
| `IDEAS.md` | 想法草稿源 | 进 backlog 前的池子 |
| `*-DESIGN.md` (RBAC/QUOTA/AUTH) | 技术 spec | 不动, tracks 引用 |
| `*-PROTOCOL.md` (PLAN-D) | 协议 spec | 不动 |
| `MAY-DEMO-*` (4 份) | demo 临时物料 | 5 月后归档 |
| `POC-PLAN.md` | PoC 模板 | 不动 |
| `POSITIONING / ELEVATOR / COMPARE / COMPETITIVE` | 销售对外口径 | 不动 |
| `STRATEGY / SSO-RATIFY / SSO-CUSTOMER / SKILL-LIFECYCLE` | 决策记录 | 历史归档 |
| `TOMORROW.md` | ⚠️ 4-26 stale | 待删 |
| `BRAND-VOICE.md` | 品牌叙述纪律 | 不动 |
| `CHANGELOG.md` | 实际 ship 日记 | 持续更 |

---

## 🔍 这次扫描的 6 个发现 (PROJECT-STATUS 漏的 / mismatch)

1. **★ email-agent 有真代码** (Foxmail mac adapter + box parser + 7 tests), PROJECT-STATUS 完全没提
2. **★ feishu-monitor 有真代码** (CDP client + monitor + handlers + relevance + tests), 没主线跟踪
3. **★ 5 个 central 服务全 stub** (distribution/mcp-registry/secret-broker/skills-hub/telemetry), README 写了 P0/P1 优先级但代码 0
4. **edge/communication-coach** 是 catfish-roleplay skill 不是产品, 名字误导
5. **edge/web-ui** README 自己写"P2 MVP 不做", 别老挂着空文件夹
6. **PROJECT-STATUS Phase 数 stale**: 五一推前 Phase 2 (20%→35%) + Phase 3 (10%→30%)

---

## 🎯 鸿波 review 重点 (你拍这些)

**已拍** (5/2):
- ✅ Win 客户端: 暂不动, 等 PoC 客户触发 (#2)
- ✅ 第 3 个 skill: project-approval (#14)
- ✅ identity-server alembic 迁: 跟 gateway 对齐 (#9)

**待拍**:
1. **email-agent / feishu-monitor demo 演不演** — 演 → 立即接通 Companion (1-2 周); 不演 → 改"P2 拖" 标记
2. **central 5 个 stub 删 / 留 / 做** — distribution/secret-broker 建议留(SOE 合规要), 其他 3 个考虑删 README 减负
3. **TOMORROW.md / PROJECT-STATUS.md 处理** — TOMORROW 4-26 stale 删了; PROJECT-STATUS 标 deprecated 还是合到 FEATURE-TRACKS?
4. **track 漏吗** — hermes-fork / hermes-customizations / hermes-plugins 是否建 track?
5. **跨 gateway 实例共享 PG 测试** (BL-D17 完整) — 0.5 天, demo 后做?

---

## 📜 这次 sprint ship 总结 (5/2 周末工作日记)

17 个 commit, 0 测试 fail, 真 PG 端到端跑通:

| # | commit | 改动 | 触发 |
|---|---|---|---|
| 1 | 五一 sprint 5/1-5/5 | 多模态 / Skill lifecycle / Plan D / RBAC / Quota / PG MVP / 浮窗 | 五一 sprint 计划 |
| 2 | datetime.utcnow 清理 | Python 3.12 deprecation 修复 | BL-L27 |
| 3 | ALLOW.md token-overlap | jieba 中文分词替代子串 | BL-L28 |
| 4 | FEATURE-TRACKS.md 主入口 | 24 个 track + 6 个发现 | 鸿波点播 |
| 5 | Dashboard 角色化 | manager 部门 quota/audit 卡片 | 鸿波点播 |
| 6 | dev_users.yaml 多账号 | DevUserSwitcher 顶部黄条 | 鸿波点播 (env hack 太丑) |
| 7 | autostart 默认 prod | DEV opt-in 走 CATFISH_AUTOSTART_ENV=dev | 鸿波点播 |
| 8 | PG migration 全套 | gateway db.py + quota PG + audit PG + alembic | 鸿波点播 |
| 9 | chat 接 record_usage | quota_events / gateway_audit 真 PG 双写 | bug 修 (PG 表空) |
| 10 | project-approval skill | 第 3 个业务 skill (BL-L6) | 鸿波拍板 |
| 11 | identity-server alembic | 双服务 version_table 隔离 | 鸿波拍板 |
| 12 | BL-E13 主动闲聊 C-MVP | LLM 起话题 + 通知 + Dashboard 卡 | 鸿波点播 (journal 攒) |

**测试**: identity 32 + gateway 78 + skill 1 + Companion 8 = 119 全过.
**PG 真验**: 5 张表 (alembic_version_gateway / _identity / users / registry_agents / quota_events / gateway_audit), chat → 双写正常, 5 行 ~106K tokens 累计.

**Phase 2 后端这次完整 ship**, 剩 Win 跨平台 + Production 部署 + Skills Hub 中央版.

---

## 📅 下次开工建议

**5 月 demo 准备 (10 天倒计时)**:
- 🔴 真机彩排 ×2 (前 3 天 + 前 1 天)
- 🔴 实录 case 视频 ×3 (1 天能搞)
- 🔴 PPT 实际填 (按 DECK 大纲)
- 🟠 让小鲶按 ProactiveCard 节奏陪聊, journal 自然攒满

**demo 后**:
- Production 部署设计 (#16, 客户能落地)
- 完整 BL-E13 (情境关联 / 节假日推断)
- Skills Hub 中央版 (#11)
- email-agent / feishu-monitor 接通 Companion 决策
