# 鲶鱼 · 项目仪表盘 (PROJECT STATUS)

> **快照日期**: 2026-04-30 (周三晚)
> **维护人**: 鸿波
> **更新节奏**: 每周日晚 / 每个里程碑后
> **用途**: 一页看清"现在在哪 / 要去哪 / 还差什么". 不替代 BACKLOG (工程颗粒) / ROADMAP (客户视角) / CHANGELOG (日记), **它们的 meta-view**.

---

## 一句话使命

> **让每个企业员工拥有一个会上网、会收邮件、越用越懂他、像同事一样记得你的数字副手, 数据全在员工本机, 中央可审不可看.**

不是 ChatGPT (聊天工具) · 不是星辰 (单层 Agent) · 是企业 LLM 中台 + 边缘副手 + Phase 3 跨员工 Federation.

---

## 当前状态 (一段话)

🐟 **Phase 1 已 ship 90%, 剩 5 月 demo 准备 + 真机彩排 + PoC 转化**.

技术栈跑稳: 三层架构 (Companion + tool-bridge + 中央 gateway) / SSO 全链路 / 凭据安全 / 中央可审 / 跨厂商 LLM / **跨 session 上下文 (4-30 接通, 5 月 demo 致命卖点)** / 2 个业务 skill (汇报材料 + 周报). 测试 ~50% 覆盖, gateway 380 + tool-bridge 165 + identity 31 + skills 42 全过.

商业上: 5 月中旬给 1-3 家客户做正式 demo, 6 月 PoC 准备, 7 月驻场部署, 8 月 10 员工试用. **没付费客户, 团队基本就鸿波 1 人 + 鲶鱼协作**.

最大风险: 团队规模小 / 销售物料 (实录 demo 视频) 没录 / Win 跨平台还没动 / SSO 真实客户对接没验过.

---

## 4 Phase 进度 (跟 ROADMAP.md 对齐)

```
Phase 1 · 单员工 AI 副手           [████████░░] 90% · 5-6 月 demo + 1 PoC 客户落地
Phase 2 · 团队版 (SSO/RBAC/Win)    [██░░░░░░░░] 20% · Q3 2026, SSO 已 ship 是基础
Phase 3 · ★ Catfish Federation     [█░░░░░░░░░] 10% · Q4 2026, 设计已定 (BL-E17/E18)
Phase 4 · 集团级 agent mesh         [░░░░░░░░░░]  0% · 2027 Q2+, 概念阶段
```

---

## 🟠 短期 · 5 月 (距 demo 17 天)

### 完成度: 75%

**已就绪**
- ✅ 5 月 demo 演讲稿全套 (DECK 32 张 / ELEVATOR V1-V5 / Q&A 13 题 / PREP 检查清单)
- ✅ 4 个核心 demo 场景跑通: EIS+汇报 / 屏幕邮件 / IT 仪表盘 / **场景 4 跨 session 记忆 (4-30 刚接通)**
- ✅ leadership-briefing skill 真复刻公文模板 (4 段 + 表格附件 + 双 backend 错别字)
- ✅ weekly-report skill (.xlsx 周报)
- ✅ SSO 链路 (catfish-identity / 飞书 / dev_token fallback)
- ✅ Companion 仪表盘 (AuditCard / 模型分布 / catfish skills 显示)

**待办 (demo 前必做, 5-1 ~ 5-15)**

★ **五一 5 天 sprint (5/1~5/5) — Phase 2 核心功能跃迁** ← 详见 BACKLOG §M
- 🔵 Day 1 多模态 (语音 macOS 原生听写 + 文件 PDF/Excel/Word)
- 🔵 Day 2 Skill 全生命周期 4 步 (版本/下线/删除/审计)
- 🔵 Day 3 Skills Hub MVP + Plan D 协议设计
- 🔵 Day 4 Plan D registry + A 端 + B 端 (MCP JSON-RPC over SSE + JWT 互信)
- 🔵 Day 5 Plan D 隐私 ALLOW.md + audit + 单机 mock 2 员工 + A2A demo

5 月 demo 准备:
- 🔴 真机彩排 ×2 (demo 前 3 天 + 前 1 天, 计时录屏复盘) ← BL-X8 / L1
- 🔴 demo 前 7 天积累真实 employee_journal.md (没内容场景 4 演不出来) ← BL-L2
- 🔴 实录 3 个 1 分钟真实 case 视频 ← BL-L19
- 🔴 PPT 实际制作 (按 DECK 大纲填 Keynote / 飞书) ← BL-L20
- 🟠 公司机器测 catfish-private (qwen 122b) 真实 tool 调用能力 ← BL-X7
- 🟠 部门汇报模板 + EIS 截图带回来 ← BL-L3 / L4
- 🟠 第 3 个 catfish skill (annual-summary 或 project-approval) ← BL-L6
- 🟠 SSO 接入文档收尾 (给客户 IT 自助接入) ← BL-L21
- 🟠 Journal 向量召回 (5/6 上班后用公司 bge-m3 验) ← BL-L8

