#!/usr/bin/env bash
# 装 catfish-browser-task skill 到当前用户的 Hermes。
# 幂等。不装 MCP server（这个 skill 只是"任务模板"，不暴露新 tool）。
# 浏览器原生工具 browser_navigate / browser_click / browser_snapshot / browser_cdp 是 Hermes 自带的。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$SCRIPT_DIR/catfish-browser-task"
HERMES_SKILLS_DIR="$HOME/.hermes/skills/productivity"
SKILL_DST="$HERMES_SKILLS_DIR/catfish-browser-task"

echo "=== catfish-browser-task Hermes skill installer ==="

if [ ! -f "$SKILL_SRC/SKILL.md" ]; then
    echo "错误：找不到 $SKILL_SRC/SKILL.md"
    exit 1
fi

mkdir -p "$HERMES_SKILLS_DIR"
ln -sfn "$SKILL_SRC" "$SKILL_DST"
echo "已装：$SKILL_DST -> $SKILL_SRC"

cat <<'EOF'

=== 装好了 ===

下一步：
  1. 退出当前 hermes（/exit）
  2. 重启：hermes
  3. banner 的 productivity 段会出现 catfish-browser-task
  4. 测试：在 hermes 里直接发"帮我去 GitHub 看 hermes-agent 有多少 star"
     预期 Hermes 按 SKILL.md 里的四步模板执行，输出结构化结果

卸载：
  rm $SKILL_DST
EOF
