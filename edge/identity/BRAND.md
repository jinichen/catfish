# 鲶鱼品牌速查 (Brand Quick Reference)

> 这是小鲶视觉身份的"使用说明书". 改 logo / 配色 / 字体 / 用法之前先读这一页.
> 配套源文件: `catfish/branding/*.svg` (logo / 吉祥物 / 头像 / app icon master).
> Companion 应用内 token: `edge/companion-app/src/styles/tokens.css` (改这一处, 改全应用).

---

## 1. 角色定位 (一句话)

**小鲶 (Little Catfish)** —— 一个胖胖的、温暖的、长着两根敏感触角的鲶鱼形象, 隐喻"贴近员工, 探听真实需求, 安静地在水底为你做事". 不是冷冰冰的工具, 是有性格的同事.

适用场景: SOE/央企/银行内部 AI 助理. 既要有温度 (拒绝传统 IT 工具的冰冷), 又要有专业感 (能登 PPT 封面 / 公文角戳).

---

## 2. Logo 体系 (4 件套)

| 文件 | 用途 | 关键特征 |
|------|------|---------|
| `logo-mascot.svg` | Onboarding 引导 / 启动闪屏 / 对外 banner / 名片 | 完整身体 + 双须 + 大眼 + 腮红, 480x480 (横游姿态) |
| `pet-mascot.svg` | **桌宠** (BL-E27, 5/5 ship) — 屏幕角落浮宠 | **站立** + **持鱼竿** + 钩上挂迷你小鱼 (员工赋权 + 自我反讽 meta), 100x140 竖直比 |
| `logo-mark.svg` | favicon / PPT 角标 / 文档抬头 | 圆形 + 双须 S 曲线 + 鱼眼锚点, 256x256 |
| `logo-mark-mono.svg` | 单色印刷 / 反白 / 邮件签名 | currentColor 单色, 缩放无失真 |
| `avatar-circle.svg` | Companion 聊天头像 / 通知图标 | 头部特写, 28x28 ~ 256x256 都清晰 |
| `app-icon-master.svg` | macOS/iOS/Windows/Android app icon | macOS squircle 圆角方形 + 极简 mark |

**三轨制 (5/5 加桌宠):**
- **吉祥物 mascot/avatar** 走亲和路线, 给员工 Onboarding / Dashboard 头像 (圆胖萌)
- **桌宠 pet-mascot** 走克制路线, 服务屏幕角落小尺寸 (站立 + 持竿, 跟主 mascot 区分语境)
- **mark/app-icon** 走专业路线, 给 IT / PPT / 正式场合 (单色简洁)

**为啥桌宠独立**: 桌宠 120x120 px 区, 横游姿态塞方框两侧浪费. 站立姿态自然填满 + 屏幕小尺寸下识别强 + 隐喻"员工驾驭 AI". 跟 Onboarding 圆胖萌的语境也区分清楚.

---

## 3. 配色 (深青 + 暖橙)

### 主色 (深青系, 占 70%)

| 名称 | HEX | RGB | CSS 变量 | 用途 |
|------|-----|-----|---------|------|
| 墨青 (主) | `#0E5F66` | 14, 95, 102 | `--catfish-cyan` | 主按钮 / 链接 / icon 底色 |
| 深青 (暗) | `#0A464C` | 10, 70, 76 | `--catfish-cyan-dim` | hover / 边框 / 文字强调 |
| 亮青 (高光) | `#1A8A95` | 26, 138, 149 | `--catfish-cyan-bright` | 渐变高光 / 信息态 |

### 辅色 (暖橙系, 占 ≤10%)

| 名称 | HEX | RGB | CSS 变量 | 用途 |
|------|-----|-----|---------|------|
| 暖橙 | `#F47B3D` | 244, 123, 61 | `--catfish-orange` | CTA 按钮 / 在线指示点 / 须尖锚点 |
| 浅橙 | `#F89866` | 248, 152, 102 | `--catfish-orange-soft` | hover / 浅徽章 / 腮红 |

### 中性 (占 20%)

