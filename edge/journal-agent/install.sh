#!/usr/bin/env bash
# 装 catfish-journal — BL-JOURNAL-TODO-EDIT-CHAT Stage 2 (5/20 鸿波).
#
#   1. pip install 这个包到 Hermes 的 venv (这样 hermes terminal tool 调
#      'catfish-journal ...' 能直接跑)
#   2. 软链 catfish-journal 到 ~/.local/bin 让 PATH 可见
#   3. 软链 SKILL.md 到 ~/.hermes/skills/productivity/ 让 hermes 加载
#
# 幂等. 卸载: pip uninstall catfish-journal; rm <skill 软链>
#
# 跟 catfish/edge/email-agent/install.sh 同套路, 调试参考那个.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_VENV_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
HERMES_SKILLS_DIR="$HOME/.hermes/skills/productivity"
SKILL_SRC="$SCRIPT_DIR/hermes-skill/catfish-journal"
SKILL_DST="$HERMES_SKILLS_DIR/catfish-journal"

echo "=== catfish-journal installer ==="
echo

# 1. 装 Python 包到 Hermes venv
if [ ! -x "$HERMES_VENV_PY" ]; then
    echo "✗ 找不到 Hermes 的 venv Python: $HERMES_VENV_PY"
    echo "  看 ~/.hermes/hermes-agent/ 是否存在."
    exit 1
fi

# 确保 venv 有 pip
if ! "$HERMES_VENV_PY" -m pip --version >/dev/null 2>&1; then
    echo "→ Hermes venv 没 pip, ensurepip bootstrap"
    "$HERMES_VENV_PY" -m ensurepip --upgrade --default-pip 2>/dev/null \
        || {
            echo "  ensurepip 失败, 试 get-pip.py"
            curl -sS https://bootstrap.pypa.io/get-pip.py | "$HERMES_VENV_PY"
        }
fi
echo "✓ pip 可用: $("$HERMES_VENV_PY" -m pip --version)"
echo

echo "→ pip install -e 到 Hermes venv"
"$HERMES_VENV_PY" -m pip install -e "$SCRIPT_DIR" --quiet
echo "✓ 装好 catfish-journal 包"
echo

# 验证 CLI 能跑
CATFISH_JOURNAL_BIN="$(dirname "$HERMES_VENV_PY")/catfish-journal"
if [ -x "$CATFISH_JOURNAL_BIN" ] && "$CATFISH_JOURNAL_BIN" --help >/dev/null 2>&1; then
    echo "✓ CLI 可调 (entry point): $CATFISH_JOURNAL_BIN"
elif "$HERMES_VENV_PY" -m catfish_journal --help >/dev/null 2>&1; then
    echo "✓ CLI 可调 (python -m): $HERMES_VENV_PY -m catfish_journal"
else
    echo "⚠ CLI 装上了但跑不通, 看 pip install 日志"
fi
echo

# 暴露 catfish-journal 到 PATH —— Hermes terminal tool spawn 的 shell 要能找到
LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"
if [ -x "$CATFISH_JOURNAL_BIN" ]; then
    ln -sfn "$CATFISH_JOURNAL_BIN" "$LOCAL_BIN/catfish-journal"
    echo "✓ 软链到 PATH: $LOCAL_BIN/catfish-journal -> $CATFISH_JOURNAL_BIN"
fi
echo

# 2. 软链 SKILL.md
if [ ! -f "$SKILL_SRC/SKILL.md" ]; then
    echo "✗ 找不到 $SKILL_SRC/SKILL.md, 跳过 skill 装载"
else
    mkdir -p "$HERMES_SKILLS_DIR"
    ln -sfn "$SKILL_SRC" "$SKILL_DST"
    echo "✓ 已装 skill: $SKILL_DST -> $SKILL_SRC"
fi

echo
cat <<'EOF'
=== 装好了 ===

下一步:
  1. 退出 hermes (/exit), 重启: catfish (注意用 catfish 不是裸 hermes, 自动跳代理)
  2. banner 的 productivity 段会出现 catfish-journal
  3. 测试: 在 hermes 里发"把 P0 bug 那个 TODO 标完成"
     预期: hermes 调 catfish-journal list 找出 line + hint, 再 catfish-journal done 改

故障排查:
  catfish-journal list --format=human       # 列未完成 TODO
  catfish-journal done --line=3 --hint='P0' # 手动测 mark done
  catfish-journal add "新任务"               # 测 add
  cat ~/.catfish/employee_journal.md         # 看真改了啥

卸载:
  $HERMES_VENV_PY -m pip uninstall catfish-journal -y
  rm "$HERMES_SKILLS_DIR/catfish-journal"
EOF
