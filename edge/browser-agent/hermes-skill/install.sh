#!/usr/bin/env bash
# 装 catfish browser skills 到当前用户的 Hermes。
# 幂等。不装 MCP server (这些 skill 只是"任务模板", 不暴露新 tool)。
# 浏览器原生工具 browser_navigate / browser_click / browser_snapshot / browser_cdp 是 Hermes 自带的。
#
# 装两个 skill:
#   catfish-browser-task        — 通用浏览器任务模板 (公网 / Jira / Confluence)
#   catfish-browser-compliance  — 内网合规平台特化 (用户管理 / 风险分析 / 报告)
#
# 卸载: rm $HOME/.hermes/skills/productivity/catfish-browser-{task,compliance}

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_SKILLS_DIR="$HOME/.hermes/skills/productivity"

SKILLS=(
    "catfish-browser-task"
    "catfish-browser-compliance"
)

echo "=== catfish browser skills installer ==="

mkdir -p "$HERMES_SKILLS_DIR"

for skill in "${SKILLS[@]}"; do
    src="$SCRIPT_DIR/$skill"
    dst="$HERMES_SKILLS_DIR/$skill"
    if [ ! -f "$src/SKILL.md" ]; then
        echo "✗ 跳过 $skill: 找不到 $src/SKILL.md"
        continue
    fi
    ln -sfn "$src" "$dst"
    echo "✓ 已装: $dst -> $src"
done

cat <<EOF

=== 装好了 ===

下一步:
  1. 退出当前 hermes (/exit)
  2. 重启: catfish (注: 用 catfish 不是裸 hermes, 自动跳代理)
  3. banner 的 productivity 段会出现:
     - catfish-browser-task
     - catfish-browser-compliance
  4. 测试通用模板: "帮我去 GitHub 看 hermes-agent 有多少 star"
  5. 测试合规版 (需公司内网 + 已登录合规平台):
     "帮我在合规平台上看下谁是系统管理员"

卸载:
  rm $HERMES_SKILLS_DIR/catfish-browser-task
  rm $HERMES_SKILLS_DIR/catfish-browser-compliance
EOF
