# 鲶鱼 · 员工 5 分钟上手

> 你刚拿到 Catfish Companion.app, 看这页就够。如果哪一步卡了, 翻底部"出问题怎么办"。

## 1. 装 (1 分钟)

1. 双击 `Catfish-Companion-X.Y.Z.dmg`
2. 把鲶鱼图标拖进 Applications
3. Spotlight 搜 "Catfish" 启动 (第一次启动 macOS 会问"是否信任开发者", 选信任)

## 2. 第一次启动 (2 分钟)

启动后 Companion 主窗弹出来, 自动引导:

### 2.1 SSO 登录
- 点"用 [客户 SSO 名] 登录"
- 浏览器跳出来走客户 IdP 流程 (跟你登 [客户内部系统] 一样)
- 完成回 Companion, 看到右上角是你的名字 ✓

### 2.2 设置个人信息 (USER.md)
鲶鱼会问你几个问题, 比如:
- 你想我怎么叫你? (老李 / 张总 / 小赵 ...)
- 你的工作是? (区域市场总监 / 项目经理 / ...)
- 你说话有什么特点? (福建口音 / 喜欢直接 / 不喜欢长篇大论 ...)

这些信息会注入鲶鱼的"对话基因", 之后聊天就自然带你的味道。

随时改: 仪表盘 → 我的设置卡 → 编辑 USER.md

## 3. 4 个核心功能 (2 分钟)

### 3.1 chat (主用)
- 主窗左侧 "对话" tab
- 像 ChatGPT 一样打字 + Enter 发送
- 区别: 鲶鱼**记得你以前的对话** (新对话也能引用一周前的事)
- 拖入 PDF/Excel/Word/CSV/图片附件直接 chat

### 3.2 桌宠
- **Cmd+Shift+P** — 桌宠出现/消失
- 桌宠在屏幕角落待着, 鲶鱼有话直接顶上冒气泡
- 点桌宠 = 唤起主窗
- **Option+Shift+1/2/3/4** = 桌宠去 4 个屏角

### 3.3 全局召唤
- **Cmd+Shift+Space** — 任何 app 里都能召唤主窗 (不用切回 Companion)
- 想专心工作?  Cmd+Shift+F = 切"专注模式" (主窗变成伪 IDE 风格, 干扰最少)

### 3.4 主动闲聊
- 鲶鱼**自己会找你** — 9:30 / 14:00 / 17:30 各一次
- 桌宠头顶冒气泡, 主窗 chat 直接出现 assistant message
- 不想被打扰? 仪表盘 → 主动闲聊卡 → 关掉

## 4. 进阶 (会用上但不急)

### 4.1 Skills (鲶鱼能干什么活)
仪表盘 → Skills 卡看到所有装好的 skill, 比如:
- `weekly-report` — 帮你写周报 (说"帮我写本周周报" 就跑)
- `leadership-briefing` — 帮你做党委汇报材料
- `project-approval` — 帮你写立项申请
- `parse_file` — 上传文件自动解析

### 4.2 文件去哪了
- 你上传的: `~/.catfish/uploads/<时间戳>-<原文件名>`
- 鲶鱼生成的 (报告/Excel等): `~/.catfish/output/<日期>/<topic>/`
- 在 Finder 看: 仪表盘 → 文件卡 → "打开 catfish 输出目录"

### 4.3 历史会话
- 主窗左侧对话 tab → 历史 sessions 列表
- 全部存在 `~/.catfish/sessions/*.sqlite` 你电脑本地, 不上传

### 4.4 audit 看谁调用过什么
- 仪表盘 → audit 卡
- 想看更详细: `~/.catfish/gateway_audit.jsonl` 自己 tail 看

## 5. 出问题怎么办

| 现象 | 先试 | 还不行 |
|---|---|---|
| Companion 起不来 | 重启 mac | 删 `~/.companion-state/` 整个 + 重启 |
| chat 卡住不响应 | Cmd+R 刷新 webview (Cmd+Option+I 开 DevTools 后用) | 切其他 tab 再回来 |
| 桌宠不显 | Cmd+Shift+P 再按一次 | 仪表盘 → 桌宠卡 → "重置位置" |
| 桌宠不冒气泡 | 仪表盘 → ProactiveCard 点 "测一下 ▶" | 见底部"工程师救命" |
| 上传文件没反应 | 文件 < 50MB? 格式支持? (.pdf .xlsx .docx .csv .txt .md) | 看仪表盘 tool-bridge 卡是不是绿 |
| LLM 响应慢 / 报错 | chat 顶部模型下拉换 qwen-flash | 看仪表盘 gateway 卡有没有错 log |
| 主动闲聊不弹 | 仪表盘 → ProactiveCard "测一下 ▶" 验证 | localStorage 清"catfish:proactive_last_fired" 重启 |
| audit log 没东西 | 5 分钟内确认调用过工具? | 见底部"工程师救命" |

### 工程师救命

如果上面都不行, **不要**自己 hack:
1. 仪表盘 → 控制台 → "导出诊断" 按钮 (生成 zip 含日志/状态/版本)
2. 把 zip 发 [客户 IT helpdesk]
3. 鲶鱼平台团队 24h 内回复

## 6. 隐私 & 安全 (你应该知道)

- **你的 chat / 文件 / 记忆全在你电脑** — 公司 IT 能拿 audit metadata, 拿不到 prompt 内容
- **公司内网 LLM 才能看你 prompt** (默认 prod 配置), 不会发到外网 OpenAI / qwen 公网
- **不小心粘了密码进 chat** — 鲶鱼会自动识别 + 不入 audit log + 提醒你改用 keychain://xxx 引用
- 卸载: Companion 拖到废纸篓 + `rm -rf ~/.catfish` 你的数据全清

详细 → [安全 & 数据流向](./DATA-FLOW-DIAGRAM.md)

## 7. 快捷键速查

| 快捷键 | 干啥 |
|---|---|
| `Cmd+Shift+Space` | 召唤主窗 (任何 app 里) |
| `Cmd+Shift+P` | 桌宠显示 / 隐藏 |
| `Cmd+Shift+F` | 切专注模式 (主窗) |
| `Option+Shift+1/2/3/4` | 桌宠去 4 个屏角 |
| `Cmd+R` | 刷新主窗 webview |
| `Cmd+Option+I` | 开 DevTools (排查问题用) |
| `Enter` | chat 发送 |
| `Shift+Enter` | chat 内换行 |
| `Cmd+V` | 粘贴文件 / 截图 |
| `Cmd+,` | 开设置 |

## 8. 用一周后问自己

- 鲶鱼知道我叫什么 / 我做啥工作 — 应该 ✅
- 鲶鱼能引用我以前聊过的事 — 应该 ✅
- 鲶鱼会主动找我 (不只我问它才答) — 应该 ✅
- 上传文件不用担心泄密 — 应该 ✅

任何一个 ❌ → 翻 [demo 5 场景脚本](./MAY-DEMO-SCRIPT-2026-05-14.md) 看预期效果, 或问 IT。
