# 鲶鱼 · Feature Tracks (主入口)

> **快照**: 2026-05-04 (周一深夜) · **维护人**: 鸿波 · **更新**: 每周日晚 + 重大 ship 时
> **角色**: 这是**唯一**的"我们在做啥 / 还差啥 / 在哪个 phase"主入口.
> 其他 doc 角色见底部 § 文档地图.

---

## 🚦 Phase 进度 (一行看清)

```
Phase 1 · 单员工 AI 副手           [██████████] 99% · 5 月 demo + 1 PoC 客户
Phase 2 · 团队版 (SSO/RBAC/Win)    [████████░░] 78% · 五一 sprint Phase 2 + Skills Hub + Production MVP + brand
Phase 3 · ★ Federation             [███░░░░░░░] 30% · 五一 sprint Plan D MVP, Q4 ship
Phase 4 · 集团级 mesh               [░░░░░░░░░░]  0% · 2027 Q2+
```

> 📈 5/2-5/3 周末 sprint 大幅推进 (Phase 2 后端 + Skills Hub + brand kit + 关系建立):
>   - Phase 1: 92% → 99% (+ project-approval skill / 主动闲聊 / Quota 真接 chat / brand kit v1 /
>     命名权 / 专注模式 / 情绪关系建立)
>   - Phase 2: 35% → 78% (+ Dashboard 角色化 / 多账号 / PG 完整双写 / alembic 双服务 /
>     Quota 100% / Manager PUT UI / Admin 全局聚合 / 中央 Skills Hub MVP / dry-run + dedup /
>     SSO 登录页 brand 升级 + 防泄漏)
>   - 5/3 晚 1 (brand): 完整 brand kit (mascot / mark / app icon / 47 个 PNG / BRAND.md /
>     tokens 升级 / 6 处占位 🐟 全替换 / 防 hermes 泄漏 dispatch scrub + SOUL 铁律)
>   - 5/3 晚 2 (人格 sprint, 4 个里 ship 3 个): **BL-E11** 命名权 (员工自定义鲶鱼名 + 3 档人设) +
>     **BL-E15** 专注模式 (Cmd+Shift+F 全屏伪 IDE) + **BL-E19** 关系建立 (SOUL 情绪铁律 + session_meta
>     时间感 + Dashboard "鲶鱼对你的印象" 透明可删) — 留 BL-E14 PPT 吐槽下周做.
>     ⚠ 注: 5/3 commit 把 E19 误叫 E16, 实际 BACKLOG BL-E16 是"社交健康检查". 后续以 E19 为准
>   - 5/4 晚 1 (Onboarding 同事感): BL-E11 后续 — 11 处 UI 自指 ("鲶鱼" / "小鲶") 全换员工自定义 dynamic name
>   - 5/4 晚 2 (Hermes 升级研究 + Curator 分析): 不动代码, 写 `docs/HERMES-UPGRADE.md` ~430 行 — 含
>     0.10→0.12 完整 changelog 摘要 + 4 层补丁脆性评估 + Curator 接口 verified + 集成方案 5 步 +
>     5/8 后议程时间表
>   - 5/4 晚 3 (记忆纪律): **BL-MM1** SOUL 加"覆盖前 read-then-write + quote 旧值"铁律 (~130 行).
>     0 代码, 修小鲶之前对话里"夸了'现在就能做'"问题

---

## 🚨 当前最大风险 / 缺位 (Top 5)

| # | 风险 / 缺位 | 影响 | 跟踪 track |
|---|---|---|---|
| 1 | **5 月 demo 真机彩排没做** (距 5/14 demo ~11 天) | 现场翻车 | #18 销售物料 |
| 2 | **PPT / 实录视频没做** | 客户问"有视频吗"无应对 | #18 销售物料 |
| 3 | **employee_journal 真业务内容不够** | 跨 session 记忆演不出, 主动闲聊已缓解 | #25 + 持续用 |
| 4 | **Win 客户端 0% 没动** (5/2 拍板暂不动) | Q3 大客户阻塞 | #2 ★ |
| 5 | **Production 部署 0%** (现都跑鸿波本机 dev mode) | 给客户东西也跑不起 | #16 ★ |
| 6 | **团队 1 人** (Phase 2 一定带不动) | Q3 KPI 跳票 | #20 |

