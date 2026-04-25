# browser-agent / scripts

Browser Agent 的员工辅助脚本。目前只做一件事：**把 Hermes 的 browser 工具链切换到"attach 员工已有 Chrome"模式**。

## 为什么要这个

Hermes 默认走 local mode：每次 `browser_navigate` 都启一个新的 headless Chromium 实例。代价：

| 问题 | 现状 | 走 CDP attach 后 |
|------|------|-----------------|
| 首次启动耗时 | 30~60 秒 | **<3 秒** |
| 登录态 | 新 Chrome 一片空白 | 用专用 Chrome profile，员工**手动登一次**后持久化 |
| 用户可见度 | 看不见（headless） | **Chrome 窗口就在眼前**，透明 |
| 跟日常 Chrome 关系 | 无关 | **完全隔离**，日常 Chrome 照常用 |

本质原理：Hermes `browser_tool.py` 原生支持从 `~/.hermes/config.yaml` 的 `browser.cdp_url` 读取 CDP 端点，有就跳过 local 启动直接 WebSocket 连过去（代码路径 213~1153 行）。

## Chrome 2024 安全策略（为什么要用独立 profile）

Google 从 2024 年起强化了 Chrome 的安全策略：**默认 profile 下 `--remote-debugging-port` 会被静默忽略**，防止恶意软件通过 debug port 劫持员工日常登录态 / cookie。

实测症状：用默认 profile 启 Chrome 加 `--remote-debugging-port=9222`，Chrome 能正常启动，但 `curl http://localhost:9222/json/version` 永远 Connection refused —— 端口压根没监听。

**对策**：加 `--user-data-dir=$HOME/.catfish/chrome-profile` 指向独立 profile。Chrome 看到这是非默认 profile 后就放行调试端口。

**副产品**：这个设计反而是**更好的隐私模型** ——
- Hermes 只能看到它专属 profile 里的东西
- 员工日常 Chrome 的 tab / 登录态 / cookie / 书签 / 扩展**完全隔离**
- 想让 Hermes 能用公司内网系统，在这个专用 Chrome 里登一次就行，登录态持久化到 profile 目录

## 用法

### macOS / Linux

```bash
bash edge/browser-agent/scripts/catfish-browser-attach.sh
```

流程：

1. 检测 Chrome 是否已在 9222 端口开调试（已开 → 直接用）
2. 没开 → 尝试启动 Chrome 带 `--remote-debugging-port=9222 --restore-last-session`
3. 如果 Chrome 已在跑但没开调试端口，**不会**粗暴 kill，会提示员工"保存工作后关 Chrome 重跑脚本"
4. 抓 `webSocketDebuggerUrl` 写进 `~/.hermes/config.yaml`
5. 下次 `hermes` 启动，browser_navigate 直接连已有 Chrome

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File edge\browser-agent\scripts\catfish-browser-attach.ps1
```

流程同上。

### 回到默认模式（脱钩）

```bash
bash edge/browser-agent/scripts/catfish-browser-detach.sh
```

只删 `browser.cdp_url` 配置，不关你 Chrome。

## 什么时候跑

- **首次**：装完 catfish 员工包后跑一次
- **每次 Chrome 重启**：CDP WebSocket URL 的 UUID 会变，要重跑刷新配置
  - 下一版会做成"Chrome 启动时自动触发"的 launchd / 任务计划，现在手动跑
- **每次升级 Chrome**：保险起见重跑一次

## 隐私边界（跟"直接共用日常 Chrome"的对比）

早期设计假设 Hermes attach 员工日常 Chrome，那样会看到**所有 tab / 登录态**，是个明显的隐私升级点，需要员工明确同意。

**现在实际方案（专用 profile）大幅收紧了隐私边界**：

| 维度 | 日常 Chrome（被拒） | 专用 profile（现在的方案） |
|------|--------------------|---------------------------|
| Hermes 能看到什么 | 所有 tab、cookie、登录态 | **只有专用 Chrome 里的东西** |
| 员工浏览是否被看 | 全看 | **完全看不到** |
| 登录态来源 | 自动拿员工日常的 | 员工**在专用 Chrome 里手动登一次** |
| 出问题的影响面 | 波及员工日常环境 | 限定在 profile 目录，`rm -rf` 即可清除 |

所以**这个方案反而不需要员工做权限升级决策** —— Hermes 只能看它自己那份。

但 onboarding 仍然默认"不启用" browser attach（`confirm` 默认 `N`），让员工主动 opt-in，保持设计一致性。

## 已知限制

- 同一 Chrome 只能有一个 CDP debugger 客户端连着；如果员工本人在 Chrome DevTools 里已经打开了"Inspect"，会跟 Hermes 抢 CDP 连接。解决：员工别手动开 DevTools，Hermes 在用。
- `--restore-last-session` 只在员工 Chrome 设置了"继续上次中断的地方"才真正生效。脚本会加这个 flag，但如果员工配置了"新标签页"为启动默认，重启还是丢 tab。所以提示里强调"保存好工作"。
- Chrome 本身没开过（全新安装）时首次会跑 Chrome Welcome 引导，脚本不处理，员工过一下即可。

## 路线图

- **P1.1（下周内）**：把 attach 也加到 `onboarding/install-catfish.sh` 里，问员工"要不要启用 browser attach 模式"，同意就跑
- **P1.2**：做成"Chrome 启动时自动注入" —— 用 LaunchAgent hook "Google Chrome.app" 启动事件，自动改启动参数。免维护
- **P2**：attach 模式下的**多标签并行控制**（打开新 tab 做事，不干扰员工当前 tab）
