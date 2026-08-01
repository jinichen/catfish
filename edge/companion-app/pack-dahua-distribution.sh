#!/usr/bin/env bash
# BL-DAHUA-DISTRIBUTION-PACK (7/19): 打达华 POC 分发 tar.gz.
#
# 用法: bash ~/person_task/catfish/edge/companion-app/pack-dahua-distribution.sh
#
# 内含:
#   1. Catfish Companion_0.18.0_aarch64.dmg (M-series mac)
#   2. Catfish Companion_0.18.0_x64.dmg (Intel mac)
#   3. install-mac.sh (员工一键装脚本)
#   4. README.md (员工装机指南)
#   5. 达华POC-3台mac-分发SOP.md (IT 分发 SOP)
#
# 出品: ~/catfish-companion-dahua-YYYYMMDD.tar.gz


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
echo " 达华 POC Companion 分发 · $TS"
echo "════════════════════════════════════════════"

# ─── 1. verify · 两 dmg 都在 ─────
echo ""
echo "→ [1/5] verify · 两 dmg 都在"
ARM64_DMG=$(ls "$BUILD_DIR/aarch64-apple-darwin/release/bundle/dmg/"*.dmg 2>/dev/null | head -1)
X64_DMG=$(ls "$BUILD_DIR/x86_64-apple-darwin/release/bundle/dmg/"*.dmg 2>/dev/null | head -1)

if [[ -z "$ARM64_DMG" ]]; then
    echo "  ❌ arm64 dmg 不在: $BUILD_DIR/aarch64-apple-darwin/release/bundle/dmg/"
    exit 1
fi
if [[ -z "$X64_DMG" ]]; then
    echo "  ❌ x64 dmg 不在: $BUILD_DIR/x86_64-apple-darwin/release/bundle/dmg/"
    exit 1
fi
echo "  ✓ arm64: $(basename "$ARM64_DMG") $(ls -lh "$ARM64_DMG" | awk '{print $5}')"
echo "  ✓ x64:   $(basename "$X64_DMG") $(ls -lh "$X64_DMG" | awk '{print $5}')"

# ─── 2. staging 目录 ─────
echo ""
echo "→ [2/5] 建 staging"
rm -rf "$STAGING"
mkdir -p "$STAGING"

# cp 两 dmg
cp "$ARM64_DMG" "$STAGING/"
cp "$X64_DMG" "$STAGING/"

# cp install-mac.sh
if [[ -f "$DELIVERY_DIR/install-mac.sh" ]]; then
    cp "$DELIVERY_DIR/install-mac.sh" "$STAGING/"
    echo "  ✓ install-mac.sh"
else
    echo "  ⚠ install-mac.sh 不在 $DELIVERY_DIR · 跳过"
fi

# cp README + SOP
if [[ -f "$DELIVERY_DIR/README.md" ]]; then
    cp "$DELIVERY_DIR/README.md" "$STAGING/"
    echo "  ✓ README.md"
fi

SOP=~/person_task/catfish/edge/companion-app/scripts/达华POC-3台mac-分发SOP.md
if [[ -f "$SOP" ]]; then
    cp "$SOP" "$STAGING/"
    echo "  ✓ 达华POC-3台mac-分发SOP.md"
fi

ls -la "$STAGING/"

# ─── 3. 写 QUICK-START.md ─────
echo ""
echo "→ [3/5] 写 QUICK-START.md"
cat > "$STAGING/QUICK-START.md" <<'MDEOF'
# Catfish Companion 达华 POC · 员工装机 3 步

## 1. 找到 dmg

- **Apple Silicon (M1/M2/M3/M4)** → `Catfish Companion_0.18.0_aarch64.dmg`
- **Intel** → `Catfish Companion_0.18.0_x64.dmg`

不确定? · 打开 · 苹果菜单 → 关于本机 · 看 "芯片" 一栏:
- 含 "Apple" → 选 aarch64
- 含 "Intel" → 选 x64

## 2. 装

**方法 A · 一键脚本** (推荐):
```bash
bash install-mac.sh
```

**方法 B · 手工**:
1. 双击 dmg
2. 拖 `Catfish Companion.app` 到 `Applications`
3. 装完弹 "已损坏 · 无法打开" → 终端跑:
   ```bash
   xattr -cr /Applications/Catfish\ Companion.app
   ```
4. 双击启动

## 3. 首次配置

1. 开机 15-30 min · Companion 会自动装 hermes-agent (装完再关闭)
2. 面板 → 服务器配置 → 输达华 gateway URL / identity URL · 保存
3. SSO 登录
4. 手机 WeChat 扫码绑 · 发 "hi" 测

## 7/19 版本关键 fix

- ✅ JWT 自动 refresh (每 25 min · 员工无感)
- ✅ `/批准 本次会话` WeChat 命令识别 (P14 slash alias)
- ✅ hermes 3 处 JWT 同步 (.env + config.yaml + auth.json)
- ✅ 面板改 IP 自动同步 hermes

## 出问题?

联系 · IT · 提 3 张截图:
1. Companion Dashboard · 服务器配置卡
2. WeChat 里 Bot 返回的错误消息
3. Terminal 跑 `tail -50 ~/catfish-gateway-$(date +%Y%m%d).log`
MDEOF
echo "  ✓ QUICK-START.md"

# ─── 4. tar.gz 打包 ─────
echo ""
echo "→ [4/5] tar.gz 打包..."
cd /tmp
tar -czf "$OUT" "$(basename $STAGING)"
SIZE=$(ls -lh "$OUT" | awk '{print $5}')
echo "  ✓ $OUT · $SIZE"

# ─── 5. verify ─────
echo ""
echo "→ [5/5] verify tar.gz"
tar -tzf "$OUT" | head -10
echo ""
echo "════════════════════════════════════════════"
echo "✅ 分发包 · $OUT"
echo "════════════════════════════════════════════"
echo ""
echo "→ scp 到达华现场 IT:"
echo "  scp $OUT dahua-it@<ip>:/tmp/"
echo ""
echo "→ 或 · 用 · airdrop / 微信 / 阿里云盘 / 电子邮件 (视达华 IT 偏好)"
