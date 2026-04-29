# 鲶鱼 vs 同质化产品 · 1 页对比

> 客户问"跟 X 啥不一样" 时翻这一页. 5 月 demo 时建议印出来或截屏作为 PPT 一张.

---

## 一句话定位差

| 产品 | 是什么 |
|---|---|
| **ChatGPT / Claude / 通义千问 chat** | 通用聊天工具 |
| **TeleAI 星辰超级智能体 / 类似 Anthropic Agent SDK 包装** | 桌面 Agent 工具 (Claude Code + 桌面客户端模式) |
| **🐟 鲶鱼** | **企业 LLM 中台 + 员工副手 + 组织 AI 基础设施** |

我们是**最右** — 既是工具, 也是企业基础设施.

---

## 核心 3 个差异 (锁层差)

### 1. 架构: 单层 vs **三层**

```
ChatGPT 类:         用户 ←→ SaaS 服务器 ←→ LLM
                              (中心化, 数据全上传)

星辰 / Agent SDK:    用户 ←→ Agent 主控 ←→ 工具
                              (单层, 没有审计 / 路由 / 身份层)

🐟 鲶鱼:             员工 ←→ Companion (边缘) 
                              ←→ tool-bridge (隔离, 工具审计)
                              ←→ central gateway (路由 / 容灾 / 审计)
                              ←→ LLM (跨厂商任选)
                                   ↑
                              identity 层 (SOUL / USER / MEMORY 完全本地)
```

**收益**: 客户 IT 拿到**3 个明确控制点** (gateway 审计 / tool-bridge 凭据 / identity 身份), SaaS 没有, 单层 Agent 也没有.

### 2. 数据流: 全上传 vs **完全本地**

| 数据 | ChatGPT 类 | 星辰 类 | 🐟 鲶鱼 |
|---|---|---|---|
| 对话内容 | ☁️ 厂商服务器 | 📍 本地 | **📍 本地** |
| 凭据 (密码 / token) | ☁️ 进 prompt 上云 | 📍 本地 (但无 secret_ref 设计) | **🔒 macOS Keychain (永远不进 prompt)** |
| audit | (你看不到) | (没有) | **📊 中央可审 metadata, 看不到内容** |
| skill / memory | ☁️ | 📍 | **📍 本地 + 跨员工 share (Phase 3)** |

### 3. 跨员工协同: 无 vs **Catfish Federation** (Phase 3)

```
ChatGPT 类:    每个员工独立账号, 数据互不通                 (无组织层)
星辰 类:       每个员工独立 agent, 没设计跨员工通信         (无设计)

🐟 鲶鱼 Phase 3 (Q4 2026 ship):
    员工 A 的 agent  ←─→  员工 B 的 agent  (隐私边界严格)

    例:
    员工 A: "问下张三老板对项目 X 怎么看, 我下午要汇报"
       ↓
    A 的鲶鱼 → B 的鲶鱼 → B 公开授权过的偏好答复
       ↓
    A 拿到答复, 不打扰张三

    部门 1 个员工教鲶鱼新流程
       ↓
    全部门 50 人鲶鱼自动学会 (skill share)
       ↓
    部门集体智慧自动沉淀 (员工离职带不走)
```

**SaaS 永远做不到** (它们是中心化架构, 改不出来).
**同质化产品没设计** (它们是工具不是基础设施).

---

## 业务功能对比表 (现在 ship)

| 功能 | ChatGPT 类 | 星辰 类 | 🐟 鲶鱼 |
|---|---|---|---|
| 内网系统打通 (EIS / OA) | ❌ 公网做不到 | ⚠️ 桌面 OK 但裸奔 | ✅ Catfish Chrome 隔离 + secret_ref |
| 跨厂商 LLM 路由 | ❌ 锁定单厂商 | ❌ 锁定 | ✅ qwen / gemini / claude / 私有自由切 |
| 容灾 / fallback | ❌ | ❌ | ✅ gateway 自动切 (主力 → flash → 公网) |
| 中央审计 | ❌ 客户看不到 | ❌ 没有 | ✅ Companion 仪表盘 + JSONL |
| 凭据安全 | ❌ 进 prompt | ⚠️ 进 prompt | ✅ Keychain + secret_ref + 检测 + warn |
| 业务 skill | ⚠️ 通用 | ⚠️ 通用 (deep-research / news 等) | ✅ 业务流程 (汇报排版 / 资质数据 / 周报) |
| 跨员工协同 | ❌ | ❌ | 🔜 Phase 3 (Q4 2026) ★ |
| 私有化部署 | ❌ SaaS only | ⚠️ 部分 | ✅ 全栈本地 |
| 客户审计代码 | ❌ 闭源 | ⚠️ 部分 | ✅ 鲶鱼自带 SSO ~600 行可读 |

---

## 商业模式对比

| | ChatGPT 类 | 星辰 类 | 🐟 鲶鱼 |
|---|---|---|---|
| 收费 | 按 token / 月费 | (内部) | **一次性 license + 年度维护** |
| API 流水 | ☑️ 抽流水 | - | ❌ 不抽 (你们用自己 LLM) |
| 部署 | SaaS only | 中国电信内 | **私有化** (你们机房 + 你们 LLM) |
| LLM 选择 | 锁定 | 锁定 (GLM) | **任意厂商, 随时切** |
| 客户 IT 审计 | ❌ | ⚠️ | ✅ |

---

## 给客户演示时的 30 秒话术

> "市面上看着像鲶鱼的产品很多 — ChatGPT、TeleAI 星辰、Hermes、OpenClaw. 我们三个核心差:
>
> **第一**, 三层架构. 别人是 SaaS 单层或者桌面 Agent 单层, 我们 Companion + tool-bridge + 中央 gateway + identity 四个明确控制点. 你们 IT 看到的不是黑盒.
>
> **第二**, 凭据完全不进 LLM. macOS Keychain + secret_ref. 你们员工说'密码是 X' 我们 gateway 直接审计标记, 推荐改用 ref. 你们 IT 一看就放心.
>
> **第三**, Phase 3 Catfish Federation. 12 个月内 ship 跨员工 agent 协作 — SaaS 做不到 (中心化), 同质化产品没设计. 这才是鲶鱼真正的护城河."

---

## 1 张图收尾

```
            通用 chat              桌面 Agent              企业基础设施
              ↓                       ↓                       ↓
         ChatGPT/Claude        星辰/Anthropic SDK            🐟 鲶鱼
               
   单层中心化 SaaS         单层桌面 Agent         三层 + identity + Federation
   数据全上传              数据本地但无审计       全本地 + 中央 metadata 可审
   锁定厂商                锁定厂商               跨厂商任意切
   无组织层                无组织层               Phase 3 Federation 跨员工
   抽 API 流水             -                      不抽流水, license
```

---

## 参考资料

- 完整 Roadmap: `docs/ROADMAP.md`
- 架构定位: `docs/POSITIONING.md`
- SSO 决策: `docs/AUTH-DESIGN.md` § 13
- 跟竞品差异化更深版: `docs/COMPETITIVE-DIFFERENTIATION.md`
