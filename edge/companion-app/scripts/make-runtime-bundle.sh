#!/usr/bin/env bash
# 打「离线运行时包」—— 给上不了公网的员工机用。
#
# ── 什么时候需要它 ──────────────────────────────────────────────────
#
# Companion 装 hermes 时, resolve_runtime_dir 在两个候选里挑**归档最全**的
# (并列时取 .app):
#     1. .app 内的 resources/mac/      ← 正常发版这里就是全的
#     2. ~/.catfish/runtime/           ← 本脚本产出的东西解压到这里
#     都不全 → install.sh 联网补 (clone + npm ci + 下 python/node/chromium)
#
# ── 那还要这个包干嘛 ────────────────────────────────────────────────
#
# 7/30 之后 .app 本身已经带全了七件套 (公证也验证过能过), 所以**正常发版的
# dmg 装机就是离线的**, 这个包不是必需品。
#
# 它现在的用途只剩两个:
#   - 手上是**老版瘦 dmg** (P3.5.85~7/29 那批只带 install.sh + uv) 的机器,
#     补一份运行时进去, 免得联网装
#   - .app 里的归档损坏 / 被安全软件隔离, 拿它顶上
#
# 历史: 7/29 曾为过 Apple 公证把四个大包挪出 .app (公证会递归解开归档检查
# 内部 Mach-O, 那上千个 ad-hoc 签名的二进制一律判 Invalid), 那时候这个包是
# 内网机器的**唯一**出路。7/30 合入的新打包方式把它们放回去并通过了公证
# (票据 53d2baf7-c824-458e-9efd-df9c1a06f30a), 所以那个约束已经解除。
#
# ── 用法 ────────────────────────────────────────────────────────────
#
#   bash scripts/make-runtime-bundle.sh aarch64
#   bash scripts/make-runtime-bundle.sh x64
#
# 前提: 对应架构的资源已生成 (bash scripts/build-mac-resources.sh <arch>)

set -uo pipefail

ARCH="${1:-}"
case "$ARCH" in
    aarch64) WANT="arm64"  ; NODE_WANT="darwin-arm64" ;;
    x64)     WANT="x86_64" ; NODE_WANT="darwin-x64"   ;;
    *)
        echo "❌ 用法: $0 <aarch64|x64>"
        exit 1
        ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC="$APP_ROOT/src-tauri/resources/mac-$ARCH"
VERSION="$(node -p "require('$APP_ROOT/package.json').version" 2>/dev/null || echo 0.0.0)"
OUT="${2:-$HOME/Downloads/catfish-runtime-$ARCH-$VERSION.tar.gz}"

echo "── 打离线运行时包 ($ARCH) ──"
echo "   源: $SRC"

if [ ! -d "$SRC" ]; then
    echo "❌ 资源目录不存在: $SRC"
    echo "   先生成: bash scripts/build-mac-resources.sh $ARCH"
    exit 1
fi

# ── 齐全性 ──────────────────────────────────────────────────────────
#
# 七个都要有。缺了的话员工机会**静默退回联网安装** —— 而这个包存在的意义
# 恰恰是那台机器上不了网, 于是表现成"装到一半卡住", 现场查不到原因。
FILES=(install.sh uv cpython-3.11.15-embed.tar.gz
       hermes-agent-bundle.tar.gz node-embed.tar.gz chromium-embed.tar.gz
       catfish-email-dist.tar.gz)
MISS=0
for f in "${FILES[@]}"; do
    if [ ! -f "$SRC/$f" ] || [ "$(stat -f%z "$SRC/$f" 2>/dev/null || stat -c%s "$SRC/$f" 2>/dev/null || echo 0)" -lt 1024 ]; then
        echo "  ❌ 缺 / 太小: $f"
        MISS=1
    fi
done
if [ "$MISS" = "1" ]; then
    echo ""
    echo "   重新生成: bash scripts/build-mac-resources.sh $ARCH"
    exit 1
fi

# ── 架构 ────────────────────────────────────────────────────────────
#
# 跟 verify-app-arch.sh 同样的道理: 只认文件里实际是什么, 不信目录名。
# 7/16 那次就是目录名对、内容是另一个架构, 十三天没人发现。
FAIL=0
UV_ARCH="$(file -b "$SRC/uv" 2>/dev/null || true)"
echo "$UV_ARCH" | grep -q "$WANT" \
    && echo "  ✓ uv       $WANT" \
    || { echo "  ❌ uv 架构不符: 期望 $WANT, 实际 → $UV_ARCH"; FAIL=1; }

NODE_DIR="$(tar tzf "$SRC/node-embed.tar.gz" 2>/dev/null | head -1 || true)"
echo "$NODE_DIR" | grep -q "$NODE_WANT" \
    && echo "  ✓ node     $NODE_WANT" \
    || { echo "  ❌ node 架构不符: 期望 $NODE_WANT, 实际 → ${NODE_DIR:-<读不出>}"; FAIL=1; }

if [ "$FAIL" = "1" ]; then
    echo ""
    echo "❌ 架构校验不过 —— 这个包不能发。"
    echo "   重新生成: bash scripts/build-mac-resources.sh $ARCH"
    exit 1
fi

# ── hermes 版本 (装机后要靠它做版本比对) ───────────────────────────
HV="$(tar xzf "$SRC/hermes-agent-bundle.tar.gz" -O hermes-agent-src/.catfish-hermes-version 2>/dev/null | tr '\n' ' ' || true)"
if [ -n "$HV" ]; then
    echo "  ✓ hermes   $HV"
else
    echo "  ⚠ hermes bundle 里没有 .catfish-hermes-version"
    echo "     装机后 Companion 读不出已装版本, 会当成"版本未知"每次都重装。"
    echo "     用新版 build-mac-resources.sh 重新生成即可。"
fi

# ── 打包 ────────────────────────────────────────────────────────────
#
# 解开就是七个文件平铺, 直接对应 ~/.catfish/runtime/ 的布局 ——
# 不带顶层目录, 免得员工解出一层 catfish-runtime-xxx/ 还要再挪一次。
echo ""
echo "→ 打包 → $OUT"
mkdir -p "$(dirname "$OUT")"
tar czf "$OUT" -C "$SRC" "${FILES[@]}" || { echo "❌ tar 失败"; exit 1; }

SIZE="$(ls -lh "$OUT" | awk '{print $5}')"
echo ""
echo "✅ 完成 · $OUT ($SIZE)"
echo ""
echo "员工机上怎么用 (内网隔离时):"
echo "    mkdir -p ~/.catfish/runtime"
echo "    tar xzf $(basename "$OUT") -C ~/.catfish/runtime"
echo "    # 然后打开 Companion, 它会用这里的包装 hermes, 全程不联网"
echo ""
echo "能上公网的机器不需要这个包 —— 直接装 dmg, Companion 会自己联网装。"
