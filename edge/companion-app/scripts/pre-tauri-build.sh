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
#
# ── 怎么读"本机版本" (7/30 修误报) ──────────────────────────────────
#
# 事实源是 .catfish-hermes-version, **不是 git**。原因:
#
#   install.sh:1398 在离线装机时会 `git init`("让 hermes update 未来有网时
#   能 pull"), 但从不 commit 也不 fetch。于是 ~/.hermes/hermes-agent/.git
#   是个空壳: HEAD → refs/heads/main, 而那个 ref 不存在, 0 个对象、无 reflog。
#
#   在这种 unborn HEAD 上 `git rev-parse HEAD` 会**把字面量 "HEAD" 打到
#   stdout**, 同时报错到 stderr、退出码 128。老写法 `... 2>/dev/null || echo ''`
#   拦不住 —— `||` 只在失败时**追加**空串, stdout 里那个 "HEAD" 已经进了变量。
#   于是 local_sha="HEAD", 非空且不等于 pin → 每次 build 都报一次版本不一致。
#
#   7/30 实测: 本机 .catfish-hermes-version 里就是 3ef6bbd2…, 跟 pin 一模一样,
#   而告警照报"本机在跑 HEAD (?)"。这条告警在**每台离线装机的机器上都会误报**。
#
# 误报的代价不是烦人, 是脱敏: 这条警告的正文写着"19 个 monkey-patch 会静默
# 失效", 每次 build 都喊一遍狼来了, 真出事那次就没人看了。
#
# 对齐 Rust 侧 installed_hermes_commit_at() 的做法: 版本文件优先, git 兜底,
# 且**必须校验是 40 位十六进制**才认。
PIN_FILE="$APP_ROOT/.hermes-git-commit"
LOCAL_HERMES="$HOME/.hermes/hermes-agent"
is_sha40() { [ ${#1} -eq 40 ] && [ -z "$(printf '%s' "$1" | tr -d '[:xdigit:]')" ]; }
if [ -f "$PIN_FILE" ] && [ -d "$LOCAL_HERMES" ]; then
    pinned="$(tr -d '[:space:]' < "$PIN_FILE")"
    local_sha=""
    local_tag="?"
    VER_FILE="$LOCAL_HERMES/.catfish-hermes-version"
    if [ -f "$VER_FILE" ]; then
        # 格式是两行: describe / sha。不认行号, 挑出那行 40 位 hex ——
        # 跟 Rust 的 parse_version_file 同样的判据, 免得哪天顺序变了就失配。
        while IFS= read -r line; do
            line="$(printf '%s' "$line" | tr -d '[:space:]')"
            if is_sha40 "$line"; then
                local_sha="$line"
            elif [ -n "$line" ] && [ "$local_tag" = "?" ]; then
                local_tag="$line"
            fi
        done < "$VER_FILE"
    fi
    if [ -z "$local_sha" ] && [ -d "$LOCAL_HERMES/.git" ]; then
        # 兜底走 git, 但只认长得像 SHA 的结果 (见上: rev-parse 失败会吐 "HEAD")
        git_sha="$(git -C "$LOCAL_HERMES" rev-parse HEAD 2>/dev/null || true)"
        git_sha="$(printf '%s' "$git_sha" | tr -d '[:space:]')"
        if is_sha40 "$git_sha"; then
            local_sha="$git_sha"
            local_tag="$(git -C "$LOCAL_HERMES" describe --tags 2>/dev/null || echo '?')"
        fi
    fi
    if [ -z "$local_sha" ]; then
        echo "  · 本机 hermes 版本读不出 (无 .catfish-hermes-version 且 git 不可用) · 跳过 pin 对照"
    elif [ "$pinned" != "$local_sha" ]; then
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
