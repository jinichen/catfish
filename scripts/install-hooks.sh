#!/usr/bin/env bash
# 装 git 钩子 —— 一行命令, 装完就生效。
#
# 用 `core.hooksPath` 而不是往 .git/hooks/ 里软链:
#   · 钩子本体在仓里 (scripts/hooks/), 改了所有人下次 pull 就生效
#   · 软链的老办法做不到这点 —— 每台机器各装各的, 改了没人知道
#   · git 2.9+ 支持 (2016 年), 不用担心版本
#
# 8/13 加这个是因为发现: CLAUDE.md §2 写着「改完代码必跑」「返回非 0 不许
# commit」, 但仓里既没有钩子也没有 Makefile, 而 CI 的 GitHub Actions 额度
# 用完了 —— 这条军规当时**完全没有执行机制**。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

chmod +x scripts/hooks/* 2>/dev/null || true
git config core.hooksPath scripts/hooks

echo "✓ core.hooksPath → scripts/hooks"
echo
echo "  装了什么:"
for h in scripts/hooks/*; do
  [ -f "$h" ] && echo "    $(basename "$h")"
done
echo
echo "  pre-commit 只查**这次提交涉及的文件**, 不扫全仓 —— 仓里 44 个 ≥800 行的"
echo "  存量不该让每次提交都红 (那样三天内所有人都会学会 --no-verify)。"
echo
echo "  紧急时跳过:  git commit --no-verify"
echo "  卸载:        git config --unset core.hooksPath"
