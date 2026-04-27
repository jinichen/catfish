---
name: catfish-browser-task
description: 帮员工用浏览器完成网页任务（访问内网系统、查数据、填表单、抓信息）。当用户说"帮我去 Jira / Confluence / OA / 内网系统做…"、"帮我在某网站看看/填个表/下载…"、"打开…看看…"时必选此 skill。它包装了一套"计划→导航→提取→核验"的稳健模板，内置重试、失败降级、结构化输出约定。只在 visible 模式跑，员工随时能看到并打断。
version: 0.1.0
author: 鲶鱼 Catfish Platform Team
license: MIT
metadata:
  hermes:
    tags: [browser, web, automation, catfish, jira, confluence, visible-mode]
    related_skills: []
---

# catfish-browser-task

让小鲶**替员工操作浏览器**，基于 Hermes 自带的 `browser_click` / `browser_snapshot` / `browser_cdp` 等原生工具，配合 catfish 自己写的 `catfish_browser_goto` (导航) + `catfish_screenshot` (像素级看图)，提供一套稳健的任务模板。

## ⚠️ 工具选择铁律 (踩过坑总结)

| 想干啥 | 用啥 | 不要用啥 |
|---|---|---|
| **打开 / navigate 一个 URL** | **`catfish_browser_goto`** (catfish 自己写, 走原生 CDP page-level Page.navigate, 返回真实 title+url) | `browser_navigate` (hermes 上游, 实测在 Companion 隔离 Chrome 上**调用 ✓ 但页面没真换**, 而且模型还会编"已打开") |
| 看页面整体 | `browser_vision` 粗看 / `catfish_screenshot` 精细 | (见下面"第 3 步 看图") |
| 拿元素结构 | `browser_snapshot` | — |
| 点击元素 | `browser_click` | — |
| 输入文字 | `browser_type` | — |
| 读 console / 网络 | `browser_console` / `browser_network` | — |

**铁律**: **导航永远用 `catfish_browser_goto`** (除非它真挂了再 fallback hermes browser_navigate). 历史教训 (2026-04-27): 用户让模型"打开搜狐", 模型 ✓ 调 browser_navigate, 然后编"搜狐首页已打开, 顶部有导航栏...", **实际 Chrome 还停在 about:blank**. 这种 hallucination 是 hermes 上游 fork 的 bug, 我们不修上游, 直接绕开。

## 何时调用（重要：browser 是"最后一公里"工具，先看有没有 API）

### 决策树

在启动任何 `browser_*` 工具之前，**必须先自问**：

1. **有没有官方 API？**
   - GitHub / GitLab（公开 repo）→ 走 `curl` REST API，远快于 browser
   - Jira / Confluence → 有 REST API，但企业常要 PAT 或 SSO，没有 token 时才退 browser
   - 公司自建 OA / ERP / 老 SaaS → 通常无 API，**这是 browser 的真正用武之地**

2. **员工有没有该系统的 API token？**
   - 有 → 走 API（用 `terminal` 调 curl，让员工 export TOKEN 或指向 ~/.secrets 文件）
   - 没有 → 走 browser

3. **页面是 SPA 还是传统 HTML？**
   - SPA 且关键内容不在 API → browser 必需
   - 传统 HTML → `curl` + 手工解析可能够用

**优先级**：官方 API（curl） > browser（本 skill） > 截图识别（兜底）

### 必选 browser 的典型触发

- "帮我去内网 OA 系统提报销"（几乎都没 API）
- "在公司 ERP 里查这个订单状态"（内部老系统无 API）
- "帮我去 Jira 看这 sprint 的 ticket"（如果没 Jira token）
- "Confluence 里搜项目 X 的页面"（如果没 Confluence token）
- "这个网站没有 API，帮我抓 …"（员工明说了）

### 先走 API 不走 browser 的场景

- "看 GitHub repo XXX 的 star 数 / issue / commit" → `curl https://api.github.com/repos/...`
- "查 PyPI 上包 X 的最新版本" → `curl https://pypi.org/pypi/X/json`
- "看推特 / Twitter API"（需要 Bearer token）
- 任何标明 "REST API" 的公共站点

