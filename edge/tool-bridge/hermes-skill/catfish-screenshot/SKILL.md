---
name: catfish-screenshot
description: 给视觉模型看员工屏幕用的截图工具. 当员工说「这个报错是什么意思」「我屏幕上 X 是什么」「截屏看下」「你看一下我这边」「这个按钮怎么用」之类涉及 GUI 的问题时, 调 catfish_screenshot 工具. 默认 mode=active_window (osascript 拿前台窗口 ID + screencapture -l 自动拍, 零打扰, 隐私友好). **浏览器场景永远用 browser_vision 不要绕到 catfish_screenshot**. 不要主动截图. 不要 fullscreen 默认调用 (会把员工聊天/银行/密码管理器一起拍到). 当前支持 macOS 全功能 + Windows 退到 fullscreen. 配合 Qwen3-VL / Qwen-Flash 多模态 / Gemini vision 看图.
version: 0.1.0
author: 鲶鱼 Catfish Platform Team
license: MIT
metadata:
  hermes:
    tags: [screenshot, vision, gui, multimodal, catfish]
    related_skills: [catfish-browser-task, catfish-browser-compliance]
---

# catfish-screenshot

让小鲶替员工 **看屏幕** — 拍一张图喂给视觉模型 (Qwen3-VL / Gemini / Qwen-Flash 多模态).

工具底层是 macOS `screencapture` / Windows `PIL.ImageGrab`. 截图存 `/tmp/catfish-shot-<unix>.png`, 同时返回 base64 给模型直接看.

---

## 何时调用

### 典型触发 (员工原话)

- "这个报错是什么意思" (员工屏幕上有报错对话框)
- "我屏幕上显示什么" / "你看一下我这边"
- "截屏看下" / "看一眼我这"
- "这个按钮在哪" / "GUI 怎么操作"
- "Excel 里这个数是怎么来的" (员工不想复制)
- "这个图怎么改" (设计 / 配色问题)

### 不该调用的场景

- **想看网页** → 用 `catfish-browser-task` 直接和 Chrome 交互, 不要绕去截图. 截图丢失了 DOM 信息, 模型只能瞎猜按钮位置.
- **想看本地文件** → 用 `read_file` 直接读, 别截图.
- **员工没主动让你看** → 你"想看一下"不算理由. 截图涉及隐私, 必须员工触发.
- **看视频 / 动态内容** → 单帧截图意义不大. 让员工自己描述.

---

## 调用方式

### 入参

```json
{
  "mode": "interactive",
  "reason": "员工反馈 Foxmail 启动报错, 看下报错框具体内容"
}
```

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `mode` | 否 | `interactive` | `interactive` 员工框选 / `fullscreen` 全屏 / `window` 选窗口 |
| `reason` | **是** | — | 一句话说明为啥要截图, 员工会看到, 也写日志 |

### 输出

成功:
```json
{
  "type": "image",
  "format": "png",
  "encoding": "base64",
  "data": "<base64 string>",
  "data_uri": "data:image/png;base64,...",
  "path": "/tmp/catfish-shot-1714200000.png",
  "size_bytes": 524288,
  "mode": "interactive",
  "reason": "员工反馈 Foxmail 启动报错...",
  "summary": "截图完成 (interactive, 512 KB), 路径: /tmp/..."
}
```

失败 / 员工取消:
```json
{
  "type": "error",
  "error": "员工取消了截图(或选区为空)"
}
```

---

## 三种 mode 怎么选

### `fullscreen` (默认, 推荐)

直接拍整个主屏 — `screencapture -x`, **0 权限 0 打扰**.

员工说"看下我 Foxmail 报错" / "截屏看下" / "看屏幕上 X" → 默认走这个. 大多数时候员工正在看的窗口就是主屏前台内容, 拍下来直接给视觉模型看就够.

**为啥默认 fullscreen 而不是 active_window**: 历史踩坑 (2026-04-27) — `active_window` 模式走 `osascript "tell application System Events"`, macOS 反复弹 Automation 权限对话框, 而 hermes venv 的 `python3.11` 没 Apple 代码签名, 系统不持久化授权, 每次截屏都问员工. 删了, fullscreen 已够用.

