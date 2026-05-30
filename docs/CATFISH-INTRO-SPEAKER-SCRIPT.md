# 鲶鱼产品介绍讲稿 — 配 catfish-intro.pptx (25 页)

> **场景**: 内部团队 / 招新 / 技术分享
> **时长**: 20-30 分钟 (讲稿 ~22 分钟, 留 8 分钟 Q&A)
> **调性**: 技术深度, 用我们自己术语, 不端着
> **配套**: catfish-intro.pptx, 同目录

---

## 讲稿全文

### Slide 1 · 封面 — 鲶鱼 (20s)

> 大家好, 今天讲一下我们做的产品 — 鲶鱼.
>
> 一句话: 给一线员工的 AI 助手, 多入口接入, 数据零出端, 124 个工具生态.
>
> 接下来 20 分钟左右我会过一遍, 先从 "为什么有鲶鱼" 讲到架构, 然后是工具生态和我们的私有 logic, 最后聊招新.

**过渡**: 翻到议程页.

---

### Slide 2 · 议程 (30s)

> 6 个 section.
> ① 为什么有鲶鱼 — 企业 AI 落地真痛是什么.
> ② 鲶鱼是什么 — 一句话定义 + 用户怎么用.
> ③ 架构总览 — 边缘 / 中央 / 上游.
> ④ 工具生态 — 124 个 MCP.
> ⑤ 私有 logic — SOUL / Memory / RecMode / plugin 模式.
> ⑥ 加入我们.

**过渡**: 先讲为什么.

---

### Slide 3 · 为什么 — 企业 AI 三痛 (2.5 分钟, 重点页)

> 我们看到很多企业引入 AI 不顺利, 总结下来是三个真痛.
>
> **第一痛: 数据合规.**
>
> 员工跟 AI 聊的东西从来不是闲聊. 销售问 "客户王总上次提的需求是啥", 法务问 "下季度报价策略", 一线员工问 "我家小孩学校老师说...". 客户信息 + 商业秘密 + 个人隐私都在里面.
>
> 现在两条路都走不通: 直接发 OpenAI / Anthropic = 数据出境违反《个保法》《数据安全法》, 法务一票否决. 反过来本地全私有 = 自己买 GPU 跑开源模型, 能力差顶级 SaaS 一档, 还得养专业 LLM 工程师团队, 中小公司扛不起. 二选一, 怎么选都难受.
>
> **第二痛: 多租户隔离.**
>
> 100 个员工分享 1 个 AI 部署 — 这是默认架构, 不然每人买账号太贵. 但 "共用 1 个" ≠ "天然隔离". quota 串 = A 把额度刷爆 B 用不了; memory 串 = AI 记得 A 提过 "我们财报 X 亿", B 问 AI 自己就吐出来; 历史串 = B 搜邮件能搜到 A 客户的信息.
>
> 这些出一次就是 P0 — 客户索赔 / 内部腐败 / HR 灾难 / 上新闻. 但市场上大多数方案没真做隔离, 开源 LLM 1 个 endpoint 全员共用, LangChain 拼的项目要主动写隔离代码, 谁会一开始就重视这个.
>
> **第三痛: 工具孤岛.**
>
> AI 在浏览器里聊 = 信息进不来出不去. 想让 AI 帮你回邮件, 你得手动复制粘贴; 想让 AI 看你日历, 没接口. AI 啥也干不成, 还不如多花 5 分钟人工.
>
> — 我们做鲶鱼就是冲这三痛去的.

**过渡**: 那鲶鱼是什么.

---

### Slide 4 · 鲶鱼是什么 — 一句话定义 (1 分钟)

> 一句话: **给一线员工的多入口 AI 助手 — 边缘自治, 中央编排, 数据零出端.**
>
> 三个词解释一下.
>
> **边缘自治** = 员工对话不离本机. 你的桌面 Companion / 手机微信 ClawBot / 飞书 都能找鲶鱼, 但对话数据留你这台机.
>
> **中央编排** = 一个 gateway 调度多 model 多 skill, 不锁单一上游. 今天用 Deepseek 明天换 Gemini, 不用换客户端.
>
> **数据零出端** = 中央 0 红线. 我们中央服务器**不存任何对话内容**, metrics 只记 user / model / tokens / latency. 你跟鲶鱼聊啥, 中央不知道.

