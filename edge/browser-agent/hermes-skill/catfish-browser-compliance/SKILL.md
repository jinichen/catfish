---
name: catfish-browser-compliance
description: 公司内网合规平台 / 风控 / 用户管理后台合规工作. 触发: 合规平台看 / 内网系统里 / 风险分析 / 合规报告 / 查管理员 / 新建用户数 / 项目合规状态. 基于 catfish-browser-task 模板, 内网特化 (登录态复用 / DOM @ref 定位 / 失败降级 / 填表不自提交).
version: 0.1.0
author: 鲶鱼 Catfish Platform Team
license: MIT
metadata:
  hermes:
    tags: [browser, compliance, internal, admin, user-management, catfish, visible-mode]
    related_skills: [catfish-browser-task]
---

# catfish-browser-compliance

让小鲶在**公司内网合规平台**上替员工做信息查询 + 报告生成 + 用户管理类工作。

源自 4-24 晚那次端到端验证 —— 登录管理系统、列用户、筛选、触发分析、读报告全流程跑通后的工程沉淀。

> **基础前提**：`catfish-browser-task` skill 已装。本 skill 是它的特化版，用同样的
> `browser_*` 原生工具 + 同样的 visible 模式约定。

---

## 何时调用

### 典型触发（员工原话）

- "帮我在合规平台上看下谁是系统管理员"
- "查下 XX 项目的合规检查状态"
- "今天有多少新建用户"
- "把所有 30 天没登录的账号筛出来"
- "对 user@example.com 这个账号跑一次风险分析"
- "下载本月合规报告"
- "查一下 abc 这个用户的权限"
- "帮我看下风控平台最近的告警"

### 不该调用的场景（用 catfish-browser-task 或 API）

- 公开网站任务（GitHub / 文档站）→ `catfish-browser-task` 即可
- 有 API 的 Jira / Confluence → 直接 curl，省时省 token
- 单纯访问内网某个页面 + 截图 → `browser_navigate` + `browser_snapshot` 就够，不必动用本 skill 的"四步合规流"

---

## 关键前置条件（员工 / 平台需具备）

跑本 skill 前确认：

1. **Catfish Chrome 已起**（`9222` CDP 端口通）—— 复用 `catfish-browser-attach.sh` 起的实例
2. **内网网络通**（VPN / 零信任 / 直连，员工自己保证）
3. **登录态复用**：员工首次登录合规平台后，专用 Chrome profile 保留 cookie，以后小鲶**不再问密码**。这是 catfish 跟"每次问密码"的工具型 agent 的本质差别
4. **平台 URL 已知**（员工在 prompt 里说，或 SOUL.md 里有"我公司合规平台是 https://xxx"）

如果有任一项不满足，**先一句话停下问员工**，不要瞎试。

---

## 核心模式（从 4-24 实战沉淀）

### 模式 1 · 列用户 / 列资源 + 按条件筛选

```
1. browser_navigate <平台 URL>/users
2. browser_snapshot — 拿 DOM accessibility tree, 找 "搜索" / "筛选" 输入框 @ref
3. browser_type @ref 填关键词
4. browser_click @ref(搜索按钮) — 触发筛选
5. browser_snapshot — 读结果列表
6. 把结果 markdown 表格化输出给员工
```

**抗失败**：
- @ref click 失败 → 用 hermes 自带的 retry (1 次)
- retry 还失败 → 不再重试, 改 `browser_navigate` 直接拼 URL 带 query string (`?q=xxx`)
- 双 fail → 报员工，提示"DOM 跟 4-24 截图不一样了，可能平台改版"

### 模式 2 · 查具体用户 / 资源详情

```
1. (前置: 已经在列表页 + 知道目标行 @ref)
2. browser_click @ref(用户名链接 / 详情按钮)
3. 等页面跳转 (browser_snapshot 直到看到详情字段)
4. 提取关键字段: 用户名 / 邮箱 / 角色 / 创建时间 / 最近登录 / 状态
5. 输出结构化结果
```

