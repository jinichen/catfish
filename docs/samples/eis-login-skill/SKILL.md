---
name: eis-login
namespace: department
version: 0.1.0-draft
author: chenhongbo@ffcs.cn
description: 登录 EIS (http://eis.ffcs.cn) 内网系统并检查待办. 含动态验证码识别.
inputs:
  - name: username
    type: string
    required: true
    description: 工号或邮箱
  - name: password_ref
    type: string
    required: true
    description: 密码引用, 格式 'keychain://<key>' 或 'env://<VAR>'. 不接受明文.
  - name: max_captcha_retry
    type: integer
    default: 3
    description: 验证码错误时最多重试几次
outputs:
  - login_ok: bool
  - todos: array
  - error: string | null
tags:
  - browser-automation
  - login
  - eis
  - internal-system
estimated_runtime_seconds: 12
risk_level: low
required_tools:
  - catfish_browser_goto
  - catfish_browser_fill
  - catfish_browser_click
  - catfish_browser_find_by_text
  - catfish_browser_screenshot
  - catfish_recognize_captcha
  - catfish_browser_snapshot
---

# eis-login — EIS 系统登录 + 检查待办

## 用途

每次员工想"上 EIS 看看今天有没有待办"时, 跑这个 skill. 不再让 LLM 一步步推理点页面 (慢, 偶发翻车). skill 把固定流程凝固成确定脚本, 验证码这一步用 `catfish_recognize_captcha` 走 vision 模型 OCR.

**鸿波 5/11 拍板路线**: 通过 LLM agent 教学一次 (跑通了就保存), 后续走这个 skill 秒开. 这是 catfish "把工作流变成可执行资产"卖点的真实落地.

## 跟纯 LLM agent 的差别

| 维度        | LLM agent              | 这个 skill                       |
|------------|-----------------------|---------------------------------|
| 单次耗时   | 30-120s (含 retry / 卡顿) | ~10s (确定 + vision OCR)        |
| 稳定性     | 偶发卡 (stop/timeout)  | 高 (确定流程 + 验证码 retry)     |
| 算力消耗   | 多轮 122b ReAct        | 1 次 vision OCR                  |
| 出错诊断   | LLM log + 截图        | 明确 step 失败原因 + 自动 retry  |

## 调用方式

```
catfish_run_skill(
  name="department/eis-login",
  args={
    "username": "chenhb",
    "password_ref": "keychain://eis_password"
  }
)
```

返:

```json
{
  "ok": true,
  "login_ok": true,
  "todos": [
    {"title": "审批: 出差申请 #1234", "deadline": "2026-05-13"},
    {"title": "评估: Q2 KPI 自评",   "deadline": "2026-05-15"}
  ],
  "error": null,
  "trace": {
    "captcha_attempts": 1,
    "total_time_ms": 9842
  }
}
```

## 流程 (8 步)

### 1. 打开登录页

```
catfish_browser_goto(url="http://eis.ffcs.cn")
```

**等待**: 页面 load 完 + `input[name="username"]` 出现 (最多 10s).

**失败处理**: URL 不可达 → 返 `{ok: false, error: "EIS 不可达, 检查内网"}`.

### 2. 截验证码图

```
catfish_browser_screenshot(selector="#captchaImg", compress="png")
```

只截验证码区域, 不截整页 — base64 小, 喂 vision 快.

**失败处理**: `#captchaImg` 找不到 → fallback `img[class*=captcha]` / `img[id*=captcha]`. 都找不到 → 可能 EIS 改版, 返 error 让员工手填.

### 3. 识别验证码 (vision OCR)

```
catfish_recognize_captcha(
  selector="#captchaImg",
  hint="alphanumeric_4"  # EIS 验证码固定 4 位字母数字
)
```

返 `{ok, text, confidence, ...}`. confidence ≥ 0.6 才用.

**失败处理**:
- confidence < 0.6 → 刷新验证码 (`page.click("#captchaImg")` 通常触发新图) → 重试 (最多 max_captcha_retry 次)
- 全失败 → 返 error, skill 退出

### 4. 填用户名

```
catfish_browser_fill(
  selector='input[name="username"]',
  text=username
)
```

### 5. 填密码 (走 secret_ref, 不进 LLM 上下文)

```
catfish_browser_fill(
  selector='input[name="password"]',
  secret_ref=password_ref
)
```

**严禁**: 把 password_ref 解析后的明文传 LLM. 必须走 tool-bridge secret_resolver 直接喂 Playwright.

### 6. 填验证码

```
catfish_browser_fill(
  selector='input[name="captcha"]',  # 或 'input[name="vcode"]', 看 EIS DOM
  text=captcha_result.text
)
```

### 7. 点登录

优先 `catfish_browser_find_by_text(text="登录", role="button")` 看 top_recommendation → 取 selector click. 找不到 (页面非标准) 走 fallback:

```
# fallback 1: 截图 + 视觉坐标
shot = catfish_browser_screenshot(full_page=false)
# LLM 看截图找位置 → catfish_browser_click(coordinates=[x, y])
# 这一步**让 LLM 做** (skill 不内嵌 LLM 调用, 但流程文档要写清楚)

# fallback 2: 已知 selector
catfish_browser_click(selector='button.login-submit')
```

### 8. 验证登录结果 + 拉待办

等跳转 / 检查页面状态:

```
# 等 url 跳到 dashboard, 最多 10s
catfish_browser_wait_for_url(pattern="**/dashboard*")
```

如果还在登录页 → 验证码 / 密码错. 看页面错误提示 → 决定重试还是失败.

成功跳转后, 抓待办列表:

```
catfish_browser_snapshot(
  max_elements=100,
  selector=".todo-list .todo-item"  # 看 EIS 真实 DOM
)
```

解析 snapshot 拿待办文字 + deadline (如果有).

返:

```json
{
  "ok": true,
  "login_ok": true,
  "todos": [...]
}
```

## 失败处理矩阵

| 错误                       | skill 响应                     |
|---------------------------|-------------------------------|
| URL 不可达                | 立即返 `{ok: false, error: "EIS 内网不可达"}` |
| 验证码识不出 (3 次)        | 返 `{ok: false, error: "验证码识别失败, 请员工手填"}` |
| 用户名 / 密码错            | 返 `{ok: false, login_ok: false, error: "登录被拒, 检查凭据"}` |
| 跳转超时 (10s 没跳)        | 截图 + 返 `{ok: false, error: "登录后未跳转, 可能页面变更", screenshot_path: "..."}` |
| 待办区找不到 (EIS 改版)    | login_ok=true 但 todos=[], `warning: "待办区 DOM 变更, 需要更新 skill"` |
| **EIS session 中途失效**  | **自动跑 session-renewal 子流程** (见下节, BL-FIX45 C 5/11) |

## Session-renewal (BL-FIX45 C, 5/11)

**场景**: skill 第一次成功登录后, 进入 dashboard 抓待办, 但 EIS 可能因为 server-side
session timeout / 多端登录踢人 / cookie 过期, 中途跳回登录页. 跑后续步骤会撞错.

**检测**:

```
每个步骤执行前 (从 step 7 开始) 调:
  state = catfish_browser_get_url()
  if state.url contains '/login' or '/auth' or page.title contains '登录':
    → session_expired = True
    → 跳 session-renewal 流程
```

更轻量版本: 步骤 8 (拉待办) 失败时, 截图 + `catfish_browser_locate(query='登录按钮')`
找到登录按钮 → 说明跳回登录页了 → 触发 renewal.

**Renewal 子流程** (复用 step 2-7 子集):

```
SESSION-RENEWAL:
  1. screenshot 看清当前页面 (确认是登录页)
  2. recognize_captcha (新验证码图)
  3. fill 用户名 + 密码(secret_ref) + 验证码
  4. click 登录
  5. 等跳转 dashboard
  6. 回到原本失败的那一步重试 (e.g. 抓待办)

renewal 失败 (重试 1 次还失败):
  → 返 {ok: false, error: "session 续期失败, 可能 EIS 限制并发登录"}
```

**Renewal 计数器**: skill 单次执行最多 renewal 2 次, 防死循环. 第 3 次直接报错.

**为啥不在 step 1 就预防**: session 失效是偶发, 大多数 skill 跑不会撞. 检测 +
按需 renewal 比每次主动 ping login state 高效.

**SOUL.md 同步纪律**: LLM agent 跑 skill 时如果发现页面回到登录页, 直接走 renewal
不要去问员工 "我看到登录页了, 要重登吗?" — 这就是 skill 的存在意义.

## 实施状态 (5/11 草版)

- [x] 流程 spec 写完
- [x] catfish_recognize_captcha 工具 ship (BL-Q3-WEBSKILL 5/11)
- [ ] 实际 EIS 调试 (5/12 鸿波内网测)
- [ ] EIS 实际 DOM 抓取调研 (login button class? captcha input name? todo list selector?)
- [ ] 跨 skill 安全审查 (password_ref 真不进 LLM)
- [ ] 写到 `~/.hermes/skills/department/eis-login/SKILL.md` + 发布 hub

## 教学路径 (5/12 实操)

1. 鸿波内网开机 → 测 EIS 登录链路 (LLM agent 跑一次, 我们看 selector 真实长啥样)
2. LLM 跑完 (用上前面 6 个 fix 包括 L8 + recognize_captcha), 拿到 EIS DOM 真实结构
3. 把真实 selector 填进这个 SKILL.md
4. `catfish_skill_publish` 发到 hub, 别的员工直接用
5. demo 时演示: "员工说 '上 EIS 看待办', 鲶鱼直接调 skill, 10 秒搞定"

## 跟 BL-MM9 (skill learning) 一脉相承

BL-MM9 的设计哲学是: 员工教鲶鱼一次, 鲶鱼凝固成 skill. 这个 EIS 登录就是该哲学的标杆 demo case. 不再让 LLM 每次重新推理, 改一次教学多次执行.

后续可以加 `eis-list-projects` / `eis-submit-leave` / 其它 EIS 操作 skill, 慢慢盖完整套.

## 安全 / 合规 nota

- **password_ref 必走 keychain / env, 不接受明文** — code level 强制校验
- **每次跑 skill 留 audit trail**: skill name, args (脱敏), 起止时间, 成功/失败原因 → quota_events / skill_audit 表
- **验证码识别 vision 调用**记一次 internal LLM event (不计员工 quota, 但 audit 看得到)
- skill 失败默认 retry 一次, 别死循环

## 已知坑

1. **EIS 改版**: 任何 selector / DOM 变化 → skill 报"DOM 变更", 走 LLM agent 重新教学 → 更新 skill
2. **验证码图加载慢**: 截图前可能图还在转 → `page.wait_for_load_state("networkidle")` 兜底
3. **多次同 IP 登录被锁**: skill 触发频率太高可能撞 EIS 安全策略 → 加 rate limit (建议 < 1 次/分钟)
4. **跨员工登录**: skill 不能用 A 员工 credential 登 B 员工 EIS account → skill_audit 强制校验 username 跟当前登录鲶鱼 user 是否匹配
