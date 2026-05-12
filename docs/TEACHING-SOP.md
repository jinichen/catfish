# Catfish 教学 SOP — 员工教鲶鱼一次, 凝固成 skill

> 写于 2026-05-12, 鸿波一天撞 12 次坑总结出来. 以后教任何 skill (打卡 / 工单 / 公文 / 等) 都按这套来, 不撞坑.
>
> 适用范围: BL-MM9-FREEZE-v2 教学→凝固→复用闭环 (`catfish_teach_start` / `catfish_teach_end` / `catfish_freeze_skill` / `catfish_run_skill`).

---

## 总览 — 5 步, 不跳

```
1) 准备     重启 Catfish Chrome (干净起点)
   ↓
2) 开场     一句话触发 catfish_teach_start
   ↓
3) 教学     逐步明确指令, 不留歧义, 禁止 LLM 自主探索
   ↓
4) 收尾     一句话触发 catfish_teach_end + catfish_freeze_skill
   ↓
5) 验证     新会话 + 干净 chrome + 一句话触发 catfish_run_skill
```

---

## 第 1 步: 准备 — chrome 干净起点

```bash
pkill -f "Chromium.*remote-debugging-port=9222"
# Companion 自动拉起新 chrome — 干净 cookie, 未登录, 空白 about:blank
```

**为啥**: chrome 是有状态的实例 (cookies / history / 打开页面). 教学时如果 chrome 在某个登录后页面, 凝固出来的 script.py 假设的初始状态就错了. 今天 13:38 那次复用撞 "已登录 cookie 让 goto 跳过 CAS → fill #name 撞 timeout" 就是这个坑.

**验证 chrome 真启动**: Companion 控制台看到"Catfish Chrome 运行中"标识, 或 `lsof -i :9222` 能看到进程.

---

## 第 2 步: 开场 — 一句话触发 teach_start

**句式** (任选一种):

> 我教你 `<X>`, 名字 `<skill-name>`, 描述 `<一句话简介>`

或:

> 记一下接下来怎么 `<X>`, 凝固成 `<skill-name>` skill

或:

> 一起做一遍 `<X>` 流程, 之后凝固成 `<skill-name>`

**LLM 应该**: 看到 "我教你 / 记一下 / 凝固成" 等触发词 → SOUL.md 教学边界铁律生效 → **第一动作调** `catfish_teach_start(name='<skill-name>', description='<...>')` → 回 "教学开始. 接下来你给指令我执行."

**LLM 没自动调 teach_start 怎么办**: 复制粘贴打断:

> 停. 先调 catfish_teach_start(name='<skill-name>', description='<...>'). 再开始.

调完后, **trace 文件 active 状态 = 1**, 后续每个业务工具 call 都被录.

---

## 第 3 步: 教学 — 逐步指令, 不留歧义

### 给指令的句式

每步明确给 selector + args, **不让 LLM 自己推**:

> 调 catfish_browser_goto(url="...")

> 调 catfish_browser_fill(selector="#name", text="chenhb")

> 调 catfish_browser_fill(selector="#pwd", **secret_ref**="keychain://eis_password") ← 密码必须 secret_ref

> 调 catfish_recognize_captcha(selector="#captchaImg", hint="alphanumeric_4")

> 调 catfish_browser_fill(selector="#captcha", text="<上一步识别结果>")

> 调 catfish_browser_click(selector="div.button-login")

### 禁忌 (今天撞的坑)

❌ LLM 自主探索 — "我先 snapshot 看看页面长啥样"  
❌ LLM 自己推 selector — "我用 `input[placeholder*='用户ID']` 试试" (今天撞过, 凝固进 skill 跑不通)  
❌ LLM 自己加调试 step — "我截个图确认一下" (会进凝固)

**看到 LLM 自主行为, 立刻打断**:

> 停. 只调我让你调的 tool. 我没说调 snapshot, 不要调.

### 密码必须 secret_ref

教学时填密码框, **永远**用:

```
catfish_browser_fill(selector="#pwd", secret_ref="keychain://<key-name>")
```

**不要**:

```
catfish_browser_fill(selector="#pwd", text="<明文密码>")  ❌
```

freeze 引擎检测到明文密码 (`#pwd` selector + 8-32 字符 + 含数字字母) 会**拒绝凝固**. 必须先把密码塞进 macOS Keychain:

```bash
security add-generic-password -a chenhb -s eis_password -w '<密码>'
# -s eis_password 对应 secret_ref="keychain://eis_password"
```

---

## 第 4 步: 收尾 — teach_end + freeze

**句式**:

> 教完了, 调 catfish_teach_end. 然后 catfish_freeze_skill(name='<skill-name>', namespace='department', description='<...>', overwrite=true)

**LLM 两 tool call**:

1. `catfish_teach_end()` → 返 `{ok, step_count, archive_path, ...}` → **检查 step_count 跟你教的步数对得上**, 不对说明教学有漏录, 重新教.
2. `catfish_freeze_skill(...)` → 拿 last_completed session → 生成 `script.py` + `SKILL.md` → 落 `catfish/skills/<namespace>/<name>/` → 自动 spawn `install_to_hermes.sh` 同步到 `~/.hermes/skills/productivity/catfish-<name>/`

