#!/usr/bin/env bash
# BL-TAURI-DMG-WORKAROUND (7/24): Tauri 官方 bundle_dmg.sh 在 macOS Sequoia +
# Tauri 2 上反复挂 (hdiutil 冲突 / SetFile 依赖 / rw.*.dmg 累积残留).
# 手工用 hdiutil create UDZO 稳 · 输出跟官方等效.
#
# 前提 · .app 已 build (`npm run tauri build -- --bundles app`)
# 用法 · bash scripts/make-dmg.sh [output-path.dmg]
#
# 输出 · ~/Downloads/Catfish-Companion-<version>-<arch>.dmg (默认)
#
# ⚠ npm script 名**没有空格**:
#   npm run tauri:build:install   ✓  (build + dmg + 装 /Applications)
#   npm run tauri:build :install  ✗  (':install' 会当 output-path 传进来 · 现已 fail-loud 拦)

set -euo pipefail

# BL-MAKE-DMG-ARG-GUARD (7/26 鸿波踩): 参数校验放**最前面** fail-loud.
#
# 真踩坑: 敲 `npm run tauri:build :install` (中间多个空格) → npm 把 `:install`
# 当参数透传给本脚本 → OUT_PATH=":install" → hdiutil 自动补后缀生成
# ":install.dmg" · 但脚本末尾 `du "$OUT_PATH"` 找无后缀的 ":install" 直接报
# "No such file or directory" · 且 557M 垃圾文件落在 companion-app 目录.
#
# 修: output path 必须以 .dmg 结尾 (hdiutil 会自动补 · 我们不猜) + 不能是
# 命令行常见误输 (以 : 或 - 开头 = 大概率打错命令名).
#
# 放最前面 · 因为参数打错是最常见错误 · 也最便宜 (不用先读 package.json / 查架构).
if [ -n "${1:-}" ]; then
    case "$1" in
        :*|-*)
            echo "❌ output-path 不能以 ':' / '-' 开头 · 收到: '$1'"
            echo ""
            echo "   看着像命令打错了. 是不是想跑这个 (注意**没有空格**):"
            echo "     npm run tauri:build:install"
            echo "     npm run tauri:build:dmg"
            echo "     npm run tauri:install"
            echo ""
            echo "   若真要指定输出路径 · 用法: bash scripts/make-dmg.sh /path/to/out.dmg"
            exit 1
            ;;
    esac
    case "$1" in
        *.dmg) ;;  # OK
        *)
            echo "❌ output-path 必须以 .dmg 结尾 · 收到: '$1'"
            echo "   (hdiutil 会自动补 .dmg · 脚本末尾 du 会找不到无后缀路径)"
            echo "   用法: bash scripts/make-dmg.sh /path/to/out.dmg"
            exit 1
            ;;
    esac
fi

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
    echo "❌ 未知架构 · $ARCH (本脚本只支持 macOS arm64 / x86_64)"
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

# BL-MAKE-DMG-ARG-GUARD (7/26): du 前先 verify 文件真在 (fail-loud 不静默).
# hdiutil 成功但文件不在 = 路径解析出乎意料 · 该报不该吞.
if [ ! -f "$OUT_PATH" ]; then
    echo "❌ hdiutil 报成功但 $OUT_PATH 不存在"
    echo "   可能 hdiutil 自动改了路径 · 查一下 · 别信这次 build"
    exit 1
fi

size=$(du -h "$OUT_PATH" | awk '{print $1}')
echo ""
echo "✅ dmg 生成完成"
echo "   路径 · $OUT_PATH"
echo "   大小 · $size"