**风险**
- 🟥 真机彩排没做过, **场景 4 跨 session 记忆**没在客户场景演过, 现场翻车概率不低
- 🟥 employee_journal 现在只 9 段, demo 前需要真实工作积累到 15-20 段才有说服力
- 🟧 1 个人准备 1-3 家客户 + 演讲 + 全套物料, 时间紧

**5 月底要拿到的结果**
- 1-3 家客户做完 demo
- 至少 1 家明确说"想试" → 启动 PoC 流程 (POC-PLAN.md)
- 收集客户 5 个最具体反馈 (不是"挺好的", 是"X 我们用不了因为 Y")

---

## 🟡 中期 · Q3-Q4 2026 (Phase 2 团队版)

### 完成度: 15-20%

**已就绪 (Phase 2 前置)**
- ✅ SSO 全链路基础 (catfish-identity OIDC + 飞书 + dev_token, 客户 IT 600 行可审)
- ✅ 中央审计 (gateway audit JSONL, 看 metadata 不看内容)
- ✅ 跨厂商 LLM 路由基础

**待办**
- ⬜ **RBAC 系统** (经理看部门数据 / 员工看自己) — 1 周, 还没动 ← BL-D8
- ⬜ **Quota 配额管理** (按部门 / 按人 / 按模型) — 1 周 ← BL-D9
- ⬜ **Skill share / Skills Hub MVP** (Plan D 入门) — 3-4 周 ← BL-D1
- ⬜ **Win 跨平台** (Tauri Win + IPC TCP + Credential Manager) — 4-5 天集中做, 暂缓到 Phase 1 末 ← BL-C2~C5
- ⬜ **钉钉 / 企微 SSO adapter** — 各 2 周, 客户视情况 ← BL-D15/D16
- ⬜ **IDP UserStore PG/LDAP backend** (现 yaml 短期够) — 3-5 天 ← BL-D17 / L13
- ⬜ **Skill 全生命周期补 5 步** (版本 / 下线 / 删除 / 审计 / 分享) — 1-2 周 ← BL-L14~L18
- ⬜ **journal 向量召回** (现 50KB tail-truncate, 长期员工要升级) — 1-2 周 ← BL-L8

**风险**
- 🟥 RBAC / Quota 还**完全没动**, Phase 2 (Q3) 启动前 6-8 周时间
- 🟥 Skills Hub 是 Phase 2 核心, 也**完全没动**, 3-4 周工作量, 团队 1 人做不完
- 🟧 Win 跨平台只是工程量大不复杂 (4-5 天), 但客户里 Win 占比高就成阻塞

**Q3 末要拿到的结果**
- 第 1 个付费客户 (50 人部门级私有部署)
- Win + Mac 全覆盖
- Skill share 雏形跑通 (一个员工教鲶鱼新流程, 同部门同事自动学会)

---

## 🟢 长期 · 2027+ (Phase 3 Federation + Phase 4 集团级)

### 完成度: 5-10%