**风险**: 拍主屏会包含背景里的窗口 (微信 / 钉钉 / 1Password 等). 但这是员工主动触发的, 而且只发给视觉模型看一次. catfish-policy 不再 deny 这个 mode (R8 已删).

### `interactive`

员工自己框一个区域. 当员工**只想给你看一小块** (比如桌面上某个角落的通知 / 屏幕上一个错误对话框) 时用.

只在员工**主动说**"我框给你看" / "让我选一块" / "我自己截" 时用. 默认别走这个 — 打扰员工.

### `fullscreen`

直接拍整个屏幕. **风险**: 员工背景里的微信 / 钉钉私聊 / 银行 app / 1Password 一起被拍下来, 跟着 base64 上传到 LLM.

→ 只在员工**明确说**"全屏"/"整个屏幕"/"截全屏"时用, 而且 reason 里要写明白 ("员工要求 fullscreen 截屏看 X").

→ catfish-policy R8 会命中并提醒员工.

### `window`

让员工点选一个窗口, 只拍那个窗口. 比 fullscreen 安全, 比 interactive 省事 (不用框).

→ 当员工说"看一下我 VS Code 窗口" / "拍下我那个浏览器窗口"时用.

---

## 隐私红线

1. **默认 interactive**, 不要主动改 fullscreen
2. **截图前要在 reason 写明白用途** — 这句会出现在员工的对话里, 他能拒绝
3. **结果里的 base64 / 路径不要写进 memory 或 skill 文件** — screenshot 是一次性资料, 不持久化
4. **不要重复拍** — 一次对话里员工框过一次后, 后面继续问就用之前的结果. 别每次问就重新截一遍.
5. **看到敏感内容立即提醒员工** — 截图里如果偶然出现密码 / 银行账号 / 私聊, 你应该说 "我看到这张图里包含 X, 你确定要继续吗" 而不是默默处理

---

## 跟视觉模型配合

### 当前支持的模型 (在 catfish-gateway models.yaml)

| 模型 | tier | 视觉能力 | 推荐场景 |
|---|---|---|---|
| `catfish-private-vision` (Qwen3-VL 30B) | 内网免费 | OCR / 图分析 / GUI 识别 | 默认首选, 内网, 不付费 |
| `catfish-public-qwen-flash` (Qwen3.6-Flash) | 公共付费 | 多模态 + tool use | Qwen3-VL 不可达时备份 |
| `catfish-public-gemini-pro` | 公共付费 | 视觉 + 长上下文 | 复杂 GUI 推理 + 长上下文 |
| `catfish-public-gemini-flash` | 公共付费 | 视觉 (快) | 简单看图, 快速响应 |

### 截图后用哪个模型

- **Qwen3-VL** 是默认: 如果 catfish-gateway 主力 = qwen-vl, 截图直接喂就行
- 如果当前对话用的是非视觉模型 (主力 deepseek-flash 不带视觉), 截图前**主动建议**员工切到视觉模型: "为了看图我切到 catfish-private-vision 行吗?"

---

## 已知限制

1. **Windows interactive 不支持** — 当前会自动退到 fullscreen 并在结果里 `platform_note` 字段说明. 员工要先关敏感窗口.
2. **截图最大 12MB raw** (base64 后约 16MB) — 5K 全屏 PNG 偶尔会超, 这时 fallback 到 interactive 让员工框小一点.
3. **Linux 没做** — 后续按需求加 (`gnome-screenshot -i` / `scrot -s`).
4. **非英语 GUI OCR** — 中文 GUI 识别取决于视觉模型, Qwen3-VL 中文 OCR 不错, Gemini 也 OK.

---

## 跟其他工具的关系

- **不要替代 `catfish-browser-task`** 看网页内容. 浏览器有结构化 DOM, 拍图反而更糟.
- **不要替代 `read_file`** 看本地文件. 文件直接读, 别绕弯.
- **跟 `catfish-email` 互补**: 员工说"这封邮件附件里那张图什么内容" → 拍 Foxmail 窗口截图 (因为附件预览在 Foxmail GUI 里, 不在邮件 SQLite).
- **跟 `catfish-roleplay` 互补**: 演练面试 / 演讲时, 员工想"看下我现在 PPT 长啥样" → 截图.
