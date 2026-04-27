# 鲶鱼 · 竞品差异化定位

> **版本**: v1, 2026-04-27
> **拍板人**: 鸿波
> **用法**: 销售对客户 / pitch 投资人 / 招人 / 内部对齐 / 应对"鲶鱼跟 X 同质化"质疑.
>
> **核心论点**: 鲶鱼**不在**开源 agent runtime (Hermes / OpenClaw / OpenInterpreter / Claude Code) 同一层,
> 鲶鱼**在它们之上的产品/平台层**. 说"同质化"是搞错了层.

---

## 核心一句话

> **鲶鱼不是 AI 引擎. 鲶鱼是基于引擎的整车 — 销售 / HR / 客服直接开走, 不需要先学修车.**

---

## 1 · 三层架构图 (这是关键)

```
                     ┌─────────────────────────────────────┐
   Catfish 在这层 →  │  企业平台 + Companion App + 中央服务  │
                     │   ─ Skills Hub / Secret Broker      │
                     │   ─ Gateway (鉴权 + 路由 + Quota)    │
                     │   ─ catfish-policy (企业红线)       │
                     │   ─ 三支柱场景 (浏览器/邮件/演练)    │
                     │   ─ 边缘主权架构                    │
                     │   ─ Companion macOS App (GUI)       │
                     │   ─ 中文 / Foxmail / 飞书 / 内网 OA │
                     └────────────────┬────────────────────┘
                                      │ 用作 runtime
                                      ▼
                     ┌─────────────────────────────────────┐
   Hermes/OpenClaw  │   Agent Runtime (开源, CLI 单机)     │
   在这层 →          │   ─ tool calling 协议                │
                     │   ─ skill 注册 / dispatch           │
                     │   ─ MCP 接入                        │
                     │   ─ multi-step reasoning            │
                     │   ─ self-improvement                │
                     └────────────────┬────────────────────┘
                                      │ 调用
                                      ▼
                     ┌─────────────────────────────────────┐
                     │  LLM API (OpenAI / Claude / Qwen)   │
                     └─────────────────────────────────────┘
```

**每一层做不一样的事**:
- **底层 LLM**: 推理引擎本身 (OpenAI / Anthropic / 国产 Qwen 等)
- **中层 Runtime**: 让 LLM 多步推理 + 调工具 (Hermes / OpenClaw)
- **上层产品**: 让普通员工**用得起来**这一切 (Catfish)

---

## 2 · 最准的类比

| Catfish vs Hermes/OpenClaw 类比 | 说"同质化"等于在说 |
|---|---|
| **Ubuntu vs Linux Kernel** | "Linux 跟 Ubuntu 一样" |
| **Glean vs OpenAI SDK** | "Glean 跟 OpenAI SDK 一样, Glean 35 亿美元估值不是白来的" |
| **法拉利 vs V8 发动机** | "拖拉机跟法拉利一样, 都用发动机" |
| **Outlook vs SMTP 协议** | "Outlook 跟 SMTP 一样, 都收发邮件" |
| **Notion vs Markdown 解析器** | "Notion 跟 Markdown 库一样, 都处理标记语言" |

**全是层错位**. 客户/投资人说同质化时, 实际上是没看到中间这一层 (产品化 / 企业部署 / 商业服务) 的价值.

---

## 3 · 三方硬指标对比

| 维度 | Hermes (Nous Research, MIT) | OpenClaw (推测同类开源 CLI agent) | **Catfish (鲶鱼)** |
|---|---|---|---|
| **是什么** | Agent runtime / Python 包 | 开源 CLI agent | **企业 AI 平台产品** |
| **目标用户** | 极客 / 开发者 (5%) | 极客 / 开发者 (5%) | **普通员工 (95%, 装不动 Hermes 那 95%)** |
| **部署形态** | `git clone` + `pip install` + 自己写 config | 同 Hermes 一类 | **BYOD: 公司装 .pkg, 员工拥有数据** |
| **UI** | CLI 终端 (黑底白字) | CLI | **Companion macOS App (Tauri 原生)** |
| **首次能用门槛** | 半小时 - 2 小时 (装 venv / 写 SOUL.md / 配 token) | 类似 | **5 分钟 (双击安装包 → SSO 登录 → 用)** |
| **企业鉴权 / Quota / Audit** | ❌ 无 | ❌ 无 | **✅ catfish-gateway 中央服务** |
| **数据主权** | 单机 (员工自管, 默认无中央) | 同上 | **✅ 边缘主权: 0 内容上传, 中央只 metadata** |
| **场景边界** | 通用 — 啥都能干啥都不专 | 同上 | **✅ 三支柱 niche: 浏览器 + 邮件 + 演练 / 复盘** |
| **跨员工协同** | ❌ 无 (无组织层概念) | ❌ 无 | **✅ Skills Hub (P2): A 学的 skill 推给 B** |
| **中文 / 中国本土** | ❌ 英文为主, 不接 Foxmail / 飞书 / 钉钉 | ❌ | **✅ 中文优先, 接 Foxmail SQLite / 飞书 / 内网 OA / 合规平台** |
| **演练 / 复盘真方法论** | ❌ 通用 yes-man | ❌ | **✅ SBI / NVC / STAR / 金字塔 / SPIN, 不是空话** |
| **商业模式** | 开源, 无收入 | 开源, 无收入 | **✅ Open Core SaaS, 50-150 元/员工/月** |
| **目标客户规模** | 个人 | 个人 | **100-500 人中国成长型企业** |
| **客户 IT 团队要求** | 必须自己配置维护 | 同上 | **5 人 IT 团队足够** |
| **产品形态** | Python 包 / GitHub repo | 同上 | **GUI 应用 + 中央管理后台 + 销售合同 + 售后** |
| **隐私 / 合规** | 客户自管 | 客户自管 | **写进合同的承诺, 提供审计脚本让 IT 自核** |

