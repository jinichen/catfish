# catfish / edge / browser-agent

三大支柱之一：**让 Agent 替员工操作浏览器**完成网页任务（不是员工自己用浏览器跟鲶鱼聊天）。

## 现状（2026-04-24）

- **P0**：`catfish-browser-task` skill 骨架落位 —— 给模型一套"计划→导航→提取→核验"的稳健任务模板，内置失败重试与降级策略。基于 Hermes 原生 `browser_*` toolset（CDP 驱动）。
- **P0 摸底结论**：
  - Hermes 自带 browser 能力实测可用（accessibility tree 够用，不用立刻上多模态截图）
  - 首次 navigate 约 40 秒，后续 <1 秒
  - 公开页面无需登录态即可工作（GitHub / 公开 wiki）
  - Selector 稳定性一般，需要 retry

## 目录

```
edge/browser-agent/
├── README.md                           # 本文件
└── hermes-skill/
    ├── install.sh                      # 一键装 skill 到员工 Hermes
    └── catfish-browser-task/
        └── SKILL.md                    # 通用浏览器任务模板（给模型看的文档）
```

暂时只有 skill，没有独立 Python 包 / MCP server / CLI —— 因为 Hermes 原生 browser 工具已经够用，catfish 层只需要贡献"任务模式"而非"新能力"。

## 路线图

### P0（当前 · 已完成）
- [x] 摸底 Hermes browser 能力（2 次实测）
- [x] 发现 Hermes 自带 `browser_vision` 多模态工具（颠覆原设计假设）
- [x] 发现 SKILL.md 对已有 browser 暗示的 prompt 控不住，但能完成任务
- [x] `catfish-browser-task` SKILL.md 0.2 版反映实测认知
- [x] `install.sh`

### P1 · 阻塞问题头号（周一开工 · 路径已清晰）
**首次 navigate 30~60 秒 + 抖动严重**

**诊断已完成**（2026-04-24 源码阅读）：
- Hermes 用 `agent-browser` CLI 作后端，local mode 每次启新的 headless Chromium 就是慢的根因
- **Hermes 原生支持 CDP attach**（`browser_tool.py` 已实现）：
  - 配置方式 A：`~/.hermes/config.yaml` 加 `browser.cdp_url: ws://localhost:9222/...`
  - 配置方式 B：环境变量 `BROWSER_CDP_URL=...`
  - 交互方式 C：`/browser connect` 命令
- 有 `cdp_url` 时跳过 local Chromium 启动，直接 WebSocket 连已有 Chrome（`browser_tool.py:1146~1153`）
- 副产品：员工登录态**完全天然复用**（用的就是他自己日常 Chrome）

**周一执行顺序**：

- [ ] **Step 1** 手工验证能不能 attach（30 分钟）
    ```bash
    # 1. 关所有 Chrome
    pkill -f 'Google Chrome'
    # 2. 带调试端口重开
    /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
        --remote-debugging-port=9222 &
    # 3. 拿 webSocketDebuggerUrl
    curl -s http://localhost:9222/json/version | jq -r .webSocketDebuggerUrl
    # 4. 写进 hermes config
    # ~/.hermes/config.yaml 加一段：
    # browser:
    #   cdp_url: ws://localhost:9222/devtools/browser/xxxxxx
    # 5. hermes 里测一次 browser_navigate，看首次耗时
    ```
    **预期**：首次 <3 秒（因为不用再启 Chromium）

- [ ] **Step 2** 把手工验证包装成 `catfish-browser-attach` 脚本（1 小时）
    - 用 AppleScript / open 启 Chrome 带调试端口（如果员工当前没开）
    - 轮询 `http://localhost:9222/json/version` 直到就绪
    - 自动写 `~/.hermes/config.yaml` 的 `browser.cdp_url`
    - 幂等：已配过就跳过
    - 放 `edge/browser-agent/scripts/catfish-browser-attach.sh`

- [ ] **Step 3** 做 Linux / Windows 版本的等价脚本（2 小时）
    - Linux：`google-chrome --remote-debugging-port=9222`
    - Windows：`chrome.exe --remote-debugging-port=9222`（注意路径）
    - 跨平台检测和启动

- [ ] **Step 4** 融入 onboarding（0.5 小时）
    - 员工装完 catfish 后，第一次要用 browser 前 `catfish browser attach`
    - 或者开机自动跑（launchd / systemd / Task Scheduler）

### P2 · 内网场景（browser 真正的价值）
- [ ] 登录态复用已在 P1 Step 1 自动获得（用员工自己 Chrome → 所有登录态在）
- [ ] 首个内网系统适配器（基于员工实际业务，Jira / Confluence / OA 挑一个）做成 `catfish-browser-jira`
- [ ] 表单自动填 + 员工确认机制（敏感操作不自己提交）

### P2 · 内网场景（browser 真正的价值）
- [ ] 登录态复用（基于 P1 结论）
- [ ] 首个内网系统适配器（基于员工实际业务，Jira / Confluence / OA 挑一个）做成 `catfish-browser-jira`
- [ ] 表单自动填 + 员工确认机制（敏感操作不自己提交）

### P3（按需）
- [ ] 多 tab 并行（批量任务）
- [ ] 文件上传下载绑定本地目录
- [ ] 反爬检测时的优雅降级

## 已废弃的想法

- ~~"多模态截图识别"自己实现~~ —— Hermes 已内置 `browser_vision`，我们只要在 SKILL 里讲清楚什么时候用
- ~~"SKILL.md 强制模型走 4 步模板"~~ —— SKILL 是参考不是契约，对已有 browser 语义暗示的 prompt 说服力有限。真要强制要走 MCP tool 路径（但 browser 没新能力，不合适）

## 装法

```bash
bash edge/browser-agent/hermes-skill/install.sh
# 然后退出重启 hermes
```

装完后 `catfish-browser-task` 出现在 Hermes 的 `productivity` namespace 下。

## 用法（员工视角）

以后直接说："帮我打开 X 看 Y / 帮我去 Jira 查 Z"，小鲶会自动调 `catfish-browser-task` 模板（先计划再行动，可见模式，员工能看到它操作 Chrome）。

## 设计参考

- `catfish-design.md` §3.1 Browser Agent
- `edge/local-search/` —— 思路对照：local-search 做的是"封装一个新能力暴露成 MCP tool"，browser-task 做的是"给已有 tool 套一个可靠的使用模式"
