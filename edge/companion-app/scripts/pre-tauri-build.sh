#!/usr/bin/env bash
# BL-TAURI-BUILD-CLEANUP (7/24): Tauri build 前清理 · 防 dmg 残留爆盘
#
# 症状: `npm run tauri build` 若中间挂 · 残留在 target/ 里的 rw.*.dmg
# 每个几百 MB · 挂 mount 也不释放. 累积几次 · 磁盘 10+ GB 白丢.
#
# 修: build 前调这个 · 强 detach 所有 Catfish 相关 mount + 删 rw.*.dmg 残留.
# 磁盘紧时提前 fail loud 让用户先清.
#
# 用法: 在 package.json tauri:build 前 chain 一下 · 或手动跑.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="$APP_ROOT/src-tauri/target"

echo "── pre-tauri-build cleanup ──"

# 1 · detach 所有 /Volumes/Catfish* mount (build 崩残留)
detached=0
if [ -d /Volumes ]; then
    for v in /Volumes/Catfish*; do
        [ -d "$v" ] || continue
        echo "  → detach $v"
        hdiutil detach "$v" -force >/dev/null 2>&1 && detached=$((detached+1)) || true
    done
fi
[ "$detached" -gt 0 ] && echo "  ✓ detached $detached mount"

# 2 · 删 target 里所有 rw.*.dmg 临时残留
if [ -d "$TARGET" ]; then
    residuals=$(find "$TARGET" -name "rw.*.dmg" 2>/dev/null | wc -l | tr -d ' ')
    if [ "$residuals" -gt 0 ]; then
        size=$(find "$TARGET" -name "rw.*.dmg" -exec du -ch {} + 2>/dev/null | tail -1 | awk '{print $1}')
        echo "  → 发现 $residuals 个 rw.*.dmg 残留 · 总 $size · 删"
        find "$TARGET" -name "rw.*.dmg" -delete
    fi
fi

# 3 · 磁盘空间 check · < 3 GB fail loud
avail_gb=$(df -g "$APP_ROOT" | tail -1 | awk '{print $4}')
if [ "$avail_gb" -lt 3 ]; then
    echo ""
    echo "❌ 磁盘空间不足 · 只剩 ${avail_gb} GB (dmg build 需 ~3 GB)"
    echo "   建议 · rm -rf $TARGET/release/bundle (清老 bundle)"
    echo "        · cargo clean (释放 5-10 GB · 但下次 build 慢)"
    exit 1
fi

echo "  ✓ 磁盘可用 ${avail_gb} GB · 继续 build"
echo ""