**关键观察**: Hermes / OpenClaw 那两列**几乎所有行**都是 ❌. 因为它们就不是产品, 是基础设施. 拿基础设施跟产品比"同质化", 是范畴谬误.

---

## 4 · 用户为什么会觉得同质化 — 三种典型误读 + 反驳话术

### 误读 1: "都是 LLM + 调工具 + 自动化, 不就是同一个东西吗"

**反驳**:
> "都是发动机驱动的, 那拖拉机和法拉利是不是同质化? 同样的 V8 内核, 完全不同的产品形态、目标用户、商业模式. 鲶鱼跟 Hermes 也一样 — 用同样的 LLM 内核, 但**给完全不同的人**做**完全不同的事**."

**进一步**:
- 客户公司 100 个员工里, 5 个是开发者 (能用 Hermes), 95 个是销售 / HR / 产品 / 客服 / 行政 (用不动)
- 鲶鱼是给那 **95 人** 做的产品
- Hermes 是那 5 个开发者的玩具

### 误读 2: "Hermes 也开源, Catfish 也开源, 有啥差别"

**反驳**:
> "鲶鱼是 **Open Core, 不是全开源**. 开源的是基础设施层 (gateway / tool-bridge / identity / browser-agent skill / email-agent), 闭源的是商业差异化层 (Companion App / Skills Hub 中央 / 飞书集成 / Secret Broker / 中央管理后台). Hermes 没有这套商业层, 它就是一个 SDK / runtime."

**进一步**:
- Open Core 模式 (GitLab / Sentry / Posthog / Supabase / HashiCorp 验证过的最优解)
- 详见 `docs/STRATEGY.md` 开闭分界章
- 开源是为了**建立信任 + 招人 + 行业事实标准**, 不是商业模式
- 商业差异化在闭源层 + SaaS 运营 know-how

### 误读 3: "我装个 Hermes 也能调工具看邮件啊"

**反驳**:
> "你是开发者, 你能装. 我们的目标客户是销售 / HR / 产品 / 客服 / 行政, 他们**装不下来**. 95% 的员工没法靠自己装一个 Python venv + 配 hermes config + 写 SOUL.md + 接 Foxmail SQLite + 调 Chrome CDP. 这正是 catfish 存在的原因 — **替这 95% 把整个技术栈包进 .pkg 安装包, 双击就能用**."

**进一步**:
- 即使开发者装得动 Hermes, **公司 IT 团队**也不能让每个员工各自装维护 — 没有统一鉴权 / 没有 quota / 没有审计 / 没有数据合规背书
- 鲶鱼就是这层"企业部署 + 统一管理"

### 误读 4 (隐式): "ChatGPT 也能做这事啊"

**反驳**:
> "ChatGPT 看不到员工本地的 Foxmail / Outlook / 内网系统. 员工要复制粘贴一上下午, 还可能漏数据 / 截图泄漏隐私. 鲶鱼直接接员工**已登录**的客户端, 5 秒读到本地数据. 而且对话 0 上传公网."

(此条已在 POSITIONING § 11)

---

## 5 · 5 条 Hard Differentiation (随便哪条都让 Hermes/OpenClaw 直接过不去)