**过渡**: 用户实际怎么用.

---

### Slide 5 · 用户视角 ① Companion 桌面 (1.5 分钟)

> 三个入口, 这是第一个 — Companion 桌面端.
>
> Tauri 2 + React + Vite 做的跨平台桌面, mac / Windows 都能跑. 全局快捷键唤起, 不打断工作流. 模型 picker 在右上角, 想用 Deepseek 还是 Gemini 还是 Nemotron 现切. 会话列表 + 跨会话语义搜索. 自动起标题, 每个员工有自己的 SOUL 个性化.
>
> 关键 — 全留本机 SQLite. 你卸载 Companion, 历史就跟你走, 中央服务器没存.
>
> 右边这个截图是 Companion 真实 UI, 鲶鱼回的话, 周末该干嘛三个选项 — 充电 / 摆烂 / 学东西, 这个回复风格是我们 SOUL 调出来的.

**过渡**: 第二个入口 — 微信.

---

### Slide 6 · 用户视角 ② 微信 ClawBot (1.5 分钟)

> 微信入口最常用. 把鲶鱼加成微信好友, 上下班路上 / 排队时聊两句立刻回. 支持图片 / 语音 / 文件 (走 vision model).
>
> 重点: **微信跟 Companion 同一 SOUL 同一 memory**. 早上在桌面问的事, 中午在微信问后续, 上下文接得上.
>
> 多租户隔离体现在这里 — 微信用户 openid 唯一标识, 100+ 员工严格不串. 我们用 `X-Catfish-User: <openid>@im.weixin` 这种合成命名空间, catfish-gateway 按 user 路由 quota / memory / audit. 真员工绑定邮箱了用真员工命名空间, 没绑就走 platform-only 隔离.
>
> 右边路径图是技术细节, 后面架构页会展开.

**过渡**: 第三个入口 — 飞书 + 后台.

---

### Slide 7 · 用户视角 ③ 飞书 + 后台 (1 分钟)

> 飞书入口走企业内部, 跟 IT 系统打通; 群聊 / 单聊都能 @鲶鱼.
>
> 但鲶鱼不止"用户问 → AI 答"这种被动模式. 还有一堆**后台任务**主动跑:
>
> - 每日早安播报: 邮件 / 日程 / 待办整合成一条消息
> - 长时任务: 写报告 / 调研 / 资质审核 — 启动后自己跑, 几小时后回结论
> - Cronjob 定时跑 skill
> - Companion email-scheduler 自动整理收件箱
>
> 这些都是"鲶鱼帮你做事", 不只是"鲶鱼跟你聊".

**过渡**: 第二章节 — 架构.

---

### Slide 8 · 架构章节页 (15s)

> 接下来讲架构. 3 层: 边缘 / 中央 / 上游 model.

---

### Slide 9 · 架构总览一图流 (2.5 分钟, 重点页)

> 一图看全部.
>
> **左边 EDGE** — 员工本机. 5 个组件:
> - companion-app: Tauri 桌面端
> - hermes-cli: 嵌在 Companion 里的 agent 引擎
> - hermes :8642: API server, 暴露 OpenAI 协议
> - catfish-tools: MCP server, 提供 124 个 tools
> - weixin/feishu: 平台 adapter
>
> 全部跑在员工本机, 数据不离这台机.
>
> **中间 CENTRAL** — 我们中央. 1 个 gateway:
> - catfish-gateway: LiteLLM proxy, 端口 8999
> - multi-model routing
> - X-Catfish-User 多租户
> - audit metrics (不存对话内容)
> - fallback chain (一个 model 错切下个)
>
> 中央 0 红线, 后面再讲.
>
> **右边 UPSTREAM** — 上游 LLM 厂商. Deepseek / Gemini / NVIDIA NIM / Qwen / OpenRouter. 不锁单一上游, 哪个好用切哪个.
>
> 数据流向: 用户问 → 边缘 hermes → 中央 catfish-gateway → 上游 model → 回来. 关键的 catfish 私有 logic 都在边缘或中央, 不在上游.

**过渡**: 边缘细节.

---

### Slide 10 · 边缘细节 (1.5 分钟)

