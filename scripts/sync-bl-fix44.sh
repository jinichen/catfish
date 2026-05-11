#!/usr/bin/env bash
# BL-FIX44 — 浏览器自动化彻底修 (5/11 鸿波 '不是够不够的问题, 是要彻底解决问题').
#
# 之前 find_by_text 抓到密码框 placeholder 含'登录'翻车. 真根因: 工具链断点 —
# screenshot 给视觉但没 selector, click 要 selector 但不给视觉, LLM 在中间硬桥
# (find_by_text 反推 selector) 桥不稳就翻.
#
# 两步组合彻底修:
#   1. catfish_browser_click 加 coordinates — LLM 截图看到位置直点, 完全绕开 selector
#   2. catfish_browser_find_by_text 返排序候选 + role/match_type/clickable 元数据,
#      LLM 看 metadata 自己判断挑哪个, 不再依赖工具猜对
#   3. SOUL.md 浏览器自动化纪律段, 教 LLM 何时走 coordinates 何时走 selector

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续)"

git add edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py
git add edge/identity/SOUL.md
git add scripts/sync-bl-fix44.sh

git commit -m "BL-FIX44 (5/11): 浏览器自动化彻底修 — 坐标 click + find_by_text 返候选

鸿波 5/11 实测 EIS 登录场景 catfish_browser_find_by_text 抓到密码框
placeholder 含'登录'翻车. 鸿波拍板: '不是够不够的问题, 是要彻底解决问题'.

真根因 (不是工具一个 bug):

  LLM 看到截图视觉上知道按钮在哪, 但工具链强迫它必须用 selector 才能 click.
  → find_by_text 反推 selector → 撞歧义就翻车

  工具链断点:
    screenshot 给视觉但没 selector
    click 要 selector 但不给视觉
    LLM 在中间硬桥 — 桥不稳就翻

两步组合彻底修:

## 改 1: catfish_browser_click 加 coordinates 参数

新接口:
  catfish_browser_click(coordinates=[450, 380])  // 视觉直点, 无歧义
  catfish_browser_click(selector='role=button[name=\"登录\"]')  // 老路径

实现: page.mouse.click(x, y) — Playwright 原生.
selector / coordinates 二选一, 都没传报错.
selector 传了优先 (老兼容), 不传走 coordinates.
返 mode='selector' 或 'coordinates' 让 LLM 知道走了哪条.

LLM 拿截图看到按钮位置直接坐标点, 完全绕开 selector 歧义这条祸根.

## 改 2: catfish_browser_find_by_text 重写返候选 + 元数据

老版返单个 element, 撞 placeholder 就错 (鸿波 EIS 场景).
新版返**排序候选列表**, 每个含:

  selector       Playwright 最稳 selector (优先 #id, [name], role=button[name=...])
                 不再用 'text=' (避免歧义)
  tag            HTML tag
  role           ARIA role (显式 attr 或隐式 from tag/input.type)
  text           实际匹配到的文字
  match_type     innerText / value / aria-label / placeholder / title / alt
  is_clickable   有原生 interactive 行为 (button/a/input) 或 onclick handler 或 cursor:pointer
  bounds         {x, y, w, h}
  center         {x, y} — 供 coordinates click 直点
  in_viewport    是否在可视区
  score          综合排序权重 (透明可解释)

排序权重 (JS 内, 透明):
  +50  role 匹配 (用户显式传 role 时)
  -20  role 不匹配 (但仍返, 不丢)
  +30  is_clickable
  +20  match_type=innerText (最强信号)
  +15  match_type=value
  +12  match_type=aria-label
  +3   match_type=placeholder (容易误匹配, 低分)
  +10  exact match
  +0~15 元素 size (log scale, 登录按钮通常 ≥100×40)
  +5   in_viewport
  -5   matched text > 20 字 (placeholder 长描述 vs 按钮短文字)
  -10  matched text > 50 字

加 role 参数 (可选): 'button' / 'link' / 'textbox' / 等. 过滤 + 大加分.
LLM 找登录按钮一定传 role='button' 避开 placeholder.

返结构加 top_recommendation 字段 (score 最高且 is_clickable 的), summary 解释
top 是怎么挑出来的, 撞 placeholder 时加 ⚠ 提示让 LLM 重传 role 重找.

## 改 3: SOUL.md 浏览器自动化纪律段

加 '浏览器自动化纪律 (BL-FIX44)' 段:

  - goto → screenshot 看页面 → 找按钮 → fill → click
  - 找按钮两条路径:
    A. 视觉直点 (推荐, 不歧义): screenshot 看清位置 → click(coordinates=[x,y])
    B. 文字找 selector: find_by_text(text='登录', role='button') → click top.selector
  - 铁律:
    ❌ 不要 selector='text=登录' (歧义)
    ✅ 能 coordinates 就 coordinates
    ✅ find_by_text 传 role='button' 找登录
    ✅ 看 match_type, placeholder 撞要重传 role
    ✅ 找不到 → screenshot + coordinates

  附真实 EIS 登录 [good]/[bad] 对比代码段.

## 排序矩阵验证 (Python 重现 JS 逻辑)

EIS 真实候选: 密码框(placeholder 含登录) / 登录按钮 / 忘记密码链接 / header

  不传 role: 登录按钮(67) > 忘记密码(65) > header(37) > 密码框(20)
  传 role=button: 登录按钮(117) >> 忘记密码(45) > header(17) > 密码框(0)

完全消除歧义, 登录按钮稳定 top.

## 工程量

  - tool-bridge catfish_tools.py: ~250 行改动 (JS 重写 + Python wrapper + 2 schema)
  - SOUL.md: ~50 行新增段
  - 总 ~300 行

## 跟之前 fix 关系

  - L7 修 LLM 半路 stop
  - FIX42 修历史截图累积 timeout
  - **FIX44 修 LLM 看视觉但工具桥不稳的祸根**
  - 三个独立 fix 解决三个独立 bug, 配合用户体验从'各种卡'到'一气呵成'

# 部署

  仅改 tool-bridge + SOUL.md, **不需要 alembic**.
  重启 tool-bridge daemon (Companion 重启会带起来), 重启 gateway (重读 SOUL).
"

if [ -n "$(git status --porcelain)" ]; then
    echo "⚠ 还有 untracked / unstaged 改动."
fi

echo
git log origin/main..HEAD --oneline
echo
read -p "确认 push? [y/N] " yn
case $yn in
    [Yy]*)
        git push origin main && {
            echo "✅ done."
            echo
            echo "mac 接下来:"
            echo "  重启 Companion (会自动重启 tool-bridge daemon)"
            echo "  重启 gateway"
            echo
            echo "然后 chat: '登录 http://eis.ffcs.cn, 查看待办'"
            echo "  期望 LLM 行为:"
            echo "  1. browser_goto → screenshot 看页面"
            echo "  2. screenshot(selector='#captchaImg') 识验证码"
            echo "  3. fill 用户名+密码+验证码"
            echo "  4. click(coordinates=[x,y]) 直点登录按钮 (视觉位置)"
        }
        ;;
    *) echo "❎ 取消." ;;
esac
