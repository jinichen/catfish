---
name: eis-checkout
version: "0.1.0-frozen"
deprecated: false
description: |-
  ⭐ 登录 EIS 后点下班按钮

  ⚙️ 由 catfish_freeze_skill 自动凝固 (2026-05-13 10:48:18, BL-MM9-FREEZE).
  源 trace: /Users/chenhongbo/.catfish/traces/session_eis-checkout_20260513_104752_81760c.jsonl (steps 1-3, ok=3/3).
  **不要手改 script.py** — 业务流程变了走"再教一次"路径让管道重新凝固.

  调用入口: `render_eis_checkout()` (script.py).
---

# eis-checkout — 登录 EIS 后点下班按钮

> 凝固于 2026-05-13 10:48:18. 教学→凝固→复用闭环 (BL-MM9-FREEZE).

## 怎么调

```
catfish_run_skill(
  skill_path="department/eis-checkout",
  params={}
)
```

## 凝固时的 8 步教学 trace

  1. `catfish_run_skill(...)`
  2. `catfish_browser_find_by_text(...)`
  3. `catfish_browser_click(div.able)`

## 参数

_(无参数)_

## 修改方式

**不要直接改 script.py**. 业务流程变化 (页面改版 / 加新步骤) → 走"再教一次":

  1. 员工: "再教一次 eis-checkout, 改的地方是 ..."
  2. LLM agent 跑新流程, trace_recorder 自动记
  3. 员工: "凝固成 v2"
  4. `catfish_freeze_skill(name="eis-checkout", overwrite=true)`

凝固管道会重新生成 script.py + SKILL.md, 覆盖本份.

## 已知限制 (5/12 MVP)

- 凝固只看顺序, 不解 trace 里的"如果 X 则 Y"分支. 业务有分支 → 拆成多个 skill.
- 等待逻辑: 每个 click / goto 后默认 networkidle wait, 5s 超时.
- captcha retry 是自动插入 (识别 confidence < 0.6 时刷图重识), 不靠 trace.
- secret_ref 必须在教学时就用 keychain://, 不接受明文密码凝固.
