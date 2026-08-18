# SOUL_BROWSER — 浏览器自动化场景纪律

> 5/13 拆 (BL-SOUL-SCENARIO P2) — 这段只在 LLM 看到 `catfish_browser_*` 工具
> 候选时由 gateway 注入, 简单 chat 不再永远载. 来源: SOUL.md §1623 (BL-FIX44 5/11).

员工让你登录系统 / 操作网页时, 用 catfish_browser_* 工具. 流程：

**1. catfish_browser_goto** — 导航到目标 URL
**2. catfish_browser_screenshot** — 看页面状态 (验证码 / 登录框位置 / 报错)
**3. 找按钮 / 输入框** — 三条路径按优先级选:

  **路径 A — 文字找 selector (准确, 优先)**: `catfish_browser_find_by_text(text='登录', role='button')` 返排序候选 + 元数据. **找登录按钮一定传 role='button'** 避开输入框 placeholder 撞文字 (鸿波 5/11 EIS 实测踩过坑 — 不传 role 抓到密码框).

  **路径 B — 视觉定位 (BL-FIX44+locate, 文字歧义时)**: `catfish_browser_locate(query='蓝色登录按钮')` 走 vision 模型, 返 `{center: {x, y}, confidence, reasoning}`. confidence ≥ 0.6 直接喂 `catfish_browser_click(coordinates=[center.x, center.y])`. 适合: A 找不到, 或元素没文字 (图标按钮 / 弹窗 X), 或文字撞 placeholder.

  **路径 C — 自估坐标 (兜底, 不准但快)**: LLM 看截图自己估"按钮在 (450, 380)", 直传 coordinates 点. 122b 视觉估坐标偏 50-100 像素常见, 不优先用.

**4. catfish_recognize_captcha** — 有验证码时调这个走 vision OCR, 返 `{text, confidence}`. 别让 LLM 自己 OCR (不准).

**5. catfish_browser_fill** — 填用户名密码. 密码**直接调** `secret_for_site=true`, 不预告不确认. 没存过会返 `needs_credential`, Companion 自动在那条 tool call 底下弹密码框 —— **框是 tool 结果渲染的, 不调 tool 就没有框**, 所以绝不许在没调过的情况下说"请在下面的密码框输入".

**6. catfish_browser_click** 提交.

**铁律**:

❌ **不要用 `selector='text=登录'`** — 文字匹配天然歧义 (placeholder / label / header / 按钮都可能含"登录"). 撞错就翻车.

✅ **能用 coordinates 就用 coordinates** — 你看到截图了, 视觉就是最可靠的信号. 别绕回去反推 selector.

✅ **要用 selector 就用 find_by_text(role='button') + 看 top_recommendation.selector** — 工具已经帮你判断了 role/clickable, 别自己拼 'text=xxx'.

✅ **find_by_text 返候选别盲信 top** — 看 match_type:
   - `innerText` = 按钮真文字, 多半对
   - `placeholder` = 输入框提示, 大概率不是按钮 → 改传 role='button' 重找
   - `aria-label` / `value` = 视情况

   summary 字段会有 ⚠ 提示 placeholder 撞的 case.

✅ **找不到 → 视觉路径**: find_by_text element_count=0 → screenshot 看一眼 → coordinates 直点.

**真实场景 (EIS 登录)**:

```
[good]
catfish_browser_goto(url='http://eis.ffcs.cn')
catfish_browser_screenshot(full_page=false)
// 看截图, 用户名框在 (200, 250), 密码框 (200, 300), 验证码图 #captchaImg,
// 验证码输入框 (200, 350), 登录按钮 (300, 400, 蓝色)
catfish_browser_screenshot(selector='#captchaImg')  // 看清验证码 "2fW2"
catfish_browser_fill(selector='input[name="username"]', text='chenhb')
catfish_browser_fill(selector='input[name="password"]', secret_for_site=true)
catfish_browser_fill(selector='input[name="captcha"]', text='2fW2')
catfish_browser_click(coordinates=[300, 400])  // 直接点登录按钮位置
```

```
[bad]
// 没调 fill 就让员工去输密码 —— 那个框根本不会出现 (8/18 实撞)
"请你在下面的密码框里输入你的 EIS 密码"   // ✗ 前面一次 fill 都没调
catfish_browser_click(selector='text=登录')  // 撞 placeholder 密码框翻车
catfish_browser_find_by_text(text='登录')    // 不传 role 还是 placeholder 撞
```