| # | 鲶鱼 | Hermes / OpenClaw |
|---|---|---|
| 1 | **直读员工已登录的 Foxmail SQLite / Chrome CDP / 内网 OA** | 没有这些适配器, 要你自己写代码接 |
| 2 | **数据 100% 在员工电脑, 公司只看 token / 延迟 metadata** | 没有这种架构, 它只是单机 |
| 3 | **演练 + 复盘走真方法论 (SBI / NVC / STAR / 金字塔 / SPIN), 不是 yes-man** | 通用 runtime, 没有领域方法论沉淀 |
| 4 | **跨员工 skill 共享 (Skills Hub, P2)** | 没有任何"组织层"概念 |
| 5 | **企业部署: SSO + Quota + Audit + 5 人 IT 团队能跑** | 完全没有, 客户要自己造 |

---

## 6 · 关于 OpenClaw 具体说明

> ⚠️ **TODO 调研**: OpenClaw 具体是什么项目, 当前不确定. 可能性:
> - https://github.com/... 某个开源 CLI agent
> - OpenInterpreter / Claude Code 类型的 mimic
> - 或者是 Anthropic Computer Use agent 的开源仿制
>
> 调研后填: 一句话定义 + 跟鲶鱼对比的具体差异 + 是否威胁我们 niche.

无论 OpenClaw 是什么, 大概率落在**开源 CLI agent / runtime**这层, 跟 Hermes 是同一类, 上面对比框架完全可复用.

---

## 7 · 一句话回应不同听众

| 听众 | 一句话 |
|---|---|
| **客户高管** | "Hermes 是引擎, 鲶鱼是车. 引擎你拿不来开, 车你能直接上路. 中间这层(产品化 + 企业部署 + 边缘主权 + 三支柱 niche), 才是鲶鱼." |
| **客户 IT** | "Hermes 让懂技术的开发者 5 分钟造个 agent. 鲶鱼让不懂技术的销售 5 秒钟用上 agent. 你愿意养 5 人 AI 工程团队还是花 100 元/员工/月?" |
| **投资人** | "Glean 跟 OpenAI SDK '同质化'吗? Glean 35 亿美元. Catfish 是中国版 Glean × Microsoft Copilot — 边缘主权架构 + 三支柱 niche + 100-500 人企业." |
| **极客 / 开发者** | "你能自己装 Hermes 跑得很顺? 那你不是我们的客户. 我们目标是你公司里那 95 个**装不动 Hermes** 的同事." |
| **怀疑者** | "你拿 Linux kernel 跟 Ubuntu 比同质化吗? 内核相同, 产品完全不同. 客户付钱买的不是内核." |

---

## 8 · 销售话术: "反对意见 4 件套"

客户一提"跟 Hermes / OpenClaw 同质化", 销售直接背:

```
他们: "这跟 Hermes 不是一个东西吗?"

你 (4 件套):

1. 承认表面相似 (建立 rapport)
   "对, 表面看都用 LLM 都调工具, 这观察没错."

2. 重新框架 (raise 层差)
   "但层次不同. Hermes 是 Linux kernel 那一层, 我们是 Ubuntu 企业版.
    你拿 Linux 跟 Ubuntu 比同质化吗?"

3. 落地差异 (具体 5 条)
   "你 IT 团队能让每个员工自己装 Python venv 配 hermes 吗?
    Hermes 接得了 Foxmail SQLite / 飞书 / 内网 OA 吗?
    Hermes 有 SBI/NVC/STAR 这些演练方法论沉淀吗?
    Hermes 有跨员工 skill 共享吗?
    Hermes 给你写 0 数据上传的合规承诺吗?
    都没有 — 这五件事才是你买单的理由."

4. Reframe 客户问题 (从同质化到适配场景)
   "你公司 100 人, 5 个开发者能用 Hermes, 95 个销售/HR/客服怎么办?
    那 95 人才是鲶鱼的客户."
```

---

## 9 · Demo 第一帧应该这么演 (锁死差异化)

**不要演**: in-character 演练 (虽然好, 但跟 Hermes 比不够锐利)

**改成演**:

> 销售小王打开公司发的 Catfish 安装包, **双击, 0 配置 0 命令行**, 进对话:
> "今天哪些客户还没回?"
>
> 5 秒后, 表格列出未读邮件 + 优先级 + 紧急程度
>
> 然后: "帮我给王总起草一封, 客气点"
> 5 秒后: 起草好的草稿, 在 Foxmail 里待发
>
> 然后: "帮我演练下午的 demo"
> 进 in-character 模式, 反客户押 5 个 push question

**这一帧锁死的是**:
1. 0 配置启动 → Hermes 半小时配置门槛直接出局
2. 接 Foxmail / 内网 → Hermes 接不到中国本土客户端
3. 演练方法论 → Hermes 没有这层