### 不要调用本 skill 的场景

- 找本地文件 → 用 `catfish-local-search`
- 查公网知识（天气、通用新闻）→ 用 `web_search`
- 员工要自己点 → 告诉他链接就够了

## 核心原则

### 1. 可见模式（visible mode）

**始终**使用 visible Chrome：员工能看到你在操作，可以随时打断。**绝不**用 headless。

这是鲶鱼的"透明不黑盒"承诺。

### 2. 不存凭据

**不要**把员工的账号密码写进任何文件、memory 或 SOUL。需要登录时走员工本人 Chrome profile 的已有登录态（见"登录态复用"）。

### 3. 先计划再行动

复杂任务（多页、多 tab、填表）**必须**先告诉员工你打算怎么做，让他有机会在你点错之前叫停。

### 4. 结构化输出

抓取类任务永远返回 JSON 或 markdown 表格，不要散文。员工后续要拿这些数据做事。

## 任务四步模板

每个 browser 任务按这四步走。跳步是 bug 源头。

### 第 1 步：计划

在调任何 `browser_*` 工具之前，先输出简短计划：
- 要访问的 URL
- 预计用到哪些 `browser_*` 工具
- 预计返回什么结构

例：
```
计划：
1. browser_navigate https://jira.company.com/browse/PROJ
2. browser_click 选 "Sprint 42" 过滤器
3. browser_snapshot 抓板子上所有 ticket
4. 返回 JSON: [{key, title, status, assignee}]
```

### 第 2 步：导航

用 `browser_navigate` 打开页面。**首次 navigate 可能要 10~60 秒**（Chrome 冷启动、CDP 握手、页面异步加载），别中途以为卡死就 retry。

超过 90 秒仍未响应才认定失败。失败了：
1. 试 `browser_snapshot` 看当前是什么
2. 如果是登录墙，进入"登录态处理"分支
3. 如果是网络错误，告诉员工"VPN / 内网不通，你这边试试能手动打开吗"

### 第 3 步：提取

两种提取方式，按页面性质选：

**A. accessibility tree（默认）** —— 走 `browser_snapshot` + ref
适用：Jira / Confluence / GitLab / GitHub / 大多数 SPA。拿到结构化元素列表后按 ref（比如 `e22`）定位。
注意 `browser_snapshot full` 遇到大页面会被**截断**，碰到就换 `compact` 或 scroll 后再 snapshot。

**B. 多模态 — 看图** —— 两个工具二选一, 看场景

| 场景 | 用啥 | 为啥 |
|---|---|---|
| 看页面**整体布局 / 颜色 / 大致内容** (粗看) | `browser_vision` | 走 question 接口直接答, 省一步往返 |
| **验证码 / 小数字 / 像素级精确字符识别 / 任何要"看清"的事** | **`catfish_screenshot mode=fullscreen`** + 让 Qwen3-VL 直接看图 | browser_vision 实测在精细识别上不可靠 (它有自己的 vision pipeline, 分辨率/采样可能丢精度, 模型有时直接瞎答) |
| canvas 图表 / PDF 嵌入 / 富媒体 accessibility 不完整 | 先试 `browser_vision`, 不准就 `catfish_screenshot` 兜底 | — |

**铁律 (踩过坑总结)**:
> **验证码 → 永远 `catfish_screenshot`**, 不要 `browser_vision`. 历史教训 (2026-04-27): browser_vision 在 catfish_browser_task 跑验证码登录时, 看似 ✓ 调用成功但实际填 e3="xtF7" 是**模型用历史数据猜的**, 跟当前页面的验证码毫无关系。换 catfish_screenshot 拍 Chrome 窗口让 Qwen3-VL 直接看就准。

**代价对比**:
- `browser_vision`: 10~20s, 走 hermes 内置 pipeline. 精度不可控.
- `catfish_screenshot fullscreen`: <1s 拍主屏 + base64 喂下一轮 user message. Qwen3-VL 直接当 user input 看, **精度跟你直接给员工看图一样**. (浏览器场景 Chrome 占主屏, fullscreen 拍下来就是浏览器内容.)

