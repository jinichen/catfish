#!/usr/bin/env bash
# 用**本机已验证的** hermes 树喂 build-mac-resources.sh 的源码缓存 (8/8).
#
# ## 什么时候用
#
# clone 不下来的时候。8/8 实录: 连续两次都死在完全相同的位置 ——
#
#     Receiving objects: 37% (3434/9241), 20.55 MiB | 854.00 KiB/s
#     error: RPC failed; curl 18 Transferred a partial file
#     fatal: fetch-pack: invalid index-pack output
#
# **两次断在同一个字节数**, 这就不是随机抖动了, 是中间有东西在按内容/大小掐。
# 换代理、重试、调 http.postBuffer 都是碰运气。
#
# 而我们手上本来就有一棵同 commit 的树 (~/.hermes/hermes-agent), 它刚跑完
# 54 项兼容审计、md5 跟独立下载的副本核对过。与其跟网络较劲, 不如用它。
#
# ## 为什么是 `git archive` 而不是 cp -R
#
# 那棵树是**在用**的: 里面有 venv (含 SQLite 修复的私有 Python)、node_modules、
# .hermes-runtime、各种 .catfish-* 标记。这些一样都不能进给员工的包。
#
# `git archive HEAD` 只吐**已跟踪文件**, 上面那些全是未跟踪的, 天然排除掉 ——
# 比手写一串 --exclude 可靠得多 (漏一个就是把 1 GB 的 venv 打进 dmg)。
#
# 额外把 `.git` 带上, 因为 build-mac-resources.sh 解开缓存之后还要跑
# `git describe` / `git rev-parse` 做落点核对 —— 那两道 fail-loud 检查不能跳。
#
# ## 用法
#
#   bash scripts/seed-hermes-src-cache.sh              # 用 ~/.hermes/hermes-agent
#   bash scripts/seed-hermes-src-cache.sh /path/to/tree
#
# 跑完直接跑 build-mac-resources.sh, 它会自己命中缓存并跳过 clone。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_TREE="${1:-$HOME/.hermes/hermes-agent}"

PINNED="$(tr -d '[:space:]' < "$APP_ROOT/.hermes-git-commit")"
CACHE_TAR="/tmp/catfish-hermes-src-${PINNED}.tar.gz"

# --no-optional-locks: 这棵树是**活的** (hermes 在跑)。`git status` 默认会刷新
# 并重写 index, 也就是往别人正在用的仓库里写东西。我们这里只是读, 不该有副作用。
GIT="git --no-optional-locks -c safe.directory=*"

echo "═══ 用本机树喂源码缓存 ═══"
echo "  源       : $SRC_TREE"
echo "  目标 pin : $PINNED"
echo "  缓存     : $CACHE_TAR"
echo ""

[ -d "$SRC_TREE/.git" ] || { echo "❌ $SRC_TREE 不是 git 仓库"; exit 1; }

echo "→ [1] commit 必须**正好**等于 pin..."
LOCAL_SHA="$($GIT -C "$SRC_TREE" rev-parse HEAD)"
if [ "$LOCAL_SHA" != "$PINNED" ]; then
    echo "  ✗ 对不上:"
    echo "     .hermes-git-commit : $PINNED"
    echo "     $SRC_TREE : $LOCAL_SHA"
    echo "  这棵树不是要打包的那一版, 拿它喂缓存就是打出一个错版本的包。"
    exit 1
fi
echo "  ✓ $LOCAL_SHA"
echo "  ✓ describe = $($GIT -C "$SRC_TREE" describe --tags 2>/dev/null || echo '?')"

# 这条比上面那条更容易被忽略, 但后果一样严重: commit 对得上, 不代表工作区
# 没被人改过。本机这棵树是天天在跑的, 谁临时改一行调试完忘了还原, 就会
# **悄悄进到发给员工的包里**, 而包上写的还是当前 pin 的上游版本。
echo "→ [2] 已跟踪文件不许有任何改动 (未跟踪的不算)..."
DIRTY="$($GIT -C "$SRC_TREE" status --porcelain --untracked-files=no)"
if [ -n "$DIRTY" ]; then
    echo "  ✗ 工作区有改动过的已跟踪文件:"
    printf '%s\n' "$DIRTY" | sed 's/^/     /'
    echo ""
    echo "  这些改动会原样进到给员工的包里, 而包上写的版本还是上游那个。"
    echo "  先还原 (git -C \"$SRC_TREE\" checkout -- .) 或者换一棵干净的树。"
    exit 1
fi
echo "  ✓ 干净"

echo "→ [3] git archive 导出已跟踪文件 (venv / node_modules 天然不在里面)..."
STAGE="$(mktemp -d /tmp/catfish-seed.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
$GIT -C "$SRC_TREE" archive --format=tar HEAD | tar x -C "$STAGE"
echo "  ✓ $(find "$STAGE" -type f | wc -l | tr -d ' ') 个文件, $(du -sh "$STAGE" | cut -f1)"

echo "→ [4] 带上 .git (落点核对要用 describe / rev-parse)..."
cp -R "$SRC_TREE/.git" "$STAGE/.git"
echo "  ✓ $(du -sh "$STAGE/.git" | cut -f1)"

echo "→ [5] 确认导出的树里没有那些不该进包的东西..."
BAD=0
for junk in venv node_modules .hermes-runtime .catfish-bootstrap-complete.json \
            .catfish-stage-ready .install_method; do
    if [ -e "$STAGE/$junk" ]; then echo "  ✗ 混进来了: $junk"; BAD=1; fi
done
[ $BAD -eq 0 ] || { echo "  ✗ 停 —— 这些不能发给员工"; exit 1; }
echo "  ✓ 干净"

echo "→ [6] 解出来的树自己核对一遍 (跟 build-mac-resources.sh 用同样的判据)..."
A_DESC="$($GIT -C "$STAGE" describe --tags --always 2>/dev/null || echo '<未知>')"
A_SHA="$($GIT -C "$STAGE" rev-parse HEAD 2>/dev/null || echo '<未知>')"
echo "  describe=$A_DESC  sha=$A_SHA"
[ "$A_SHA" = "$PINNED" ] || { echo "  ✗ 复核不过"; exit 1; }

echo "→ [7] 打包..."
tar czf "$CACHE_TAR.tmp" -C "$STAGE" . && mv "$CACHE_TAR.tmp" "$CACHE_TAR"
echo "  ✓ $CACHE_TAR ($(ls -lh "$CACHE_TAR" | awk '{print $5}'))"

echo ""
echo "═══ 好了 ═══"
echo "  现在跑 bash scripts/build-mac-resources.sh aarch64"
echo "  第 1 步会打 '↻ 命中源码缓存' 并跳过 clone。"
echo "  (想强制走网络: rm $CACHE_TAR)"