**返回看几个关键字段**:
- `ok: true`
- `trace_steps_used`: 应等于你教的 step_count
- `params`: 自动推断的参数 (`username` / `password_ref` / `max_captcha_retry`), 跟你期望对得上不对得上
- `install.returncode: 0`

---

## 第 5 步: 验证 — 新会话 + 干净 chrome 测复用

```bash
# 再重启 chrome (确保从未登录态 / 默认状态测复用)
pkill -f "Chromium.*remote-debugging-port=9222"
```

**关掉教学会话**, 开**新空会话** (会话也有状态, 教学会话里 LLM 看过 trace 流程会被诱导手工复演). 一句话触发:

> `<员工自然语言>` (e.g. "上 EIS 看待办" / "上班打卡" / "查今天工单")

LLM 应该**直接**调 `catfish_run_skill(skill_path='department/<name>', params={...})` → 凝固版 script.py 跑 → 看耗时.

**< 30 秒秒过** = 闭环成功. 验证 BL-MM9 卖点.

### 失败时 — 不要降级手工

LLM 按 SOUL.md "skill 失败不降级手工" 铁律, 应该报告员工 + 问 1/2/3:

1. **再试一次** (chrome warm 完, 网络抖动恢复)
2. **重教这个 skill** (业务流程变了 / DOM 改版)
3. **手工接管** (你 explicit 说 "手工" LLM 才能调 catfish_browser_*)

**绝不**选 "3 手工"如果你想验证 skill — 那只让你停留在"手工跑通"假象, skill 没真证明. 选 "1 再试" 或 "2 重教".

如果再试还失败 — 让 LLM 把 skill 返回的**完整 error 字段** (不是它翻译的简短描述) 贴给开发者. 真 bug 才真修.

---

## 例子 1: eis-checkin (上班打卡) — 完整教学剧本

**目标**: 凝固一个 skill, 员工说"上班打卡" → 鲶鱼自动登 EIS → 点上班打卡按钮 → 报告"已打卡 HH:MM".

### 准备

```bash
# Keychain 已有 eis_password (从 eis-login 教学时设的)
security find-generic-password -a chenhb -s eis_password  # 应该输出 password 元信息

# 重启 chrome
pkill -f "Chromium.*remote-debugging-port=9222"
sleep 3
```

### 教学剧本 (按顺序逐句发给 Companion)

**句 1 (开场)**:
> 我教你 EIS 上班打卡, 名字 eis-checkin, 描述 "登录 EIS 后点上班打卡按钮"

LLM 调 `catfish_teach_start(name='eis-checkin', description='登录 EIS 后点上班打卡按钮')` → 回 "教学开始".

**句 2 (导航到 EIS)**:
> 调 catfish_browser_goto(url="http://eis.ffcs.cn", wait_until="load")

**句 3 (填用户名)**:
> 调 catfish_browser_fill(selector="#name", text="chenhb")

**句 4 (填密码)**:
> 调 catfish_browser_fill(selector="#pwd", secret_ref="keychain://eis_password")

**句 5 (识别验证码)**:
> 调 catfish_recognize_captcha(selector="#captchaImg", hint="alphanumeric_4")

**句 6 (填验证码)**:
> 调 catfish_browser_fill(selector="#captcha", text="<上一步识别结果>")

LLM 把验证码识别 result 的 `text` 字段填进去. freeze 引擎识别到 "这是 captcha 数据流" 自动改成 `text=captcha_text` 变量.

**句 7 (点登录)**:
> 调 catfish_browser_click(selector="div.button-login")

LLM 应该报"已跳转到 dashboard". 此时 chrome 在 `http://eis.ffcs.cn/login?code=...`.

**句 8 (找上班打卡按钮)**:
> 调 catfish_browser_find_by_text(text="上班打卡", role="button")

LLM 返回找到的 selector, 例如 `top_recommendation.selector = "div.checkin-btn-morning"` (实际看你 EIS dashboard 的 DOM).

**句 9 (点上班打卡)**:
> 调 catfish_browser_click(selector="<上一步 top_recommendation.selector>")

页面应该弹"打卡成功"提示, 或者按钮文字变成"已打卡 HH:MM".

**句 10 (截图确认)**:
> 调 catfish_browser_screenshot(compress="auto", full_page=false)

看截图 — 如果按钮显示"已打卡 08:20"或类似 → 教学成功.

**句 11 (收尾)**:
> 教完了, 调 catfish_teach_end. 然后 catfish_freeze_skill(name='eis-checkin', namespace='department', description='登录 EIS 后点上班打卡按钮, 返回打卡时间')

LLM 调 teach_end → 报 step_count=10 (句 2-11 实际录的). 然后 freeze_skill → 落地. ⚠ 注意 step 10 screenshot 是为"确认成功"录的, 凝固时会被标 "skip (LLM-only)" — 不进 script.py. 实际 script.py 只 8 步可执行.