> ⚠️ **工具名清单 — 别瞎猜**:
> - 浏览器**粗看页面**: `browser_vision`
> - 浏览器**精确识别** (验证码 / 数字 / 细节): `catfish_screenshot mode=fullscreen` (Chrome 窗口在前台时拍 Chrome)
> - 员工**桌面应用** (Excel / Foxmail / 桌面 GUI): `catfish_screenshot mode=fullscreen`
> - **没有** `browser_screenshot` / `screenshot` / `take_screenshot` 这种工具.

**规则**：
- 先 A 后 B
- A 能拿到数据坚决不上 B
- 一次任务里 B 调用 ≤ 3 次（超过就说明任务在 accessibility 不足的页面上，应该跟员工说"这个页面非结构化难抓，要不换个方式"）

### 第 4 步：核验

输出之前自问：
- 字段数对吗？（员工要 3 个 issue，你给 3 个 吗？）
- 数值有无"114k"这种缩写需要展开？
- 时间戳是相对（"3 分钟前"）还是绝对（`2026-04-24T11:15`）？员工可能更想要绝对时间
- 如果有"你不知道"的字段，**明说不知道**，不要编

### 第 5 步：评估能不能存 skill (重要 · 配套 SOUL "Skill 生成纪律")

任务跑完输出结果之后, **快速判断这事是不是值得存 skill**:

**满足以下全部 3 条**才主动建议:

1. **重复性**: 用 `memory_recall` 查近 7 天员工有没有做过类似 (同域名 / 同步骤数 / 同最终目标), 数 ≥ 3 才算
2. **无红线**: 这次没涉及 send_email / delete / 改外部数据 / 读他人数据 / 操作凭据
3. **员工没标记 ad-hoc**: 员工没说"算了" / "这是一次性的" / "下次别这么做"

满足 → **主动一句话** (不强推):

```
"我注意到你这周 X 次跑同一个 [域名/任务], 步骤几乎一样.
 要不要我把它存成 skill, 下次说'帮我做 [任务名]' 直接走?
 存的话步骤会给你 review, 不存我也理解."
```

不满足 → **不要提**, 直接结束

如果员工 explicit 说"存成 skill" / "保存这个" / "记下来这个流程",
**立即**走 SOUL "Skill 生成纪律" 章的"何时立即建" 路径, 不需要 ≥ 3 次条件.

**红线 (绝不做)**:
- ❌ 没员工 yes 自动 `skill_manage(action=create)`
- ❌ skill 内容里存真实数据 (用户名 / 数字 / 邮箱 / 等), 只存"步骤模板 + 参数定义"
- ❌ skill 命名用 "auto-1" 这种, 必须业务可读 (例 `catfish-jira-sprint-status`)

详见 SOUL.md "Skill 生成纪律" 章.

## 常见失败模式与对策

### 失败 A：click 点了没反应

现象：`browser_click e22` 返回 OK，但 snapshot 显示页面没变。

原因：selector 漂移、元素被遮挡、JS 事件没挂上。

对策：
1. 先 `browser_snapshot full` 确认 e22 真的还在、真的是目标元素
2. 如果 ref 变了（页面重排），重新从 snapshot 找
3. 2 次失败后换策略：直接 `browser_navigate` 到目标 URL（比如 Issues tab 的直接链接），绕过点击

### 失败 B：首次 navigate 超时（连续失败 2 次以上）

现象：`browser_navigate` 返回 timeout，30 秒左右就 error 掉，retry 也过不去。

根因（观察到的）：Chrome 冷启动 + CDP attach + 页面异步加载 + 可能的网络波动。
已知 Hermes 0.10 的 browser toolset 在首次启动有较大概率触发这个路径。

对策（按顺序）：

1. **连续失败 2 次，立刻考虑 API 降级**。很多"网页任务"其实有更快的 API 解法（GitHub → `api.github.com`、PyPI → JSON 端点、Jira → REST API）。
2. 告诉员工："browser 这边连超时了，我走 API 更快，你接受吗？"
3. 确认有 API 路径时，直接用 `terminal` + `curl`。这不是 workaround，在有 API 的场景下这**就是更优方案**。
4. 确认没有 API 且必须 browser 时，等 Chrome 真的启好后 3 次内还不通就停，告诉员工"browser 工具当前不稳定，我这边没法绕过"。