**已就绪 (Phase 3 前置)**
- ✅ 设计文档 (BL-E17/E18, IDEAS #16+#42, 在 BACKLOG 上不是临时想出来)
- ✅ 三层架构 + identity 层独立 — 已经为 Phase 3 留好接口
- ✅ SSO + audit 是 Phase 3 必须前置 (已 ship)

**待办**
- ⬜ **Catfish Federation 协议设计** — agent identity (基于 SSO) + mutual auth + audit
- ⬜ **隐私边界严格化** — A 鲶鱼只回答 A 显式授权过的事, 默认拒绝
- ⬜ **跨员工 agent 协作 demo** — 例如"问下张三老板对项目 X 怎么看"
- ⬜ **集体知识沉淀机制** — 一个员工教的流程同部门自动学
- ⬜ **集团级 skill 库** — 跨子公司贡献 + 中央审核 + 复用 (Phase 4)
- ⬜ **集团级 agent mesh** — 跨部门 / 跨子公司协作 (Phase 4)

**风险**
- 🟥 Phase 3 是**真正护城河**, 但 1 人团队规模做不出来, 必须先扩到 3-5 人
- 🟥 Phase 3 Q4 ship 是给客户的承诺, 要交付得起 — 否则被打脸
- 🟧 Phase 4 在 2027+, 取决于 Phase 1/2 的客户基础规模

**2027 末要拿到的结果**
- Phase 3 Catfish Federation ship
- 5+ 部门用户 (跨员工协作真实场景验证)
- ARR 100-500 万 / 月活 50+ / 私部 10+ 客户 (Phase 3 KPI BL-B11)

---

## 🔴 当前不足 (诚实复盘)

### 技术不足

| 维度 | 现状 | 缺口 |
|---|---|---|
| 测试覆盖 | ~50% (gateway 380 / tool-bridge 165 / identity 31 / skills 42) | 目标 70%+, 缺端到端集成测试 (BL-G6) |
| Win 跨平台 | 完全没动 | 4-5 天工程量, 但暂缓到 Phase 1 末 |
| Skill 全生命周期 | 5/10 步 ship (设计/加载/调用/inject/guard) | 缺 5 步: 版本/下线/删除/审计/分享 |
| journal 向量召回 | 现 50KB tail-truncate | 长期员工 (2-3 年) 要升级 |
| IDP UserStore | yaml 短期够 | Phase 2 前要 PG/LDAP |
| CI/CD pipeline | 没 (本机手动跑测试) | GitHub Actions 半周搞定 |
| 监控告警 | 没 (gateway 错误率 / hermes 崩溃没报警) | Phase 2 前必做 |

### 产品不足

| 维度 | 现状 | 缺口 |
|---|---|---|
| 业务 skill 数量 | 2 个 (汇报 / 周报) | 至少 5-8 个才支撑 demo 多样性 |
| Companion UI 形态 | 单一对话窗 | 浮窗 / 菜单栏 / 托盘还没做 (BL-D10/D11) |
| 多模态 | 截图 + vision 已通 | 语音输入 / 文件上传 PDF/Excel 还没做 (BL-I1/I2) |
| Onboarding 体验 | 没 (员工自己读 README 摸索) | 全图形化引导 (BL-F3) |
| 邮件 tab GUI | 没 (4 个 adapter 齐了再做) | (BL-D13) |

### 商业不足

| 维度 | 现状 | 缺口 |
|---|---|---|
| 付费客户 | 0 | 5 月 demo 转 1-3 PoC, Q3 转 1 付费 |
| 销售物料 | 文档全 (DECK/ELEVATOR/Q&A/PREP), 但**视频 0 个** | 实录 3 个 1 分钟 case 视频, 1 天能搞 |
| 实际 PPT | 没做 (只有 DECK 大纲) | 1 天填 Keynote/飞书 |
| 报价单 | 决策没拍 (BL-B2) | 50/200/1000+ 三档 |
| 销售路径 | 决策没拍 (BL-B6) | 自销 vs 渠道, 中国 to-B 特殊 |
| 营销网站 | 没 (catfish.ai 落地页) | 1 周 (BL-B5) |
| 品牌 / Logo / 名片 | 部分 (squircle icon 已基础) | 公司名片 / 邮件签名 / Press kit (BL-J3/J4) |

### 团队不足

| 维度 | 现状 | 缺口 |
|---|---|---|
| 团队规模 | 鸿波 1 人 + 鲶鱼协作 (dogfood) | Phase 2 要扩 3-5 人 (1 后端 + 1 前端 + 1 销售) |
| 销售 / BD | 0 | 5 月 demo 后必须考虑 (1 个商务) |
| 客户成功 | 0 | PoC 启动后必须 (1 个驻场 / FAE) |
| 法务 / 合规 | 0 | 软著 + 商标 + 法律 review (BL-H 全段) |

### 资源不足

| 维度 | 现状 | 缺口 |
|---|---|---|
| 公司注册 / 工商 | 不确定 (BL-H3) | Phase 1 内必做 |
| 软著申报 | 没 | Phase 1 内 (CHANGELOG 已经为这个写) |
| 商标"鲶鱼/Catfish" | 没注 | 容易被抢注 |
| Production 服务器 | 没 (现都在鸿波本机 + dev mode) | gateway 部署 (BL-F7), 1 周 |
| 备份方案 | 没 (memory + state.db + journal 没自动备份) | 0.5 周 (BL-F5) |

---

## 🎯 本月 (5 月) 关键决策清单

> 不能再拖, 5 月底前必须拍板:

| 项 | 决策时间 | 决策人 | 当前状态 |
|---|---|---|---|
| 5 月 demo 客户选 1-3 家具体名单 | 5-5 前 | 鸿波 | ⬜ |
| PoC 报价单 (50/200/1000+ 三档) | demo 前 | 鸿波 | ⬜ (BL-B2) |
| 销售路径 (自销 vs 渠道) | 5 月底 | 鸿波 | ⬜ (BL-B6) |
| 第 3 个 catfish skill 选哪个 (annual-summary 还是 project-approval) | 5-3 前 | 鸿波 | ⬜ (BL-L6) |
| catfish skill 开闭分界 (哪些开源哪些闭) | demo 后 | 鸿波 | ⬜ (BL-A8) |
| 是否注册公司 + 商标 | 5 月内 | 鸿波 | 🚧 |
| Phase 2 团队扩招启动 (1 后端 + 1 销售) | 5 月底 | 鸿波 | ⬜ |
| catfish-private (qwen 122b) tool 能力实测 | 5-1 (在公司测) | 鸿波 | 🚧 (BL-X7) |

---

## 🚨 Top 5 风险 (按概率 × 影响排序)

| # | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| 1 | 5 月 demo 现场翻车 (尤其场景 4 跨 session 没演过) | 🟥 中高 | 🟥 高 (客户跑掉) | 真机彩排 ×2, 准备录屏 backup, 紧急话术 (Q&A 13 题已备) |
| 2 | 1 人团队带不动 Phase 2 (RBAC / Skills Hub / Win 同时上) | 🟥 高 | 🟥 高 (Q3 KPI 跳票) | 5 月底前启动扩招, 同步把 Phase 2 大功能 backlog 做细 |
| 3 | demo 顺利但客户不签 (PoC 漏斗 0 转化) | 🟧 中 | 🟥 高 (商业基础没了) | POC-PLAN.md 已备, demo 后立即跟进, 7 天内发 PoC 邀请 |
| 4 | 跨 session 记忆隐私设计被 CISO 质疑 (尤其国央企) | 🟧 中 | 🟧 中 (掉 1 客户但不致命) | Q&A #13 已备 (5 条隐私保证 + cat journal 现场演), 客户 IT 可 grep 审计 |
| 5 | hermes upstream 改动破坏 catfish 集成 (skill / state.db / venv) | 🟨 低中 | 🟧 中 (修 1 周) | 4-30 已加 SOUL 反幻觉铁律, 4-29 修了 hermes 自创 skill 抢占 |

---

## 进度跟踪

> 每周日晚更新一行, 看趋势.

| 周 | Phase 1 % | Phase 2 % | Phase 3 % | 关键 ship | 阻塞 |
|---|---|---|---|---|---|
| W17 (4-21~27) | 75% | 10% | 5% | gateway P0 + 仪表盘 + Companion v1 | hermes venv proxy |
| W18 (4-28~5-4) | 85% | 15% | 8% | SSO 全链路 + Skill 系统 + 业务 skill | tool 调用模型选择 |
| **W19 (4-29~5-5)** | **90%** | **20%** | **10%** | ★ 跨 session 上下文 + leadership 4 段 + 双 backend 错别字 + 演讲稿全套 + BACKLOG v2 + MATRIX | 真机彩排没做 |
| **W19a (五一 sprint 5/1~5/5)** | **95%** (目标) | **35%** (目标) | **15%** (目标 ★) | 多模态 (语音+文件) + Skill 全生命周期 4 步 + Skills Hub MVP + **Plan D B 协议真实现 (单机 mock)** | macOS 听写 / SSE+JWT 跨实例 调试 |
| W20 (5-6~12) | 95% (目标) | 25% | 12% | demo 前彩排 + journal 积累 + PPT + 视频 + SSO 客户文档 | (待) |
| W21 (5-13~19) | 100% (目标) | 30% | 15% | 5 月 demo 完成 1-3 家客户 + PoC 邀请 | (待) |

---

## 跟其他文档关系

```
PROJECT-STATUS.md (本) ─── meta-view, 高密度 ────────────→ 鸿波每周翻 + 给投资人/团队看
   ↓
ROADMAP.md ────────────── 4 Phase 客户视角 ───────────→ 客户翻
BACKLOG.md ────────────── 全量积压 (186 项) ────────→ 鸿波每周一 review
CAPABILITY-MATRIX.md ──── 现状能力快照 (35+ 项) ──→ 客户/法务/自己回查能力
CHANGELOG.md ──────────── 每天 ship 日记 ──────────→ 鸿波每天写, 软著申报
catfish-design.md ────── 14 章基础架构 (4-22) ───→ 新人入坑读
```

---

## 维护

- **每周日晚**: 更新"进度跟踪"加一行 + 调整 Phase % + 更新关键决策状态
- **每个里程碑** (demo / PoC 启动 / 付费客户 / Phase 升级): 重写"当前状态" 一段 + 调"风险" 排序
- **每月 1 号**: 整页 review, 调"4 Phase 进度" + "短中长期"完成度 + "不足" 缺口

---

## 决策签名

> v1 = 2026-04-30 创建. 解决"BACKLOG / MATRIX / CHANGELOG / ROADMAP 四件套都看不到一页全景" 问题.
>
> 创始人 / 投资人 / 团队 / 自己回顾时, 翻这一份够看现状 + 规划 + 不足.
> 工程颗粒细节去 BACKLOG, 时间日记去 CHANGELOG, 客户视角去 ROADMAP, 能力清单去 CAPABILITY-MATRIX.
