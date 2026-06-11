#!/usr/bin/env bash
# P3.3.26 (6/11): build 后装到 ~/Applications/.
#
# 跑前先 npm run tauri:build (build 产物在 src-tauri/target/release/bundle/macos/).
# 这脚本只做"装到位": 关老进程 → rsync 覆盖 → 提示装好.
#
# 装 ~/Applications/ 不要 sudo (用户级目录), 比 /Applications/ 安全.
# 老版本被覆盖, --delete 防老 plugin / 老资源残留.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$SCRIPT_DIR/../src-tauri/target/release/bundle/macos/Catfish Companion.app"
DEST_DIR="$HOME/Applications"
DEST="$DEST_DIR/Catfish Companion.app"

if [ ! -d "$SRC" ]; then
    echo "❌ build 产物不存在: $SRC"
    echo "   先跑: npm run tauri:build"
    exit 1
fi

mkdir -p "$DEST_DIR"

# 关老进程 (开着会让 rsync 失败; 用 osascript quit 温和, 让员工先存好状态)
if pgrep -f "Catfish Companion.app/Contents/MacOS" > /dev/null; then
    echo "⚠ 检测到 Catfish Companion 在跑, 优雅关闭..."
    osascript -e 'quit app "Catfish Companion"' 2>/dev/null || true
    # 给 app 2 秒优雅退出 (写 cache / 关 sqlite WAL 等)
    for i in 1 2 3 4; do
        sleep 0.5
        if ! pgrep -f "Catfish Companion.app/Contents/MacOS" > /dev/null; then
            break
        fi
    done
    if pgrep -f "Catfish Companion.app/Contents/MacOS" > /dev/null; then
        echo "❌ 关不掉, 请手动 Cmd+Q 退出 Catfish Companion 后重跑"
        exit 1
    fi
fi

# rsync --delete 保证 dest 干净, 老 plugin / 老资源不混
rsync -a --delete "$SRC/" "$DEST/"

echo ""
echo "✅ 装好: $DEST"
echo "   启动: Launchpad 找 'Catfish Companion' 或 open '$DEST'"