> 📊 **后端 / 卖点全部 ship 完, demo 阻塞全在演讲准备侧** (彩排 / PPT / 视频).

---

## 📋 全部 Tracks (按 phase + 重要性排)

### Phase 1 · 已 ship 主线

#### #1 Companion macOS [Phase 1, 95%]  ★ 5/3 加 Onboarding MVP
> Tauri 桌面客户端, 鲶鱼员工每天打开的入口.
- ✅ 三 tab (对话/控制台/仪表盘) + 浮窗 Cmd+Shift+Space 召唤 (5/5)
- ✅ 多模态 (Whisper.cpp 语音 + PDF/Excel/Word 文件解析)
- ✅ 仪表盘 5 卡 (身份/服务/Catalog/Skills/审计/Quota 接通)
- ✅ **Onboarding 引导 4 步** (5/3 BL-F3 MVP): welcome / 鉴权 / 选模型 / 试聊, localStorage 记 onboarded
- ⬜ 数据迁移工具 (换电脑搬 memory/skill/state) · 0.5 周 (BL-F4)
- ⬜ 备份方案 (auto backup) · 0.5 周 (BL-F5)
- ⬜ 完整 Onboarding (动画 / 多语言 / 真试聊 / 跟 SSO flow 集成) · 0.5 周

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

