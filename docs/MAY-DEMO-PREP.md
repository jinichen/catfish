# 5 月客户交流 · 准备清单

> 鸿波个人备忘. 5 月中旬给客户做 demo, 用这份对着收集 / 检查.
> 距 demo: 17 天 (今天 4-29).
>
> 📌 **2026-04-30 更新**: 加场景 4 (跨 session 记忆) 准备清单 + leadership-briefing 双 backend 错别字检查准备项.

---

## 一、明天去公司要拿回来的素材 (5 分钟搞定)

### 必拿

- [ ] **部门汇报材料模板** (`.docx`)
  - 1 个空白模板
  - 1-2 个最近实际写过的, 让小鲶学风格 (用词 / 结构 / 数据展示偏好)
  - 注意: 部门 logo / 报头红线 / 页脚部门名 / 字号字体习惯

- [ ] **EIS 资质列表页面**
  - 截图当前页 (看清字段: 编号 / 类别 / 部门 / 到期日 / 状态)
  - 看 URL: 有没有 `?dept=xxx&page=1` 类参数 (推断后端 API)
  - 看右上角 / 工具栏: 有没有 **"导出"** 按钮 (有的话方案 D 直接成立, 不用爬)
  - F12 → Network → 翻一页, 看有没有 `/api/.../list` 类调用 (后端 API 路径)

- [ ] **部门周报模板** (如果有)
  - 是 docx 还是 markdown / 文本?
  - "本周完成 / 下周计划 / 风险" 这 3 段是不是标准结构?

### 可选 (有时间就拿)

- [ ] **OA 工作台截图** (如果用 OA 而非 EIS)
- [ ] **公司 LOGO / 品牌色** (设计 5 月 PPT 时用)
- [ ] **报销系统截图** (如果做报销自动 skill)

### 顺手 30 秒录像

- [ ] 屏幕录制: 你正常用 catfish 干活的过程 (登录 / 截图 / 抓数据 任意一个场景)
- 路径: cmd+shift+5 → 选录制窗口 → 30s 即可
- 用途: 5 月 demo PPT 嵌入 "真实使用" 片段

---

## 二、拿回来后我帮你做 (1 天)

### 业务 skill scaffolding (3 个)

- [ ] `eis-qualification-export` — 用你拿的 EIS 字段写 selector / API 路径
- [ ] `weekly-report` — 用你的周报模板写格式映射
- [ ] `leadership-briefing` — 用你的汇报模板套 docx 风格

每个 skill 包含: SKILL.md / triggers / 步骤模板 / 边界 / 错误处理. 你跑通 1 次后 commit.

---

## 三、demo 前 1 周必做的工程检查 (5 月 8-12 日)

### 服务稳定性

- [ ] 4 个服务 7×24 跑稳: gateway / catfish-identity / tool-bridge / Companion
- [ ] watchdog respawn 实测: `kill -9` 后 5-10s 自动起
- [ ] 日志没异常 ERROR (`tail -f ~/person_task/catfish/.companion-state/*.log`)
- [ ] `~/.catfish/gateway_audit.jsonl` 文件 > 100 行 (有真实数据可演)

### SSO 链路

- [ ] catfish-identity port 8998 响应正常 (`curl /healthz`)
- [ ] gateway port 8999 响应正常 + auth=oidc (.env 配 prod)
- [ ] Companion `~/.catfish/companion.yaml` 配置正确
- [ ] 端到端登录: 浏览器 → catfish-identity → Companion 进主界面
- [ ] dev_token 切换可行 (备用)
- [ ] **演示账号**: 创 2 个 demo user (`demo1@ffcs.cn`, `demo2@ffcs.cn`), 现场切换看权限隔离

### Skill / 业务

- [ ] 3 个业务 skill 都跑通 1 次端到端
- [ ] EIS 资质数据真抓 145+ 条 (CSV 落盘)
- [ ] 月度汇报材料真生成 1 份 .docx (套部门模板, **4 段公文 + 表格转 CSV 附件 + 双 backend 错别字检查全流程跑通**)
- [ ] **typo_check + pycorrector 双 backend** 在 hermes venv 跑通 (4-30 接通, demo 现场要确认 hermes venv 装了 litellm + pycorrector + kenlm + Chinese model)
- [ ] 周报草稿真生成 1 份 (包含日历 + 工单, **方式 A+ 用 session_search 自动抽近 7 天**)

