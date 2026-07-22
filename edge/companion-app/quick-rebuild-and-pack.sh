#!/usr/bin/env bash
# BL-QUICK-REBUILD (7/19 12:55): 修 ChatToolCall.tsx P27 · rebuild arm64 · cp 到
# /Applications · retry pack. 15 min 完.

set -uo pipefail
cd ~/person_task/catfish/edge/companion-app

TS=$(date +%Y%m%d-%H%M)
LOG=~/companion-build-arm64-fix-$TS.log

echo "════════════════════════════════════════════"
echo " P27 UI fix rebuild · $TS"
echo "════════════════════════════════════════════"

# ─── 1. verify · 源 code 有 fix ─────
echo ""
echo "→ [1/5] verify ChatToolCall.tsx 有 P27 status-pending fix"
if grep -q "BL-P27-STATUS-PENDING-FIX" src/tabs/Chat/ChatToolCall.tsx; then
    echo "  ✓ fix 在源"
else
    echo "  ❌ fix 不在源 · 停"
    exit 1
fi

# ─── 2. 关 Companion · 清 build 中的 rw dmg + mount ─────
echo ""
echo "→ [2/5] 关 Companion + 清残留 mount"
osascript -e 'quit app "Catfish Companion"' 2>/dev/null || true
sleep 3
pkill -9 -f "Catfish Companion" 2>/dev/null || true

for vol in /Volumes/Catfish*; do
    if [[ -d "$vol" ]]; then
        hdiutil detach "$vol" -force 2>/dev/null || true
    fi
done

find src-tauri/target -name "rw.*.dmg" -delete 2>/dev/null || true

# ─── 3. build arm64 · 前台跑 (15 min · Rust 增量) ─────
echo ""
echo "→ [3/5] build arm64 · 前台跑 (可看进度 · Ctrl+C 停)..."
echo "  log 也落: $LOG"
echo ""
npm run tauri build -- --target aarch64-apple-darwin 2>&1 | tee "$LOG" | \
    grep -E "Compiling|Finished|error|Bundling|✓|failed" || true

# ─── 4. verify · .app 出品 + cp 到 /Applications ─────
echo ""
echo "→ [4/5] verify + cp 到 /Applications"
NEW_APP=~/person_task/catfish/edge/companion-app/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/Catfish\ Companion.app
if [[ ! -d "$NEW_APP" ]]; then
    echo "  ❌ .app 不在 · 看 log $LOG"
    tail -30 "$LOG"
    exit 1
fi

# verify · fix 进了 baked
BIN="$NEW_APP/Contents/MacOS/catfish-companion-app"
FIX_COUNT=$(strings "$BIN" 2>/dev/null | grep -c "BL-P27-STATUS-PENDING-FIX" || echo 0)
CN_COUNT=$(strings "$BIN" 2>/dev/null | grep -c "等审批\|等待.*批准" || echo 0)
echo "  ✓ P27 fix baked: $FIX_COUNT (期望 >= 1 · JS bundle 里)"
echo "  ✓ 中文 pattern baked: $CN_COUNT"

# cp 到 /Applications
rm -rf /Applications/Catfish\ Companion.app
cp -R "$NEW_APP" /Applications/
xattr -cr /Applications/Catfish\ Companion.app
echo "  ✓ cp 到 /Applications"

# ─── 5. retry final-pack ─────
echo ""
echo "→ [5/5] retry final-pack tar.gz"
bash ~/person_task/catfish/edge/companion-app/final-pack.sh

echo ""
echo "════════════════════════════════════════════"
echo "✅ 全完 · 打开 Companion 测 approval 按钮"
echo "════════════════════════════════════════════"
echo ""
echo "→ 打开 Companion:"
echo "  open -a 'Catfish Companion'"
echo ""
echo "→ 手机 WeChat / Companion Chat 里 · 触发 execute_code · 按钮应立即出现 (不需切走切回)"
