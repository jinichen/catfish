#!/usr/bin/env bash
# BL-COMPANION-DUAL-ARCH-BUILD (7/19 Task #15 rebuild): 双架构 Companion dmg 后台 build.
#
# 用法:
#   bash ~/person_task/catfish/edge/companion-app/build-dual-arch.sh
#
# 做:
#   1. cd companion-app
#   2. Terminal 1: aarch64-apple-darwin (Apple Silicon) 后台 · log 落盘
#   3. Terminal 2: x86_64-apple-darwin (Intel) 后台 · log 落盘
#   4. print · 2 个 pid + 2 个 log path · 用户 tail 看

set -uo pipefail

cd ~/person_task/catfish/edge/companion-app

TS=$(date +%Y%m%d-%H%M)
LOG_ARM64=~/companion-build-arm64-$TS.log
LOG_X64=~/companion-build-x64-$TS.log

echo "════════════════════════════════════════════"
echo " Companion 双架构 dmg build · $TS"
echo "════════════════════════════════════════════"

# ─── arm64 (Apple Silicon) ─────────────
echo ""
echo "→ [1/2] arm64 后台 build..."
nohup npm run tauri build -- --target aarch64-apple-darwin \
    > "$LOG_ARM64" 2>&1 &
ARM64_PID=$!
echo "  ✓ pid=$ARM64_PID · log=$LOG_ARM64"

# ─── x86_64 (Intel) ─────────────
echo ""
echo "→ [2/2] x86_64 后台 build..."
nohup npm run tauri build -- --target x86_64-apple-darwin \
    > "$LOG_X64" 2>&1 &
X64_PID=$!
echo "  ✓ pid=$X64_PID · log=$LOG_X64"

echo ""
echo "════════════════════════════════════════════"
echo "两 build 都后台跑 · 各 ~30-40 min (M-series mac)"
echo "════════════════════════════════════════════"
echo ""
echo "→ 看进度 (Ctrl+C 停 tail · 不停 build):"
echo "  tail -f $LOG_ARM64"
echo "  tail -f $LOG_X64"
echo ""
echo "→ 抓关键行:"
echo "  grep -E 'Compiling|Finished|error\\[|error:|dmg|Bundling' $LOG_ARM64 | tail -20"
echo "  grep -E 'Compiling|Finished|error\\[|error:|dmg|Bundling' $LOG_X64 | tail -20"
echo ""
echo "→ verify 是否还跑:"
echo "  ps -p $ARM64_PID $X64_PID -o pid,etime,command"
echo ""
echo "→ 出品位置 (build 完):"
echo "  src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/*.dmg"
echo "  src-tauri/target/x86_64-apple-darwin/release/bundle/dmg/*.dmg"
echo ""
echo "→ 若挂 (log 尾有 error): 贴 error 行 · 我 debug"
