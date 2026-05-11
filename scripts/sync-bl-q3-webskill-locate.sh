#!/usr/bin/env bash
# BL-Q3-WEBSKILL-locate (5/11) — catfish_browser_locate 视觉定位工具.
#
# 跟 catfish_recognize_captcha 形成视觉双子: captcha = OCR 字符, locate = 找元素位置.
# 配合 BL-FIX44 的 catfish_browser_click(coordinates=[x,y]), 形成完整的视觉驱动
# 浏览器自动化链路. 解决 find_by_text 文字歧义 / 无文字元素 (X 按钮 / 图标) /
# placeholder 撞文字 的死结.

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续)"

git add edge/tool-bridge/src/catfish_tool_bridge/browser_locate.py
git add edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py
git add edge/identity/SOUL.md
git add scripts/sync-bl-q3-webskill-locate.sh

git commit -m "BL-Q3-WEBSKILL-locate (5/11): catfish_browser_locate 视觉定位工具

跟 catfish_recognize_captcha 同模板, 解决 LLM '看到登录按钮但 selector
找不准' 的死结. 视觉双子完整了:

  catfish_recognize_captcha — OCR 字符 (验证码识别)
  catfish_browser_locate    — 找元素位置 (返坐标)
  catfish_browser_click(coordinates) — 直接喂坐标点击 (BL-FIX44 已 ship)

# 工具实现 (~330 行)

edge/tool-bridge/src/catfish_tool_bridge/browser_locate.py:
  - 接 query 自然语言 + 截图 (selector / full_page / image_b64 三选一)
  - 内部 PNG header 解 (无 PIL 依赖) 拿图片宽高
  - 调 gateway loopback POST /v1/chat/completions → catfish-private-vision
  - strict system prompt 要求返**只 JSON** (不 markdown 包裹)
  - _parse_locate_json 容忍 markdown / 前缀文字噪音
  - _validate_and_normalize 校验:
    - 坐标在图片范围内 (out-of-range → found=false)
    - 尺寸合理 (w/h ≥ 5px)
    - clamp width/height 防溢出图边
    - 算 center = (x+w/2, y+h/2) 给 click 直接用
  - max_retry (默认 1, 最大 3) 模型偶发返 garbage 时重试

# 工具 schema (catfish_tools.py)

加 catfish_browser_locate 到 CATFISH_NATIVE_TOOLS:
  - 详细 description 教 LLM 何时用 (文字找不到时 / 无文字元素 / placeholder 撞文字)
  - 跟 catfish_browser_find_by_text 优先级关系: 有文字优先 find_by_text, 没/歧义走 locate
  - 跟 catfish_recognize_captcha 区别: OCR vs 空间定位
  - input_schema: query 必填, selector / image_b64 / full_page / max_retry 可选
  - 返结构示例 + confidence 阈值建议

# SOUL.md 浏览器自动化纪律段升级

三条路径优先级:
  A. find_by_text(role='button') — DOM 精确, 优先
  B. browser_locate(query='...') — vision 找位置, 文字歧义时用
  C. LLM 自估坐标 — 兜底, 122b 估坐标常偏 50-100 像素

# 视觉链路完整框架

| 类型              | 已 ship | 新加 |
|-------------------|--------|------|
| 视觉 (vision LLM) | recognize_captcha | **locate** |
| 结构 (DOM 直查)   | snapshot, find_by_text, screenshot | (extract_table 待加) |
| 动作 (Playwright) | goto, click(coordinates), fill | (wait_for_url 待加) |

# 10 个单测全过 (helper 级)

  - markdown 包裹 JSON 解析
  - 前缀噪音 JSON 解析
  - garbage 返 None
  - validate 正常 case + center 计算
  - clamp 防溢出 (700+200 在 800 宽图 → clamp 100)
  - 坐标超图片 → found=False (拒幻觉)
  - 尺寸 <5px → 拒
  - found=False 透传
  - PNG header 尺寸解析 (无 PIL 依赖)

# 部署

新加 1 文件 + 改 catfish_tools.py schema/dispatch + SOUL.md.
不动 DB / alembic. 重启 Companion (tool-bridge 自动重载) + gateway.

# 明天 EIS 测路径

第 2 步 (LLM agent 跑 EIS 登录), 期望 LLM 行为:
  1. screenshot
  2. find_by_text(text='登录', role='button') — 先试 DOM
     - 如果撞 placeholder 密码框 (5/11 现象) → 走 locate
  3. browser_locate(query='页面下方蓝色登录按钮') → 拿 center
  4. browser_click(coordinates=[center.x, center.y]) → 点
  5. 成了就跳转, 失败再 screenshot 看错况
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
            echo "  重启 Companion (tool-bridge 自动重载, 新工具 catfish_browser_locate 加载)"
            echo "  重启 gateway (重读 SOUL.md)"
        }
        ;;
    *) echo "❎ 取消." ;;
esac
