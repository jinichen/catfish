# 5/14 demo 现场应急表

> 央企现场常见挂法的 30 秒切到稳态预案. 5/13 rehearsal 配合演练.
>
> **铁律**: 出问题不慌, 现场只说 "稍等一下我换个路径", 不要解释 root cause.
> **武器**: Cmd+Shift+P (桌宠 toggle) / Cmd+Shift+Space (主窗召唤) / 仪表盘卡 (人工触发) / 录屏 (兜底播放).

---

## A · 网络挂

### A1. 内网 LLM (Hermes) 502 / 慢 / 不响应
**症状**: chat 发出后 30s+ 没响应, gateway 报 503/UNAVAILABLE
**应急**:
1. **不要等** — 直接 chat 顶部下拉菜单切到 `qwen-flash` 或外网 fallback
2. 客户问"为啥换" → "这个场景换个轻量模型快一些"
3. 现场说: 鲶鱼有 fallback chain 自动降级 (其实手动切的, 但 prod 真有自动 fallback, 不算撒谎)

**验证**: 5/13 rehearsal 时 mock 一次 — 临时改 gateway env `INTERNAL_LLM_BASE_QWEN_MAIN=http://invalid`, 看 chat 报错就切 fallback

### A2. 公司局域网 WiFi 抖 / 中间断
**症状**: chat 流式中断, "ServerDisconnected" / "Connection error"
**应急**:
1. Companion chat 自动重试 (gateway timeout 180s 内)
2. 真断了重启 vpn (Clash 那个 7890 端口) 后再发
3. 现场说: "网络抖了一下我重发" (5s 内做完)

### A3. VPN 整个挂
**症状**: 内网 LLM 直接连不上, gateway 报"上游连接失败"
**应急**:
1. **录屏 fallback** — 我们提前录好 5 个场景的视频 (5/12 前录完), 现场播这个
2. 客户问"实时演示呢" → "今天网络条件特殊, 我们先看完整流程, demo 完留你们工程师对接环境"
3. 不要现场调网络 — 浪费客户时间

---

## B · Companion 本身挂

### B1. Companion 启动崩 / 闪退
**症状**: 双击 .app 起不来 / 起来 0.5s 自闭
**应急**:
1. **打开 Console.app 看 catfish-companion 进程 crash log** — 通常是 Tauri permission / port 被占
2. **15s 内**: kill 残留 `gateway` / `tool-bridge` / `chrome` 进程 (`pkill -f catfish_gateway && pkill -f catfish_tool_bridge`), 重启 Companion
3. 不行就**录屏 fallback**

**预防**: 5/13 在跟 demo 同一台机子上跑一整天 (含 watchdog respawn 行为), 3 次启动 0 崩才算稳

### B2. 主窗白屏 (webview 加载失败)
**症状**: Companion 起来但只见白色窗口
**应急**:
1. **Cmd+R** 刷新 webview (主窗右键检查元素, devtools 里 ⌘R)
2. 不行 Cmd+Q 关 + 重启 Companion
3. 仍不行: rm `~/.companion-state/` 整个 + 重启 (重置所有本地状态)

### B3. 桌宠不显 / 不冒泡
**症状**: Cmd+Shift+P 没反应, 或者桌宠出来但 "测一下 ▶" 不冒气泡
**应急**:
1. **Cmd+Shift+P 再按一次** (有时第一次 toggle 卡, 第二次正常)
2. 仪表盘 → ProactiveCard "测一下 ▶" 直接驱动一次
3. 桌宠不显: 可能员工屏幕分辨率跟存的位置不匹, Option+Shift+1/2/3/4 切到屏角

### B4. chat 输入框卡死 / 不能打字
**症状**: 点了输入框光标不闪, 打字没反应
**应急**:
1. 切到其他 tab (控制台 / 仪表盘) 再回 chat — 强制 React re-mount
2. 不行 Cmd+R 刷新
3. 不行 + 新对话 — 旧会话状态可能脏, 新开一个

---

## C · 工具 / Skill 挂

### C1. tool-bridge 死掉 (LLM 调工具报 timeout)
**症状**: chat 流到一半"工具不存在" / 30s 后才 fail
**应急**:
1. watchdog 5s 内 respawn — 等 5-10s 再调一次工具
2. 实在不响: 终端 `pkill -f tool_bridge`, autostart 自动起
3. **真烂**: 仪表盘 tool-bridge 卡片点"重启" 按钮
4. 现场说: "我们这边后端有点抖, 稍等一下" (10-15s)

