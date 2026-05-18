# 鲶鱼 (Catfish) 产品定位 — Post-Hermes-Cutover Charter

> **决策日期**: 2026-05-19
> **决策人**: 鸿波
> **背景**: 5/19 凌晨 hermes cutover 完成后, 团队明确**不跟 hermes 比 AI 能力**, 转而做**企业员工生产力产品**
> **状态**: 正式 charter, 所有后续技术 / 产品决策按本文件过滤

## 一句话定位

**鲶鱼是基于 hermes 的中国企业员工生产力产品** — 不是 AI 框架, 不是 agent runtime, 而是把 AI 能力包装成"员工日常工作能用、企业能买、合规能过"的产品.

## 我们是什么 (Subtractive Clarity)

### ✓ 是

- **企业员工的数字副手** — 服务的是会计、审核员、运营、销售这些非技术员工, 不是开发者
- **hermes 的企业中国发行版** — 类似 RedHat 之于 Linux, GitLab 之于 Git
- **生产力工具** — 帮员工把日常工作 (写邮件 / 整资料 / 查信息 / 写报告) 做更快更好
- **企业级 SaaS** — RBAC, 审计, 多租户, SSO, 合规, 都是必备 (不是可选)
- **中文优先** — 中文 prompt 工程, 国产模型 (qwen / deepseek / 私有) 优先, 中国企业惯用语

### ✗ 不是

- **AI 框架 / Agent 引擎** — hermes 在做了, 比我们做得好
- **跟 OpenAI / Anthropic 比智能的产品** — 永远赢不了, 没必要
- **开发者工具** — CLI / API 不是我们重点, 不投入精力
- **通用 chatbot** — 我们是企业场景的专用副手, 不是 ChatGPT

## 五层护城河 (按重要性排)

### 1. 企业部署 / 合规 (最深护城河)

- **中央/边缘数据分离** — 员工业务数据不出 Mac, gateway 只做 LLM 代理 + metadata
- **私有 + 公有模型混用** — 公司内网 qwen / 私有 GPU 集群 + 公有云 deepseek / gemini, 一套 quota 系统调度
- **中国企业 SSO 协议** — 飞书 / 钉钉 / 企业微信 / 客户自建 OIDC 一键集成
- **合规审计** — 完整审计日志, 数据保留策略, 等保支持
- **完全私有化部署可选** — 全套服务能跑在客户内网

*hermes 永远不会做这层, 因为这是脏活累活. 这是我们最难被替代的部分.*

### 2. 非技术员工 UX

- **Companion macOS / Windows 桌面应用** — 不是 CLI
- **业务工具集成** — 邮件 (IMAP / Exchange / Foxmail), 日历 (Apple / Outlook), 文件, 提醒
- **中文 brand voice** — 鲶鱼人设, 中文对话风格, 中国工作文化
- **教学模式 / 反馈循环** — 员工能教鲶鱼, 鲶鱼"懂员工"
- **接续 / 再思考 / 主动 starter** — 非技术员工不会写好 prompt, UX 引导

*hermes 是 CLI 工具给开发者, 不可能服务非技术员工.*

### 3. 业务数据 / 知识接入

- **邮件 / 日历 / 文档 / 任务系统**直连
- **employee_journal** — 员工工作记录, 跨 session 累积
- **feedback** — 员工对回复的好坏标注 (👍👎 / 改建议)
- **企业知识库接入** (未来) — 内部 wiki / SOP / 政策文档

*这些数据是企业独有的, 不在 hermes 通用框架里.*

### 4. RBAC / 审计 / Quota / 多租户

- **catfish-identity** OIDC server, 客户没 SSO 直接用, 有就接客户的
- **部门级 quota** — 按部门 / 角色配 model 访问 + token 预算
- **完整审计** — 谁在什么时候调了什么 tool, 给客户合规导出
- **多租户隔离** — 一套系统服务多个企业客户, 数据完全隔离

*hermes 基本不管这些 — 它是给单开发者跑的, 不是给 1000 人公司.*

### 5. B2B 销售 / 客户成功

- **上线 SOP** — 客户企业怎么从签合同到全员用上, 完整 playbook
- **培训** — 给客户内训, 教员工怎么用鲶鱼
- **客户支持** — 7x24, 中文, 现场支持
- **故障处理 / rollback** — MEMORY-ROLLBACK-PROCEDURE 这种 emergency runbook

*开源软件没有这层. 这是我们的商业模式核心.*

## 主动放弃清单 (Stop Doing)

切完后**不再投入精力**做这些 (跟 hermes 重叠且做不过它):

