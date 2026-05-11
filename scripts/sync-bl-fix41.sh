#!/usr/bin/env bash
# BL-FIX41 — tool 消息内容硬截断 (5/11 鸿波 demo 前夜).
#
# 修 BL-FIX23 L6 + BL-FIX40 上线之后仍然 context overflow 117-125% 的真根因:
# tool_msgs=63 累积 100-200KB 把 128K context 烧光.

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续 — 离线 commit)"

git add central/llm-gateway/src/catfish_gateway/tool_msg_truncator.py
git add central/llm-gateway/src/catfish_gateway/app.py
git add central/llm-gateway/tests/test_tool_msg_truncator.py
git add scripts/sync-bl-fix41.sh

git commit -m "BL-FIX41 tool 消息内容硬截断 (5/11): 修 context overflow 真根因

鸿波 5/11 实测: 上 BL-FIX23 L6 + BL-FIX40 之后, gateway log 仍然:

  BL-FIX2 pre-unwrap: total=199, tool_msgs=63, tool_with_image_marker=0
  context overflow: prompt_tokens=149623 (117%)

journal 已经从 50KB 砍到 6KB (FIX40 生效), 但 tool_msgs=63 累积 100-200KB
是真根因 — 一条 execute_code pytest stdout 5-20KB, browser_snapshot 10KB+,
ReAct 链跑十几轮就把 128K context 全占了.

修法 (新模块 tool_msg_truncator.py):

  每条 role=tool message content > 2KB → 截前 1KB + 后 1KB + 中间替成
  '...[已截断 N 字节 (BL-FIX41 tool content cap)]...'

  - 消息数量保持不变 — Hermes ReAct / OpenAI tool-calling 依赖
    assistant ↔ tool 一对一配对, 删 tool message 会切断 chain.
  - 保前 + 保后 — 前 1KB 通常是 status / 关键路径, 后 1KB 是 final
    result / exit code, 中间循环输出 / 重复日志最该砍.
  - 2KB 阈值 — 50 条 × 2KB = 100KB, 留 28KB 给 system + journal +
    user messages, 128K context 下安全.
  - 字节级截断 + errors='ignore' decode 兜底 — 中文一字符 3 字节,
    切半时不崩.
  - 幂等 — MIN_TRIGGER = CAP + 200 给标记留 slack, 跑两次结果一致.

注入点: app.py chat_completions, 紧接 BL-FIX2 unwrap_tool_images 之后
(含图 tool 已经重组成 user multipart, 剩下 role=tool 都是纯文本).

实测 (sandbox unit test):
  - 199 messages / 63 tool / 258KB → 114KB, 省 143KB (~35K tokens)
  - 117% overflow 直接干到 ~80% headroom

11 单测全过:
  - 短内容不动 / 长英文截 / 长中文截 (字节安全)
  - 消息数量不变 / 幂等 / 不污染原 list
  - role != tool 不动 / content 是 list 不动
  - 临界 (just cap / slack / over slack) 三段路径
  - 真实 overflow 场景一次过

跟 BL-FIX40 互补 — FIX40 砍 prompt 顶部 (journal), FIX41 砍 prompt
中部 (tool history). Q3 上 LLM cache 摘要替代硬切.
"

if [ -n "$(git status --porcelain)" ]; then
    echo "⚠ 还有 untracked / unstaged 改动, 跳过. 这次只关心 FIX41."
fi

echo
git log origin/main..HEAD --oneline
echo
read -p "确认 push? [y/N] " yn
case $yn in
    [Yy]*) git push origin main && echo "✅ done — 重启 gateway 即生效" ;;
    *) echo "❎ 取消. 后续: git push origin main" ;;
esac
