#!/usr/bin/env bash
# BL-FIX46 (5/11) — 请示停顿铁律 (SOUL 加段, 修 LLM 过度行动).
#
# 鸿波 5/11 实测翻车: 问 LLM "今天做了啥", LLM 答完后说 "要不要继续看待办?",
# 然后立刻自己 catfish_browser_screenshot() 开始截图 — 完全没等回答, 还
# 折腾浏览器状态发现 EIS session 丢了. 教科书级过度行动 (overaction).
#
# 这跟之前 L7/L8 修的"半路 stop"是反方向问题:
#   L7/L8 修: 该 act 没 act (LLM 停了)
#   FIX46 修: 不该 act 却 act (LLM 自作主张)
#
# 修法: SOUL.md 加"请示停顿铁律"段, 跟"做完才说"(L1) + "做完不再问"(FIX24) 互补.

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续)"

git add edge/identity/SOUL.md
git add scripts/sync-bl-fix46.sh

git commit -m "BL-FIX46 (5/11): 请示停顿铁律 (修 LLM 过度行动)

鸿波 5/11 实测翻车场景:

  员工: '今天做了啥'
  LLM:  '今天 A/B/C... 要不要继续看待办?'
        ↓ 立刻 catfish_browser_screenshot()       ← 没等回答
        ↓ 发现是 YouTube (跟问题无关)
        ↓ 又 browser_goto 试图救场                 ← 烧 token
        ↓ EIS session 丢了, 越救越乱

LLM 把'要不要 X?' 请示句当成 license 接着自己干. 经典 overaction.

# 跟之前 fix 不冲突 (反方向问题)

  L7/L8 (5/11): 修'该 act 没 act' (LLM 中途 stop)
  FIX46 (本条): 修'不该 act 却 act' (LLM 自作主张)

两类都是 turn 控制问题, 但方向相反. L7/L8 是 retry helper, FIX46 是 SOUL
软纪律. 不冲突 — L7/L8 在 finish_reason=stop 时触发, FIX46 是 prompt 教导
不让 LLM 写 '?' 句之后跟 tool_call.

# SOUL 加段 (跟 L1/FIX24 同位置, 配套 3 条铁律)

  - 做完才说 (L1, 5/9): 反馈来了立刻动手, 不发 plan-only
  - 做完不再问 (FIX24, 5/9): 做完沉默报告, 不发回环问句
  - **请示停顿 (FIX46, 5/11): 问了就真等, 不要自己接着干**

请示句式列表:
  - '要不要 X?' / '需要我 X 吗?' / '是否需要 X?' / '继续吗?'
  - '如果你...' / '建议是否...' / '看起来需要...' / '你想...'
  - 任何带 '?' 的疑问句指向员工决策
  - '我可以帮你 ...' (隐含请示)

合理 vs 假请示:
  ✅ '要存在 ~/Desktop 还是 ~/Documents?' (路径模糊)
  ✅ '要删 ./old/ 整目录 (20 个文件)?' (高风险)
  ❌ '你要看截图吗?' (看就看, 不问)
  ❌ '需要继续吗?' (跑得通就跑)
  ❌ '我去 X 行吗?' (该做就做)

# 没工程兜底

跟 BL-FIX24 重复 tool_call 检测不同, 这条**纯软纪律**. 因为工程拦截
'? 句之后跟 tool_call' 容易误杀真合理 case (e.g. LLM 一边问一边 catfish_remember
存事实). SOUL 教导更准.

# 部署

仅改 SOUL.md. Companion 不重启 gateway 重读即生效 (skills_loader 没 cache
SOUL).

# 跟今晚累积 fix 整理

  今天 (5/11) ship 总计 11 个修:
  - L7 plan-only retry mid-task
  - Q3-ARCHIVE fix1 (User.email → User.sub)
  - Q3-ARCHIVE fix2 (summary 用 chat 同款模型)
  - FIX42 历史截图折叠
  - FIX44 浏览器自动化彻底修 (coords click + find_by_text 候选)
  - L8 反向判定 task-complete
  - WEBSKILL captcha 子 LLM 工具
  - WEBSKILL locate 视觉定位
  - FIX45 错误自动恢复 (401 reauth + 500 fallback + skill session-renewal)
  - eis-login SKILL.md 骨架 + renewal 段
  - **FIX46 请示停顿铁律 (本条)**
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
            echo "  gateway 不需要重启 (skills_loader 不 cache SOUL.md)"
            echo "  下次 chat LLM 自动读到新 SOUL"
        }
        ;;
    *) echo "❎ 取消." ;;
esac
