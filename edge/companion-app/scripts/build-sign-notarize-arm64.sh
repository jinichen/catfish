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

  local entries=()
  while IFS= read -r entry; do
    entries+=("$entry")
  done < <(find "$unpack" -mindepth 1 -maxdepth 1 -exec basename {} \;)
  tar czf "$repacked" -C "$unpack" "${entries[@]}"
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

MAC_RESOURCES="$APP_PATH/Contents/Resources/resources/mac"

echo "=== 签名 App 内直接二进制 ==="
sign_macho_files "$MAC_RESOURCES"

echo "=== 签名归档内运行时 ==="
for archive in \
  "$MAC_RESOURCES/cpython-3.11.15-embed.tar.gz" \
  "$MAC_RESOURCES/chromium-embed.tar.gz" \
  "$MAC_RESOURCES/hermes-agent-bundle.tar.gz"; do
  if [[ -f "$archive" ]]; then
    sign_archive "$archive"
  fi
done

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
