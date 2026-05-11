#!/usr/bin/env bash
# BL-FIX23 L6 紧急 sync (5/11 鸿波 demo 前夜).
#
# 修死循环 — 单文件改, 直接推一个 commit.

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续 — 离线 commit)"

git add central/llm-gateway/src/catfish_gateway/app.py
git add scripts/sync-bl-fix23-l6.sh

git commit -m "BL-FIX23 L6 紧急修 (5/11): retry 死循环 — dedup + 上限降 + tool-result 不 retry

鸿波 5/11 demo 前夜 mac 实测: LLM 陷入 24+ 轮 'plan-only stop → L5 retry →
execute_code 生成同文档 → plan-only stop' 死循环, context 烧到 94%, 烧 token.

L5 (5/9 ship) 单次 request 内最多 retry 2 次, 但每次 Companion 客户端发新
POST /v1/chat/completions 都重置 counter — 累积 24+ 次实际 retry.

L6 修 3 条 (单文件 app.py):

1. _MAX_PLAN_ONLY_RETRIES 2 → 1: 单次 request 内 1 次足够, 1 次还 plan-only
   就接受是'等用户反馈', 不是 LLM 偷懒.

2. _assistant_history_too_repetitive(messages, current_content): 检测当前
   响应跟历史 assistant 消息 Jaccard bigram ≥ 0.55 → 死循环征兆, 不 retry.
   验证: 真实重复 case (鸿波 log 里那段 '已生成请检查' 模板) Jaccard 0.888,
        不同主题 0.010, 阈值充分够用.

3. _last_role_is_tool_result(messages): 检测最近 message 是 role=tool, 说明
   上一轮 LLM 已经调过 tool, 现在汇报合理, 不应该再 retry 强制它再调一次.

加 _jaccard_bigram helper (字符 bigram 集合, 前 200 字, 中文 / 英文都敏感).
所有 helper 跑过 sandbox 单测, 真实长度 case 数字漂亮 (0.888 / 0.010).

skip retry 时加 logger.info 日志方便后续观察 — 'BL-FIX23 L6 skip retry:
last_is_tool=%s too_repetitive=%s retries=%d/%d (避免死循环)'.
"

if [ -n "$(git status --porcelain)" ]; then
    echo "⚠ 还有 untracked / unstaged 改动, 跳过. 这次只关心 L6 修."
fi

echo
git log origin/main..HEAD --oneline
echo
read -p "确认 push? [y/N] " yn
case $yn in
    [Yy]*) git push origin main && echo "✅ done — 重启 gateway 即生效" ;;
    *) echo "❎ 取消. 后续: git push origin main" ;;
esac