### 验证

```bash
pkill -f "Chromium.*remote-debugging-port=9222"
sleep 3
```

新会话, 一句话:

> 上班打卡

LLM 应该:
1. 看到 "上班打卡" → 匹配 skill_catalog 里 `department/eis-checkin`
2. 调 `catfish_run_skill(skill_path='department/eis-checkin', params={'username': 'chenhb'})`
3. script.py 跑 8 步 (登录 7 步 + 点打卡 1 步)
4. 返 `{ok: true, captcha_attempts: 1, duration_ms: ~5000}`
5. LLM 跟你说: "✅ 已打卡上班 (用时 5 秒, 1 次验证码识别)"

---

## 例子 2: eis-checkout (下班打卡) — 跟上班几乎一样, 改 1 行

跟 `eis-checkin` 完全相同流程, **只改 1 步**:

句 8 改成:
> 调 catfish_browser_find_by_text(text="下班打卡", role="button")

其它都一样. 凝固后 LLM 看到 "下班打卡" / "下班" / "今天下班了" 等关键词触发.

⚠ 注意: EIS 下班打卡按钮通常上班打卡之后才激活 (你截图里"下班打卡 下班" 是激活状态). 教学时确保已经打过上班卡, 否则按钮可能 disabled, click 失败.

---

## 常见坑对照表 (今天血泪)

| 撞的坑 | 对应漏了 SOP 哪步 | 怎么避 |
|---|---|---|
| LLM 没调 teach_start, 7 步白教 → teach_end 报 "无 active session" | 第 2 步开场 | 用 "我教你 / 记一下 / 凝固成" 等触发词, 看不到 LLM 调 teach_start 立刻打断重来 |
| trace 混入 4 次重复 goto + 错 selector, 凝固出垃圾 skill | 第 3 步纪律 | LLM 自主探索立刻打断, 只调你 explicit 让它调的 |
| 凝固出 script.py "goto 失败" 报错 | 工具层 bug (已修 schema 兼容) | 确认 `grep "def _call" .../script.py` 看到 `retries=2, retry_delay=2.5` |
| 复用第一次 cold start `goto 失败` | 第 1 步 + 第 5 步准备 | 跑 skill 之前重启 chrome, 不带历史状态 |
| 复用时 chrome 已登录, fill #name 撞 timeout | 第 5 步准备 (没重启 chrome) | 复用验证前**也**必须 pkill -f Chromium |
| LLM 在 skill 失败时自己手工调 catfish_browser_* | SOUL.md 失败铁律 | 加 SOUL.md "skill 失败必须报告员工" 铁律 (今天加了) |
| 教学会话里教完接着测复用, LLM 复演手工流程 | 第 5 步隔离 | 复用必须**新会话** + 干净 chrome |
| 多次教学没 rotate trace, freeze 时混入老步骤 | trace_recorder v2 修了 | start_session 自动 rotate 残留 active.jsonl |
| 密码用 text= 明文填 | 第 3 步纪律 | 永远用 secret_ref="keychain://..." |

---

## 真要彻底不撞坑 — Companion UI 加教学模式 (BL-MM10 backlog)

SOP 是"员工记规范 + LLM 守纪律"两层软兜底. 真彻底是物理 UI 边界:

- Companion 顶部加 **"▶️ 开始教学"按钮** → 弹框填 name + description → 点确认 → 自动 catfish_teach_start, 顶部状态条变成 "🎓 教学中: <name> (录了 N 步)"
- 教学期间, 每个 tool call **弹小确认**: "☑️ 加入 skill | ☒ 跳过(LLM-only) | ✏️ 改 args 再录"
- 教完点 **"⏹ 结束 + 凝固"按钮** → 自动 teach_end + freeze, 弹框确认 name + description
- 凝固完跳出 "✅ 凝固 6/7 步成功, hermes 端 catfish-<name>, 测一下复用?" 按钮

UI 物理边界 = 员工不需要记 5 步 SOP, LLM 不需要靠 SOUL.md 软纪律自觉. 边界就在那里.

落地周期: 2-3 天 (Tauri 改 + tool-bridge 加 hook + SOUL.md 改纪律配合).

---

## 附: 教学 SOP 摘要 — 贴墙版

```
1) pkill -f "Chromium.*9222"               # 重启 chrome
2) "我教你 X, 名字 Y, 描述 Z"               # 触发 teach_start
3) 逐步明确指令, 密码用 secret_ref          # 教学
4) "教完了, teach_end + freeze, ..."        # 收尾
5) pkill chrome + 新会话 + 一句话触发        # 验证

失败选 1 再试 / 2 重教. **永不**选 3 手工接管 (那是假成功).
```

---

更新历史:

- **2026-05-12 v1**: 鸿波一天撞 12 次坑总结. 含 eis-checkin / eis-checkout 完整例子. (chenhongbo@ffcs.cn)
