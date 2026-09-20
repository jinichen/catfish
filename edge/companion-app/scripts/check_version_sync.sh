#!/usr/bin/env bash
# Companion 三处版本号一致性检查 (BL-COMPANION-VERSION-SYNC, 5/18).
#
# 背景: Companion 同时被 Tauri (Rust Cargo.toml + tauri.conf.json) 和前端
# (package.json) 引用, 三处版本号必须一致, 否则:
#   - tauri bundle 的 .app / .msi / .dmg 写的版本号不对
#   - 前端关于页 vs 系统"关于本机"对不上
#   - 升级检查脚本误判 (服务器看 package.json, 本地看 Cargo.toml)
#
# Companion 和 Hermes 是两个独立发布物，版本号不能强制相等。这个脚本负责：
#   1. Companion 自己的 3 处版本一致；
#   2. Hermes tag / commit / pyproject target 等 pin 自洽。
# Companion 的补丁版本必须能独立递增，否则同版本 MSI 无法可靠覆盖旧二进制。
#
# 用法:
#   bash edge/companion-app/scripts/check_version_sync.sh
#
# 退出码:
#   0 = 三处一致
#   1 = 不一致 (打印差异)
#   2 = 读取失败 (文件缺 / 解析不出)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPANION_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PACKAGE_JSON="$COMPANION_DIR/package.json"
CARGO_TOML="$COMPANION_DIR/src-tauri/Cargo.toml"
TAURI_CONF="$COMPANION_DIR/src-tauri/tauri.conf.json"

for f in "$PACKAGE_JSON" "$CARGO_TOML" "$TAURI_CONF"; do
    if [ ! -f "$f" ]; then
        echo "❌ 找不到 $f" >&2
        exit 2
    fi
done

