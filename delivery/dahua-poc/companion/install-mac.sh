#!/usr/bin/env bash
# 达华员工 Mac 装 Companion · 一键脚本
#
# 用法:
#   bash install-mac.sh
#
# 自动:
#   1. 检测 CPU 架构 · 选对应 dmg
#   2. mount dmg · cp .app 到 /Applications
#   3. 清 quarantine (未签名 · macOS Gatekeeper 会拒)
#   4. 首次起 Companion

set -euo pipefail
cd "$(dirname "$0")"

# 检测架构
ARCH=$(uname -m)
if [[ "$ARCH" == "arm64" ]]; then
    DMG="Catfish Companion_0.18.0_aarch64.dmg"
    echo "✓ Apple Silicon (arm64) 检测"
elif [[ "$ARCH" == "x86_64" ]]; then
    DMG="Catfish Companion_0.18.0_x64.dmg"
    echo "✓ Intel Mac (x86_64) 检测"
else
    echo "❌ 未知架构: $ARCH · 联系 IT" >&2
    exit 1
fi

if [[ ! -f "$DMG" ]]; then
    echo "❌ $DMG 不在当前目录 · 检查你解压的位置" >&2
    exit 1
fi

echo "→ 清 dmg quarantine..."
xattr -cr "$DMG"

echo "→ mount dmg..."
MOUNT_POINT=$(hdiutil attach "$DMG" -nobrowse | tail -1 | awk '{print $NF}')
echo "  mount 到: $MOUNT_POINT"

echo "→ 覆盖装到 /Applications..."
if [[ -d "/Applications/Catfish Companion.app" ]]; then
    echo "  发现老版本 · 移除..."
    rm -rf "/Applications/Catfish Companion.app"
fi
cp -R "$MOUNT_POINT/Catfish Companion.app" /Applications/

echo "→ umount dmg..."
hdiutil detach "$MOUNT_POINT" >/dev/null

echo "→ 清 app quarantine (未签名, Gatekeeper 需清)..."
xattr -cr "/Applications/Catfish Companion.app"

echo "→ 首次起 Companion..."
echo "  Companion 首次起来会自动装 hermes-agent (约 10 min · 首次一次性)"
echo "  BL-CATFISH-EMAIL-LINK (7/18): hermes-install 完成后 · Rust 端会自动建"
echo "  ~/.local/bin/catfish-email 软链 (员工零手工)"
open -a "Catfish Companion"

echo ""
echo "════════════════════════════════════════════"
echo "✅ 装完 · Companion 起来了"
echo "════════════════════════════════════════════"
echo "下一步 (你手动做):"
echo "  1. Onboarding · 输达华 gateway/identity URL"
echo "  2. SSO 登录"
echo "  3. 发 chat 'hi' verify"
echo ""
echo "分发 SOP: 达华POC-3台mac-分发SOP.md"
