# 品牌设计 token

> 改这些值就改全应用的视觉。所有定义在 `src/styles/tokens.css`，
> 任何组件不准写死颜色 / 间距 / 字号。

## 配色

| 用途 | 变量 | 浅色值 | 深色值 |
|------|------|--------|--------|
| 主色（鲶鱼青） | `--catfish-cyan` | `#06b6d4` | 同 |
| 主色暗 | `--catfish-cyan-dim` | `#0891b2` | 同 |
| 背景 | `--catfish-bg` | `#fafaf9` | `#1c1917` |
| 卡片背景 | `--catfish-bg-elevated` | `#ffffff` | `#292524` |
| 边框 | `--catfish-border` | `#e7e5e4` | `#44403c` |
| 正文 | `--catfish-text` | `#1c1917` | `#fafaf9` |
| 弱化文字 | `--catfish-text-muted` | `#78716c` | `#a8a29e` |

## 状态色

`✓ 绿 / ⚠ 黄 / ✗ 红 / ○ 灰` —— 用 `<StatusDot status="ok" />` 渲染：

| 状态 | 变量 | 值 |
|------|------|---|
| ok | `--status-ok` | `#10b981` |
| warn | `--status-warn` | `#f59e0b` |
| err | `--status-err` | `#ef4444` |
| idle | `--status-idle` | `#a8a29e` |

## 间距阶梯

`--space-1` 到 `--space-8`，对应 `4 / 8 / 12 / 16 / 24 / 32 px`。
**不要写死像素值**，永远用 token。

## 字体

- Sans：`-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif`
- Mono：`"SF Mono", Menlo, Consolas, "Source Code Pro", monospace`

中文走苹方 / 微软雅黑系统字体，避免引入 webfont（首屏快+离线可用）。

## 圆角 + 阴影

- `--radius-sm` 4px / `--radius-md` 8px / `--radius-lg` 12px
- `--shadow-sm` 极轻投影 / `--shadow-md` 卡片悬浮

## 暗色模式

通过 `prefers-color-scheme: dark` 自动切换。
不提供手动开关（暂时） —— 跟随系统是大多数员工的预期行为。

## Logo

`public/catfish-logo.svg` 是占位 logo（青色圆 + 🐟 emoji）。
真实版本由设计稿替换；尺寸 64×64，单色，可缩放。

## 鲶鱼语调（文案）

UI 文案遵循鲶鱼平台的统一语调（详见 `catfish/edge/identity/SOUL.md`）：

- 用"你"不用"您"
- 直接、不堆套话、不加敬语
- 错误状态写人话，不写堆栈或 errno
  - ✗ 不要："ECONNREFUSED 127.0.0.1:8999"
  - ✓ 要："Gateway 没起来 —— 点这里启动"
