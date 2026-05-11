#!/usr/bin/env bash
# BL-FIX23 L8 (5/11) — 反向判定 task-complete (修 L7 keyword 太严).
#
# 鸿波 5/11 实测: LLM 截图后说 '已识别验证码 2fW2, 请确认' → 卡死, L7 没 retry.
# 真根因: L7 retry 条件要求 _is_plan_only_content=True, 但 LLM 这种中性陈述句
# 没踩 plan-only keyword list (列表是 '已生成/立刻/现在' 这种), 第一关就过不了.
#
# L8 改反向判定: 不问"是不是 plan-only", 改问"是不是真任务完成 / 等用户决策".
# last_is_tool + 没踩 task_complete 列表 → 中途停 → retry.

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续)"

git add central/llm-gateway/src/catfish_gateway/app.py
git add central/llm-gateway/tests/test_plan_only_retry.py
git add scripts/sync-bl-fix23-l8.sh

git commit -m "BL-FIX23 L8 (5/11): 反向判定 task-complete (修 L7 keyword 太严)

鸿波 5/11 EIS 实测仍卡: LLM 截图后说 '已识别验证码 2fW2, 请确认' → 不 retry.

L7 retry 条件链:
  finish_reason=stop ✓
  no tool_call ✓
  _is_plan_only_content ← **第一关挂这里**
  ...

问题: _is_plan_only_content 检测的 keyword 列表 ('已生成/已完成/立刻/现在 X'
等) 抓不全 LLM 真实话术. '已识别验证码' 这种中性步骤汇报句没踩列表 → 不 retry.

# L8 反向判定

不问'内容是不是 plan-only' (太多变体抓不到), 改问'内容是不是真任务完成':

新 helper _is_task_complete_claim, 精短特异列表:
  1. 任务完成态 ('任务完成' / '处理完毕' / '已完成全部')
  2. 查询型空结果 ('0 项' / '无待办' / '未发现')
  3. 终态失败 ('登录失败' / '权限不足')
  4. 等用户决策 ('请问' / '请你确认' / '是否继续')

新 retry 决策:

  should_retry = (
    finish_reason=stop AND no tool_call
    AND retries < 1 AND not too_repetitive
    AND (
      # Path A (L8 主路径): 刚跑过 tool + 没说任务完成 → 中途停, retry
      (last_is_tool AND NOT _is_task_complete_claim(content))
      OR
      # Path B (老 L5 路径): 没 tool 但用户反馈 + LLM plan-only
      (NOT last_is_tool AND user_feedback AND _is_plan_only_content(content))
    )
  )

**关键变化**: Path A 不再要求 _is_plan_only_content. 鸿波 EIS 中性陈述
'已识别验证码 XXXX' 现在能 retry.

# 死循环保险全保留

  - retries < 1 (上限)
  - too_repetitive (Jaccard ≥ 0.55)
  - task_complete 是真完成时 block (避免回环)
  - '请问/请你确认' 也算 task_complete (LLM 等用户回, 别 retry 烦用户)

# 演化简史

  L5 (5/9): plan-only keyword + 用户反馈 → retry
  L6 (5/11): 加 3 条死循环保险 (上限 1 / Jaccard / last_is_tool 一刀切)
  L7 (5/11): 拆 last_is_tool — 完成态 vs 未来意图
  L8 (5/11): keyword 太严, 改反向判定 task_complete

# 单测

tests/test_plan_only_retry.py 加 L8 段:
  - _is_task_complete_claim 正向 (任务完成 / 空结果 / 失败 / 等用户)
  - _is_task_complete_claim 反向 (step 报告**不**算: '已识别', '已加载', '已截图')
  - 决策矩阵: EIS step 报告 → retry, 真完成 → block, 等用户 → block,
            死循环保护, retry 上限, 老 L5 反馈 path 仍 work
  - 9 个新 case, 老 20+ case 不破

# Sandbox

20/20 老 case + 9/9 L8 新 case 全过. L7 漏的 EIS 场景现在能 retry.
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