### ★ 跨 session 记忆 (4-30 新增, 场景 4 必备)

- [ ] **演示前 7 天**: 真实用 catfish 工作 (跑 demo 准备 / 写代码 / 写 PPT 等), 确保 `~/.catfish/employee_journal.md` 至少有 5-10 段 session 摘要 (没内容客户看不到效果)
- [ ] `~/.hermes/state.db` 至少 5 个 session 有 message_count > 3 (档1 才注入)
- [ ] DASHSCOPE_API_KEY 配在 hermes venv `.env` 里, session_summarizer 后台异步总结正常
- [ ] `tail -20 ~/.catfish/employee_journal.md` 看最近总结质量 (主题简洁 / 第三人称 / 抓重点)
- [ ] 演示**前一晚关掉 Companion 重启一次**, 触发后台总结, 确保 demo 时刚好有"昨天那个汇报材料" 的真实记忆
- [ ] gateway inject 链路实测: `curl -X POST gateway/v1/chat/completions ...` 看 system prompt 含 `## 📅 员工最近 7 天 session 历史` + `## 📝 员工长期日记`
- [ ] **录屏 backup**: 录一段"员工新对话 → 鲶鱼引用昨天上下文"的场景 4 录屏, 现场 live 演示翻车有 fallback

### 演示环境

- [ ] Companion 仪表盘有真实数据 (token 用量 / 模型分布 / TTFT)
- [ ] AuditCard 显示有 security_concern 计数 (故意触发 1 次"密码是 xxx" 的 prompt 留痕)
- [ ] 会议室 WiFi / 投影 / 备用 4G 热点 测一遍
- [ ] 笔记本电源 + 备用充电器
- [ ] 备用电脑 (一台跑 demo, 一台备用)

### 内容资料

- [ ] PPT 32 张内 (开场痛点 / **4 demo 场景** (含跨 session 记忆) / 差异化 / Roadmap / 商业模式 / Q&A)
- [ ] `docs/ROADMAP.md` 备好 (随时翻这一页)
- [ ] `docs/POSITIONING.md` 客户问差异化时翻
- [ ] 3 个 1 分钟真实 case 短视频 (你录的)
- [ ] 跟星辰 / Hermes 1 页对比图 (我写, 见 `docs/COMPARE-1PAGER.md`)

---

## 四、demo 现场演示流程 (15 分钟核心)

### 0-3 分钟: 痛点 (不讲 AI)

> "你们公司员工早上打开电脑, 要登录 EIS / OA / 飞书 / 邮箱 4-5 个系统.
> 一份报销要跨 3 个系统填 12 个字段. 这就是鲶鱼要解决的."

### 3-15 分钟: 4 个 demo 场景 (4-30 加场景 4)

```
1. 员工: "登录 EIS 抓今天 145 条资质, 用部门模板生成本月汇报"
   → secret_ref + Catfish Chrome 隔离 + skill 翻页 + 落盘 + execute_code 统计 + CSV 导出
   → 4 段公文 (概况/分项/问题/下一步) + 表格转附件 + 双 backend 错别字自审 → .docx

2. 员工: "看下我屏幕处理这封邮件"
   → 截图自动 route 到 vision 模型 + 不替员工按发送 (隐私设计)

3. IT admin: 打开 Companion 仪表盘
   → audit / token / 模型分布 / security_concern 标签
   → 兑现"中央可审看不到内容" 卖点

4. ★ 第二天员工新对话: "昨天那个汇报怎么改"
   → 关 Companion 重启, 鲶鱼直接接上下文 (档1 + 档2)
   → 引用上周 session 摘要 + 长期 journal
   → 当场 cat ~/.catfish/employee_journal.md 给客户看"记忆全本地"
   → 兑现"真同事不是聊天机" 卖点
```

### 15-17 分钟: Roadmap + 商业 (翻 ROADMAP.md)

> Phase 1 现在 ship · 单员工副手
> Phase 2 Q3 2026 · 团队版 (SSO + RBAC + Skill share)
> ★ Phase 3 Q4 · Catfish Federation - 跨员工 agent 协作 (护城河)
> Phase 4 2027 Q2+ · 集团级网格