| 名称 | HEX | CSS 变量 | 用途 |
|------|-----|---------|------|
| 暖米底 | `#FAF7F2` | `--catfish-bg` | 应用背景 (比纯白柔和) |
| 暖米卡 | `#FAF1E4` | `--catfish-bg-cream` | 聊天气泡 / 浅卡片 |
| 暖灰边框 | `#E7E1D6` | `--catfish-border` | 卡片边框 / 分隔线 |
| 主文字 | `#1A2E33` | `--catfish-text` | 正文 (深青墨, 不用纯黑) |
| 弱文字 | `#6B7775` | `--catfish-text-muted` | 次要文字 / 占位符 |

### 状态色 (跟 brand 协调)

`ok=#1A8A95` (复用亮青, 一致感) / `warn=#E8A33D` (暖琥珀) / `err=#D9534F` (暖红, 不刺眼) / `idle=#A8A29E`.

---

## 4. 字体

- **中文**: 苹方 (PingFang SC) / 微软雅黑 / 系统默认 — 不引入 webfont, 首屏快 + 离线可用
- **西文**: -apple-system / BlinkMacSystemFont / Helvetica Neue
- **等宽**: SF Mono / Menlo / Consolas

字号阶 (Companion 应用): 12 (注释/标签) / 14 (正文) / 16 (强调) / 20 (标题) / 28 (大标题). PPT 用 24 (正文) / 36 (副标题) / 60 (主标题).

字重: **400 常规 + 500 中等** 两档, 不用 700/900 (太重不符温暖感).

---

## 5. 净空区 + 最小尺寸

- **logo-mark 净空**: 四周留出 ≥ logo 高度 1/4 的空白, 不要紧贴文字/边框
- **logo-mascot 净空**: 四周留出 ≥ 须尖到身体距离的空白
- **最小尺寸**: mark 不小于 24px (favicon 极限), mascot 不小于 80px (再小须看不清)
- **app icon**: macOS 1024x1024 master, 自动渲染下到 16/32/64/128/256/512

---

## 6. 用法红线 (× 错误用法)

- × 不要拉伸/挤压 logo (永远等比例缩放)
- × 不要改 logo 颜色 (单色场景用 `logo-mark-mono.svg` 的 currentColor)
- × 不要给 logo 加投影/渐变/外发光等装饰效果 (本身已有渐变)
- × 不要在彩色背景上放彩色 logo (用 mono 反白版)
- × 不要把吉祥物用于"严肃公文" (用 mark; 吉祥物只用于面向员工的友好场景)
- × 不要旋转 logo (鱼眼朝向有方向感, 旋转后看着头朝下)
- × 不要在 logo 旁加 "powered by xxx" / 多 logo 拼贴

---

## 7. 文案语调 (跟 SOUL.md 一致)

UI / Email / 公告文案统一遵循 SOUL.md 的"鲶鱼语调":

- 用"你"不用"您" (距离感 < 尊敬感)
- 直接, 不堆套话, 不加敬语
- 错误状态写人话, 不写堆栈/errno:
  - × `ECONNREFUSED 127.0.0.1:8999`
  - ✓ `Gateway 没起来 — 点这里启动`
- **绝对不能出现** `hermes` / `~/.hermes/` / "未初始化"等内部品牌泄漏 (见 SOUL.md § 品牌铁律)

---

## 8. 应用清单 (5/14 demo 用)

- [x] macOS app icon (Dock 图标)
- [x] favicon (浏览器 tab)
- [x] Companion 聊天头像
- [x] Onboarding 引导 logo
- [x] Demo PPT 封面
- [ ] 桌面壁纸 (P1, demo 后)
- [ ] 名片 / 邮件签名模板 (P1, demo 后)
- [ ] H5 介绍页 (P2)

---

## 9. 改这些时怎么办

| 想改什么 | 改哪里 | 谁批 |
|---------|--------|------|
| 主色 / 辅色 | `tokens.css` + 重渲染 `branding/*.svg` | 鸿波 |
| Logo 形状 | 改 `branding/*.svg` 后跑 `python3 branding/render_icons.py` | 鸿波 |
| 字体 | `tokens.css` `--font-sans` | 设计 + IT |
| 用法红线 | 本文件 § 6 | 鸿波 |

---

最后更新: 2026-05-03 (五一 sprint 5/3)