# P3.5.157 (7/1): 老 \s 元字符 GNU 支持 macOS BSD 不支持 → 本机跑挂, CI 上一直
# PASS 是因为 Linux GNU 支持. 改用 POSIX [[:space:]] 跨平台通用.
# 抓 package.json "version": "x.y.z"
PKG_VER="$(grep -E '^[[:space:]]*"version"[[:space:]]*:' "$PACKAGE_JSON" | head -1 | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
# 抓 Cargo.toml 顶部 [package] 段的 version = "x.y.z" (注意不要抓到 dependencies 段的)
CARGO_VER="$(awk '/^\[package\]/{f=1} f && /^version[[:space:]]*=/{gsub(/.*version[[:space:]]*=[[:space:]]*"/, ""); gsub(/".*/, ""); print; exit}' "$CARGO_TOML")"
# 抓 tauri.conf.json "version": "x.y.z"
TAURI_VER="$(grep -E '^[[:space:]]*"version"[[:space:]]*:' "$TAURI_CONF" | head -1 | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"

if [ -z "$PKG_VER" ] || [ -z "$CARGO_VER" ] || [ -z "$TAURI_VER" ]; then
    echo "❌ 版本号解析失败" >&2
    echo "  package.json: '$PKG_VER'" >&2
    echo "  Cargo.toml:   '$CARGO_VER'" >&2
    echo "  tauri.conf:   '$TAURI_VER'" >&2
    exit 2
fi

if [ "$PKG_VER" = "$CARGO_VER" ] && [ "$CARGO_VER" = "$TAURI_VER" ]; then
    echo "✓ Companion 版本一致: $PKG_VER"

    # Hermes 的 pyproject 版本是独立 pin，只要求存在且格式合法。不要拿它跟
    # Companion 比较：Companion 可能只修 UI / Windows 安装器，需要单独 bump，
    # 而 Hermes 仍保持原版本。
    TARGET_VER_FILE="$COMPANION_DIR/.hermes-target-version"
    if [ ! -f "$TARGET_VER_FILE" ]; then
        echo "❌ 找不到 Hermes target pin: $TARGET_VER_FILE" >&2
        exit 2
    fi
    TARGET_VER="$(head -1 "$TARGET_VER_FILE" | tr -d '[:space:]')"
    if ! printf '%s' "$TARGET_VER" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.-]+)?$'; then
        echo "❌ Hermes target 版本格式非法: '$TARGET_VER'" >&2
        exit 2
    fi
    echo "✓ Hermes target pin: ${TARGET_VER}（与 Companion ${PKG_VER} 独立）"

    # ── Windows offline 补丁的上游 pin (8/1) ──────────────────────────
    #
    # 升级 hermes 时要一起动的东西不止版本号, 还有
    # edge/hermes-fork/patch_install_ps1_offline.py 里钉死的上游 install.ps1
    # SHA256 —— 上游装机脚本变了, 我们那 7 处 offline 注入的锚点就未必还贴得上。
    #
    # 7/29 那次 release (1c5ca28) 就漏了它: .hermes-target-version / .hermes-git-tag /
    # 3 处版本号全改了, 唯独没改这个脚本。后果是从那天起每一次 Windows MSI 构建
    # 都在 "Patch install.ps1 offline mode" 那步红掉, 而那要 clone 完上游、跑到
    # 第 5 步才报 —— 一分钟起步, 且没人天天盯 Actions, 于是一直红着没人知道。
    #
    # 版本号那条链当场就被这个脚本拦住了, 这条却要等 40 分钟的 Windows 构建。
    # 同样是"升级 hermes 忘了同步", 两种反馈速度差了三个数量级。
    #
    # 这里不去算上游文件的 SHA (CI runner 上没有上游源码, 算不了), 而是校验
    # "那个 SHA 是从哪个 commit 算的" 跟 .hermes-git-commit 一致 —— 纯本地、
    # 离线、一秒。pin 挪了而补丁脚本没跟着改, 立刻报。
    # 8/8: 从只查 .ps1 扩到**两个都查**。
    #
    # 8/1 加这段时只盖了 Windows 的 patch_install_ps1_offline.py。而
    # patch_install_sh_offline.py (mac / Linux) 当时根本没有 UPSTREAM_COMMIT ——
    # 也就是说我们自己天天在走的 mac 这条路, "升级 hermes 忘了同步 patch 脚本"
    # 一直没有任何护栏, 只能等 build-mac-resources.sh 跑到第 2 步 SHA drift 才炸。
    # 8/8 给 sh 脚本补了 UPSTREAM_COMMIT, 这里跟着一起查。
    GIT_COMMIT_FILE="$COMPANION_DIR/.hermes-git-commit"
    if [ -f "$GIT_COMMIT_FILE" ]; then
        PINNED_COMMIT="$(head -1 "$GIT_COMMIT_FILE" | tr -d '[:space:]')"
        for _spec in "Windows MSI:install.ps1:7:patch_install_ps1_offline.py" \
                     "mac/Linux:install.sh:8:patch_install_sh_offline.py"; do
            PLAT="${_spec%%:*}";      _rest="${_spec#*:}"
            UPSTREAM_FILE="${_rest%%:*}"; _rest="${_rest#*:}"
            N_ANCHOR="${_rest%%:*}"
            PATCH_NAME="${_rest#*:}"
            PATCH_SCRIPT="$COMPANION_DIR/../hermes-fork/$PATCH_NAME"
            [ -f "$PATCH_SCRIPT" ] || continue
            PATCH_COMMIT="$(grep -E '^UPSTREAM_COMMIT[[:space:]]*=' "$PATCH_SCRIPT" \
                            | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
            if [ -z "$PATCH_COMMIT" ]; then
                echo "⚠ $PATCH_NAME 里没有 UPSTREAM_COMMIT, 跳过 $PLAT 的 pin 校验"
            elif [ "$PATCH_COMMIT" = "$PINNED_COMMIT" ]; then
                echo "✓ $PLAT offline 补丁的上游 pin 一致: ${PINNED_COMMIT:0:12}"
            else
                echo "❌ $PLAT offline 补丁没跟上 hermes 的 pin:"
                echo "   .hermes-git-commit : $PINNED_COMMIT"
                echo "   $PATCH_NAME : $PATCH_COMMIT"
                echo ""
                echo "   上游 $UPSTREAM_FILE 换了一版, 我们那 $N_ANCHOR 处 offline 注入的锚点"
                echo "   未必还贴得上。**不能只改 SHA 了事**, 按顺序做:"
                echo "     1. clone 上游到 pin 的 commit, 逐处确认 $N_ANCHOR 个 anchor 各命中 1 次"
                echo "        (0 次 = 上游改了那段; >1 次 = anchor 不再 unique, 都要重 audit)"
                echo "     2. 更新 UPSTREAM_SHA256 = 新 $UPSTREAM_FILE 的 sha256"
                echo "     3. 更新 UPSTREAM_COMMIT = $PINNED_COMMIT"
                echo "     4. 本地跑一遍 patch 脚本 --check, 确认 $N_ANCHOR 处注入都在"
                echo ""
                echo "   不修的话表现是: 该平台的构建每次都红, 而且要等一分钟"
                echo "   clone 完才报错。"
                exit 1
            fi
        done
    fi

    # 本机有 Hermes 时，跟 Hermes target 比较，不跟 Companion 版本比较。
    # 不 fail：开发机可能刻意装旧 Hermes 做兼容测试。
    HERMES_PYPROJECT="${HOME}/.hermes/hermes-agent/pyproject.toml"
    if [ -f "$HERMES_PYPROJECT" ]; then
        HERMES_VER="$(awk -F'"' '/^version[[:space:]]*=/ {print $2; exit}' "$HERMES_PYPROJECT" 2>/dev/null)"
        if [ -n "$HERMES_VER" ] && [ "$HERMES_VER" != "$TARGET_VER" ]; then
            echo "⚠ 本机装的 Hermes 跟 target pin 不一致 (不 fail, 仅提醒):"
            echo "  target : $TARGET_VER"
            echo "  local  : $HERMES_VER  ($HERMES_PYPROJECT)"
        fi
    fi

    # ── 版本号必须跟着代码一起动 (9/20) ──────────────────────────
    #
    # 本文件开头第 14 行从 5/18 起就写着:
    #
    #     "Companion 的补丁版本必须能独立递增, 否则同版本 MSI 无法可靠覆盖旧二进制"
    #
    # 然后版本号从 4 月的初始提交一直停在 1.0.2, 没有任何东西查过它。
    # 9/20 的代价: MSI #209 (638da56) 构建成功、安装成功、注册表 InstallDate
    # 写的是当天 —— 而 exe 还是 9/17 那个文件, 因为两个包都叫 1.0.2。Windows
    # Installer 对带版本资源的文件只在"新版本更高"时覆盖, 相等就保留磁盘上的。
    # 表现是界面上整个 IMAP 功能不存在, 而所有环节都显示成功。查了半天才从
    # bootstrap 日志尾巴上那行 `companion: v1.0.2 (e8f1cf7fbe, built 09-17)`
    # 看出来跑的是旧包。
    #
    # 一条只写在注释里、没有执行机制的规矩 = 没有规矩。跟这个仓库里
    # gitleaks 的 docs/ 白名单、bench 基线那两桩是同一个形状。
    #
    # 判据: 从"当前版本号被引入的那个 commit"到 HEAD, Companion 的**源码**
    # 有没有改过。改过就必须 bump。
    #
    # 只看源码目录 (src/ 和 src-tauri/src/ 和 wix/), 不看测试、文档、脚本 ——
    # 改一个注释就逼人 bump 版本, 三天之内所有人都会学会绕过它, 那就跟
    # pre-commit 钩子当年那版一个下场。判据是"发出去的二进制会不会不一样"。
    if git -C "$COMPANION_DIR" rev-parse --git-dir >/dev/null 2>&1; then
        # ⚠ 浅克隆里这道检查是**空的**, 必须当场拦住, 不能"查不到就跳过"。
        #
        # actions/checkout 默认 fetch-depth: 1。那种仓库里只有一个 commit,
        # 没有父提交, 于是 `git log -S` 会把 HEAD 自己算成"引入版本号的那次",
        # 范围 HEAD..HEAD 为空, CHANGED=0, 检查放行 —— 而且什么都不说。
        #
        # 9/20 第一版就是这样交出去的: 本机跑得好好的, CI 上从来不会触发。
        # 一道只在作者机器上生效的门禁, 比没有门禁更坏。
        if [ "$(git -C "$COMPANION_DIR" rev-parse --is-shallow-repository 2>/dev/null)" = "true" ]; then
            echo "❌ 这是个浅克隆, 版本号 bump 检查在这里跑不了 (会静默放行)。" >&2
            echo "" >&2
            echo "   CI 里给对应 job 的 actions/checkout 加:" >&2
            echo "       with:" >&2
            echo "         fetch-depth: 0" >&2
            echo "   本机: git fetch --unshallow" >&2
            exit 2
        fi
        # 当前版本号是哪个 commit 引入的 (-S 查内容增删, tail -1 取最早那次)
        VER_COMMIT="$(git -C "$COMPANION_DIR" log --format=%H \
            -S"\"version\": \"$PKG_VER\"" -- package.json 2>/dev/null | tail -1)"
        if [ -n "$VER_COMMIT" ]; then
            CHANGED="$(git -C "$COMPANION_DIR" log --oneline "$VER_COMMIT..HEAD" -- \
                src src-tauri/src src-tauri/wix 2>/dev/null | wc -l | tr -d ' ')"
            if [ "${CHANGED:-0}" -gt 0 ]; then
                echo "❌ 版本号还是 $PKG_VER, 但从它被定下来之后 Companion 源码改了 $CHANGED 次。" >&2
                echo "" >&2
                echo "   同版本号的两个 MSI 在 Windows 上是**无法区分**的: 安装程序会认为" >&2
                echo "   磁盘上那个 exe 已经是这个版本, 直接跳过覆盖, 然后报告安装成功。" >&2
                echo "   9/20 就是这么丢了一整天 —— 详见本文件这一段的注释。" >&2
                echo "" >&2
                echo "   改法: 把 patch 位 +1, 三处一起改" >&2
                echo "     package.json / src-tauri/Cargo.toml / src-tauri/tauri.conf.json" >&2
                echo "     (Cargo.lock 里 catfish-companion-app 那条也要跟)" >&2
                echo "" >&2
                echo "   最近改动:" >&2
                # ⚠ `| head -5` 会让 git 吃到 SIGPIPE, 而本脚本开头是
                #    `set -euo pipefail` —— 于是整条管道返 141, set -e 当场把
                #    脚本杀掉, **根本走不到下面的 exit 1**。
                #    第一版就是这样, 自测时退出码是 141 不是 1。CI 只认 1,
                #    141 会被当成"脚本自己崩了"而不是"版本号没 bump"。
                #    报错信息照样打全了, 所以肉眼看不出问题 —— 正是这个脚本
                #    通篇在治的那种病。
                git -C "$COMPANION_DIR" log --oneline "$VER_COMMIT..HEAD" -- \
                    src src-tauri/src src-tauri/wix 2>/dev/null | head -5 \
                    | sed 's/^/     /' >&2 || true
                exit 1
            fi
            echo "✓ 版本 $PKG_VER 之后源码没动过, 不需要 bump"
        else
            echo "⚠ 查不到版本 $PKG_VER 是哪个 commit 引入的, 跳过 bump 校验"
        fi
    fi

    exit 0
fi

echo "❌ Companion 三处版本号不一致:"
echo "  package.json    : $PKG_VER"
echo "  Cargo.toml      : $CARGO_VER"
echo "  tauri.conf.json : $TAURI_VER"
echo ""
echo "改这三处都到同一个 version (一般跟 hermes upstream 同步, 见 docs/HERMES-014-UPGRADE-RUNBOOK.md)."
exit 1
