#!/usr/bin/env bash
# BL-DAHUA-APP-PACK (7/19): x64 dmg 卡 bundle_dmg.sh · 直接打 .app 分发 · 员工 cp 装.
#
# 用法: bash ~/person_task/catfish/edge/companion-app/pack-dahua-app-based.sh
#
# 内含:
#   catfish-companion-dahua-YYYYMMDD/
#     arm64/Catfish Companion.app          (M-series mac)
#     arm64/Catfish Companion_0.18.0_aarch64.dmg  (如已好)
#     x64/Catfish Companion.app            (Intel mac)
#     install-mac.sh                        (员工一键装脚本 · 自动检 arch + cp .app)
#     QUICK-START.md                        (员工装机指南)


# ─── 已废弃 (8/1) ────────────────────────────────────────────────
#
# 这是 7/19 那天为一次具体分发临时写的脚本 (看上面的注释, 精确到分钟),
# 里面写死了 `Catfish Companion_0.18.0_*.dmg` —— 那些文件今天不存在,
# 现在是 0.19.0, 而且版本号跟 hermes 钉死 (见 check_version_sync.sh)。
#
# 照它跑不会报错, 只会拷不到文件然后打出误导性的提示 —— 而这类脚本恰恰是
# 半年后有人翻出来"看着像能用"就直接跑的那种。
#
# 现在的正确做法:
#   bash scripts/build-mac-resources.sh aarch64   # 或 x64
#   npm run tauri:build:arm64                     # 或 tauri:build:x64
#
# 保留本文件只为记录这段历史 (跟 scripts/build-intel-dmg.sh 同一个处理)。
exit_deprecated() {
    echo "❌ 本脚本已废弃 (7/19 一次性分发脚本, 写死 0.18.0 的文件名)"
    echo "   现在用: bash scripts/build-mac-resources.sh <arch> && npm run tauri:build:<arch>"
    exit 1
}
exit_deprecated

set -uo pipefail

BUILD_DIR=~/person_task/catfish/edge/companion-app/src-tauri/target
DELIVERY_DIR=~/person_task/catfish/delivery/dahua-poc/companion
TS=$(date +%Y%m%d)
STAGING=/tmp/catfish-dahua-$TS
OUT=~/catfish-companion-dahua-$TS.tar.gz

echo "════════════════════════════════════════════"
echo " 达华 POC · .app 分发 · $TS"
echo "════════════════════════════════════════════"

# ─── 1. verify · 两 .app 都在 ─────
echo ""
echo "→ [1/5] verify · 两 .app 都在"
# BL-PACK-FALLBACK-DEFAULT-TARGET (7/19 12:35 catch): 若跑了 npm run tauri build 无 --target ·
# .app 出到 target/release/bundle/macos/ · 不是 aarch64-apple-darwin. fallback 找.
ARM64_APP="$BUILD_DIR/aarch64-apple-darwin/release/bundle/macos/Catfish Companion.app"
X64_APP="$BUILD_DIR/x86_64-apple-darwin/release/bundle/macos/Catfish Companion.app"
DEFAULT_APP="$BUILD_DIR/release/bundle/macos/Catfish Companion.app"

if [[ ! -d "$ARM64_APP" ]]; then
    if [[ -d "$DEFAULT_APP" ]]; then
        echo "  ⚠ arm64 target 目录无 .app · fallback default release/"
        ARM64_APP="$DEFAULT_APP"
    else
        echo "  ❌ arm64 .app 都不在:"
        echo "    $ARM64_APP"
        echo "    $DEFAULT_APP"
        find "$BUILD_DIR" -name "Catfish Companion.app" -type d 2>/dev/null
        exit 1
    fi
fi
if [[ ! -d "$X64_APP" ]]; then
    echo "  ❌ x64 .app 不在: $X64_APP"
    find "$BUILD_DIR" -name "Catfish Companion.app" -type d 2>/dev/null
    exit 1
fi

ARM64_SIZE=$(du -sh "$ARM64_APP" | awk '{print $1}')
X64_SIZE=$(du -sh "$X64_APP" | awk '{print $1}')
echo "  ✓ arm64: $ARM64_SIZE"
echo "  ✓ x64:   $X64_SIZE"

# ─── 2. staging ─────
echo ""
echo "→ [2/5] staging"
rm -rf "$STAGING"
mkdir -p "$STAGING/arm64" "$STAGING/x64"

# cp .app
cp -R "$ARM64_APP" "$STAGING/arm64/"
cp -R "$X64_APP" "$STAGING/x64/"
echo "  ✓ 两 .app cp 完"

# cp arm64 dmg (若有)
ARM64_DMG=$(ls "$BUILD_DIR/aarch64-apple-darwin/release/bundle/dmg/"*.dmg 2>/dev/null | head -1)
if [[ -n "$ARM64_DMG" && -f "$ARM64_DMG" ]]; then
    cp "$ARM64_DMG" "$STAGING/arm64/"
    echo "  ✓ arm64 dmg 也带上 (备份): $(basename "$ARM64_DMG")"
fi

# ─── 3. install-mac.sh (支持 .app 优先) ─────
echo ""
echo "→ [3/5] install-mac.sh (员工一键装)"
cat > "$STAGING/install-mac.sh" <<'SHEOF'
#!/usr/bin/env bash
# 达华员工 Mac 装 Companion · 一键脚本 · v0.18.0 (7/19)
#
# 用法:
#   cd /path/to/unzipped/
#   bash install-mac.sh
#
# 做:
#   1. 检 CPU 架构 · 选对应 .app (或 dmg)
#   2. cp .app 到 /Applications (若 arm64 有 dmg · 优先用 dmg)
#   3. 清 quarantine (未签名 · macOS Gatekeeper 会拒)
#   4. 首次启动 Companion