### 模式 3 · 触发合规 / 风险分析

```
1. browser_navigate <平台 URL>/compliance/run 或类似
2. browser_snapshot — 找"开始分析" / "运行检查"按钮 @ref
3. ⚠ STOP — **不要直接点提交按钮**, 先 quote 给员工看:
   "我准备点 [开始分析], 对象 = [X], 范围 = [Y], 你确认?"
4. 拿到员工 yes/确认 才 browser_click
5. 等待结果 (poll snapshot 直到看到"分析完成" / 报告链接)
6. 抽报告关键数字给员工
```

### 模式 4 · 下载报告

```
1. browser_navigate <报告页>
2. browser_snapshot — 找下载按钮
3. browser_click 下载 — 文件落到员工默认下载目录
4. 报员工"已下载到 ~/Downloads/<filename>"
```

---

## 流程结束 · 评估能不能存 skill (配套 SOUL "Skill 生成纪律")

每跑完一个完整流程, 快速判断是否值得存为可复用 skill:

**满足全部 3 条**才主动建议:

1. **重复**: `memory_recall` 查近 7 天员工同类合规查询 ≥ 3 次
2. **无红线**: 这次没触发"红线"段里任何一条
3. **员工没说一次性**: 员工没标"这次是特殊查"

满足 → **一句话主动建议**:

```
"我注意到你这周做过 X 次类似的'合规平台用户筛选 + 风险分析' 流程,
 步骤几乎一样. 要不要我存成 skill 叫 'catfish-compliance-user-risk-check',
 下次说'帮我跑用户风险分析'我直接走?
 存的话步骤会给你 review, 不存我也理解."
```

**skill 内容里只存模板, 不存真实数据**:
- ✅ `<URL>/users` URL pattern
- ✅ "搜索框 @ref - 输入关键词 - 点搜索" 步骤
- ✅ "结果表格抽 5 列输出 markdown" 输出规范
- ❌ 真实用户名 / 邮箱 / 部门信息
- ❌ 任何具体数字 / 风险分数 / 报告内容

如果员工 explicit 说"存成 skill" / "保存这个流程", 立即走 SOUL "立即建" 路径
(quote 步骤模板 → 员工 review → yes 才创建).

详见 SOUL.md "Skill 生成纪律" 章.

## ⚠ 红线（写进 SOUL 也写在这里防遗忘）

这些操作**绝对不能**在没员工明确同意时做：

- ❌ **删除用户 / 删除资源 / 重置密码**
- ❌ **批量禁用账号**
- ❌ **修改任何人的角色或权限**
- ❌ **触发会发邮件 / 短信通知到全员的操作**
- ❌ **导出全员敏感数据**（即便员工有权限）

**填表但不自提交**原则：
- 看到表单 → 可以 type 填字段（让员工看到内容）
- 看到 [提交] / [确认] / [删除] 按钮 → 一律先停, quote 整个表单内容给员工预览, 拿到 explicit yes 才 click

如果员工说"直接提交"——
- ✅ 简单查询表单（搜索、筛选）：可以直接点
- ❌ 任何写操作（创建/修改/删除）：仍然先 quote 一遍内容再 click

---

## 失败降级策略

### 1. DOM 选择器失效（@ref click error）

第一次 retry 是 hermes 自动做的。**第二次失败你（小鲶）做：**
- 不再 retry click
- 试 `browser_navigate` 直接拼 URL（带 query string / path）
- 都不行 → 报员工"页面 DOM 跟我记忆中不一样, 可能平台改版了, 你帮我看一眼?"

### 2. 登录态失效（被踢）

症状：第一次 navigate 后 snapshot 里出现"登录" / "请重新登录"

处理：
- **不要尝试自动填密码**（密码不在你这里）
- 直接报员工："看起来登录态过期了, 请你在 Catfish Chrome 窗口里重新登录, 然后我继续"
- 等员工 reply "OK 登好了" 再 retry

### 3. 平台慢 / 转圈圈

