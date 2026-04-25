#!/usr/bin/env bash
# 还原 catfish 品牌补丁 —— 把所有 .before-catfish 备份恢复到原文件
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_PY="$SCRIPT_DIR/apply_brand_patch.py"

echo "=== Catfish brand patch uninstaller ==="
python3 "$PATCH_PY" --revert

echo
echo "还原完成。重启 hermes 看到的应该是原始 Nous Research/Hermes 字样。"
