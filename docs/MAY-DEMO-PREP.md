# 5 月客户交流 · 准备清单

> 鸿波个人备忘. 5 月中旬给客户做 demo, 用这份对着收集 / 检查.
> 距 demo: 17 天 (今天 4-29).

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
- [ ] 月度汇报材料真生成 1 份 .docx (套部门模板)
- [ ] 周报草稿真生成 1 份 (包含日历 + 工单)

### 演示环境

- [ ] Companion 仪表盘有真实数据 (token 用量 / 模型分布 / TTFT)
- [ ] AuditCard 显示有 security_concern 计数 (故意触发 1 次"密码是 xxx" 的 prompt 留痕)
- [ ] 会议室 WiFi / 投影 / 备用 4G 热点 测一遍
- [ ] 笔记本电源 + 备用充电器
- [ ] 备用电脑 (一台跑 demo, 一台备用)

### 内容资料

- [ ] PPT 30 张内 (开场痛点 / 3 demo 场景 / 差异化 / Roadmap / 商业模式 / Q&A)
- [ ] `docs/ROADMAP.md` 备好 (随时翻这一页)
- [ ] `docs/POSITIONING.md` 客户问差异化时翻
- [ ] 3 个 1 分钟真实 case 短视频 (你录的)
- [ ] 跟星辰 / Hermes 1 页对比图 (我写, 见 `docs/COMPARE-1PAGER.md`)

---

## 四、demo 现场演示流程 (15 分钟核心)

### 0-3 分钟: 痛点 (不讲 AI)

> "你们公司员工早上打开电脑, 要登录 EIS / OA / 飞书 / 邮箱 4-5 个系统.
> 一份报销要跨 3 个系统填 12 个字段. 这就是鲶鱼要解决的."

### 3-13 分钟: 3 个 demo 场景

```
1. 员工: "登录 EIS 抓今天 145 条资质"
   → secret_ref + Catfish Chrome 隔离 + skill 翻页 + 落盘 + execute_code 统计 + CSV 导出

2. 员工: "看下我屏幕处理这封邮件"
   → 截图自动 route 到 vision 模型 + 不替员工按发送 (隐私设计)

3. IT admin: 打开 Companion 仪表盘
   → audit / token / 模型分布 / security_concern 标签
   → 兑现"中央可审看不到内容" 卖点
```

### 13-15 分钟: Roadmap + 商业 (翻 ROADMAP.md)

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

---

## 八、demo 后跟进 (5 月 16 日起)

- [ ] 收集客户 5 个**最具体**的反馈 (不是"挺好的", 是"X 我们用不了因为 Y")
- [ ] 写客户 case 一页 (姓名 / 部门 / 关注的功能 / 反馈 / 下一步)
- [ ] 给客户 1 周内发后续邮件: 跟进 PoC 安排
- [ ] 复盘 demo 哪些场景说服力强 / 哪些弱, 改 PPT
- [ ] 哪些问题没答好, 写 FAQ