set -euo pipefail
cd "$(dirname "$0")"

ARCH=$(uname -m)
if [[ "$ARCH" == "arm64" ]]; then
    APP_DIR="arm64"
    ARCH_NAME="Apple Silicon (M1/M2/M3/M4)"
elif [[ "$ARCH" == "x86_64" ]]; then
    APP_DIR="x64"
    ARCH_NAME="Intel"
else
    echo "❌ 未知架构: $ARCH · 联系 IT" >&2
    exit 1
fi

echo "✓ $ARCH_NAME 检测 · 用 $APP_DIR/"

# 优先 dmg (arm64 有) · fallback .app
DMG=$(ls "$APP_DIR"/*.dmg 2>/dev/null | head -1)
APP="$APP_DIR/Catfish Companion.app"

if [[ ! -d "$APP" ]]; then
    echo "❌ $APP 不在 · 检查解压位置" >&2
    exit 1
fi

# 关掉老 Companion (若在跑)
osascript -e 'quit app "Catfish Companion"' 2>/dev/null
sleep 3
pkill -9 -f "Catfish Companion" 2>/dev/null || true

if [[ -n "$DMG" ]]; then
    echo "→ 走 dmg 路径: $DMG"
    xattr -cr "$DMG"
    MOUNT_POINT=$(hdiutil attach "$DMG" -nobrowse | tail -1 | awk '{print $NF}')
    echo "  mount 到: $MOUNT_POINT"
    if [[ -d "/Applications/Catfish Companion.app" ]]; then
        echo "  移除老版本..."
        rm -rf "/Applications/Catfish Companion.app"
    fi
    cp -R "$MOUNT_POINT/Catfish Companion.app" /Applications/
    hdiutil detach "$MOUNT_POINT" >/dev/null
else
    echo "→ 走 .app 路径 (无 dmg)"
    if [[ -d "/Applications/Catfish Companion.app" ]]; then
        echo "  移除老版本..."
        rm -rf "/Applications/Catfish Companion.app"
    fi
    cp -R "$APP" /Applications/
fi

echo "→ 清 quarantine (未签名 · Gatekeeper 需清)..."
xattr -cr "/Applications/Catfish Companion.app"

echo "→ 启动 Companion..."
open -a "Catfish Companion"

echo ""
echo "════════════════════════════════════════════"
echo "✅ 装完 · Companion 起来了"
echo "════════════════════════════════════════════"
echo "首次启动 · Companion 会自动装 hermes-agent (约 10-15 min · 只此一次)"
echo "装完 · 面板 → 服务器配置 → 输达华 gateway/identity URL · 保存 · SSO 登录"
SHEOF
chmod +x "$STAGING/install-mac.sh"
echo "  ✓ install-mac.sh"

# ─── 4. QUICK-START.md ─────
echo ""
echo "→ [4/5] QUICK-START.md"
cat > "$STAGING/QUICK-START.md" <<'MDEOF'
# Catfish Companion 达华 POC · 员工装机 (v0.18.0 · 7/19)

## 你的 mac 架构

- **Apple Silicon (M1/M2/M3/M4)** → 用 `arm64/` 目录
- **Intel** → 用 `x64/` 目录

不确定? · 苹果菜单 → 关于本机 → 芯片:
- "Apple ..." → arm64
- "Intel ..." → x64

## 装 · 一键

```bash
bash install-mac.sh
```

自动检 arch · 装 · 启动.

## 装完 · 3 步配置

1. 首启 · Companion 后台装 hermes-agent (10-15 min · 只此一次 · 别关)
2. 面板 → 服务器配置 → 输达华 gateway/identity URL · 保存
3. SSO 登录 · 手机 WeChat 扫码绑 · 发 "hi" 测

## 7/19 版本 · 关键 fix (对达华场景)

- ✅ JWT 自动 refresh (每 25 min + 面板改 IP + 启动 · 员工无感 · 不再撞 "Provider auth failed" 英文)
- ✅ WeChat `/批准 本次会话` 命令识别 (P14 slash alias fix)
- ✅ hermes 3 处 JWT 同步 (.env + config.yaml + auth.json)
- ✅ execute_code sandbox history 污染 fix (P28 死码删)
- ✅ catfish-auto 动态 model routing (hermes 静态 · gateway resolve)

## 出问题 · 3 张截图给 IT

1. Companion Dashboard · 服务器配置卡
2. WeChat 里 Bot 返错误消息
3. `tail -50 ~/catfish-gateway-$(date +%Y%m%d).log`
MDEOF
echo "  ✓ QUICK-START.md"

# ─── 5. tar.gz ─────
echo ""
echo "→ [5/5] tar.gz 打包"
cd /tmp
tar -czf "$OUT" "$(basename $STAGING)"
SIZE=$(ls -lh "$OUT" | awk '{print $5}')

echo ""
echo "════════════════════════════════════════════"
echo "✅ 分发包 · $OUT · $SIZE"
echo "════════════════════════════════════════════"
echo ""
echo "→ tar 目录预览:"
tar -tzf "$OUT" | head -15
echo ""
echo "→ 分发到达华现场:"
echo "  scp $OUT dahua-it@<ip>:/tmp/"
echo "  或 airdrop / 阿里云盘"
