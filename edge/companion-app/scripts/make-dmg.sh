#!/usr/bin/env bash
# BL-TAURI-DMG-WORKAROUND (7/24): Tauri 官方 bundle_dmg.sh 在 macOS Sequoia +
# Tauri 2 上反复挂 (hdiutil 冲突 / SetFile 依赖 / rw.*.dmg 累积残留).
# 手工用 hdiutil create UDZO 稳 · 输出跟官方等效.
#
# 前提 · .app 已 build (`npm run tauri build -- --bundles app`)
# 用法 · bash scripts/make-dmg.sh [output-path]
#
# 输出 · ~/Downloads/Catfish-Companion-<version>-<arch>.dmg (默认)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 读版本 (跟 package.json 同步)
VERSION=$(node -p "require('$APP_ROOT/package.json').version")

# 检测当前 arch (arm64 = aarch64 · x86_64 = x64)
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
    TAG=aarch64
elif [ "$ARCH" = "x86_64" ]; then
    TAG=x64
else
    echo "❌ 未知架构 · $ARCH"
    exit 1
fi

APP_PATH="$APP_ROOT/src-tauri/target/release/bundle/macos/Catfish Companion.app"
OUT_PATH="${1:-$HOME/Downloads/Catfish-Companion-${VERSION}-${TAG}.dmg}"

if [ ! -d "$APP_PATH" ]; then
    echo "❌ .app 不存在 · $APP_PATH"
    echo "   先跑 · npm run tauri build -- --bundles app"
    exit 1
fi

# 防冲突 · detach 所有 Catfish mount
for v in /Volumes/Catfish*; do
    [ -d "$v" ] || continue
    hdiutil detach "$v" -force >/dev/null 2>&1 || true
done

echo "→ hdiutil create UDZO  ·  $OUT_PATH"
hdiutil create \
    -volname "Catfish Companion" \
    -srcfolder "$APP_PATH" \
    -ov -format UDZO \
    "$OUT_PATH"

size=$(du -h "$OUT_PATH" | awk '{print $1}')
echo ""
echo "✅ dmg 生成完成"
echo "   路径 · $OUT_PATH"
echo "   大小 · $size"