### C2. chrome MCP 不响应 (browser_navigate 卡)
**症状**: 调 `catfish_browser_navigate` 30s 没结果
**应急**:
1. 仪表盘 chrome 卡片点"开 Chrome" 重启 chrome 子进程
2. 不行: 直接 kill chrome (`pkill chrome`), 仪表盘 → 重新点"开 Chrome"
3. 现场说: "浏览器 session 重置一下"

### C3. skill render 报错 (docx/xlsx 生成失败)
**症状**: 调用 skill 后 chat 报 stack trace, 没文件出
**应急**:
1. **跳过这个 skill** — 切到下一个 demo 场景
2. **录屏 fallback** — 提前录的 sample-output.docx / .xlsx 直接演示
3. 5/13 前必须把 3 个 skill (weekly-report / leadership-briefing / project-approval) 各跑 5 遍 0 错

### C4. 上传文件 parse_file 超时
**症状**: 拖入大 PDF 后 30s+ 没 preview
**应急**:
1. **不要等** — 撤回 (chat 输入框附件 X 删掉) + 改用 catfish_skill 直接调 (绕过 LLM 看 preview)
2. 演示提前: "今天 demo 用提前预热过的小文件, 实际生产你们 IT 配置缓存" (推卸 + 真理)

---

## D · 主动闲聊不触发

**症状**: 9:30/14:00/17:30 该弹气泡没弹
**应急**:
1. 仪表盘 ProactiveCard "测一下 ▶" 手动触发 — 现场都用这个驱动
2. 别讲"它定时会弹" — 直接演示"鲶鱼想跟我聊一句", 推按钮立刻冒泡
3. 真要演定时: demo 前 5 分钟把系统时间改到 14:01 (临时 demo 机子), 触发再改回来

---

## E · 关键节点检查清单 (5/14 当天)

### 前 1 小时
- [ ] Companion 在 demo 机子重启 1 次, 看启动 ≤ 5s, 仪表盘所有卡片绿
- [ ] gateway / tool-bridge 健康 (`tail ~/Library/Logs/catfish/gateway.log` 看有没 ERROR)
- [ ] LLM 模型可达 (chat 发"hi"看 5s 内有响应)
- [ ] 桌宠 Cmd+Shift+P 显示 + Option+Shift+1/2/3/4 4 屏角试一遍
- [ ] 主动闲聊 ProactiveCard "测一下 ▶" 验证气泡冒
- [ ] 三个业务 skill 各跑一遍 (weekly-report / leadership-briefing / project-approval)
- [ ] 上传一份 1MB Excel + 一份 1MB PDF 验证 parse_file 顺
- [ ] 录屏备份 (5 场景全套 demo 视频, 万一现场全挂播这个)

### 前 5 分钟
- [ ] **关闭** 所有跟 demo 无关的 app (Slack / Mail / 微信 / Notion ...) 防通知干扰
- [ ] 通知中心**勿扰模式开** (避免别的通知弹出来 — 但 macOS 通知 fallback 测试除外)
- [ ] 屏幕分辨率切到 demo 投影仪推荐分辨率
- [ ] 演示账号登录确认 (员工身份 = 老李/小赵/...)
- [ ] 主动闲聊清今天 fired 记录 (devtools console: `localStorage.removeItem('catfish:proactive_last_fired')`), 这样 demo 时手动触发是新鲜的

### 现场救命三连
1. **Cmd+R** 刷新 webview (90% 卡死靠这个救)
2. **仪表盘 → 重启** (gateway / tool-bridge / chrome 任一)
3. **录屏 fallback** (任何路径都救不回来时)

---

## F · 现场用语模板

| 场景 | 不说 | 说 |
|---|---|---|
| 网络抖 | "网络挂了, 等一下" | "我换个路径再演示一次" |
| 工具 timeout | "tool-bridge 死了" | "后端要预热一下, 稍等" |
| skill 报错 | "render() 抛异常了" | "这个 skill 在迭代, 看下个场景" |
| LLM 慢 | "LLM 卡了" | "私有 122B 模型今天负载略高, 切到 flash 给你看响应速度" |
| 桌宠不显 | "桌宠 hide 了" | "今天演示先关桌宠保持页面整洁" |
| 真崩 | "Companion 闪退了我重启" | "稍等一下回来" (重启时打字盖过) |

---

## G · 真挂了的兜底剧本

如果 1 分钟内救不回来:
1. **不要继续演示**
2. 镇定切换: "今天我先把整体架构 + 安全设计跟你们走完, demo 部分留 5/15 派工程师上门联调"
3. 把 SECURITY-REVIEW + DATA-FLOW-DIAGRAM + 5 场景**录屏视频**给客户
4. 5/14 当晚复盘 + 5/16 二次 demo 邀请

**这种情况算丢分但不算输** — 央企看重过程严谨 + 文档齐, demo 失败不致命; 演示"假装成功"被识破才致命.