| 之前我们做 | 现在归 hermes | 我们的角色 |
|---|---|---|
| Agent runtime / loop | hermes | 透传 |
| Memory 算法 (语义压缩 / 检索) | hermes plugins | 数据接入桥 (catfish-memory 短期还在, 长期消失) |
| Skill 框架 | hermes (整合中) | 我们的 skill 迁移到 hermes 格式, 共享框架 |
| Tool calling 协议 | hermes MCP | catfish_* tool 通过 MCP 暴露 |
| Context engineering | hermes context_engine plugins | 不动, 借用 |
| Multi-agent A2A | hermes 协议 | 对齐 hermes |

**核心原则**: AI 能力借 hermes, 不要重复造轮子. 我们的工程时间花在**hermes 不会做的企业层**.

## 下一阶段优先级 (5/19 起)

### Q2 5-6 月 (本月起)

1. **员工技能提升闭环**
   - skills 整合: catfish skills → hermes skills, 共享 registry
   - skills 学习曲线: 员工怎么发现 skill / 学会用 / 形成习惯
   - feedback → skill 进化: 员工反馈直接驱动 skill 改版

2. **邮件 / 日历 / 文档深度集成**
   - 邮件 step5+ (撰写 / 发送 / 优先级 / 智能归类)
   - 日历: 智能排期, 跟员工日历自动同步
   - 文档: Office 文档 (Word / Excel / PPT) 智能处理

3. **Companion UX 重做**
   - 3 按钮 audit (学习 / 接续再思考 / 停在发) — 见 SPRINT-LOG-20260519
   - 暴露 hermes 原生特性的合适入口
   - 中文 brand voice 系统化

4. **RBAC 完整化**
   - RBAC-DAY8+: 部门 / 角色 / 客户 三级权限
   - quota 精细化: 不同 model 不同 quota
   - audit 导出格式 (客户合规需要)

### Q3 7-9 月

5. **企业知识库接入** — 内部 wiki / SOP / 政策文档 RAG
6. **多租户**生产部署 — 一套系统服务多个企业客户
7. **私有化部署** runbook — 客户全套内网部署
8. **客户成功 SOP** — 从签合同到全员用上, 标准化

### Q4 10-12 月

9. **B2B 销售 motion** — 跟客户成功联动, 销售方法论
10. **Web 版 Companion** — 不强依赖 macOS desktop
11. **国产模型深度优化** — 国产 model 性能/质量调优

## 不做清单 (永远不做)

避免被诱惑:

- ✗ 跟 OpenAI / Anthropic / 智谱比 base model — 我们不训模型
- ✗ 自建 agent runtime — hermes 在做, 不重复
- ✗ 开放 API 给开发者 build agent — 不是我们用户
- ✗ 通用聊天机器人 — 鲶鱼是企业场景副手, 不是 ChatGPT 替代
- ✗ 海外市场 — 中文中国市场, 别分心
- ✗ C 端用户 — B2B 企业客户, 不卖个人

## 跟客户 / 投资人讲鲶鱼是什么

### 一句话 (电梯)

"鲶鱼是企业员工的 AI 副手 — 接你的邮件日历文档, 用你的合规 RBAC, 走你的国产模型, 让员工写邮件 / 整资料 / 做报告快 3-5 倍."

### 两段 (融资 / 客户洽谈)

> 大型企业想用 AI 提员工效率, 但市面上 ChatGPT 这类产品都是 SaaS, 数据出境合规过不了, 且不能接公司邮件日历, 员工实际用不上.
>
> 鲶鱼的方案: 中央服务 (LLM 代理 + RBAC + 审计) 跑在客户内网, 员工电脑装 Companion 桌面应用接员工本地数据 (邮件 / 文件 / 日历), AI 引擎用开源 hermes (NousResearch 出品), 模型支持国产 (qwen / deepseek) + 客户私有 GPU. 完整中文产品, 合规 + 数据隔离 + 国产化, 适合国央企 + 大型民企采购.

## 给团队的"宪法"

任何新功能 / 新需求, 过三问:

1. **这件事 hermes 已经做了吗?** → 是 → 用它的, 不要重复
2. **客户企业会因为这功能买单吗?** → 否 → 砍掉, 资源转移
3. **这是 AI 能力还是企业能力?** → AI 能力 → 借 hermes; 企业能力 → 我们做

---
*作者: 鸿波 + Claude (Cowork mode), 5/19 早上 7:00 拍板*
*依据: 5/19 凌晨 28h sprint 完成 hermes cutover 后的战略反思*
*相关: SPRINT-LOG-20260519.md · MEMORY-OWNERSHIP-ARCHITECTURE.md · POSITIONING.md (旧版, 待更新对齐)*
