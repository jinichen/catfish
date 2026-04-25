#!/usr/bin/env bash
# 还原 rebrand.sh 改过的所有文件 —— 从 .catfish-backup 拷回。
set -euo pipefail

MANIFEST="$HOME/.hermes/.catfish-rebrand-manifest"

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'

if [ ! -f "$MANIFEST" ]; then
    echo "    没有 manifest 文件 ($MANIFEST)，没改过任何东西。"
    exit 0
fi

echo "=== 还原 rebrand 改动 ==="
n=0
fail=0
while IFS= read -r f; do
    [ -z "$f" ] && continue
    backup="${f}.catfish-backup"
    if [ -f "$backup" ]; then
        cp "$backup" "$f"
        rm "$backup"
        echo "    ${GREEN}✓${RESET} $(basename "$f")"
        n=$((n + 1))
    else
        echo "    ${YELLOW}!${RESET} 备份缺失：$backup（跳过）"
        fail=$((fail + 1))
    fi
done < "$MANIFEST"

rm "$MANIFEST"
echo
echo "    还原 $n 个文件，跳过 $fail 个"
echo "    重启 hermes 看回到原版 Hermes UI"
