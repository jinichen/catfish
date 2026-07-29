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
#
# P3.5.83 (7/29): 第一个参数现在也可以是架构名 (aarch64 / x64), 用来指定
# 目标架构而不是输出路径 —— 见下面的 "目标架构" 段。这两个是**白名单里的
# 固定词**, 跟"路径打错"分得开, 所以直接放行, 不进下面的路径校验。
case "${1:-}" in
    aarch64|x64) ;;   # 架构名 · 合法, 跳过路径校验
    *)
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
            echo "   指定架构则用: bash scripts/make-dmg.sh aarch64|x64"
            exit 1
            ;;
    esac
fi
        ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 读版本 (跟 package.json 同步)
VERSION=$(node -p "require('$APP_ROOT/package.json').version")

# ── 目标架构 ────────────────────────────────────────────────────────
#
# P3.5.83 (7/29): 第一个参数可以显式指定架构 (aarch64 | x64)。
#
# 原来只有 `uname -m` 一条路 —— 那是**构建机**的架构, 不是产物的架构。
# 在 Apple Silicon 上交叉编 Intel 包时, uname 说 arm64, 于是 dmg 被命名成
# aarch64、还去 target/release/ 找 .app (交叉编译的产物在
# target/x86_64-apple-darwin/release/), 两头都错。
#
# 兼容旧用法: 参数不是架构名时, 仍当作输出路径 (老调用方直接传 dmg 路径)。
case "${1:-}" in
    aarch64|x64)
        TAG="$1"
        shift
        ;;
    *)
        ARCH=$(uname -m)
        if [ "$ARCH" = "arm64" ]; then
            TAG=aarch64
        elif [ "$ARCH" = "x86_64" ]; then
            TAG=x64
        else
            echo "❌ 未知架构 · $ARCH (本脚本只支持 macOS arm64 / x86_64)"
            exit 1
        fi
        ;;
esac

# 交叉编译的产物在 target/<rust-triple>/release/ 下, 本机架构的在 target/release/
if [ "$TAG" = "x64" ] && [ "$(uname -m)" = "arm64" ]; then
    APP_PATH="$APP_ROOT/src-tauri/target/x86_64-apple-darwin/release/bundle/macos/Catfish Companion.app"
else
    APP_PATH="$APP_ROOT/src-tauri/target/release/bundle/macos/Catfish Companion.app"
fi
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
