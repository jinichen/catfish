---
name: eis-login
version: "0.1.0-frozen"
deprecated: false
kind: procedural
triggers:
  - 登录 EIS
  - 登录eis
  - 进 EIS
  - 打开 EIS
  - 开 EIS
  - EIS 登录
description: |-
  ⭐ 登录 EIS 一站式信息门户（更新 URL 为 http://eis.ffcs.cn，支持验证码自动识别）

  ⚙️ 由 catfish_freeze_skill 自动凝固 (2026-05-12 13:26:39, BL-MM9-FREEZE).
  源 trace: /Users/chenhongbo/.catfish/traces/session_eis-login_20260512_131432_c7a2a9.jsonl (steps 1-7, ok=7/7).
  **不要手改 script.py** — 业务流程变了走"再教一次"路径让管道重新凝固.

  调用入口: `render_eis_login(username, password_ref, max_captcha_retry)` (script.py).
---

# eis-login — 登录 EIS 一站式信息门户（更新 URL 为 http://eis.ffcs.cn，支持验证码自动识别）

> 凝固于 2026-05-12 13:26:39. 教学→凝固→复用闭环 (BL-MM9-FREEZE).

## 怎么调

```
catfish_run_skill(
  skill_path="department/eis-login",
  params={"username": 'chenhb', "password_ref": 'keychain://eis_password', "max_captcha_retry": 3}
)
```

## 凝固时的 8 步教学 trace

  1. `catfish_browser_goto(http://eis.ffcs.cn)`
  2. `catfish_browser_snapshot(...)`
  3. `catfish_browser_fill(#name)`
  4. `catfish_browser_fill(#pwd)`
  5. `catfish_recognize_captcha(#captchaImg)`
  6. `catfish_browser_fill(#captcha)`
  7. `catfish_browser_click(div.button-login)`

## 参数

| 参数 | 类型 | 默认 |
|---|---|---|
| `username` | str | 'chenhb' |
| `password_ref` | str | 'keychain://eis_password' |
| `max_captcha_retry` | int | 3 |

## 修改方式

**不要直接改 script.py**. 业务流程变化 (页面改版 / 加新步骤) → 走"再教一次":

  1. 员工: "再教一次 eis-login, 改的地方是 ..."
  2. LLM agent 跑新流程, trace_recorder 自动记
  3. 员工: "凝固成 v2"
  4. `catfish_freeze_skill(name="eis-login", overwrite=true)`

凝固管道会重新生成 script.py + SKILL.md, 覆盖本份.

## 已知限制 (5/12 MVP)

- 凝固只看顺序, 不解 trace 里的"如果 X 则 Y"分支. 业务有分支 → 拆成多个 skill.
- 等待逻辑: 每个 click / goto 后默认 networkidle wait, 5s 超时.
- captcha retry 是自动插入 (识别 confidence < 0.6 时刷图重识), 不靠 trace.
- secret_ref 必须在教学时就用 keychain://, 不接受明文密码凝固.