长期优化（P1，平台侧）：保持 Chrome 常驻（CDP persistent session），让"首次 30 秒"变成"首次 3 秒"。

### 失败 C：页面要登录

现象：navigate 后页面是登录页，不是你要的内容。

原因：Hermes 默认启的 Chrome 可能是全新 profile，没复用员工日常的登录态。

对策：
1. 查是否有环境变量 / 配置指定员工 Chrome profile 路径
2. 没有就**停**，告诉员工："这个页面需要登录，我默认的 Chrome 没你的登录态。两个办法：a) 你在这个 Chrome 里登一次我就能用；b) 配置让我用你日常 Chrome profile（鲶鱼平台团队在做中）"
3. **绝不**要求员工把账号密码发给你

（长期：接员工已打开的 Chrome 的 CDP 9222 端口，直接拿到所有登录态，见下一节）

### 失败 D：被反爬 / 风控挡住

现象：Cloudflare 验证码、"检测到异常流量"之类。

对策：**立刻停**，告诉员工。**绝不**尝试绕过。这不是技术问题，是合规问题。

## 登录态复用（路线图）

**现状 P0**：Hermes 默认启全新 Chrome，没登录态。

**目标 P1**：两种方案二选一（由平台团队配置）

**方案 1**：Hermes 启动时 `browser_cdp attach` 到员工已开的 Chrome（需要员工启动 Chrome 时加 `--remote-debugging-port=9222`）。优点：登录态天然复用。缺点：员工要改启动方式。

**方案 2**：Hermes 用独立 Chrome 但复用员工 Chrome profile 目录（`--user-data-dir`）。优点：员工不用改习惯。缺点：同一 profile 不能同时开两个 Chrome 实例。

P1 上线前，任何需要登录的公司系统任务都要先走"失败 C"路径。

## 典型场景模板

### 场景 1：抓取公开信息（**先走 API**）

```
员工："帮我看看 github.com/NousResearch/hermes-agent 现在有多少 star"

决策：GitHub 有公开 REST API，不走 browser。
terminal 调：curl -s https://api.github.com/repos/NousResearch/hermes-agent | jq '.stargazers_count'
返回：{"stars": 114000, "as_of": "2026-04-24 11:30"}
```

只有当同样的问题是问**没有 API 的站点**（比如员工公司自建 wiki），才走 browser_navigate。

### 场景 2：Jira sprint ticket 列表（需登录，P1）

```
员工："帮我看看 Jira 里这 sprint 所有已关闭的 ticket"

前置检查：browser_navigate 公司 Jira → 是登录页吗？是则走"失败 C"

计划：
1. browser_navigate https://jira.company.com/projects/XXX
2. 过滤 Sprint=当前 / Status=Closed
3. browser_snapshot 所有 ticket
4. 返回：[{"key": "PROJ-123", "title": "...", "assignee": "...", "closed_at": "..."}]
```

### 场景 3：填表（敏感操作，必须确认）

```
员工："帮我在 OA 系统提个本月报销，金额 3500"

关键：填好后**不要自己点提交**。让员工自己点。

计划：
1. browser_navigate OA 报销页
2. 填表单字段
3. 停住，截图给员工看："表格填好了，请你本人核对后点提交。"
```

## 性能约束

- 首次 navigate：容忍 90 秒
- 后续 navigate / click / snapshot：容忍 15 秒
- 一个任务总时长超 5 分钟：主动 checkpoint，告诉员工"目前进度…，要继续还是停？"

## 隐私边界

- **不存**：账号密码、浏览历史、页面原始内容
- **可存**（经员工同意）：任务模板（"我下次再要 sprint ticket 就这样查"）、结果结构（"Jira 返回格式是这样"）
- **永不存**：cookies、Authorization header、session token

如果员工问"记一下我公司 Jira 地址是 X"，可以用 memory 工具记，但**只记公开信息**，不记凭据。
