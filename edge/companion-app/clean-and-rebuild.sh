#!/usr/bin/env bash
# BL-COMPANION-REBUILD-P14 (7/19): 清残留 + 重 build 双架构 · 让 dmg baked plugin.py 是新版.
#
# 用法: bash ~/person_task/catfish/edge/companion-app/clean-and-rebuild.sh

set -uo pipefail
cd ~/person_task/catfish/edge/companion-app

echo "════════════════════════════════════════════"
echo " Companion 清 + 重 build 双架构 · 7/19"
echo "════════════════════════════════════════════"

# ─── 1. verify · plugin.py 有 P14 fix (dmg 会 include_str! baked 这文件) ─────
echo ""
echo "→ [1/5] verify 源 plugin.py 有 P14 fix"
SRC_PLUGIN=~/person_task/catfish/edge/hermes-plugins/catfish-xcatfish-user/plugin.py
if grep -q "BL-P14-SLASH-ALIAS" "$SRC_PLUGIN"; then
    echo "  ✓ P14 fix 在源"
else
    echo "  ❌ 源 plugin.py 没 P14 fix · 停 · 先修"
    exit 1
fi

# ─── 2. 卸 Catfish mount + kill Companion ─────
echo ""
echo "→ [2/5] 卸 Catfish mount + kill Companion + 关 dev process"
for m in $(hdiutil info 2>/dev/null | grep -i "catfish" | grep "/Volumes/" | awk '{print $1}'); do
    echo "  detach $m"
    hdiutil detach "$m" -force 2>/dev/null
done
osascript -e 'quit app "Catfish Companion"' 2>/dev/null
sleep 3
pkill -9 -f "Catfish Companion" 2>/dev/null || true
pkill -9 -f "catfish-companion-app" 2>/dev/null || true
sleep 2
echo "  ✓ 卸完"

# ─── 3. 清 tmp + 上次残留 dmg ─────
echo ""
echo "→ [3/5] 清 tmp + 上次残留 dmg (bundle_dmg.sh 挂主因)"
rm -rf src-tauri/target/release/bundle/dmg 2>/dev/null
rm -rf src-tauri/target/aarch64-apple-darwin/release/bundle/dmg 2>/dev/null
rm -rf src-tauri/target/x86_64-apple-darwin/release/bundle/dmg 2>/dev/null
find src-tauri/target -name "*.tmp" -delete 2>/dev/null
find src-tauri/target -name "*.dmg" -delete 2>/dev/null
find src-tauri/target -name "rw.*.dmg" -delete 2>/dev/null
echo "  ✓ 清完"

# ─── 4. verify 干净 ─────
echo ""
echo "→ [4/5] verify 干净"
# BL-VERIFY-MOUNT-FIX (7/19): 之前用 hdiutil info | grep -c "catfish" · 每 mount
# 输出 3-4 行含 catfish · 假报 11 处. 换 · 只看 · /Volumes/Catfish* 真 mount point.
LEFT_MOUNT=$(ls -d /Volumes/Catfish* 2>/dev/null | wc -l | tr -d ' ')
LEFT_APP=$(pgrep -f "Catfish Companion" 2>/dev/null | wc -l | tr -d ' ')
LEFT_DMG=$(find src-tauri/target -name "*.dmg" 2>/dev/null | wc -l | tr -d ' ')
echo "  /Volumes/Catfish* 真 mount: $LEFT_MOUNT (期望 0)"
echo "  Companion 进程: $LEFT_APP (期望 0)"
echo "  老 dmg 残留: $LEFT_DMG (期望 0)"
if [[ "$LEFT_MOUNT" != "0" || "$LEFT_APP" != "0" || "$LEFT_DMG" != "0" ]]; then
    echo "  ⚠ 还有残留 · 手工清后再跑"
    exit 1
fi

# ─── 5. 双架构后台 build ─────
TS=$(date +%Y%m%d-%H%M)
LOG_ARM64=~/companion-build-arm64-$TS.log
LOG_X64=~/companion-build-x64-$TS.log

echo ""
echo "→ [5/5] 双架构后台 build (~30-40 min each · 并行)"

nohup npm run tauri build -- --target aarch64-apple-darwin \
    > "$LOG_ARM64" 2>&1 &
ARM64_PID=$!
disown
echo "  ✓ arm64 pid=$ARM64_PID · log=$LOG_ARM64"

nohup npm run tauri build -- --target x86_64-apple-darwin \
    > "$LOG_X64" 2>&1 &
X64_PID=$!
disown
echo "  ✓ x64 pid=$X64_PID · log=$LOG_X64"

echo ""
echo "════════════════════════════════════════════"
echo "两 build 后台跑 · 45 min 后回来查"
echo "════════════════════════════════════════════"
echo ""
echo "→ 查进度:"
echo "  grep -E 'Compiling|Finished|error|Bundling' $LOG_ARM64 | tail -20"
echo "  grep -E 'Compiling|Finished|error|Bundling' $LOG_X64 | tail -20"
echo ""
echo "→ 查还在跑:"
echo "  ps -p $ARM64_PID $X64_PID -o pid,etime,command 2>/dev/null"
echo ""
echo "→ 完成后出品:"
echo "  ls -lh src-tauri/target/{aarch64,x86_64}-apple-darwin/release/bundle/dmg/*.dmg"
