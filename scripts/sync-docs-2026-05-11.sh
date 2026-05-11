#!/usr/bin/env bash
# 5/11 进度文件回写 — CHANGELOG.md + FEATURE-TRACKS.md.

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续)"

git add CHANGELOG.md
git add docs/FEATURE-TRACKS.md
git add scripts/sync-docs-2026-05-11.sh

git commit -m "docs (5/11): CHANGELOG + FEATURE-TRACKS 回写 13 commit 一晚 ship 全清单

把今天 (2026-05-11) 一晚累积的工作 + 战略反思整理回写进度文件.

# CHANGELOG.md

加 2026-05-11 完整 entry, 涵盖:

  ## Q3-ARCHIVE — tool message archive 双层 (替代 FIX41 硬切)
    - 设计 18 段 + 实施 1300 行 + 31 单测
    - PG 主 + jsonl 兜底, alembic 20260511_003 + 004 (origin_model)
    - 实测 199 messages 258KB → 112KB lossless
    - Fix 1 (User.email → sub), Fix 2 (summary 用 chat 同款模型)

  ## BL-FIX23 L6/L7/L8 plan-only retry 三轮迭代
    - L6 死循环紧急修, L7 拆 last_is_tool 二分, L8 反向判定 task_complete
    - 29 单测全过 + 20 老 case 不破

  ## BL-FIX42 历史截图折叠
    - 修 Companion Tauri fetch idle timeout
    - 4 张图 → 1 张, prompt 减 75%

  ## BL-FIX44 浏览器自动化彻底修 (真根因路线)
    - 鸿波 '不是够不够的问题, 是要彻底解决问题'
    - coords click + find_by_text 返候选 + role 过滤
    - 工具链断点诊断 + 排序矩阵 Python 验证

  ## BL-Q3-WEBSKILL 视觉双子 + eis-login skill 骨架
    - catfish_recognize_captcha (~250 行)
    - catfish_browser_locate (~330 行, 10 单测)
    - eis-login SKILL.md 骨架 (~300 行) 5/12 内网测填充

  ## BL-FIX45 错误自动恢复 UX (3 类)
    - 401 auto reauth + 500 auto fallback + skill session-renewal

  ## BL-FIX46 请示停顿铁律
    - 跟 L7/L8 反向 (该 act vs 不该 act)
    - SOUL 跟 L1/FIX24 三条互补

  ## demo 路线大重置
    - 从 'AI 多智能' → '员工教 catfish 一次凝固 skill'
    - 跟 BL-Q3-FACT 同源, Q3-WEBSKILL 产品线起点
    - ROI: 100 流程 × 1000 员工 = 600 万 RPA 节省/年

  ## 鸿波诊断功劳 + 教训

# FEATURE-TRACKS.md

  - 顶部快照日期 5/8 → 5/11
  - Phase 进度块加 5/11 BL-Q3-WEBSKILL 战略反思框
  - 📈 列表加 5/11 (周一深夜) 完整 progress entry (在 5/9 之前)

# 不动代码

仅文档. 不需要重启服务. 后续 5/12-5/14 sprint 在此基础上继续.
"

if [ -n "$(git status --porcelain)" ]; then
    echo "⚠ 还有 untracked / unstaged 改动."
fi

echo
git log origin/main..HEAD --oneline
echo
read -p "确认 push? [y/N] " yn
case $yn in
    [Yy]*) git push origin main && echo "✅ done — 进度回写完成" ;;
    *) echo "❎ 取消." ;;
esac
