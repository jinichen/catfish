#!/usr/bin/env bash
# 取一棵**干净且版本核对过**的 hermes 源码树, 放到 $HERMES_SRC。
#
# 从 build-mac-resources.sh 抽出来 (8/8): 那个文件到 937 行, 越过军规 800 红线。
# 抽这一块而不是随便切一刀 —— "拿到一棵可信的上游源码"本来就是一件完整的事:
# 缓存命中判断、clone、代理兜底、落点核对、写版本文件、存缓存, 六步互相依赖,
# 分开反而更难读。
#
# 由 build-mac-resources.sh source 进去 (不是当子进程跑) —— 它要读写调用方的
# HERMES_SRC / HERMES_TAG / HERMES_COMMIT, 子进程改不了父进程的变量。
#
# 需要调用方先设好: HERMES_SRC · HERMES_TAG · HERMES_COMMIT

# source 进来的文件最容易出的事就是"调用方少设了一个变量"。set -u 下那会变成
# 一句没头没尾的 "unbound variable", 看不出是这里的契约没满足。先自己报清楚。
for _v in HERMES_SRC HERMES_TAG HERMES_COMMIT; do
    if [ -z "${!_v:-}" ]; then
        echo "❌ fetch-hermes-src.sh: 调用方没设 \$$_v" >&2
        echo "   这个文件是被 source 进去的, 三个变量都得由调用方准备好。" >&2
        return 1 2>/dev/null || exit 1
    fi
done
unset _v

if [ -d "$HERMES_SRC" ]; then
    echo "  clean $HERMES_SRC"
    rm -rf "$HERMES_SRC"
fi
# $HERMES_TAG 若是 annotated tag, git 会打一行:
#     warning: refs/tags/<tag> <sha> is not a commit!
# 那个 <sha> 是 **tag 对象**自己的 sha, 不是它指向的 commit —— 浅克隆时 git
# 就这么提示。checkout 落点是对的 (紧接着的核对会验), 这行可以忽略。
#
# 先说一句, 是因为它每次都出现: 一条长期存在、其实无害的 warning 会让人对
# 真正的 warning 脱敏 —— 底下那些"版本对不上"的检查才是要看的。
echo "  (annotated tag 会打一行 'is not a commit!' warning · 正常, 落点由下面的核对负责)"
# 代理挂了就直连重试一次 (8/8 加)。
#
# 8/8 实录: git 全局配了 http.proxy → 127.0.0.1:7890 (Clash 之类), 那天代理
# 没开, clone 一秒就死在
#     Failed to connect to 127.0.0.1 port 7890 after 0 ms
# 而整条打包链最贵的一步在这之后 —— 下 500 MB 的 chromium/python/node。
# 卡在第 1 步反而是运气好, 但报错只有 git 那一行, 看不出"是代理不是网"。
#
# 这个文件里下 GitHub Release 早就有同款兜底 (GH_PROXY 试几次转直连),
# clone 这一步一直没有。补齐, 顺便把"这是代理的问题"说清楚。
# ── 源码缓存 (8/8 加) ────────────────────────────────────────────────
#
# 这一步是整个脚本里**最容易反复重来**的一步: 60 MB, 走的是 GitHub 直连,
# 8/8 实测 850 KB/s 左右, 一次一分多钟, 中途断一次就得从头再来
# (fetch-pack: unexpected disconnect)。而后面任何一步失败 —— 那天连着撞了
# 代理、Node 版本、npm engines 三次 —— 都要求整脚本重跑, 于是这 60 MB 被
# 重下了四遍。
#
# 缓存文件名带 commit sha, 所以**不存在过期问题**: pin 一改, 文件名就变,
# 老缓存自然用不上 (也就不需要什么失效逻辑)。
CACHE_TAR="/tmp/catfish-hermes-src-${HERMES_COMMIT}.tar.gz"

