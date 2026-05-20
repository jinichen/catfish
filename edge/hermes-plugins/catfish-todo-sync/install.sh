#!/usr/bin/env bash
# 装 catfish-todo-sync plugin — BL-CATFISH-TODO-SYNC (5/20).
#
#   1. 软链 plugin 目录到 ~/.hermes/plugins/catfish-todo-sync/
#   2. hermes 启动时自动 discover + load
#
# 依赖: catfish-journal CLI 必须先装 (cd edge/journal-agent && bash install.sh)
#
# 幂等. 卸载: rm ~/.hermes/plugins/catfish-todo-sync

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 5/20 v0.1.3 修: 装到 hermes-agent/plugins/memory/ (跟 catfish-memory 同位置)
# 之前装在 ~/.hermes/plugins/ hermes 根本不扫那里, 找了一晚没生效
HERMES_MEMORY_PLUGINS_DIR="$HOME/.hermes/hermes-agent/plugins/memory"
PLUGIN_SRC="$SCRIPT_DIR"
PLUGIN_DST="$HERMES_MEMORY_PLUGINS_DIR/catfish-todo-sync"
# 兼容旧错位置的软链, install.sh 同时清理
OLD_WRONG_DST="$HOME/.hermes/plugins/catfish-todo-sync"

echo "=== catfish-todo-sync plugin installer ==="
echo

# 1. 检查 catfish-journal CLI 装了没
if ! command -v catfish-journal >/dev/null 2>&1 && \
   [ ! -x "$HOME/.hermes/hermes-agent/venv/bin/catfish-journal" ]; then
    echo "✗ catfish-journal CLI 没装"
    echo "  请先装 catfish-journal:"
    echo "  cd $(dirname $SCRIPT_DIR)/../journal-agent && bash install.sh"
    exit 1
fi
echo "✓ catfish-journal CLI 已装"
echo

# 2. 清理旧错位置的软链 (5/20 之前装到 ~/.hermes/plugins/, hermes 不扫那里)
if [ -L "$OLD_WRONG_DST" ]; then
    rm "$OLD_WRONG_DST"
    echo "✓ 已删旧错位置软链: $OLD_WRONG_DST"
fi

# 3. 软链到正确位置 (hermes-agent/plugins/memory/, 跟 catfish-memory 同位置)
mkdir -p "$HERMES_MEMORY_PLUGINS_DIR"
ln -sfn "$PLUGIN_SRC" "$PLUGIN_DST"
echo "✓ 已装 plugin: $PLUGIN_DST -> $PLUGIN_SRC"

echo
cat <<'EOF'
=== 装好了 ===

下一步:
  1. 退出 hermes (/exit), 重启: catfish
  2. 启动 banner 应能看到 catfish-todo-sync plugin loaded
  3. chat 里测: "加一个TODO: 测试自动同步"
     预期: LLM 调内置 todo tool (in-memory)
           plugin monkey-patch 同步到 journal 文件
  4. 验证: cat ~/.catfish/employee_journal.md | tail -5
     应该看到 - [ ] 测试自动同步

故障排查:
  # 看 plugin 是否真 patched (启动 log 应有 catfish-todo-sync 行)
  grep catfish-todo-sync ~/.hermes/logs/*.log 2>/dev/null | tail -5
  # 手动测 sync 函数
  python3 -c "
  import sys
  sys.path.insert(0, '$PLUGIN_SRC')
  from catfish_todo_sync import _sync_to_journal
  _sync_to_journal([{'content': '测试 sync 函数', 'status': 'pending'}])
  "
  cat ~/.catfish/employee_journal.md | tail -3

卸载:
  rm "$PLUGIN_DST"
  # 重启 hermes 后 patch 自动失效 (新进程 TodoStore 类干净)
EOF