#### #11 Skills Hub (中央托管) [Phase 2, 70%]  ★ 5/2 中央 server MVP
> 组织级技能市场, 让员工分享 skill, 部门集体学习.
- ✅ 本机版 MVP (skill_install 工具) (5/3)
- ✅ **中央 hub server FastAPI MVP** (5/2): publish / list / get / download / delete + audit
- ✅ **dry-run 验证** (5/2 BL-C12): skill_install 后 import 检查 + 失败 rollback
- ✅ **dedup 检查** (5/2 BL-C13): install 前查同名 / 描述相似的 skill, force_install 跳
- ✅ 文件系统存储 (~/.catfish-hub/), 多 version 共存, audit jsonl
- ✅ 21 storage 测过 + 6 dry-run/dedup 测过
- ⬜ 审核流 (manager publish → admin approve → live) · 1 周 (现 MVP 直发)
- ⬜ Companion catfish_skill_install 改 hub URL 拉取 (现只本机 source_dir) · 0.5 周
- ⬜ 部门级 skill auto-推 · 1-2 周 (依赖 #8 federation 协议)
- ⬜ PG 存储替代文件 (Phase 2.5) · 1 周

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
- ✅ 专注模式 (前 "领导来了") · 5/3 ship (BL-E15, 见 #27)
- ⬜ menubar 状态指示 · 0.5 周

#### #25 ★ 主动闲聊 (BL-E13) [Phase 1, 30% C-MVP]  ★ 5/2 ship MVP
> 让小鲶按时段主动找员工聊, 不让 employee_journal 饿死 (demo 跨 session 记忆有内容).
- ✅ gateway proactive.py: 读 journal tail + 时段 + qwen-flash 生成 starter
- ✅ /api/proactive/starter 端点
- ✅ Companion ProactiveCard (Dashboard 第一卡, 30min 自动换)
- ✅ "跟小鲶聊聊 →" 按钮 → 切 chat tab + 输入框预填 starter (zustand)
- ✅ useProactiveScheduler hook: 9:30 / 14:00 / 17:30 自动 macOS 通知 (localStorage 防重)
- ✅ Tauri notify() 用 osascript display notification (无新依赖)
- ⚠ **BL-E13-FIX (5/4 鸿波反馈"还是不能自动聊天")**: 现实测概率 3 个 slot 全 miss (员工不在前台). 修复 demo 前必做:
  - ⬜ catch-up 逻辑: mount 时补发当天已过期但未 fire 的 slot (~10 分钟)
  - ⬜ 加宽时段窗口: 9:30 → 08:00-10:00, 14:00 → 12:00-14:00, 17:30 → 17:00-19:00 (~20 分钟)
  - ⬜ 通知权限引导: 第一次启动主动调一次空通知触发 macOS 权限弹窗 + LoginGate 提示开权限 (~5 分钟)
  - **合计 ~40 分钟, 0 风险, demo 前做**
- ⬜ 节假日 / 晚 10 点不打扰 (推断状态) · 1 周
- ⬜ snooze / 拒绝机制 (现 localStorage 开关) · 0.5 周
- ⬜ 配置 UI (时段 / 频率 / 模板) · 0.5 周
- ⬜ 跨 session 深度情境关联 (journal 结构化解析) · 1-2 周
- ⬜ Phase 2.5: 真主动 (AI 行为信号触发, 不死时间) · 1-2 周, demo 后
- **完整 BL-E13**: 1-2 周, demo 后做

#### #26 ★ 品牌视觉身份 (brand kit v1) [Phase 1, 90%]  ★ 5/3 晚 ship 完整套件
> 之前 logo / 配色 / 头像全是 🐟 emoji 占位. 5 月 demo 客户看到必扣分. 今晚一次到位 ship.
- ✅ **5 件套 SVG** (`branding/`): logo-mascot (完整吉祥物 480x480) + logo-mark (极简圆形 256x256) +
  logo-mark-mono (单色反白) + avatar-circle (聊天头像) + app-icon-master (macOS squircle)
- ✅ **47 个 app icon 全套** (`render_icons.py` 一键): macOS .icns + Windows .ico (multi-size embedded) +
  iOS 18 个尺寸 + Android 5 密度 mipmap × 3 类 + Windows tiles 9 个 + favicon
- ✅ **BRAND.md 速查** (`edge/identity/`, 跟 SOUL.md 同级): 角色定位 + 4 件套用途表 + 配色 +
  字体 + 净空区 + 用法红线 + 文案语调 + 应用清单 + 改 brand SOP
- ✅ **配色升级** (`tokens.css`): 旧 #06b6d4 亮青 → #0E5F66 墨青 + 暖橙 #F47B3D + 暖米 #FAF1E4
  (深色模式同步) - 6 个新 CSS 变量
- ✅ **6 处占位 🐟 → 正式 mascot** (Companion 前端): LoginGate / OnboardingWizard /
  ChatPanel / ChatTab / LearningCard / useProactiveScheduler 通知
- ✅ **SSO 登录页 brand 升级** (identity-server): 内联 mark SVG (无静态文件依赖) + 全页色升级 +
  暖橙 CTA + favicon (data: URI) + Cache-Control no-store 防浏览器缓存老 HTML
- ✅ **Demo PPT 封面** (`branding/demo-cover.pptx`): 深青底 + 大字"鲶鱼 Catfish" +
  暖橙锚点 slogan + 三行卖点 (本机算力 / 公文报表 / 审计合规)
- ✅ **品牌铁律** (SOUL.md 加章节): 禁止小鲶向员工说 "hermes / ~/.hermes / 未初始化", 必说"鲶鱼内置存储"
- ✅ **dispatch 层 brand scrub** (adapter.py): hermes memory_* 工具响应里的 ~/.hermes 路径 +
  hermes 字眼自动过滤再给 LLM (audit log 留原文); 11 个测试覆盖
- ⬜ 桌面壁纸 / 名片 / 邮件签名模板 (P1, demo 后) · 0.5 周
- ⬜ H5 介绍页 (P2) · 1 周
- 见 `edge/identity/BRAND.md` (完整速查) + `branding/` (源文件)

#### #27 ★ 人格 sprint (BL-E11 + E15 + E16) [Phase 1, 75%]  ★ 5/3 晚 ship 3/4
> "鲶鱼不是 IT 工具, 是有性格 / 站员工这边 / 记得你的同事" — 跟 ChatGPT/Copilot 区分点.
> 4 个 demo 杀器 ship 3 个 (留 BL-E14 PPT 吐槽下周做).
- ✅ **BL-E11 命名权** (1 天): 员工 Onboarding 第 2 步给鲶鱼起名 + 3 档人设 (温柔/直爽/毒舌);
  yaml 持久化 + Tauri commands + zustand store + Dashboard AgentPrefsCard 改名;
  X-Catfish-Agent-Name/Personality header 让 gateway 拼 personalization preamble 在 SOUL 前面;
  9 Rust 单测 + 12 Python 单测
- ✅ **BL-E15 专注模式** (1 天, 央企语境从"领导来了"改名): Cmd+Shift+F 全屏伪 IDE;
  状态行/计时/底栏/光标都拟真 (GitHub Dark 配色);
  TabBar 加"⏸ 专注"按钮 (不知快捷键的员工也能用); Esc/快捷键/按钮 3 种退出
- ✅ **BL-E16 鲶鱼情绪 / 关系建立** (1.5 天):
  SOUL.md 加"情绪 / 关系建立"铁律 (5 类 ✅ 适合做 + 6 类 ❌ 不做 + 频率: 每 5-10 session 1 次);
  session_meta.py 持久化 last_chat_at + today_count, 给 LLM 时间感 ("3 天没找我"/"今天第 5 次");
  Dashboard "鲶鱼对你的印象" 卡 (透明 + 一键清空, 防 creepy);
  17 Python 单测 + 7 Rust 单测
- ⬜ **BL-E14 鲶鱼吐槽 PPT** (3-5 天, 留下周): pptx parser + critical personality + 拖放 UI
- 见 `docs/IDEAS.md` § 10/13/14/17 + `docs/BACKLOG.md` BL-E11/E14/E15/E19 (E16 是另外的"社交健康检查")

#### #28 ★ 记忆纪律 / 记忆是资产 / 越用越懂 (BL-MM1~MM8) [Phase 1.5, 25%]  ★ 5/4 晚拍板 + ship MM1+MM5
> 鸿波 5/4 共识: **记忆是资产, 错了 update > 删除, 留版本作为成长痕迹**.
> 5/4 晚扩: 加"越用越懂员工偏好" 维度 (鸿波问"feedback / 性格 / 工作模式 / 文书风格"); 4 维全 partial 或 ❌, 4 个工作量等级方案 A 已 ship, B/C/D 排 5/8 后启动.

**M.1 记忆覆盖 (改不删, 留版本):**
- ✅ **BL-MM1 记忆覆盖纪律 (SOUL 章节)** (5/4): 0 后端改动. 走 read-then-write + 把旧值 inline 塞进新值的备注里, 模拟版本感. 4 个 ❌ 禁止 (空说"改了"/编旧值/blind overwrite/不告知)
- ⬜ **BL-MM2 catfish_remember 后端真版本化** (~0.7 天, 5/8 后议程): `session_facts.json` schema 改 `{key: [{value, ts, ...}]}` + 工具返回加 `previous_value` 字段
- ⬜ **BL-MM3 hermes memory_save 包一层版本化** (~0.5 天, 5/15 hermes 升级窗口一并): adapter.py read-modify-write 双调用模拟, BL-D9 思路扩展
- ⬜ **BL-MM4 Dashboard 记忆版本历史卡** (~1-2 天, demo 后): 列所有 key + 时间线 + 两版本 diff (像 git log). **真客户卖点**: "鲶鱼对你的认知怎么演化"

**M.2 主动学习 / feedback / 越用越懂员工:**
- ✅ **BL-MM5 主动学习员工偏好 (SOUL 章节)** (5/4 晚, **方案 A**): 0 后端代码. 4 类信号 (强显式立即落盘 / 弱显式攒 3 次主动问 / 强隐式不主动学 / 弱隐式不学) + 频率纪律 (每 session 1 次封顶) + 落盘结构化模板 + ❌ 禁止 (不评论生活/情绪/不假装观察). 跟 BL-MM1 区分: MM1 被动 correction, MM5 主动学 preference
- ⬜ **BL-MM6 显式 feedback UI** (~2-3 天, **方案 B**, 5/8 后): ChatBubble 加 👍/👎/"改一下" 按钮 + `/api/feedback` + `~/.catfish/feedback.jsonl` + Dashboard "你给我的反馈" 卡
- ⬜ **BL-MM7 结构化用户画像** (~1 周, **方案 C**, 5/8 后): `~/.catfish/user_profile.json` (writing_style / work_pattern / personality_traits + evidence_count) + 满 N 次主动确认 + Dashboard 卡可调可锁. 真"自进化"故事卖点
- ⬜ **BL-MM8 文书风格 fingerprint** (~1-2 周, **方案 D**, 6 月起): 员工历史文档抽风格指纹, 写新文档前调用. 解决"每次写汇报小鲶都从零猜" 央企痛点

- 见 `edge/identity/SOUL.md § 记忆覆盖纪律 + § 主动学习员工偏好` + `docs/BACKLOG.md § M`

#### #29 ★ Hermes 升级 + Curator 集成 [Phase 1.5, 30% 研究完成]  ★ 5/4 晚研究, 5/8 后启动议程
> NousResearch hermes-agent 升 0.12.0. 我们 0.10.0. 完整研究 + 集成方案归档, demo 前不动.
- ✅ **0.10→0.12 changelog 完整摘要** (5/4): 0.11 React/Ink CLI 重写 + Profile 系统 + Transport ABC. 0.12 后台 Curator + 57% 冷启 + 多 provider
- ✅ **catfish 4 层补丁脆性评估**: apply_brand_patch.py 468 行 AST 🔴 高 / rebrand.sh 183 行 sed 🔴 高 / string-map.yaml 84 行 🔴 高 / dispatch scrub (BL-D9) 🟢 低
- ✅ **Curator 接口 verified** (源码 quote): `curator.enabled: false` 一行 disable, 4 个参数 yaml 可调, Strict invariant *only touches agent-created skills*, **never auto-deletes — only archives**, pin 可豁免, `.curator_state` 可外部写
- ✅ **Curator vs Skills Hub 集成方案 5 步**: 不禁用, 默认开 + 保守参数 + 装时 pin 双保险 + Onboarding 知情同意 + Phase 2.5 反向数据流 → admin 看公司级 skill 健康热图
- ⬜ **5/8 后 verify 2 件**: `tools/skill_usage.is_agent_created()` 判定逻辑 + `skill_manage` pin API
- ⬜ **5/15-5/22 升级实施** (走 HERMES-UPGRADE.md § 5 阶段 B): brand patch 重构 字符串 → 三层运行时拦截 (wrapper subprocess + Shell Hook + 兜底 source patch). 估计 468 行能裁到 50-100 行
- ⬜ **Q3 上游 PR i18n hook**: 给 NousResearch 提 PR, 一劳永逸
- 完整方案见 `docs/HERMES-UPGRADE.md` (430 行, 含时间表 / 风险表 / 升级回归 checklist)

---

### 缺位 area · 完全没动

#### #16 ★ Production 部署 + 运维 [Phase 1.5, 50% MVP]  ★ 5/3 docker-compose ship
> 现都跑鸿波本机 dev mode, 给客户也跑不起.
- ✅ **docker-compose 全栈** (5/3 BL-F7 MVP): postgres + identity + gateway + skills-hub + nginx 反代
- ✅ **Dockerfile × 2** (gateway / identity, builder + runtime 两阶段, 非 root, healthcheck, alembic 启动建表)
- ✅ **nginx.conf.example** (HTTPS 强制 / SSE 不缓冲 / /api / /sso / /hub 路由 / cert 配置)
- ✅ **.env.production.example** (PG 密码 / OIDC issuer / API key / hub token)
- ✅ **docs/PRODUCTION-DEPLOYMENT.md** (~250 行, 5 步 15 分钟装好 + 升级 + 监控 + 备份 + 5 个常见问题)
- ⬜ k8s manifests (Helm chart, 大客户 ≥ 200 员工用) · 1 周
- ⬜ catfish-cloud 多租户运营手册 (SaaS 版) · 1 周 (BL-F8)
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

**~30 个 commit**, 127+ 单测 0 fail, 真机端到端跑通 5 大子系统 (PG / RBAC / Quota / Hub / 主动闲聊).

| # | commit 主题 | 改动 | 触发 |
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
| 13 | RBAC 三件套 | manager PUT quota / admin 全局聚合 / DepartmentQuotaCard inline edit | 鸿波点播 |
| 14 | Quota 接 chat 阻断 | check_quota → 429 + Companion friendly UI | 鸿波点播 |
| 15 | useChat status 覆盖 bug 修 | streamChat 后无条件 done 覆盖 onError 的 error 状态 (UI 不显错的根因) | 真机调试 |
| 16 | BL-C12 dry-run + 回滚 | skill_install 后 importlib 验证 + 失败 rollback | 鸿波点播 |
| 17 | BL-C13 dedup 检查 | install 前查同名 / 描述相似 skill | 鸿波点播 |
| 18 | 中央 Skills Hub server MVP | central/skills-hub FastAPI: publish/list/get/download/delete + audit | 鸿波点播 |

**测试**: tool-bridge 23 + skills-hub 21 + gateway 83 + identity 32 + Companion 8 = **167 全过**.

**真机验证 (Mac)**:
- ✅ PG 真双写: 5 张表 (alembic_version_gateway/_identity / users / registry_agents / quota_events / gateway_audit)
- ✅ Quota 429 闭环: Alice → 撞 429 → friendly 横条 → manager 改 → 再聊通过
- ✅ Skills Hub publish/list/download/audit 全通
- ✅ Companion 角色切换 (admin/manager/employee 看不同卡)
- ✅ 跨 session 记忆 (employee_journal 真注入, 小鲶引用上次具体事项)

**Phase 2 后端 + 用户态 + Skills Hub 中央 MVP 完整 ship**, 剩:
- Win 跨平台 (拍板暂不动)
- Production 部署 (0%)
- Hub 审核流 / Companion hub URL 拉取 / 部门 auto-push (3 周, demo 后)
- email-agent / feishu-monitor 接通 Companion (待决策)

---

## 📅 下次开工建议

**🔴 demo 阻塞 (5/14 demo, 距今 ~11 天)**:
- 真机彩排 ×2 (前 3 天 5/11 + 前 1 天 5/13)
- 实录 case 视频 ×3 (1 天搞)
- PPT 实际填 (按 docs/MAY-DEMO-DECK.md 大纲, 1 天)
- employee_journal 持续攒 (主动闲聊 ProactiveCard 帮你, 每天聊 2-3 句)

**🟠 demo 后做**:
- Production 部署 (#16, 客户能落地)
- Companion `catfish_skill_install` 改 hub URL 拉取 (~0.5 周)
- Skills Hub 审核流 (#11, ~1 周)
- 部门 auto-push (依赖 federation, ~1-2 周)
- 完整 BL-E13 主动闲聊 (情境关联 / 节假日推断, ~1-2 周)
- email-agent / feishu-monitor 接通决策

**🔴 商业决策 (鸿波拍板)**:
- 5 月 demo 选 1-3 家具体客户名单 (5/5 前)
- PoC 报价单 (50/200/1000+ 三档)
- 销售路径 (自销 vs 渠道)
- 5 月底前启动扩招 (1 后端 + 1 销售)

---

## 📊 demo 卖点 verified 全清单 (10 个)

| 卖点 | 演法 | 状态 |
|---|---|---|
| 1. **跨 session 记忆** | "早上好" → 小鲶引用 journal 具体事 | ✅ 真验过 |
| 2. **多模态** | 上传 PDF/Excel + 语音 | ✅ |
| 3. **业务 skill (3 个)** | 汇报 / 周报 / 立项 .docx 真出文件 | ✅ |
| 4. **RBAC 三角色** | DEV 切 admin/manager/employee 看不同卡 | ✅ |
| 5. **Quota 闭环** | 撞 429 → friendly 提示 → manager 改 → 通过 | ✅ |
| 6. **中央 PG audit** | psql 直查 quota_events / gateway_audit | ✅ |
| 7. **Skills Hub** | publish → list → download | ✅ |
| 8. **Plan D federation 协议** | alice ↔ bob 单机 mock (给 IT lead 看) | ✅ |
| 9. **主动闲聊** | macOS 通知 + Dashboard 起话题 | ✅ |
| 10. **浮窗** | Cmd+Shift+Space 全局召唤 | ✅ |

10 个卖点全部技术 ready, demo 主战场转移到**演讲 / 物料**.
