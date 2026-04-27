#!/usr/bin/env bash
# 装 tool-bridge 自带的 hermes skill (现在只有 catfish-screenshot).
#
# tool-bridge 跟 email-agent 不一样 — 它没有独立的 Python pkg 要 pip install,
# 工具实现已经直接写在 catfish_tool_bridge/catfish_tools.py 里 (作为 native tool).
# 这个脚本只干一件事: 把 SKILL.md 软链到 ~/.hermes/skills/ 让 hermes 加载.
#
# 幂等. 卸载: rm ~/.hermes/skills/productivity/catfish-screenshot

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_SKILLS_DIR="$HOME/.hermes/skills/productivity"

echo "=== tool-bridge skills installer ==="
echo

# 遍历 hermes-skill/ 下每个 catfish-* 子目录, 各自软链
shopt -s nullglob
COUNT=0
for skill_src in "$SCRIPT_DIR"/catfish-*/; do
    skill_src="${skill_src%/}"   # 去尾斜杠
    skill_name="$(basename "$skill_src")"
    skill_md="$skill_src/SKILL.md"

    if [ ! -f "$skill_md" ]; then
        echo "⚠ 跳过 $skill_name — 没有 SKILL.md"
        continue
    fi

    mkdir -p "$HERMES_SKILLS_DIR"
    skill_dst="$HERMES_SKILLS_DIR/$skill_name"
    ln -sfn "$skill_src" "$skill_dst"
    echo "✓ $skill_dst -> $skill_src"
    COUNT=$((COUNT + 1))
done

echo
if [ "$COUNT" -eq 0 ]; then
    echo "⚠ 没找到任何 catfish-* skill 目录, 检查 $SCRIPT_DIR"
else
    echo "✓ 装好 $COUNT 个 skill"
fi

cat <<'EOF'

下一步:
  1. 重启 tool-bridge (Companion 会 autostart respawn, 或手动:
     pkill -f catfish_tool_bridge
     cd ~/person_task/catfish/edge/tool-bridge
     PYTHONPATH=src python -m catfish_tool_bridge
     )
  2. /exit 退出 hermes 重新进 — 让 hermes 加载新 skill
  3. 跟小鲶说"截屏看下我屏幕" 验证 catfish_screenshot 工作

卸载:
  rm ~/.hermes/skills/productivity/catfish-screenshot
EOF
