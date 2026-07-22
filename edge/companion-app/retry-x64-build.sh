#!/usr/bin/env bash
# BL-X64-RETRY (7/19): x64 bundle_dmg 卡 · kill + retry.

set -uo pipefail

echo "→ [1/4] kill 卡的 build + hdiutil"
pkill -9 -f "tauri build" 2>/dev/null || true
pkill -9 -f "bundle_dmg" 2>/dev/null || true
pkill -9 -f "hdiutil" 2>/dev/null || true
sleep 3

echo "→ [2/4] 卸 Catfish mount (若有)"
for vol in /Volumes/Catfish*; do
    if [[ -d "$vol" ]]; then
        echo "  detach $vol"
        hdiutil detach "$vol" -force 2>/dev/null || true
    fi
done

echo "→ [3/4] 清 rw 临时 dmg"
find ~/person_task/catfish/edge/companion-app/src-tauri/target -name "rw.*.dmg" -delete 2>/dev/null || true
# 也清 x64 的 dmg 目录 · 防 bundle_dmg 检测到老残留挂
rm -rf ~/person_task/catfish/edge/companion-app/src-tauri/target/x86_64-apple-darwin/release/bundle/dmg 2>/dev/null

echo "→ [4/4] retry x64 build (Rust compile 已缓存 · 只 bundle · ~5 min)"
cd ~/person_task/catfish/edge/companion-app
LOG=~/companion-build-x64-retry-$(date +%Y%m%d-%H%M).log
nohup npm run tauri build -- --target x86_64-apple-darwin > "$LOG" 2>&1 &
disown

sleep 5
NEW_PID=$(pgrep -f "tauri build" | tail -1)
if [[ -n "$NEW_PID" ]]; then
    echo ""
    echo "✅ x64 retry 后台跑 · pid=$NEW_PID · log=$LOG"
    echo ""
    echo "→ 3-5 min 后查:"
    echo "  grep -E 'Bundling|Finished 2' $LOG | tail -5"
    echo "  ls -lh ~/person_task/catfish/edge/companion-app/src-tauri/target/x86_64-apple-darwin/release/bundle/dmg/*.dmg 2>/dev/null"
else
    echo ""
    echo "❌ retry 没起来 · 看 log:"
    tail -20 "$LOG"
fi
