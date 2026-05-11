#!/usr/bin/env bash
# BL-FIX23 L7 — 区分 mid-task plan-only vs 真完成 (5/11 鸿波 "怎么老是留一半").
#
# 修 L6 'not last_is_tool' 一刀切. 鸿波实测:
#   tool screenshot → '现在自动填入用户名' stop → 被 L6 block 不 retry
# 应识别为 mid-task (LLM 说了"现在去 X" 但没真调 tool), 继续 retry.

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续)"

git add central/llm-gateway/src/catfish_gateway/app.py
git add central/llm-gateway/tests/test_plan_only_retry.py
git add scripts/sync-bl-fix23-l7.sh

git commit -m "BL-FIX23 L7 (5/11): 区分 mid-task plan-only vs 真完成 (修 L6 一刀切)

鸿波 5/11 实测 + 反馈: '怎么老是留一半, 回头就不做了'.

L6 用 'not last_is_tool' 一刀切跳 retry, 把两种语义混了:
  A. tool → '已完成检查' stop  ← 真完成, 该 stop (避免死循环)
  B. tool → '现在自动填入 X' stop  ← mid-task 偷停, 应 retry

L7 拆 promise 词为两类:
  COMPLETION_KEYWORDS:    '已生成 / 已完成 / 已保存 / 已修改...'
  FUTURE_INTENT_KEYWORDS: '立刻 / 现在 / 接下来 / 下一步 / 我去 / 继续...'
                          + 鸿波场景词 '现在自动 / 现在填'

新 helper:
  _has_completion_claim(content) — 完成态
  _has_future_intent(content)    — 未来意图

新 retry 决策:
  real_completion_after_tool = (
      last_is_tool AND has_completion AND NOT has_future_intent
  )
  triggering_scenario = (
      user_msg_is_feedback        ← 老 L5 路径
      OR (last_is_tool AND has_future_intent)  ← 新 L7 路径
  )
  should_retry = (
      finish_reason=stop AND no tool_call AND plan-only content
      AND retries < 1 AND NOT too_repetitive
      AND NOT real_completion_after_tool       ← L7 替代 L6 'not last_is_tool'
      AND triggering_scenario                  ← L7 加 mid-task 路径
  )

决策矩阵 (4 维, sandbox 单测验证):
  | last_is_tool | future_intent | completion | retry? | 场景               |
  |--------------|---------------|------------|--------|--------------------|
  | False        | False         | False      | 不      | 一般闲聊            |
  | True         | True          | False      | **是** | 鸿波 mid-task       |
  | True         | False         | True       | **不** | 真做完汇报          |
  | True         | True          | True       | **是** | 混合 '已生成 X 接下来 Y' |

retry hint 升 L7, 加 'browser_fill/click/goto' 例子 + 强调 '现在自动填 X' 这种
mid-task 句式要继续 tool_call.

单测 (tests/test_plan_only_retry.py):
  - 老 L5/L6 测试更新 marker + retries 上限 1
  - L7 新加 11 个测试覆盖 _has_future_intent / _has_completion_claim /
    决策矩阵 4 个场景

Sandbox 决策矩阵 4/4 过. mac 实测重启 gateway 后看 log:
  'BL-FIX23 L7 skip retry: ...' (skip 时打出 future_intent/completion 各值)
  'BL-FIX23 L5 plan-only retry 1/1: ...' (触发时)
"

if [ -n "$(git status --porcelain)" ]; then
    echo "⚠ 还有 untracked / unstaged 改动."
fi

echo
git log origin/main..HEAD --oneline
echo
read -p "确认 push? [y/N] " yn
case $yn in
    [Yy]*) git push origin main && echo "✅ done — 重启 gateway 即生效" ;;
    *) echo "❎ 取消." ;;
esac
