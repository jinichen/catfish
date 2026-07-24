#!/usr/bin/env bash
# P3.3.26 (6/11) → 7/24 (BL-INSTALL-SYSTEM-APPS 鸿波 catch "为什么不复制到应用文件夹"):
# build 后装到 /Applications/ (系统级, macOS 惯例, Finder 侧栏"应用程序"直接显).
#
# 老: ~/Applications/ 无 sudo 但 Finder 侧栏不显 · Launchpad 也可能滞后.
# 新: /Applications/ 需 sudo 一次 · Finder / Spotlight / Launchpad 全都显.
# 迁移: 若老 ~/Applications/ 有 Catfish Companion.app 残留 · 先删掉防两 app 并存
#   (员工 Cmd+Space 会出两个 · Launchpad 显两个 icon 混乱).
#
# 跑前先 npm run tauri:build (build 产物在 src-tauri/target/release/bundle/macos/).
# 这脚本做: 关老进程 (双位置 pgrep) → 清老 ~/Applications 版本 → sudo rsync 到 /Applications.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$SCRIPT_DIR/../src-tauri/target/release/bundle/macos/Catfish Companion.app"
DEST_DIR="/Applications"
DEST="$DEST_DIR/Catfish Companion.app"
LEGACY_DEST="$HOME/Applications/Catfish Companion.app"  # 7/24 老位置 · 迁移时删

if [ ! -d "$SRC" ]; then
    echo "❌ build 产物不存在: $SRC"
    echo "   先跑: npm run tauri:build"
    exit 1
fi

# 关老进程 (开着会让 rsync 失败; 用 osascript quit 温和, 让员工先存好状态)
# pgrep 模式 "Catfish Companion.app/Contents/MacOS" 是 substring 匹配 · 同时
# catch 到 ~/Applications 和 /Applications 两个位置的进程 · 不用改.
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

# 7/24 迁移: 老位置 ~/Applications 有 Catfish Companion.app 就删掉
# (用户级目录, 不需 sudo). 防两 app 并存 → Launchpad / Spotlight 双 icon 混乱.
# rm -rf 兜底 (老版本 signature 可能带 quarantine attr, 但 rm 不受此约束).
if [ -d "$LEGACY_DEST" ]; then
    echo "→ 检测到老位置有 Catfish Companion.app"
    echo "  路径: $LEGACY_DEST"
    echo "  删掉防跟 /Applications 里新版并存 (双 icon 混乱)"
    rm -rf "$LEGACY_DEST"
    echo "  ✓ 老位置已清"
fi

# sudo prompt 提前告知 · 防员工看到 sudo 静默等密码以为挂了
# sudo -n true 若 credential cache 里已有 = exit 0 · 跳提示 · 无痛
if ! sudo -n true 2>/dev/null; then
    echo ""
    echo "🔒 装到 /Applications 需 sudo 密码 (macOS 系统级目录, 一次即可):"
fi

# rsync --delete 保证 dest 干净, 老 plugin / 老资源不混.
# sudo 因 /Applications 是系统级 · 需 root 写. rsync 失败 (set -e) 直接 exit.
sudo rsync -a --delete "$SRC/" "$DEST/"

# 顺手清 quarantine attr · 防 Gatekeeper 弹 "无法验证开发者" 挡启动
# (手工 build 未签名, 首次 open 会拦; xattr -dr 一次搞定). 失败静默 (可能已无 attr).
sudo xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true

echo ""
echo "✅ 装好: $DEST"
echo "   Finder → 应用程序 里直接能看到"
echo "   启动: Launchpad 找 'Catfish Companion' 或 open '$DEST'"