> 左边 Companion 桌面: Tauri 2 (Rust + WebView) + React. zustand 状态管理. 关键体验是全局快捷键和系统托盘 — 工作的时候 Ctrl+Space 唤起, 不切窗口.
>
> 右边 hermes + tool-bridge: hermes-agent 是 NousResearch 开源 (https://github.com/NousResearch/hermes-agent) 的 fork, 我们在上面挂了 plugin (后面 slide 21 详讲). AIAgent 跑调度循环 + tool calling. 跟 Companion / 微信 / 飞书 都通过 /v1/chat/completions 这种 OpenAI 协议 endpoint 通信.
>
> tool-bridge 是个 MCP server, 124 个工具都从这里暴露给 hermes.

**过渡**: 中央细节.

---

### Slide 11 · 中央细节 — catfish-gateway (2 分钟)

> 这一页讲中央 — catfish-gateway, LiteLLM proxy 在 8999 端口.
>
> **做什么** (左半):
> - 接收 hermes 来的 OpenAI 协议请求
> - 提 X-Catfish-User header, 按 user 路由
> - model 名转真上游 (例如 `catfish-public-deepseek-flash` → 真 Deepseek v4 endpoint)
> - rate-limit / quota / fallback / retry
> - tool sanitizer (BL-TOOL-CAP 110 个工具上限, 防 context 爆)
> - LiteLLM 调上游 (chat + responses + stream)
>
> **不做什么** (右半, 这是 0 红线):
> - 不存对话内容到中央 PG / disk
> - metrics 只记 user / model / tokens / latency, 不记 prompt / completion 文本
> - audit log 同样 0 内容
> - 跨员工数据物理隔离 (按 user namespace)
> - 虚拟员工 (im.weixin 命名空间) 跟真员工分开
>
> 这两半合起来 = "中央只做转发和路由, 不碰内容". 合规审查我们能直接拿日志给法务看 — 字段就那 5 个, 没法泄露什么.

**过渡**: 把零出端 + 多租户 具象化.

---

### Slide 12 · 数据零出端 + 多租户 (1.5 分钟)

> 几个数字:
> - **100+** 员工真用户
> - **0** 中央对话内容
> - **3** 入口同步 SOUL (Companion / 微信 / 飞书)
> - **124+** MCP 工具
>
> 下面 4 个隔离维度展开:
> - **quota**: 每员工独立配额, A 烧完 B 不影响
> - **memory**: SOUL / context 不串
> - **audit**: metrics 按 user 标记, 出问题反查
> - **skill**: RecMode 推荐用员工自己偏好, 不串

**过渡**: 第四章节 — 工具生态.

---

### Slide 13 · 工具生态章节页 (10s)

> 124 个 MCP tools, 6 大类. AI 真有手能干活.

---

### Slide 14 · 124+ MCP 6 大类鸟瞰 (2 分钟, 重点页)

> 6 大类分一下.
>
> **知识 & 记忆**: memory / session_search / today_summary / email_search / expert_consult. 让 AI 知道 "我之前聊过啥 / 邮件里说过啥 / 同事谁是某领域专家".
>
> **任务 & 协作**: kanban 10 个 actions / todo / cronjob / delegate_task / reminder / calendar. AI 帮你管事.
>
> **执行环境**: browser 一整套 (cdp / click / snapshot / vision) / file / process / terminal / execute_code. AI 真能动. 后面深入.
>
> **内容生产**: image / video / TTS / draft_email / web search & extract.
>
> **catfish 私有**: skill_install/run/manage / teach_start/end / freeze_skill / run_task / a2a_ask. 这块是我们独有 logic, 后面 SOUL/Memory/RecMode 详讲.
>
> **集成 & 平台**: feishu doc/drive / discord / ha (Home Assistant) / 云豹 / weixin sticker. 跟一堆外部系统集成.

**过渡**: 挑几个深入讲.

---

### Slide 15 · 工具深入: kanban + 邮件 + 日历 (2 分钟)

> 任务协作这块. 鲶鱼真能管你的待办.
>
> **kanban 10 个 action**:
> - create — AI 起新任务, 自动 assign 给合适的人
> - list — 看你 / 团队的看板状态
> - heartbeat — 你汇报进度, AI 推断哪个任务滞后, 自动提醒
> - block / unblock — 卡 / 解卡
> - complete — 完成自动归档 + 汇报相关人
> - link — 关联文档 / 邮件 / 其它 kanban
>
> **邮件 + 日历**:
> - email_search — 按发件人 / 主题 / 时间筛
> - draft_email_reply — AI 起草, 按你说话风格 (从历史邮件学的)
> - compose_followup_list — "本周谁的邮件还没回" 自动列
> - create_calendar_event — 建会议 + 通知与会人 + 加日程
> - draft_meeting_brief — 会前生成简报, 把相关资料整合好

**过渡**: 执行环境.

---

### Slide 16 · 工具深入: browser + file + 执行 (1.5 分钟)

> 让 AI 真能动手.
>
> **browser** 一整套 — 不是简单 web fetch, 是真控浏览器:
> - cdp 直接接 Chrome DevTools
> - click / type / press 真模拟点击
> - snapshot 拿 DOM
> - screenshot + vision 看截图理解
>
> AI 能给你登录系统 / 填表单 / 查数据.
>
> **file / process** — 读写文件 / patch 应用 diff / 跑 shell / 跑 Python / Home Assistant 控智能家居.
>
> **搜索 / 视觉** — web_search 多 provider / x_search (Twitter) / vision_analyze 看图 / 验证码识别.

**过渡**: 第五章节 — 私有 logic.

---

### Slide 17 · 私有 logic 章节页 (15s)

> 接下来讲我们的 catfish 私有 logic. SOUL / Memory / RecMode / 多模型 routing / plugin 模式.

---

### Slide 18 · SOUL + Memory (2 分钟)

> 个性化和长期记忆这块.
>
> **SOUL** — 每员工一份 ~/.hermes/SOUL.md, 每次对话都注入. 包含:
> - 人格语气 (鲶鱼叫你 "老板" 还是 "鸿波" 还是别的)
> - 你的专业领域
> - 偏好 (我喜欢简洁回复 / 中文 / 不用 emoji)
> - 禁忌 (不要做啥)
> - 公司行业上下文
>
> SOUL 数据存本机, 中央完全不知道. 你换电脑就需要把 SOUL.md 带过去.
>
> **Memory** — catfish-memory plugin 实现的 hermes MemoryProvider. 聚合 5 源:
> - employee_journal — 员工日志
> - skills_catalog — 已装 skill
> - feedback — 反馈
> - session_meta — 会话元数据
> - skill_guard — 安全策略
>
> 这 5 源都 inject 到 system prompt. 微信聊过 Companion 也接得上 — 因为 SOUL 跟 memory 都本地, 三个入口共享同一份.

**过渡**: skill 推荐怎么工作.

---

### Slide 19 · Skill 编排 + RecMode (2 分钟)

> Skill 是鲶鱼自动化的核心. 用户开新会话:
>
> **第 1 步**: 用户说 "帮我做下周资质审核"
>
> **第 2 步 RecMode**: 扫 skill_catalog + 历史, 推荐 top-3 候选 skill. 比如 "资质审核 skill v3" / "通用文档审查 skill" / "类似上次审核的复用 skill".
>
> **第 3 步**: 用户选确认, 或者鲶鱼自主推断 (置信度够就直接跑).
>
> **第 4 步 执行**: skill plan 拆步骤 → 跑 tool → reflection 反思中间结果 → 调整 → 输出.
>
> **第 5 步**: 成功的写回 employee_journal 给下次复用; 失败的 propose 一个改进版本, 走 teach_start/end 流程让员工教正确做法.
>
> 这套 RecMode 中文 prompt 我们 5/22 才上线, 之前是英文输出.

**过渡**: 多模型怎么用.

---

### Slide 20 · 多模型 routing (1.5 分钟)

> 我们 catfish-gateway 提供 8 个 model:
> - deepseek-flash / gemini-flash / gemini-pro / qwen
> - nvidia-llama / nvidia-nemotron / groq
> - private-vision (内部部署)
>
> 用户怎么切?
>
> **路径 1**: Companion picker UI 切. body.model 直传 → hermes _create_agent model_override → LiteLLM 调对应 catfish-* → catfish-gateway 路由真上游.
>
> **路径 2**: config.yaml model.default — 默认 model.
>
> **路径 3**: fallback chain — 一个 model 错就切下个.
>
> picker 这块我们 5/28 刚 ship 真生效. 之前 picker UI 上切了但 hermes 实际还是 deepseek, 现在端到端通了.

**过渡**: plugin 模式 — 今天 5/29 ship 的成果.

---

### Slide 21 · plugin 模式 (2 分钟, 重点页)

> 这是我们今天刚搞定的事, 讲一下背景.
>
> **BEFORE**: hermes-agent 是 NousResearch 上游开源, 我们 fork 改了 5 个文件加 catfish 私有 logic — 大概 430 行. 但每次 hermes 上游升级 (5/19 / 5/27 / 5/28 / 5/29 我们升了 4 次), rebase 都几千行冲突, brand_patch hook 卡死, 升级一次半天. 更危险的是 silent break — hermes refactor 把方法挪个位置, 我们 patch 静默失效, 主对话 200 OK 但跨员工串数据, P0 隐私漏洞看不见.
>
> **AFTER**: 把 11 处 patch 全搬出来变成独立 plugin (catfish-xcatfish-user). hermes 仓回 upstream pristine. 关键技术:
> - **monkey-patch** 风格挂 hermes 内部 attribute
> - **fail-loud verify** — refactor 改名立刻 ImportError, hermes 启动失败, 不允许 silent
> - **contextvar 跨 thread** — 让 X-Catfish-User 在 asyncio executor 透传
>
> 验证: 9/9 端到端 smoke 全过, 22/22 self-test 全过.
>
> 下次升级 hermes 流程: git pull → pytest → 重启 → smoke, 几分钟. 不再是几小时硬扛.

**过渡**: 可观测.

---

### Slide 22 · 可观测 (1 分钟)

> 出问题怎么快速定位 — 三个手段.
>
> **catfish.metrics**: 中央这边的 JSON 日志, 一行包含 user / model / tokens / latency / status. 出问题按 user 反查.
>
> **hermes log**: 边缘这边的 agent conversation loop + tool call trace. 看哪一步出错.
>
> **/health + smoke**: hermes 是否起 + 9 项端到端验证. 1 秒看健康 + 几十秒看核心功能.
>
> 这三个手段配合 — 中央 + 边缘双侧对账, 出事 5 分钟内能定位是 model 上游问题 / hermes plugin 问题 / 还是网络问题.

**过渡**: Roadmap.

---

### Slide 23 · Roadmap (1.5 分钟)

> **短期 1-2 周**:
> - plugin 实际生产稳定性观察 (今天 ship, 看 1-2 周真用户场景)
> - 上游 PR — 把 picker model_override 推给 hermes upstream, 通用功能他们应该接
> - CI smoke 接 GitHub Actions, 升级 PR 必跑
>
> **中期 1-2 月**:
> - 跟 hermes upstream 谈正式 plugin hook API, 进一步减我们 monkey-patch 面积
> - skill marketplace — 员工之间共享 / 推荐 skill
> - vision agent 完善 (browser_vision 强化)
>
> **长期 3-6 月**:
> - 开放部分 skill 给外部协作伙伴 (供应商 / 客户)
> - 邮件 / 文档全自动化 (rule + AI 结合)
> - agent-to-agent 协作, 跨员工任务委托

**过渡**: 招新.

---

### Slide 24 · 招新 — 4 个 role (2 分钟)

> 我们正在找这 4 类人.
>
> **Agent & LLM 工程**: Python / hermes / LiteLLM / MCP. 玩过 agent loop / tool calling / context engineering / skill 设计. 这块决定鲶鱼的"脑子"有多聪明.
>
> **桌面 / 前端**: Tauri / Rust / React. 做 Companion 桌面体验. 全局快捷键 / 系统托盘 / Rust IPC 这些细节. 这块决定鲶鱼的"手感"好不好.
>
> **Infra & 数据**: 多租户 / 可观测 / 0 红线设计. gateway / PG / SQLite / audit / Prometheus. 这块决定鲶鱼"靠不靠谱".
>
> **产品 & DesignOps**: AI 产品体验 + 内部用户研究. UX for AI / skill 设计 / 工作流梳理. 这块决定鲶鱼"用不用得起来".
>
> 不一定全栈, 也不要求全面会, **但要有一个领域真打过实战**.

**过渡**: 最后一页.

---

### Slide 25 · 加入鲶鱼 + Q&A (15s)

> 联系我: chenhongbo@ffcs.cn. 也可以扫码加微信 ClawBot, 跟鲶鱼直接聊.
>
> 接下来 Q&A.

---

## Q&A 预案 (按类组织)

> 准备这些问题的标准答案, 听到熟悉的关键词直接套. 不熟的问题坦诚说"这块还没定型 / 正在做 / 不知道".

### A · 架构 / 技术问题

**Q1: 为什么选 hermes-agent 不自己写?**

A: 三个原因.
1. agent loop / tool calling / context compression / model fallback 这些通用 logic 让上游做, 我们专注 catfish 私有 (多租户 / SOUL / 工具). 不重复造轮子.
2. NousResearch 维护积极, 月度大更新带新 model / 新 feature. 我们 fork 就能拿到.
3. hermes 设计上是 plugin-friendly — 有 register(ctx) 机制, 我们的 catfish-memory / catfish-xcatfish-user 都是 plugin 形式挂载.
4. 上游有 124 个 MCP tools 生态 + 14 个 platform adapter, 自己写至少 3 个月起.

**Q2: monkey-patch 不是反 pattern 吗? 升级容易破?**

A: 是反 pattern, 但是当前最优解, 原因:
1. hermes 上游目前没暴露足够的 hook API 覆盖我们需求 (X-Catfish-User 多租户 / picker model_override / CORS 等). 等他们接 PR 几个月, 我们等不起.
2. monkey-patch 风险我们承认, 所以加了 fail-loud verify — refactor 改名立刻 ImportError, 不会 silent 跨员工串数据 (P0 漏洞).
3. 升级前跑 22 项 self-test, 升级后跑 9 项 smoke. 出问题立刻发现.
4. 长期我们走 B2: 跟上游谈正式 hook API, 把 monkey-patch 替换成 hook 调用.

**Q3: contextvar 跨 thread 那块怎么做的?**

A: hermes _run_agent 用 loop.run_in_executor 把 _create_agent 推到 thread pool 跑. 默认 ThreadPoolExecutor worker thread 不继承 main loop 的 contextvars. 我们的 X-Catfish-User middleware 在 main loop 设 CV, 到 executor thread get() 是空.

修法: monkey-patch asyncio.BaseEventLoop.run_in_executor, 自动用 functools.wraps + contextvars.copy_context().run() 包 func. 这样 executor thread 看到的是 main loop 的 context snapshot.

这是全 process 范围 patch, 影响所有 asyncio executor 调用. 但安全, 没人 require executor 不见 CV.

**Q4: LiteLLM 你们为啥单独再加一层 catfish-gateway, 不直接 hermes 调 LiteLLM?**

A: 因为多租户隔离 + 0 红线需要在中央做, 不在边缘做.
- quota 限制要中央统一计数 (边缘不知道 A 烧了多少额度)
- model 路由要中央配 (上游 endpoint / api key 不能下发到员工本机)
- audit metrics 要中央归集 (法务审计找一个地方看)
- fallback chain 要中央协调 (一个上游挂了切下一个)

catfish-gateway 这一层是必要的. 边缘 hermes 直连 LiteLLM 等于把 OpenAI / Anthropic api key 放员工本机, 安全不可接受.

**Q5: model 切换为什么用 body.model 不用 header?**

A: OpenAI 协议 body.model 是标准字段, Companion / Cursor / 第三方客户端都直接传. 用 header 是 catfish 私有约定, 客户端要专门支持. body.model 兼容性最好.

而且我们打算把 body.model 这块改动推给 hermes upstream (Plan A 上游 PR). 通用客户端都受益, 不只是 catfish.

---

### B · 安全 / 合规问题

**Q6: 你们说"中央 0 红线", 这怎么验证不是嘴说说?**

A: 三层验证:
1. **代码可审计** — catfish-gateway 是 open source 给法务看, 中央 metrics 字段就那 5 个 (user / model / prompt_tokens / completion_tokens / latency), 你能 grep 出来 `db.insert(prompt)` 这种调用 = 0.
2. **catfish.metrics 日志可查** — 抽样看实际写入字段, 完全没 prompt / completion 文本. 法务现场看.
3. **网络抓包** — Companion 连 catfish-gateway, 抓包看请求/响应只走标准 OpenAI 协议 body, 中央 ack 不会反过来要内容存档.

我们能给等保 / ISO 27001 审计直接交工件.

**Q7: SaaS 上游 (OpenAI / Anthropic) 那边怎么办? 你们说留本地, 但单次推理还是过 SaaS 啊?**

A: 这是合规上承认的妥协. 几个层面:
1. **数据本地优先**: 真敏感对话 (体检 / 家事 / 高敏 客户合同) 可走 `catfish-private-vision` 等本地 model, 不上 SaaS.
2. **单次脱敏**: 中央可以做 PII detection + 脱敏 (路线图上, 还没 ship). 客户姓名替换成 "客户 A".
3. **选 SaaS 时挑数据驻留地** — 比如 Deepseek / Qwen 国内厂, 数据不出境. Gemini / GPT 这种境外只用做不含敏感数据的任务.
4. **SaaS 厂商不训练承诺** — API 模式很多厂商承诺不用对话训练, 合同里写明.

完美方案不存在, 但比 "全发 ChatGPT 没保护" 好几个数量级.

**Q8: 100 个员工的隔离, 你怎么保证 memory store 真分了 namespace?**

A: 几个验证方式:
1. **代码层** — catfish-memory plugin 每次调用都强制按 user_id 分 namespace. plugin 用 X-Catfish-User header 路由, 没 user header 直接 400.
2. **存储层** — employee_journal / skills_catalog / session_meta 这些表都有 user_id 字段, query 时 WHERE user_id = ?. SQL 注入式串库我们做了防护测试.
3. **测试覆盖** — pytest 里专门有跨 user 隔离测试, 模拟 user A 写 → user B 读应该读不到.
4. **审计可追溯** — 出问题按 user 反查 metrics 能定位到具体哪个 user_id 哪条请求. 真出隔离事故能快速定位.

但承认 — 任何"隔离"都不是 100% 安全, vector search 可能有边缘 case 跨 namespace 返回. 这是我们持续在关注的.

**Q9: 微信用户没绑邮箱怎么算 user? 不会跟真员工串吗?**

A: 不会. 真员工 namespace 是 email 形态 (e.g. `chenhongbo@ffcs.cn`), 没绑的微信用户走合成 namespace `<openid>@im.weixin`. 两个 namespace 物理隔开, catfish-gateway 路由按 user 字段精确匹配.

如果某天 admin 给某 openid 跑 `hermes pairing approve --email chenhongbo@ffcs.cn`, 之后那个 openid 的请求会带真 email 上来, 走真员工 namespace. 但之前合成 namespace 下的对话不会自动迁移过来 — 历史隔离.

---

### C · 工程 / 实施问题

**Q10: 124 个 MCP tools 不会让 context 爆吗?**

A: 会, 所以我们有 BL-TOOL-CAP 110 cap.
- 工具按优先级排序 (always-on tools 保留, "other" 类切尾巴)
- session 内只 inject 当前 task 相关的 tools
- catfish-gateway tool_sanitizer 在请求进 LiteLLM 前砍多余 tools
- 实际 LLM 看到的一般 30-60 个 tools, 不是全 124 个

这是 trade-off — 工具多了能力强但 token 贵, 我们持续在优化"什么 tools 关键 / 什么是冗余".

**Q11: hermes 升级一年 12 次, 你们 plugin 维护得过来吗?**

A: 这就是为什么今天搬 plugin. 之前 1 个月升 3 次, 每次半天到一天. 现在 plugin 模式下:
- 90% 的升级 plugin 不用动 (hermes 内部 refactor 不破我们引用的稳定 attribute)
- 10% 出 fail loud, self-test 第一时间报, 知道改哪
- 改动量从"几小时手写 port"变成"几行 attribute name 替换"

升级总成本估计降到原来 1/10.

**Q12: 你们用 Tauri 不用 Electron, 不怕生态小?**

A: 选 Tauri 三个考虑:
1. **安装包小** — Tauri 几 MB, Electron 几十 MB. 内部用户体验比 Electron 好.
2. **Rust 底层** — 性能 / 内存比 Electron 强一档.
3. **生态够用** — 我们核心需求 (WebView + 全局快捷键 + 系统托盘 + 本地文件) Tauri 都覆盖. 没用到 Electron 独有的复杂 API.

Tauri 2 比 Tauri 1 稳定很多, 生产可用. 但承认 — 找前端工程师 Tauri 比 Electron 少, 招聘有点难.

---

### D · 商业 / 战略问题

**Q13: 公司商业模式是什么? 卖给谁?**

A: 这块产品/商业团队定. 我所了解:
- 目前内部用 (我们公司一线员工)
- 后续会考虑对外: 国企 / 央企 / 合规要求高的中大型企业 (金融 / 医疗 / 政府)
- 定价/盈利模式未公开

技术上我们的设计已经考虑了"多企业部署"场景 — 一套架构在不同企业各自部署 catfish-gateway + Companion, 不串数据.

**Q14: 跟现有"企业 AI 助手"产品 (微软 Copilot / 百川企业版等) 比, 鲶鱼优势在哪?**

A: 3 个真差异:
1. **数据真留本地** — Copilot 实质还是云服务, 数据出企业. 鲶鱼对话内容本机 SQLite.
2. **多入口同 SOUL** — Copilot 不同入口不同账号, 鲶鱼 Companion / 微信 / 飞书 共享同一份 SOUL/memory.
3. **多模型 routing** — 不锁单一上游, 哪个 model 好用切哪个 (Copilot 锁定 GPT-4, 百川锁 baichuan).

但承认 — Copilot 有 Microsoft 生态深度集成 (Word / Excel / Teams), 我们目前生态浅. 走的是不同路线.

**Q15: 怎么证明客户/员工真在用, 不是"做了没人用"?**

A: 我们内部已经 100+ 员工在用 (今天 catfish.metrics log 有真实活动). 微信 ClawBot 是最常用入口. 销售 / 法务 / 技术员工各种角色都有用案例.

但 — 我们还在 internal beta, 没正式对外发. 大规模生产数据要再过几个月才有.

---

### E · 招新问题

**Q16: 这工作日常长啥样? 加班吗?**

A: 老实说 — 我们是初创早期阶段, 节奏快但不是 996. 我个人风格是 "白天高强度 + 晚上不打扰". 上周升级 hermes + 写 plugin 那两天我连续干了 14 小时, 但前后几天正常下班. 自己掌握节奏.

加班高峰: 上游大改 / 出 P0 bug / 重要 ship. 平时正常.

**Q17: 我没玩过 hermes 也没用过 MCP, 能加入吗?**

A: 能. 我们看的是 fundamentals — Python / 系统编程 / async / 网络. hermes / MCP 这些 1-2 周能上手. 关键是出现新工具新 framework, 能不能快速学 + 改造.

如果你做过 agent 类项目 (LangChain / LlamaIndex / 自己拼) 加分.

**Q18: 用什么语言 stack? 我只会 Python 不会 Rust, OK 吗?**

A: 后端 / agent 全 Python (hermes 是 Python). Companion 前端 React + 一点 Rust IPC. 招 Agent & LLM 工程师纯 Python 就够; 招 桌面 / 前端要会 Rust + React; Infra 是 Python + 一点 shell + ops.

不会的可以学, 但要诚实告诉自己 "我愿意 1 个月内补上吗".

**Q19: 多大团队? 我加入是单兵作战还是有人带?**

A: 当前核心团队不大 (这块产品/HR 同事可以细说). 但每个领域有资深的人, 不会"扔进去自生自灭". 我自己是 hands-on 风格, 跟新人会 pair 一起写第一周的代码.

**Q20: 鲶鱼这名字怎么来的? 跟产品啥关系?**

A: 哈哈. 鲶鱼 = catfish, 在中文里也有 "鲶鱼效应" 的意思 — 搅动死气沉沉的环境带来活力. 我们做内部 AI 助手就是想给员工日常工作"搅一下", 不只是个聊天框, 而是真能帮你做事. 名字也好记.

---

## 讲完之后

如果时间还多, 可以扩展讲:
- demo 一个真实场景 (现场让鲶鱼回邮件 / 起 kanban / 查日历)
- 看一个真实 commit (e.g. 今天的 plugin commit) 讲我们 git 工作流
- 看一段真实 SOUL.md 文件讲个性化怎么配
- 跑一遍 smoke test 让大家看一下端到端验证

---

*作者: 鸿波 + Claude (Cowork)*
*配套: catfish-intro.pptx*
*生成时间: 2026-05-29*
