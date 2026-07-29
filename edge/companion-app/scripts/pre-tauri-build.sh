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

# ─── 4 · hermes 版本 pin 对照 (P3.5.87 · 7/29) ──────────────────────
#
# .hermes-git-commit 是**唯一事实源** —— 它被 include_str! 烤进二进制, 员工机
# 装机时作为 `--commit` 传给 install.sh。不钉死的话 install.sh 装 main 分支,
# 员工拿到的是"装机当天上游长什么样"。
#
# 为什么不干脆打包时自动读本机版本: 那样构建机上谁跑一次 `hermes update`,
# 下一个包就静默换了 hermes 版本, 而且同一份代码在两台机器上打出的包不一样。
# 这正是我们一直在打的那类问题 —— 所以 pin 必须是仓库里显式写死的值。
#
# 但"写死"容易跟现实脱节: 开发机上验证的是 A 版本, 包里钉的还是 B。所以这里
# 做一次对照, 不一致就大声说出来 —— 让人当场决定是"该更新 pin" 还是
# "开发机被 hermes 自更新偷偷改了"。
#
# 只在开发机上有 hermes 时检查; CI 上没有 ~/.hermes, 跳过不影响。
PIN_FILE="$APP_ROOT/.hermes-git-commit"
LOCAL_HERMES="$HOME/.hermes/hermes-agent"
if [ -f "$PIN_FILE" ] && [ -d "$LOCAL_HERMES/.git" ]; then
    pinned="$(tr -d '[:space:]' < "$PIN_FILE")"
    local_sha="$(git -C "$LOCAL_HERMES" rev-parse HEAD 2>/dev/null || echo '')"
    local_tag="$(git -C "$LOCAL_HERMES" describe --tags 2>/dev/null || echo '?')"
    if [ -n "$local_sha" ] && [ "$pinned" != "$local_sha" ]; then
        echo ""
        echo "⚠️  hermes 版本 pin 跟本机装的不一致:"
        echo "     包里会装 (.hermes-git-commit) : ${pinned:0:12}"
        echo "     本机在跑                      : ${local_sha:0:12}  ($local_tag)"
        echo ""
        echo "   catfish 的 19 个 monkey-patch 是按特定 hermes 版本写的, 版本不对"
        echo "   就静默失效 (warning + skip): 多租户 header / RBAC / 审批 / picker"
        echo "   联动全部悄悄不工作, 而界面和聊天一切正常。"
        echo ""
        echo "   两种情况, 自己判断:"
        echo "     · 本机这个是验证过的 → 更新 pin:"
        echo "         git -C \"$LOCAL_HERMES\" rev-parse HEAD > \"$PIN_FILE\""
        echo "         git -C \"$LOCAL_HERMES\" describe --tags > \"$APP_ROOT/.hermes-git-tag\""
        echo "       然后**在干净机器上装一遍**, 确认 patch 都加载 (日志无 skip)"
        echo "     · 本机是被 hermes 自更新偷偷改的 → 别动 pin, 先查为什么变了"
        echo ""
        echo "   (只是提醒, 不阻塞 build)"
    else
        echo "  ✓ hermes pin 与本机一致 · ${pinned:0:12} ($local_tag)"
    fi
fi
