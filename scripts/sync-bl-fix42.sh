#!/usr/bin/env bash
# BL-FIX42 — 历史截图折叠 (5/11 鸿波 "一截图就卡住").
#
# 真根因诊断 (gateway log 实测):
#   tool_with_image_marker=4 → 重组 4 条 tool 含图 → user multipart
#   latency_ms=108725 status=ok ttft_ms=28374
#   Companion fetch idle 60-120s 默认 timeout abort
#
# 多张 base64 截图累积 → prompt 5-10MB → 上游 122b 推理 60-108s → 客户端断了.
# 跟 L7 plan-only retry 互补 (一个修 LLM 半路停, 一个修 LLM 跑太慢).

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续)"

git add central/llm-gateway/src/catfish_gateway/tool_archive/image_folder.py
git add central/llm-gateway/src/catfish_gateway/app.py
git add scripts/sync-bl-fix42.sh

git commit -m "BL-FIX42 (5/11): 历史截图折叠 — 修 '一截图就卡住' (LLM 跑太慢, 客户端 timeout abort)

鸿波 5/11 实测: 第 2 次截图之后 chat 一直卡, '无法连接 gateway: Fetch is aborted'.

gateway log 真根因:
  tool_with_image_marker=4 → 重组 4 条 → user multipart
  latency_ms=108725 status=ok ttft_ms=28374
  prompt_tokens=69234 (token 估算不准, 实际请求体 5-10MB)
  ↓
  Companion macOS / Tauri 底层 fetch idle 60-120s 默认 timeout 自动 abort
  gateway 仍在算 → 完成后 status=ok 但客户端早断了

修法 (新模块 tool_archive/image_folder.py):
  multimodal_tool_unwrap 之后, 检测 user multipart 含图 message:
  - 保留最近 1 张图 (LLM 当前轮必须看)
  - 老图的 image_url part 替成文本占位 '[历史截图已折叠]'
  - text part 保留, LLM 仍知道这步发生过什么

不存图: 历史截图意义不大 (页面已变), LLM 要重看就调 catfish_browser_screenshot
再截. 比 base64 archive 简单.

灰度 env:
  CATFISH_HISTORY_IMAGE_FOLDING_ENABLED  默认 true
  CATFISH_HISTORY_IMAGE_FOLDING_KEEP     保留最近几张, 默认 1

接入点:
  app.py chat_completions, 在 unwrap_tool_images 之后 / prepare_tool_messages 之前.
  跟 BL-Q3-ARCHIVE 配套 (一个砍 tool 文本一个砍 user 图片).

实测 (sandbox):
  4 张图 → 1 张, 节省 prompt ~75%
  边界 case 全过: 无图 / 单图 / 多图

跟之前 fix 关系:
  - L7 修 LLM 半路 stop (mid-task plan-only)
  - FIX42 修 LLM 跑太慢 timeout
  - 两者并行触发, 这次实测同时撞了
"

if [ -n "$(git status --porcelain)" ]; then
    echo "⚠ 还有 untracked / unstaged 改动."
fi

echo
git log origin/main..HEAD --oneline
echo
read -p "确认 push? [y/N] " yn
case $yn in
    [Yy]*) git push origin main && echo "✅ done — 重启 gateway 即生效, 不需要 alembic" ;;
    *) echo "❎ 取消." ;;
esac
