#!/usr/bin/env bash
# 装 catfish-communication-coach skills 到 Hermes.
# 这是个 skill-only 模块 (没 Python 包 / 没 binary), 装的是 SKILL.md 文档让 LLM 加载.
#
# 当前包含:
#   catfish-roleplay  — 演练模式 (扮演老板/客户/同事 + 多轮 + 系统复盘)
#
# 后续会加:
#   catfish-feedback  — 复盘软技能进步追踪
#   catfish-emotion   — 情绪共情 helper (TBD, 可能直接进 SOUL.md 不做单独 skill)
#
# 卸载: rm $HOME/.hermes/skills/productivity/catfish-roleplay

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_SKILLS_DIR="$HOME/.hermes/skills/productivity"

SKILLS=(
    "catfish-roleplay"
)

echo "=== catfish communication coach skills installer ==="

mkdir -p "$HERMES_SKILLS_DIR"

for skill in "${SKILLS[@]}"; do
    src="$SCRIPT_DIR/hermes-skill/$skill"
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
  1. 退出 hermes (/exit), 重启: catfish
  2. banner productivity 段会出现 catfish-roleplay
  3. 测试触发 (员工自然语言):
     - "帮我演练跟老板谈 raise"
     - "周三要 demo, 模拟下 Q&A"
     - "我要拒绝 PM 的 unreasonable request, 怎么开口"

预期行为:
  - Phase 1: 小鲶问 4 件事 (我演谁 / 你目标 / 时间形式 / 多激进)
  - Phase 2: in-character 多轮对话, 你说"暂停"才出戏
  - Phase 3: 系统复盘 (✓3 + ✗3 + 方法论 + 下次建议)

如果 LLM 没进 in-character 模式 / 复盘格式不对 → 通常是模型问题
(尤其 Qwen-Flash 偶尔走泛 chatbot, Qwen-Max / Gemini Pro 更稳)

卸载:
  rm $HERMES_SKILLS_DIR/catfish-roleplay
EOF