_clone() { git clone --depth 1 --branch "$HERMES_TAG" "$@" \
    https://github.com/NousResearch/hermes-agent.git "$HERMES_SRC"; }

FROM_CACHE=0
if [ -f "$CACHE_TAR" ] && gzip -t "$CACHE_TAR" 2>/dev/null; then
    echo "  ↻ 命中源码缓存 $(basename "$CACHE_TAR") ($(ls -lh "$CACHE_TAR" | awk '{print $5}'))"
    echo "    (跳过 clone。想强制重下: rm $CACHE_TAR)"
    mkdir -p "$HERMES_SRC"
    tar xzf "$CACHE_TAR" -C "$HERMES_SRC" && FROM_CACHE=1 || {
        echo "  ⚠ 缓存解不开, 删掉走网络"
        rm -f "$CACHE_TAR"; rm -rf "$HERMES_SRC"
    }
fi

if [ "$FROM_CACHE" = "0" ] && ! _clone; then
    # 代理挂了就直连重试一次。
    #
    # 8/8 实录: 代理没开时 clone 一秒就死在
    #     Failed to connect to 127.0.0.1 port 7890 after 0 ms
    # 而整条打包链最贵的部分在这之后 —— 卡在第 1 步反而是运气好, 但报错只有
    # git 那一行, 看不出"是代理不是网"。
    #
    # 这个文件里下 GitHub Release 早就有同款兜底 (GH_PROXY 试几次转直连),
    # clone 这一步一直没有。
    #
    # 代理有两个来源, **两个都要看**: git config 的 http.proxy, 和环境变量
    # http_proxy / https_proxy / ALL_PROXY (环境变量优先级更高)。8/8 第一版
    # 只查了 git config, 结果那台机器是环境变量配的 —— 脚本读不到, 判成
    # "网络本身的问题", 把人往错方向指。是鸿波自己 unset 掉环境变量才通的。
    GIT_PROXY="$(git config --get http.proxy || true)"
    ENV_PROXY="${https_proxy:-${HTTPS_PROXY:-${http_proxy:-${HTTP_PROXY:-${ALL_PROXY:-${all_proxy:-}}}}}}"
    ANY_PROXY="${GIT_PROXY:-$ENV_PROXY}"
    if [ -n "$ANY_PROXY" ]; then
        echo ""
        echo "  ⚠ clone 失败, 而这台机器配了代理:"
        [ -n "$GIT_PROXY" ] && echo "      git config http.proxy = $GIT_PROXY"
        [ -n "$ENV_PROXY" ] && echo "      环境变量              = $ENV_PROXY"
        echo "    代理没开的话就是它。直连重试一次 (git config 和环境变量都绕开)..."
        rm -rf "$HERMES_SRC"
        env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
            -u ALL_PROXY -u all_proxy \
            git clone --depth 1 --branch "$HERMES_TAG" \
                -c http.proxy= -c https.proxy= \
                https://github.com/NousResearch/hermes-agent.git "$HERMES_SRC" || {
            echo ""
            echo "❌ 带代理和直连都 clone 不下来。"
            echo "   · 代理软件开着吗"
            [ -n "$GIT_PROXY" ] && echo "   · 临时去掉 git 的: git config --global --unset http.proxy"
            [ -n "$ENV_PROXY" ] && echo "   · 临时去掉环境的: unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy"
            exit 1
        }
        echo "  ✓ 直连成功 (这次绕过了 $ANY_PROXY)"
    else
        echo ""
        echo "❌ clone 失败, 且没查到任何代理配置 —— 是网络本身的问题。"
        echo "   断在中途 (unexpected disconnect) 的话直接重跑, 下面会有缓存兜着。"
        exit 1
    fi
fi

