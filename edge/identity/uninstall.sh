#!/usr/bin/env bash
# 卸载 catfish identity，把 ~/.hermes/SOUL.md 还原成 catfish 接管前的状态。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOUL_DST="$HOME/.hermes/SOUL.md"
BACKUP="$HOME/.hermes/SOUL.md.before-catfish"

echo "=== Catfish identity uninstaller ==="

if [ -L "$SOUL_DST" ]; then
    target="$(readlink "$SOUL_DST")"
    rm "$SOUL_DST"
    echo "    已删软链：$SOUL_DST -> $target"
fi

if [ -f "$BACKUP" ]; then
    cp "$BACKUP" "$SOUL_DST"
    echo "    已还原备份：$BACKUP -> $SOUL_DST"
    echo "    （备份文件保留在 $BACKUP，确认无误后可手动删）"
else
    echo "    没有备份文件，~/.hermes/SOUL.md 现在不存在"
    echo "    Hermes 会用默认 SOUL（自我介绍恢复成 'I'm Hermes'）"
fi

echo
echo "重启 hermes 生效。"