播完客户自动会问: "Hermes 能这样吗?" 答案: "不能, 你得花两周自己造."

---

## 10 · 营销 / 网站首页这么写

不要等客户问, 直接放一段:

> **鲶鱼基于 Hermes (开源 agent runtime, Apache 2.0). 我们做了 Hermes 不做的事**:
>
> - ✅ 企业鉴权 + 中央 quota + audit
> - ✅ Companion 桌面 GUI App, 5 分钟上手
> - ✅ 三支柱业务场景 (浏览器 / 邮件 / 演练)
> - ✅ 边缘主权架构, 0 内容上传
> - ✅ SaaS 商业化, 50-150 元/员工/月
> - ✅ 中文优先, 接 Foxmail / 飞书 / 内网 OA / 合规平台
>
> **如果你能自己装 Hermes 跑得很顺**, 你不是我们的客户, 你是开发者.
> **如果你公司有 100-500 个装不动 Hermes 的员工**, 那是我们存在的理由.

把"装不动 Hermes" 直接写出来. 这是差异化的核心.

---

## 11 · 当 Hermes 上游进化时怎么办

**风险**: Hermes 越来越好, 加了 GUI / 鉴权 / 企业功能, 是不是会蚕食我们?

**回应**:
1. Nous Research 是研究机构, 不做商业产品, 历史上没做过 to-B 销售
2. 即使他们做, 他们目标用户也是开发者社区, 不是 100-500 人中国企业
3. 我们的护城河不在 runtime, 在 (a) 中文 / 中国本土场景 (b) 边缘主权 + 企业合规架构 (c) 三支柱 niche 沉淀 (d) SaaS 多租户运营 know-how (e) 客户关系 + 销售网络
4. Hermes 即使复刻 Companion, 也得花 2 年才能补上述 5 项. 那时候我们已经站稳

**如果 Hermes 真的开始做 to-B 商业**:
- 他们要么 fork 我们 (Apache 2.0 允许), 要么自己造
- fork 我们 = 验证我们的方向对了
- 自己造 = 中国市场他们不熟, 我们有 2 年时间窗口

**最坏情况**: Hermes 上游 fork 我们的 catfish-gateway / catfish-policy 自己卖. 那时候:
- 中国客户 80% 不会买 Nous Research 的服务 (海外品牌 + 数据合规风险)
- 我们的真护城河 (Companion / Skills Hub / 飞书 / 服务) 都闭源, 抄不了

---

## 12 · 反向: 我们什么时候**该**像 Hermes

不是所有 Hermes 的事我们都不做. 在**底层基础设施**上, 我们应该 align Hermes 让兼容性最大化:

- ✅ tool calling 协议: 跟 OpenAI / Hermes 兼容, 这样 hermes skill 可以无缝跑
- ✅ MCP 接入: 跟 Hermes / Claude / 通用 MCP 生态兼容
- ✅ Skill .md 格式: 跟 hermes/agentskills.io 标准兼容, 让员工 skill 能跨平台用
- ✅ session log 格式: 跟 hermes state.db 兼容, 让员工换平台不丢历史

**反而**: 我们闭源那层 (Companion / Skills Hub) 完全自由, 不需要兼容任何人.

这是 Open Core 的精神: **底层尽量标准化, 顶层尽量差异化**.

---

## 13 · 一页纸总结 (给销售/招聘背)

```
鲶鱼 ≠ Hermes / OpenClaw
鲶鱼 = (Hermes 内核) + 企业平台 + GUI + niche + SaaS

类比: Ubuntu = Linux 内核 + 安装器 + 商业服务

客户买的不是内核, 是"装得动 + 用得起来 + 有人负责"

5 条硬差异:
1. 直读员工 Foxmail / Chrome / 内网
2. 0 数据上传, 边缘主权
3. 演练真方法论
4. 跨员工 skill 共享
5. 企业鉴权 + IT 5 人能跑
```

---

## 决策签名

> 此文档代表 2026-04-27 的竞品差异化定位口径.
>
> 修改这份文档需要主理人 (鸿波) 显式同意.
>
> **凡是对外说"鲶鱼跟 X 有什么不同", 都从这里取**.

---

## 跟其他文档的关系

```
STRATEGY.md           ─→  开闭分界, 商业战略 (3 月一动)
POSITIONING.md        ─→  产品定位 one-pager (拍板后稳定)
COMPETITIVE-DIFF.md   ─→  应对"跟 X 同质化"质疑 (本文档)
                      ┴───→ 三者互相引用, 改一份要 align 另两份
```

---

*文档由 catfish team 维护. 任何对外口径调整, 先改这里再统一.*