> "Phase 3 是真正护城河 - SaaS 做不到 (中心化), 同质化产品没设计 (没组织层概念).
> 12 个月内 ship → 客户长期被锁定."

---

## 五、客户必问的问题 + 你的答案

| 问题 | 答案 (3-5 句话, 不展开) |
|---|---|
| 跟 ChatGPT / 通义比? | 它们是聊天工具, 鲶鱼是工作流副手. 不一样的产品形态. |
| 数据安全? | 三层架构. 对话不出员工本机, 中央 gateway 只看 metadata + audit. 客户 IT 可以审计代码 + 配置. |
| 上线时间? | 现在内部 dogfood 中, Q3 给你们 PoC, 驻场部署 + 培训. |
| 跨 LLM 厂商? | 现在支持 qwen / gemini / 你们自己 LLM, 接新厂商 1 周 (LiteLLM 标准协议). |
| 成本? | 私有化部署, 一次性 license + 年度维护. 不抽 API token 流水 (你们用自己 LLM). 50-200 人 SMB ~X 万 / 年, 集团单独谈. |
| 跟星辰超级智能体什么不同? | 我们是企业 LLM 中台 + 边缘副手, 三层架构 + 身份层 + 凭据安全. 详见对比图 (翻 docs/COMPARE-1PAGER.md). |
| 多员工协作? | Phase 3 (Q4 2026) Catfish Federation 跨员工 Agent 协作. 这是我们核心差异化. |
| Win 跨平台? | Mac 现在 ship, Win Q3 出. |

---

## 六、demo 当天检查清单 (上场前 1 小时)

- [ ] 笔记本电量满 + 充电器
- [ ] 关掉所有非 catfish 窗口 (减少演示分心)
- [ ] Catfish Companion / catfish-identity / gateway / tool-bridge 全绿
- [ ] 网络 (公司 WiFi + VPN 测)
- [ ] dev_token 备用 (万一 SSO 现场抽风, 切 dev_token 不影响其他)
- [ ] 浏览器只开必需 tab (EIS 登录 + Companion + PPT)
- [ ] 截屏 / 录屏快捷键试一遍 (cmd+shift+4 / cmd+shift+5)
- [ ] 杯水 + 备用 USB-C HDMI 适配器

---

## 七、紧急情况 fallback

| 情况 | 处理 |
|---|---|
| catfish-identity 挂了 / SSO 不通 | `unset CATFISH_OIDC_ISSUER` + `launchctl setenv CATFISH_DEV_TOKEN xxx` → dev_token 模式继续演 |
| gateway 挂了 | watchdog 5s 内重启, 不行手动 `pkill -9 -f catfish_gateway` 让 watchdog 拉 |
| 私有 LLM 不通 | fallback 链自动切 qwen-flash (公网), demo 时讲"看, 容灾自动切" 反而是卖点 |
| Catfish Chrome 不响应 | Companion 控制台 → Chrome 卡片 → 重启 |
| 演示中模型答错 | 别慌. 说"模型偶尔会错, 关键是我们能审计 / 能纠错 / 能换模型. 这就是 catfish 中央 gateway 的价值." |
| 模型自数错 (人数 / 行数) | 说"这就是为什么我们强制 execute_code, 我现在演示一下精确版" → 重跑用 `统计`关键词触发 stats_guard |
| 场景 4 鲶鱼记不起来 | "看, employee_journal 里这条 session 还没被后台总结 — 异步设计不阻塞主流程. 我直接 cat 给大家看已总结的几段 (引用别的真实 session)" → 现场打开 markdown 让客户看记忆颗粒度 |
| 客户怀疑跨 session 是上传云端 | 当场终端 `cat ~/.catfish/employee_journal.md` + `ls ~/.catfish/` 给客户看. "这是文件本体, 在我笔记本上, 没有任何云端备份." |

---

## 八、demo 后跟进 (5 月 16 日起)

- [ ] 收集客户 5 个**最具体**的反馈 (不是"挺好的", 是"X 我们用不了因为 Y")
- [ ] 写客户 case 一页 (姓名 / 部门 / 关注的功能 / 反馈 / 下一步)
- [ ] 给客户 1 周内发后续邮件: 跟进 PoC 安排
- [ ] 复盘 demo 哪些场景说服力强 / 哪些弱, 改 PPT
- [ ] 哪些问题没答好, 写 FAQ
