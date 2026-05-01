# 鲶鱼文档目录

> **2026-04-30 升级**: BACKLOG v2 + CAPABILITY-MATRIX 落地, 本 README 同步加入路标.

**设计文档主体**: 见 `/Users/chenhongbo/person_task/catfish-design.md` (本目录外, 独立文件; 4-22 写, 14 章基础架构仍正确, 但功能进度过时, 以本目录 + CHANGELOG + BACKLOG v2 + CAPABILITY-MATRIX 为准)

---

## ★★ 一页看全 (鸿波每周翻 + 投资人 / 团队 / 客户高层看)

→ **[`PROJECT-STATUS.md`](PROJECT-STATUS.md)** ← 项目仪表盘 (一句话使命 / 4 Phase 进度条 / 短-中-长期 / 当前不足 / 本月决策 / Top 5 风险)

如果你只看一份文档, 看这份.

---

## ★ 核心 4 件套 (维护节奏)

| 文档 | 视角 | 节奏 | 用途 |
|---|---|---|---|
| `BACKLOG.md` | 意图 (做过的 + 待做的 + 想法) | 每周一 review + 每天回写 ✅ | 全量积压, 防漂移 |
| `CHANGELOG.md` | 时间 (每天 ship 的事) | 每天收工补 | 日记, 软著申报 |
| `ROADMAP.md` | 战略 (4 Phase 演化) | 季度调 | 客户视角, 给客户看 |
| `CAPABILITY-MATRIX.md` | 能力 (现状快照, ✅ 项 + 代码位置) | 每 sprint 末更新 | 销售 / 法务 / 自己回查 |

4 份配合: 看意图翻 BACKLOG / 看节奏翻 CHANGELOG / 看演化翻 ROADMAP / 看能力翻 CAPABILITY-MATRIX.

---

## 5 月 demo 物料 (4-30 全套定稿)

| 文档 | 用途 |
|---|---|
| `MAY-DEMO-DECK.md` | PPT 大纲 32 张 + 每张口语稿 |
| `MAY-DEMO-Q-AND-A.md` | 客户必问 13 题 + 紧急话术 |
| `MAY-DEMO-PREP.md` | 个人检查清单 (装备 / 准备 / 应急) |
| `ELEVATOR-PITCH.md` | 5 个 30 秒电梯演讲 (CTO / 部门 / 员工 / CISO / 财务) |
| `POSITIONING.md` | 产品定位 one-pager |
| `COMPARE-1PAGER.md` | 跟竞品对比 (vs 星辰 / Hermes / OpenClaw) |
| `COMPETITIVE-DIFFERENTIATION.md` | 竞品差异化深度 |
| `POC-PLAN.md` | demo 后客户说"想试" → 给这一份 |
| `BRAND-VOICE.md` | 品牌语气 / 红线词汇 |

---

## 设计 / 架构

| 文档 | 内容 |
|---|---|
| `STRATEGY.md` | 开源 + 商业化战略 (3 个月一动) |
| `AUTH-DESIGN.md` | SSO 6 决策点 + 落地路径 (客户 IT 自助接入参考) |
| `SSO-RATIFY.md` | SSO 拍板记录 |
| `SSO-CUSTOMER-INTEGRATION.md` | 给客户 IT 的 SSO 接入文档 |
| `SKILL-LIFECYCLE.md` | Skill 5 阶段框架 (元文档, 指导 SOUL/policy/tool 设计) |
| `IDEAS.md` | 长尾创意池 (24+ 项, 持续加) |
| `TOMORROW.md` | 当周 sprint 计划 (每周写) |

---

## 子目录

| 目录 | 放什么 | 当前状态 |
|---|---|---|
| `operations/` | 运维手册 (部署 / 升级 / 回滚 / 故障处置) | ✅ troubleshooting.md |
| `contributors/` | 给外部贡献者的指南 | ✅ CONVENTIONS.md, AI-USAGE.md |
| `architecture/` | 各模块独立架构文档 | ⬜ 按需补 |
| `employees/` | 给终端员工的使用手册 | ⬜ Phase 1 末做 |
| `decisions/` | ADR 架构决策记录 | ⬜ 重要决策时落 |

---

## 文档关系图

```
PROJECT-STATUS.md (★ 一页看全, 鸿波每周翻) ←── meta-view, 浓缩以下所有
       ↑
catfish-design.md (4-22 基础架构, 仍正确)
       ↓
ROADMAP.md (4 Phase 客户视角)
       ↓
STRATEGY.md (开源 + 商业化战略)  ──→  BACKLOG.md A (战略)
       ↓
POSITIONING.md (产品定位)        ──→  BACKLOG.md B (GTM)
       ↓
IDEAS.md (创意池)                ──→  BACKLOG.md E (P3 远期)
       ↓
BACKLOG.md (全量积压, 每周 review) →   Cowork tasks (sprint 跟踪)
       ↓
CHANGELOG.md (每天补)            ←─   BACKLOG.md ✅ 项归宿
       ↓
CAPABILITY-MATRIX.md (现状快照)  ←─   CHANGELOG ✅ 项汇总, 给客户看
```

---

## 怎么入坑

**新人加入 / 客户 IT 想了解**:
1. 读 `../README.md` (顶层) 看项目概览
2. 读 `ROADMAP.md` 看 4 Phase 战略演化
3. 读 `CAPABILITY-MATRIX.md` 看现在到底有哪些能力
4. 挑感兴趣的模块翻 `architecture/` 或源代码

**鸿波每天**:
1. 早上看 `BACKLOG.md` § C / L 挑 sprint 任务
2. 收工写 `CHANGELOG.md` + 在 BACKLOG 标 ✅ (防漂移)
3. 每周一 review BACKLOG, 决定下周提哪些进 task tracker
