#!/usr/bin/env bash
#
# 中央端六个镜像的版本号: 单一来源 + 改了源码就必须 bump。
#
# ── 为什么需要这个 ────────────────────────────────────────────────
#
# 9/12 (020f172) 把六个镜像统一钉成 0.1.2, 从那之后**每一次交付都还是
# 0.1.2** —— 不同的构建用了相同的版本号。后果:
#
#   · `docker images` 看不出装的是哪一版, 新旧都显示 0.1.2
#   · 回滚没有下手的地方 (旧镜像被 load 挤成 dangling, 只剩一个 ID)
#   · 两份 compose (central/ 和 delivery/) 各存一份 tag, 谁也不保证同步
#
# 这跟 9/20 在 Windows MSI 上丢掉一整天的是**同一类问题**: 安装程序看见
# 版本号相同就跳过覆盖, 然后报告安装成功。那次至少还能查 exe 时间戳。
#
# ── 判据 ──────────────────────────────────────────────────────────
#
# 跟 edge/companion-app/scripts/check_version_sync.sh 一个思路:
#
#   1. central/VERSION 是唯一来源, 两份 compose 的六个 tag 必须都等于它
#   2. VERSION 定下来之后, 只要 central/ 下的镜像源码改过, 就必须 bump
#
# 第 2 条用 `git log -S` 找 VERSION 这个值是哪个提交引入的, 再看那之后
# 镜像源码有没有动过。
#
# 退出码: 0 = OK / 1 = 要 bump 或不同步 / 2 = 没法判断 (浅克隆)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION_FILE="central/VERSION"
[ -f "$VERSION_FILE" ] || { echo "❌ 找不到 $VERSION_FILE"; exit 1; }
VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
[ -n "$VERSION" ] || { echo "❌ $VERSION_FILE 是空的"; exit 1; }

COMPOSES=("central/docker-compose.yml" "delivery/catfish-poc/docker-compose.yml")
IMAGES=(catfish-identity catfish-gateway catfish-skills-hub catfish-web catfish-mcp-registry catfish-wiki-hub)

fail=0
for f in "${COMPOSES[@]}"; do
    [ -f "$f" ] || { echo "❌ 找不到 $f"; fail=1; continue; }
    for img in "${IMAGES[@]}"; do
        got="$(grep -oE "image:[[:space:]]+${img}:[^[:space:]]+" "$f" | head -1 | sed "s|.*${img}:||" || true)"
        if [ -z "$got" ]; then
            echo "❌ $f 里没有 $img 的 image 行"
            fail=1
        elif [ "$got" != "$VERSION" ]; then
            echo "❌ $f · $img:$got  ≠  VERSION($VERSION)"
            fail=1
        fi
    done
done
[ "$fail" -eq 0 ] && echo "✓ 六个镜像 tag 在两份 compose 里都等于 VERSION($VERSION)"

# ── 源码改了就得 bump ──────────────────────────────────────────
#
# 浅克隆下 `git log -S` 只能看到被拉下来的那几个提交, 找不到引入点就会
# **静默判成"没改过"** —— 门还在, 但已经空了。所以先探, 探到就 exit 2
# 明确说"没法判断", 而不是给一个没根据的绿灯。
if [ "$(git rev-parse --is-shallow-repository 2>/dev/null || echo true)" = "true" ]; then
    echo "⚠ 浅克隆, 没法判断 VERSION 之后源码有没有改过。"
    echo "  CI 里给 actions/checkout 加 fetch-depth: 0。"
    exit 2
fi

INTRO="$(git log -S "$VERSION" --format=%H -- "$VERSION_FILE" | tail -1 || true)"
if [ -z "$INTRO" ]; then
    echo "⚠ 找不到 VERSION=$VERSION 是哪个提交引入的 (还没提交?), 跳过 bump 检查。"
    exit "$fail"
fi

SRC_PATHS=(central/identity-server central/llm-gateway central/skills-hub central/web central/mcp-registry central/wiki-hub)
EXISTING=()
for p in "${SRC_PATHS[@]}"; do [ -d "$p" ] && EXISTING+=("$p"); done

CHANGED="$(git log --oneline "${INTRO}..HEAD" -- "${EXISTING[@]}" | head -5 || true)"
if [ -n "$CHANGED" ]; then
    echo ""
    echo "❌ VERSION 还是 $VERSION, 但从它定下来之后中央端镜像源码改过:"
    echo "$CHANGED" | sed 's/^/     /'
    echo ""
    echo "   同版本号的两个镜像在 docker images 里**无法区分** —— 装完看不出"
    echo "   是哪一版, 也没法回滚。改法: 把 patch 位 +1 写进 central/VERSION,"
    echo "   两份 compose 的六个 tag 跟着改 (这个脚本会校验)。"
    exit 1
fi

echo "✓ VERSION($VERSION) 之后中央端镜像源码没动过, 不需要 bump"
exit "$fail"
