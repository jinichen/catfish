#!/usr/bin/env bash
# BL-Q3-WEBSKILL (5/11) — catfish_recognize_captcha + eis-login 骨架.
#
# 鸿波 5/11 反思: '一直打补丁治症状不治病'. 真路线: LLM agent 教学一次 → 凝固
# 成 skill, 后续走确定脚本. EIS 这种重复任务不该让 LLM 每次重新推理.
#
# 但 EIS 有动态验证码, 必须每次重识别. 所以加一个 catfish_recognize_captcha
# 子 LLM 工具 (走 vision 模型 OCR), skill 把这步当原子能力用.

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续)"

git add edge/tool-bridge/src/catfish_tool_bridge/recognize_captcha.py
git add edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py
git add central/llm-gateway/src/catfish_gateway/internal_models.py
git add docs/samples/eis-login-skill/SKILL.md
git add scripts/sync-bl-q3-webskill.sh

git commit -m "BL-Q3-WEBSKILL (5/11): catfish_recognize_captcha + eis-login skill 骨架

鸿波 5/11 战略反思:
  '一直打补丁是治症状不治病. 真路线: LLM agent 教学一次 → 凝固成 skill,
   后续走确定脚本.' EIS 登录这种日常重复任务不该每次让 LLM 重新推理.

但动态验证码这步无法做成纯确定脚本 — 每次图都不一样, 必须 OCR.
所以加 catfish_recognize_captcha 走 vision 子 LLM, skill 把它当原子能力用.

# 1. catfish_recognize_captcha 子 LLM 工具

实现: edge/tool-bridge/recognize_captcha.py (~250 行)
  - 接收 selector / image_b64 (二选一)
  - Playwright locator.screenshot 截图 → base64 data URL
  - 调 gateway loopback /v1/chat/completions, 走 catfish-private-vision
  - prompt 强约束: '只返字符, 不要解释 / 引号 / 前缀'
  - 解析返回 + confidence 估算 (长度对得上 hint = 高, 含解释词 = 低)
  - max_retry 偶发失败重试 (默认 1, 最大 3)

LLM tool 注册: CATFISH_NATIVE_TOOLS 加 schema + 分发 (catfish_tools.py).
LLM 可直接调; 也可 Python skill import 直接用 (跨 LLM agent / deterministic 两路径).

internal_models KNOWN_USE_CASES 加 'captcha_ocr' (后续 catalog 给 vision 模型
打 tag 让 pick_internal_model 自动选).

# 2. eis-login skill 骨架 (SKILL.md spec)

位置: docs/samples/eis-login-skill/SKILL.md
内容:
  - frontmatter (inputs/outputs/required_tools/risk_level)
  - 8 步流程: goto → screenshot captcha → recognize → fill 3 字段 → click 登录 → 等跳转 → 抓待办
  - 失败处理矩阵 (5 种错况 + 响应)
  - 跟 LLM agent 对比: ~10s 确定 vs 30-120s 不稳
  - 教学路径 (5/12 鸿波内网测 → 拿真实 selector → 填进 skill → publish hub)
  - 安全 nota (password_ref 不进 LLM, audit, rate limit, 跨员工拒绝)

这是 spec, 真 execute 文件待 5/12 拿到 EIS 真实 DOM 后填.

# 跟之前 fix 关系

L5-L8 / FIX42 / FIX44 改善 LLM agent 路径 — 但 LLM agent 不该是用户日常路径,
它是'教鲶鱼新流程'的一次性工具. 鸿波 5/11 反思后, 真正卖点是 skill 凝固
('一次到无数次'), LLM agent 是入口不是终点.

# 不破老 case

  - 不改任何 fix 文件 (L5-L8 / FIX42 / FIX44 都不动)
  - 新加 1 文件 + 改 2 现有文件 schema + dispatch
  - 没 alembic, 没新 DB schema

# 部署

  推 commit, 重启 Companion (tool-bridge 自动重载) + gateway (重读 SOUL +
  internal_models). 不动 PG.

# 测试路径 (5/12 鸿波内网)

  1. 单测 catfish_recognize_captcha: 找一张已知验证码图 → 工具调用 →
     验证 text + confidence
  2. 整链路: LLM agent 跑一次 EIS 登录, 用上 recognize_captcha (替代之前
     LLM 自己 OCR)
  3. 跑通后 → 把真实 selector 填进 docs/samples/eis-login-skill/SKILL.md
  4. skill_publish 到 hub
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
            echo "  重启 Companion (tool-bridge 自动重载新工具)"
            echo "  重启 gateway (catalog + internal_models 重读)"
            echo
            echo "5/12 内网测路径:"
            echo "  1. 拿一张验证码图测 catfish_recognize_captcha"
            echo "  2. LLM agent 跑 EIS 登录走通"
            echo "  3. 真实 selector 填回 docs/samples/eis-login-skill/SKILL.md"
            echo "  4. publish 到 hub"
        }
        ;;
    *) echo "❎ 取消." ;;
esac
