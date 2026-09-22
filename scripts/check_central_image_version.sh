#!/usr/bin/env bash
#
# 中央端六个镜像的版本号: 两份 compose 一致, 且跟源码对得上。
#
# ── 判据换过一次 (9/22) ────────────────────────────────────────────
#
# 旧判据: "VERSION 定下来之后 central/ 的源码改过吗" —— 用 git log -S 找
#         VERSION 值的引入提交, 再看那之后源码动没动。改了就要求人手动 bump。
#
# 新判据: "VERSION 里的内容哈希 == 现在源码算出来的哈希" —— 一次比对, 完。
#
# 换的原因不是旧判据不准, 是**它要求人做一件会被跳过的事**。旧判据红了之后,
# 人得决定"这次算 patch 还是 minor"然后手填。9/12 到 9/22 之间那十天里这件事
# 一次都没发生 —— 六个镜像一直是 0.1.2, 每次交付都一样。
#
# 现在版本号由 scripts/central_version.sh 算出来 (日期 + 源码内容哈希),
# 没有"填什么"这个问题, 只有"跑没跑" —— 而这条检查就是在问这个。红了的修法
# 只有一条命令, 不需要任何判断:
#
#     bash scripts/central_version.sh --write
#
# 顺带: 新判据不用 git log, 所以**不再需要完整历史** (浅克隆下旧判据会
# 静默判成"没改过", 得靠 fetch-depth: 0 兜)。CI 里那条可以去掉了。
#
# 退出码: 0 = OK / 1 = 不一致
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION_FILE="central/VERSION"
GEN="scripts/central_version.sh"
COMPOSES=("central/docker-compose.yml" "delivery/catfish-poc/docker-compose.yml")
IMAGES=(catfish-identity catfish-gateway catfish-skills-hub catfish-web catfish-mcp-registry catfish-wiki-hub)

[ -f "$VERSION_FILE" ] || { echo "❌ 找不到 $VERSION_FILE"; exit 1; }
[ -f "$GEN" ] || { echo "❌ 找不到 $GEN (版本号的算法在那里)"; exit 1; }

VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
[ -n "$VERSION" ] || { echo "❌ $VERSION_FILE 是空的"; exit 1; }

fail=0

# ── 1. 格式 ──────────────────────────────────────────────────────
# 钉住格式, 否则有人手填一个 "1.0" 进去, 下面的哈希比对会以一种很绕的方式红。
if ! printf '%s' "$VERSION" | grep -qE '^[0-9]{8}-[0-9a-f]{8}$'; then
    echo "❌ $VERSION_FILE 的格式不对: $VERSION"
    echo "   应该是 YYYYMMDD-<8位十六进制>, 由 $GEN 生成, 不要手填。"
    echo "   修: bash $GEN --write"
    fail=1
fi

# ── 2. 两份 compose 的六个 tag 都等于 VERSION ────────────────────
for f in "${COMPOSES[@]}"; do
    [ -f "$f" ] || { echo "❌ 找不到 $f"; fail=1; continue; }
    for img in "${IMAGES[@]}"; do
        got="$(grep -oE "image:[[:space:]]+${img}:[^[:space:]]+" "$f" | head -1 | sed "s|.*${img}:||" || true)"
        if [ -z "$got" ]; then
            echo "❌ $f 里没有 $img 的 image 行"; fail=1
        elif [ "$got" != "$VERSION" ]; then
            echo "❌ $f · $img:$got  ≠  VERSION($VERSION)"; fail=1
        fi
    done
done

# ── 3. VERSION 里的哈希跟源码现在算出来的一致 ────────────────────
#
# 这条替代了老的"改了源码记得 bump"。日期段不比 —— 同一份代码隔天重打
# 得到不同日期是正常的, 那不是错误。
WANT="$(bash "$GEN" --hash 2>/dev/null || true)"
GOT="${VERSION##*-}"
if [ -z "$WANT" ]; then
    echo "❌ 算不出源码哈希 ($GEN --hash 失败)"
    fail=1
elif [ "$WANT" != "$GOT" ]; then
    echo ""
    echo "❌ VERSION 跟镜像源码对不上:"
    echo "     VERSION 里写着   $GOT"
    echo "     源码现在算出来是 $WANT"
    echo ""
    echo "   也就是说: 镜像源码改过了, 但版本号还是上一次的。同版本号的两个"
    echo "   镜像在 docker images 里**无法区分** —— 装完看不出是哪一版。"
    echo ""
    echo "   修 (一条命令, 不需要任何判断):"
    echo "     bash $GEN --write"
    echo "   然后把 $VERSION_FILE 和两份 compose 的改动一起提交。"
    echo ""
    fail=1
fi

if [ "$fail" -eq 0 ]; then
    echo "✓ 版本号 $VERSION · 两份 compose 的 ${#IMAGES[@]} 个 tag 一致 · 跟源码对得上"
fi
exit "$fail"
