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

# 3.5 · 报一下"旁边躺着多少可回收的" (8/5 鸿波「空间又被吃完了」)
#
# 之前这个脚本的行为是: 磁盘 16 GB → 打一句"够用"→ 放行。然后同一天就爆盘。
# 因为它只认 rw.*.dmg 那一种残留, 而真正吃盘的是:
#
#     target/debug                    15 G   (tauri dev 留的, 跟出包毫无关系)
#     target/x86_64-apple-darwin      2.2 G  (上次打 Intel 包留的, 一周没动)
#     resources/mac-x64               902 M  (打包脚本能重新生成)
#
# 三样加起来 18 G, 就在 TARGET 隔壁, 脚本一个字都没提。
# "检查了但只检查最不重要的那项", 跟这两天修的其它毛病是同一个形状。
#
# **不自动删** —— target/debug 可能正是别人在跑 tauri dev 的成果, 替人做主
# 删掉几分钟的编译不合适。这里只把不可见的东西变可见, 删不删由人定。
report_reclaimable() {
    local -a items=()
    local total_kb=0
    local p sz_kb sz_h age_d
    for p in "$TARGET/debug" "$TARGET/x86_64-apple-darwin" \
             "$APP_ROOT/src-tauri/resources/mac-x64" \
             "$APP_ROOT/src-tauri/resources/mac-aarch64"; do
        [ -d "$p" ] || continue
        # 今天动过的不报 —— 那是正在用的
        age_d=$(( ( $(date +%s) - $(stat -f %m "$p" 2>/dev/null || echo 0) ) / 86400 ))
        [ "$age_d" -lt 1 ] && continue
        sz_kb=$(du -sk "$p" 2>/dev/null | awk '{print $1}')
        [ -n "$sz_kb" ] || continue
        sz_h=$(du -sh "$p" 2>/dev/null | awk '{print $1}')
        total_kb=$(( total_kb + sz_kb ))
        items+=("     ${sz_h}\t${age_d} 天没动\t${p#$APP_ROOT/}")
    done
    [ ${#items[@]} -eq 0 ] && return 0
    echo ""
    echo "  · 顺带一提, 这些是可回收的 (都不影响当前出包):"
    printf '%b\n' "${items[@]}"
    echo "     合计 ~$(( total_kb / 1024 / 1024 )) GB · 删了下次要用时会重新编译/重新生成"
}
report_reclaimable
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

    # ── 包里那份 hermes 跟 pin 对不对 (8/8 加) ──────────────────────
    #
    # 上面那段比的是 **本机装的** hermes 跟 pin, 而员工机上装的是
    # resources/mac-*/hermes-agent-bundle.tar.gz 里那份 —— 两者没有任何关系。
    # 之前**没有任何检查**把它们对起来。
    #
    # 8/8 差点踩实: 改完 .hermes-git-commit → 3c27eb6 之后没重跑
    # build-mac-resources.sh, 包里还是 3ef6bbd2。这种包装到干净机器上是
    # **死循环**:
    #
    #   bootstrap 铺包里的 v0.19  →  core_health_problems 拿 3ef6bbd2 比
    #   二进制里烤的 3c27eb6 →「版本不匹配」→ 判 broken 挪走 → 重铺 v0.19
    #   → 再不匹配 → …… 每次启动重装几百 MB, 永远好不了。
    #
    # 而这是要发给现场员工的包。所以这条**阻塞 build**, 不是提醒 ——
    # 上面那条 pin 不一致只是"可能静默失效", 这条是"包一定是坏的"。
    for _res in "$APP_ROOT/src-tauri/resources/mac-aarch64" \
                "$APP_ROOT/src-tauri/resources/mac-x64"; do
        _tar="$_res/hermes-agent-bundle.tar.gz"
        [ -f "$_tar" ] || continue
        _bundled=""
        while IFS= read -r line; do
            line="$(printf '%s' "$line" | tr -d '[:space:]')"
            is_sha40 "$line" && _bundled="$line"
        done <<EOF
$(tar xzf "$_tar" -O hermes-agent-src/.catfish-hermes-version 2>/dev/null || true)
EOF
        if [ -z "$_bundled" ]; then
            echo "  ⚠ $(basename "$_res") 的 bundle 里读不出 commit · 跳过比对"
        elif [ "$_bundled" = "$pinned" ]; then
            echo "  ✓ $(basename "$_res") bundle 与 pin 一致 · ${pinned:0:12}"
        else
            echo ""
            echo "❌ $(basename "$_res") 里的 hermes 跟 pin 对不上 —— 这个包是坏的:"
            echo "     二进制会烤进去 (.hermes-git-commit) : ${pinned:0:12}"
            echo "     包里实际装的 (bundle)               : ${_bundled:0:12}"
            echo ""
            echo "   装到干净机器上是死循环: 铺包里那版 → 判版本不匹配 → 挪走重铺"
            echo "   → 再不匹配 …… 每次启动重装几百 MB, 员工那头永远好不了。"
            echo ""
            echo "   修: bash scripts/build-mac-resources.sh $(basename "$_res" | sed 's/^mac-//')"
            echo ""
            exit 1
        fi
    done
fi
