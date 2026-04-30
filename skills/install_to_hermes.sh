#!/usr/bin/env bash
#
# install_to_hermes.sh
# ====================
# 把 catfish 工程审定 skill (本目录 department/*) 同步安装到 hermes 标准
# skill 路径 (~/.hermes/skills/productivity/catfish-*), 让 hermes 原生
# skills_list / Companion 仪表盘直接识别, 不需要 catfish_run_skill 特殊调用.
#
# 设计思路 (鸿波 2026-04-30 反馈):
#   - 之前给 catfish skill 单独搞 catfish:department namespace + catfish_run_skill
#     工具 + skill_guard 工程级保护是过度设计. LLM 反复混淆两套 skill 系统.
#   - 改成跟 catfish-email / catfish-browser-task 同等地位, 装到 hermes 标准
#     productivity/ namespace 下, 模型调用方式跟其它 catfish-* skill 一致.
#
# 同步策略:
#   - 删旧目录, 全量覆盖 (skill 本身比较小, 不做 diff)
#   - SKILL.md 的 name 字段自动加 "catfish-" 前缀 (例 leadership-briefing →
#     catfish-leadership-briefing), 跟 catfish-email 命名风格一致
#   - 只复制 SKILL.md / script.py / README.md / tests/ — 不带 sample-output / .csv 等
#     运行产出物
#
# 用法:
#   bash ~/person_task/catfish/skills/install_to_hermes.sh
#
# 跑完之后:
#   - hermes 自动认到新 skill (Companion 仪表盘 productivity 段 13 → 15)
#   - tool-bridge 需重启一次让它重扫 skill 列表:
#       pkill -9 -f catfish_tool_bridge && sleep 8

set -euo pipefail

# ── 路径 ────────────────────────────────────────────────────────
CATFISH_SKILLS_DIR="$(cd "$(dirname "$0")" && pwd)"
HERMES_PRODUCTIVITY_DIR="$HOME/.hermes/skills/productivity"

# ── 颜色 (终端友好) ─────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
RESET='\033[0m'

echo "📦 catfish skill → hermes productivity namespace 同步"
echo "=========================================="
echo "源目录: $CATFISH_SKILLS_DIR/department/"
echo "目标:   $HERMES_PRODUCTIVITY_DIR/catfish-*"
echo ""

# 检查 hermes 目录存在
if [ ! -d "$HOME/.hermes" ]; then
  echo -e "${RED}✗ ~/.hermes/ 不存在 — hermes 未安装?${RESET}"
  exit 1
fi

mkdir -p "$HERMES_PRODUCTIVITY_DIR"

# ── 同步每个 skill ──────────────────────────────────────────────
synced=0
for src_dir in "$CATFISH_SKILLS_DIR"/department/*/; do
  [ -d "$src_dir" ] || continue
  skill_name=$(basename "$src_dir")

  # 必须有 SKILL.md, 否则不是合法 skill
  if [ ! -f "$src_dir/SKILL.md" ]; then
    echo -e "${YELLOW}⚠ 跳过 $skill_name — 没找到 SKILL.md${RESET}"
    continue
  fi

  target_name="catfish-$skill_name"
  target_dir="$HERMES_PRODUCTIVITY_DIR/$target_name"

  # 删旧 + 重建
  rm -rf "$target_dir"
  mkdir -p "$target_dir"

  # 复制 SKILL.md, sed 改 name 字段加 catfish- 前缀
  # 用 awk 处理 yaml frontmatter (只改第一个 name: 字段, 不动 description 里的 name)
  awk -v new_name="$target_name" '
    BEGIN { in_front=0; name_done=0 }
    /^---[[:space:]]*$/ {
      if (in_front == 0) { in_front=1; print; next }
      else { in_front=2; print; next }
    }
    in_front == 1 && name_done == 0 && /^name:[[:space:]]*/ {
      print "name: " new_name
      name_done=1
      next
    }
    { print }
  ' "$src_dir/SKILL.md" > "$target_dir/SKILL.md"

  # 复制 script.py / README.md (如有)
  for f in script.py README.md; do
    [ -f "$src_dir/$f" ] && cp "$src_dir/$f" "$target_dir/$f"
  done

  # 复制 tests/ (如有)
  [ -d "$src_dir/tests" ] && cp -r "$src_dir/tests" "$target_dir/tests"

  echo -e "${GREEN}✓${RESET} $skill_name → $target_name"
  synced=$((synced + 1))
done

echo ""
echo "=========================================="
echo -e "${GREEN}✓ 已同步 $synced 个 skill${RESET}"
echo ""
echo "下一步:"
echo "  1. 重启 tool-bridge 让它重扫 skill 列表:"
echo "       pkill -9 -f catfish_tool_bridge && sleep 8"
echo ""
echo "  2. Companion 仪表盘 productivity 段应该多出 catfish-* 条目"
echo ""
echo "  3. 新对话发指令测试 (无需 catfish_run_skill 工具):"
echo "       '写一份本周周报' → 模型走 hermes 原生识别 productivity/catfish-weekly-report"
echo "       '写一份给公司领导的汇报' → productivity/catfish-leadership-briefing"
