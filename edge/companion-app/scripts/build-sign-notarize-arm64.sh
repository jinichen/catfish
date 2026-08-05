#!/usr/bin/env bash
# Build, sign, notarize, and staple the complete offline ARM64 macOS package.
#
# Required environment (本机实际值, 8/5 填实 —— 占位符每次都要重查一遍):
#   SIGNING_IDENTITY="Developer ID Application: dan takaragi (LNCT7279Z6)"
#   NOTARY_PROFILE="catfish-notary"
#
# 这两个值不是机密: Developer ID 印在每个签过名的二进制里, 公开可读;
# NOTARY_PROFILE 只是钥匙串里一条记录的**名字**, 真正的 app-specific password
# 在钥匙串里, 不在这个文件里。所以可以入库。
#
# 忘了值怎么查:
#   security find-identity -v -p codesigning | grep "Developer ID Application"
#   xcrun notarytool history --keychain-profile catfish-notary   # 能跑通就是对的
#
# 首次在新机器上配 notary profile:
#   xcrun notarytool store-credentials catfish-notary \
#     --apple-id <Apple ID> --team-id LNCT7279Z6
#
# Usage:
#   SIGNING_IDENTITY="Developer ID Application: dan takaragi (LNCT7279Z6)" \
#   NOTARY_PROFILE="catfish-notary" \
#   bash scripts/build-sign-notarize-arm64.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_DIR="$APP_ROOT/src-tauri/target/release/bundle/macos"
APP_PATH="$TARGET_DIR/Catfish Companion.app"
DMG_PATH="$HOME/Downloads/Catfish-Companion-0.19.0-aarch64.dmg"
WORK_DIR="$(mktemp -d /tmp/catfish-sign.XXXXXX)"

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

: "${SIGNING_IDENTITY:?请设置 SIGNING_IDENTITY}"
: "${NOTARY_PROFILE:?请设置 NOTARY_PROFILE}"

sign_macho_files() {
  local root="$1"
  while IFS= read -r -d '' file; do
    if file "$file" | grep -q "Mach-O"; then
      echo "→ 签名 · $file"
      codesign --force --options runtime --timestamp \
        --sign "$SIGNING_IDENTITY" "$file"
    fi
  done < <(find "$root" -type f -print0)
}

sign_archive() {
  local archive="$1"
  local name="$(basename "$archive")"
  local unpack="$WORK_DIR/${name%.tar.gz}"
  local repacked="$WORK_DIR/$name"

  echo "→ 解压并签名 · $archive"
  mkdir -p "$unpack"
  tar xzf "$archive" -C "$unpack"
  sign_macho_files "$unpack"

  # 重新签名归档内的 App bundle（例如 Chromium 的嵌套 Helper）。
  while IFS= read -r -d '' bundle; do
    codesign --deep --force --options runtime --timestamp \
      --sign "$SIGNING_IDENTITY" "$bundle"
  done < <(find "$unpack" -type d -name '*.app' -print0)

  # 8/5: 原来先 find 出顶层条目再 `tar czf ... "${entries[@]}"`。两个毛病:
  #   1. macOS 自带 bash 3.2 下, 空数组展开 "${entries[@]}" 在 `set -u` 里直接
  #      "unbound variable" 退出 —— 归档一旦是空的就炸。
  #   2. 纯属多余 —— `-C "$unpack" .` 打出的结构完全一样, 而且这正是
  #      build-mac-resources.sh 自己用的写法 (第 246、364 行)。
  tar czf "$repacked" -C "$unpack" .
  cp "$repacked" "$archive"
}

echo "=== 构建完整 ARM64 App ==="
cd "$APP_ROOT"
npm run tauri:build:clean
npx tauri build --bundles app --config src-tauri/tauri.aarch64.conf.json

if [[ ! -d "$APP_PATH" ]]; then
  echo "❌ 找不到构建产物：$APP_PATH" >&2
  exit 1
fi

# 8/5: 遍历**整个** Contents/Resources, 不是只 resources/mac。
#
# Apple 公证驳回 (submission 8c3f698a) 的唯一硬错误就是这个:
#   Contents/Resources/catfish-calendar —— The binary is not signed.
#   (x86_64 和 arm64 两个架构各报 3 条: 没签名 / 没安全时间戳 / 没 hardened runtime)
#
# 原来只签 Contents/Resources/resources/mac/, 而 catfish-calendar 在**上一层**,
# 于是从来没被签过 —— 而且构建、签 App、做 dmg、spctl 全部一路绿, 直到提交给
# Apple 才炸。又是一个"本地全过、外部才发现"。
#
# 改成遍历整个 Resources: sign_macho_files 里用 `file | grep Mach-O` 过滤,
# 非 Mach-O (Windows 的 exe / tar.gz / 文本) 自然跳过, 不会误签。
# 好处是以后再往 Resources 里加二进制不用记得改这里。
MAC_RESOURCES="$APP_PATH/Contents/Resources"

echo "=== 签名 App 内直接二进制 ==="
sign_macho_files "$MAC_RESOURCES"

# 8/5: 这一段以前**一次都没执行过**。
#
# 原来写死三条路径 `$MAC_RESOURCES/cpython-3.11.15-embed.tar.gz` 等, 但归档的真实
# 位置是 `$MAC_RESOURCES/resources/mac/cpython-3.11.15-embed.tar.gz` —— tauri.
# aarch64.conf.json 里 "resources/mac-aarch64/X": "./resources/mac/X", 目标端多一层
# `resources/mac/`。于是 `if [[ -f ... ]]` 每次都为假, 三个归档全部跳过, 而 for 循环
# 悄无声息地走完 —— 日志里"=== 签名归档内运行时 ==="下面一行都没有, 谁也没看出来。
# 又是一次"检查写了, 但恒假", 跟 catfish-calendar 漏签是同一个病。
#
# 改成扫出 Resources 下**所有** .tar.gz。好处跟上面签二进制一样: 以后再加归档
# (node-embed、catfish-email-dist、hermes-deps-dist 本来就不在那三条里) 不用记得
# 回来改这里。非 Mach-O 内容由 sign_macho_files 的 `file | grep Mach-O` 自然过滤。
#
# resources/windows/ 下那几个是 0 字节占位文件 (真件由 CI 现下), 用 -s 跳过 ——
# 否则 `tar xzf` 对空文件报错, set -e 会把整个脚本带走。
echo "=== 签名归档内运行时 ==="
while IFS= read -r -d '' archive; do
  if [[ ! -s "$archive" ]]; then
    echo "→ 跳过空归档 (占位文件) · $archive"
    continue
  fi
  sign_archive "$archive"
done < <(find "$MAC_RESOURCES" -type f -name '*.tar.gz' -print0)

echo "=== 签名 App ==="
codesign --deep --force --options runtime --timestamp \
  --sign "$SIGNING_IDENTITY" "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

echo "=== 制作并签名 DMG ==="
bash "$SCRIPT_DIR/make-dmg.sh"
codesign --force --timestamp --sign "$SIGNING_IDENTITY" "$DMG_PATH"
codesign --verify --verbose=2 "$DMG_PATH"

echo "=== 提交 Apple 公证 ==="
xcrun notarytool submit "$DMG_PATH" \
  --keychain-profile "$NOTARY_PROFILE" \
  --wait

echo "=== 装订公证票据 ==="
xcrun stapler staple "$DMG_PATH"
xcrun stapler validate "$DMG_PATH"

echo "✅ 完成：$DMG_PATH"