# ── 校验真的落在了要的那个版本上 (P3.5.86 · 7/29) ────────────────────
#
# 7/29 实录: pin 写的是 v2026.7.1, git clone 打了一行
#     warning: refs/tags/v2026.7.1 462c8b02... is not a commit!
#     Note: switching to '7c1a0295...'
# 然后**自己切到了别的 commit**, 脚本毫不知情继续打包 —— 包里究竟是哪个
# 版本没有任何记录。
#
# 这件事的后果不在打包时, 在运行时: catfish 的 19 个 monkey-patch 是 patch
# hermes 内部函数的 (gateway.run._resolve_gateway_model 这类), 版本对不上
# 就加载失败, 而失败方式是 logger.warning + silent skip —— 多租户 header、
# picker 联动、RBAC、审批全部悄悄不工作, 界面上一切正常。
#
# 所以这里 fail-loud: 落点跟 pin 对不上就停, 不许打出一个"不知道装的是什么"
# 的包。同时把实际 commit 记进 bundle, 出问题时能回溯。
ACTUAL_DESC="$(cd "$HERMES_SRC" && git describe --tags --always 2>/dev/null || echo '<未知>')"
ACTUAL_SHA="$(cd "$HERMES_SRC" && git rev-parse HEAD 2>/dev/null || echo '<未知>')"
if [ "$ACTUAL_DESC" != "$HERMES_TAG" ]; then
    echo ""
    echo "❌ clone 落点跟 pin 对不上:"
    echo "     .hermes-git-tag 要的 : $HERMES_TAG"
    echo "     实际 checkout 的     : $ACTUAL_DESC  ($ACTUAL_SHA)"
    echo ""
    echo "   多半是这个 tag 指向的不是 commit (annotated tag 指到了 tree/blob),"
    echo "   或者 tag 名写错了。上游可用的 tag:"
    (cd "$HERMES_SRC" && git ls-remote --tags origin 2>/dev/null \
        | awk -F/ '{print "     " $NF}' | grep -v '\^{}' | tail -10) || true
    echo ""
    echo "   catfish 的 19 个 monkey-patch 是按特定 hermes 版本写的,"
    echo "   装错版本会静默失效 —— 所以这里不允许继续。"
    exit 1
fi
if [ "$ACTUAL_SHA" != "$HERMES_COMMIT" ]; then
    echo ""
    echo "❌ Hermes commit 跟 pin 不一致:"
    echo "     .hermes-git-commit: $HERMES_COMMIT"
    echo "     实际 checkout:      $ACTUAL_SHA"
    echo "   tag 名相同也不能继续；annotated tag 或远端移动都必须重新审计。"
    exit 1
fi
echo "  ✓ hermes 版本核对: $ACTUAL_DESC ($ACTUAL_SHA)"
# 把版本写进 bundle, 装机后可查 (~/.hermes/hermes-agent/.catfish-hermes-version)
printf '%s\n%s\n' "$ACTUAL_DESC" "$ACTUAL_SHA" > "$HERMES_SRC/.catfish-hermes-version"

# 存缓存 —— **必须在这里**, 不能更晚。
#
# 下面紧接着就往树里 cp catfish 插件、装 node_modules、解 chromium。缓存要的是
# 一棵**干净的上游树**, 混进那些东西之后再存, 下次复用就等于把上一次的构建
# 残留带进新包 —— 那种污染很难查。
#
# 也不能更早: 版本核对 (上面那两段 fail-loud) 没过的树不配进缓存。
if [ "$FROM_CACHE" = "0" ]; then
    echo "  → 存源码缓存 (下次重跑跳过这 60 MB)..."
    tar czf "$CACHE_TAR.tmp" -C "$HERMES_SRC" . \
        && mv "$CACHE_TAR.tmp" "$CACHE_TAR" \
        && echo "    ✓ $CACHE_TAR ($(ls -lh "$CACHE_TAR" | awk '{print $5}'))" \
        || { echo "    ⚠ 存缓存失败, 不影响本次构建"; rm -f "$CACHE_TAR.tmp"; }
fi