症状：snapshot 一直显示 loading spinner

处理：
- 等 5 秒再 snapshot 一次
- 还是 loading → 报员工"页面卡住, 我等了 5s, 要继续等还是放弃?"

### 4. 内网不通

症状：navigate 直接 timeout / connection refused

处理：
- **立刻停止**, 不重试
- 报员工："访问 [URL] 失败, 看起来内网不通。请检查 VPN / 零信任连接"

---

## 输出格式约定

### 列表类查询

输出 markdown 表格 + 总结一句话:

```
我在 [URL] 上查到 5 个 30 天没登录的账号:

| 用户名 | 邮箱 | 角色 | 最近登录 |
|---|---|---|---|
| alice | alice@x.com | viewer | 2026-03-25 |
...

要不要我对这些账号触发风险分析?
```

### 单实体详情

```
**用户 abc 详情** (来源: [URL])

- 邮箱: abc@x.com
- 角色: 系统管理员 ⚠
- 创建: 2025-08-01
- 最近登录: 2 小时前
- 状态: 启用
- 权限组: 全部读 + 部分写
```

### 报告类输出

```
**[报告名] · 来源 [URL]**

关键数字:
- 总告警数: 12 (高危 3, 中危 5, 低危 4)
- 待处理: 8
- 涉及用户: 4 个

详情已下载到 ~/Downloads/report-2026-04-26.pdf
```

---

## Quick Examples

### Example 1: "查谁是系统管理员"

```
你: 帮我查下合规平台上谁是系统管理员
小鲶: [browser_navigate <URL>/users → snapshot → 输入"管理员"筛选 → click 搜索]

我在合规平台上找到 3 个系统管理员:

| 用户名 | 邮箱 | 创建 |
|---|---|---|
| admin | admin@company.com | 2024-01-01 |
| chenhongbo | hongbo@company.com | 2025-08-01 |
| emergency | ops@company.com | 2025-09-15 |

注: emergency 这个账号 6 个月没登录, 要不要顺手禁用?
```

### Example 2: "对 alice 跑一次风险分析"

```
你: 给 alice@x.com 跑一次风险分析
小鲶: [navigate users/alice → click 风险分析]

我准备做这个操作:
- 对象: alice@x.com (角色 viewer)
- 范围: 默认全维度 (登录行为 / 权限变更 / 数据访问)
- 入口: <URL>/users/alice/analyze

确认运行?

你: 确认
小鲶: [browser_click → 等结果]

分析跑完了, 来源 <URL>/reports/2026-04-26-alice:

- 总分: 72/100 (中等风险)
- 高风险项 1: 凌晨 3 点登录 (2026-04-22)
- 中风险项 2: 跨部门数据访问

详情报告已下载到 ~/Downloads/risk-alice-2026-04-26.pdf
```

---

## 调试 hint (给开发者看, 员工看不到)

如果这个 skill 行为不对，先看:

1. `~/.catfish/chrome-profile/` 是不是登录态丢了 → 让员工重新登录
2. `tail -f ~/person_task/catfish/.companion-state/tool-bridge.log` 确认 browser_* 工具还在
3. `curl --noproxy '*' http://localhost:9222/json/version` 确认 Chrome CDP 9222 通
4. 平台改版 → 用 `browser_snapshot` 看一眼最新 DOM, 把 SKILL.md 里的 @ref 寻找规则更新

---

## 后续规划（v0.2+）

- [ ] 把"高频询问模式"做成 quick-action shortcut（"今天新建 N 个" / "本周告警 M 条"）
- [ ] 数据本地缓存（snapshot 后存 ~/.catfish/compliance-cache/, 重复查询用缓存避免重新 navigate）
- [ ] 跨平台能力：Jira / Confluence / 自建 OA 也走同一套四模式（创建 catfish-browser-jira / -confluence 同源 skill）
- [ ] 集成 catfish-policy: 红线操作（删除等）走 policy plugin 实时拦, 不光靠 SKILL 文字约束
