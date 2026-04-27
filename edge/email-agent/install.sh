#!/usr/bin/env bash
# 装 catfish-email:
#   1. pip install 这个包到 Hermes 的 venv (这样 hermes terminal tool 调
#      'catfish-email ...' 能直接跑)
#   2. 软链 SKILL.md 到 ~/.hermes/skills/productivity/ 让 hermes 加载
#
# 幂等. 卸载: pip uninstall catfish-email; rm <skill 软链>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_VENV_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
HERMES_SKILLS_DIR="$HOME/.hermes/skills/productivity"
SKILL_SRC="$SCRIPT_DIR/hermes-skill/catfish-email"
SKILL_DST="$HERMES_SKILLS_DIR/catfish-email"

echo "=== catfish-email installer ==="
echo

# 1. 装 Python 包到 Hermes venv
if [ ! -x "$HERMES_VENV_PY" ]; then
    echo "✗ 找不到 Hermes 的 venv Python: $HERMES_VENV_PY"
    echo "  装 Hermes 时通常会建在这, 看 ~/.hermes/hermes-agent/ 是否存在."
    exit 1
fi

# 先确保 venv 有 pip — Hermes 某些版本的 venv 没装 pip
if ! "$HERMES_VENV_PY" -m pip --version >/dev/null 2>&1; then
    echo "→ Hermes venv 没 pip, ensurepip bootstrap"
    "$HERMES_VENV_PY" -m ensurepip --upgrade --default-pip 2>/dev/null \
        || {
            # ensurepip 也挂了 (可能 pyvenv.cfg 禁了), 用 get-pip.py 救一把
            echo "  ensurepip 失败, 试 get-pip.py"
            curl -sS https://bootstrap.pypa.io/get-pip.py | "$HERMES_VENV_PY"
        }
fi
echo "✓ pip 可用: $("$HERMES_VENV_PY" -m pip --version)"
echo

echo "→ pip install -e 到 Hermes venv"
"$HERMES_VENV_PY" -m pip install -e "$SCRIPT_DIR" --quiet
echo "✓ 装好 catfish-email 包"
echo

# 验证 CLI 能跑 (优先看 console_scripts entry, 退化到 -m)
CATFISH_EMAIL_BIN="$(dirname "$HERMES_VENV_PY")/catfish-email"
if [ -x "$CATFISH_EMAIL_BIN" ] && "$CATFISH_EMAIL_BIN" --help >/dev/null 2>&1; then
    echo "✓ CLI 可调 (entry point): $CATFISH_EMAIL_BIN"
elif "$HERMES_VENV_PY" -m catfish_email --help >/dev/null 2>&1; then
    echo "✓ CLI 可调 (python -m): $HERMES_VENV_PY -m catfish_email"
else
    echo "⚠ CLI 装上了但跑不通, 看 pip install 日志"
fi
echo

# 暴露 catfish-email 到 PATH —— Hermes terminal tool spawn 的 shell 要能找到它
# 把 venv/bin 软链到 ~/.local/bin (macOS 默认 PATH 含此目录)
LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"
if [ -x "$CATFISH_EMAIL_BIN" ]; then
    ln -sfn "$CATFISH_EMAIL_BIN" "$LOCAL_BIN/catfish-email"
    echo "✓ 软链到 PATH: $LOCAL_BIN/catfish-email -> $CATFISH_EMAIL_BIN"
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
  2. banner 的 productivity 段会出现 catfish-email
  3. 测试: 在 hermes 里发"今天有什么邮件没回"
     预期: hermes 调 catfish-email list --since=<today> --unread, 列出未读

故障排查:
  catfish-email accounts --human  # 命令行直接跑, 看是否列出账号
  catfish-email list --human --limit=3
  catfish-email search "关键词" --human

卸载:
  $HERMES_VENV_PY -m pip uninstall catfish-email -y
  rm "$HERMES_SKILLS_DIR/catfish-email"
EOF
