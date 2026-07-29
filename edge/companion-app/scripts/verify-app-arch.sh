#!/usr/bin/env bash
# 验证打出来的 .app 里, 内嵌资源的架构跟目标架构一致。
#
# ── 为什么要有这一步 ────────────────────────────────────────────────
#
# Companion 的 dmg 里内嵌了一整套离线运行时 (uv / cpython / node / hermes /
# chromium), 它们**必须跟 dmg 的目标架构一致**。而这件事以前没有任何检查:
#
#   2026-07-16 打 Intel 包时把 resources/mac/ 换成了 x86_64 版, 打完没还原。
#   之后十三天里打出的每一个 aarch64 dmg, 内嵌的都是 x86 运行时。
#   全程没有一处报错 —— macOS 的 Rosetta 2 会透明转译, 于是"装得上、跑得动,
#   只是慢", 直到 7/29 手工 file 一下才发现。
#
# 企业环境里若禁装 Rosetta, 这种包会直接起不来 hermes, 而现场只会看到
# "副手没反应", 根本联想不到是打包时装错了架构。
#
# 所以这里检查的是**产物**而不是过程: 不管构建脚本怎么写、有没有人忘了还原,
# 只认最终 .app 里那个二进制到底是什么架构。
#
# 用法:
#   bash scripts/verify-app-arch.sh <aarch64|x64> <path/to/Catfish Companion.app>

set -uo pipefail

ARCH="${1:-}"
APP="${2:-}"

if [ -z "$ARCH" ] || [ -z "$APP" ]; then
    echo "❌ 用法: $0 <aarch64|x64> <Catfish Companion.app 路径>"
    exit 1
fi

case "$ARCH" in
    aarch64) WANT="arm64" ;;
    x64)     WANT="x86_64" ;;
    *)       echo "❌ 架构只能是 aarch64 或 x64, 收到: $ARCH"; exit 1 ;;
esac

RES="$APP/Contents/Resources/resources/mac"

echo "── 验证 .app 内嵌资源架构 (期望 $WANT) ──"
echo "   $RES"

FAIL=0

# 1 · P3.5.85 起, .app 里只放 install.sh + uv 两个文件。
#
# 四个运行时大包 (cpython / hermes / node / chromium) 已挪出 .app —— Apple
# 公证会递归解开归档检查里面的 Mach-O, 那上千个二进制大多只有 ad-hoc 签名,
# 一律判 Invalid。挪出去之后公证扫描范围只剩这两个裸文件。
#
# 它们改由 Companion 首次装机时下载到 ~/.catfish/runtime/。
for f in install.sh uv; do
    if [ ! -f "$RES/$f" ]; then
        echo "  ❌ 缺 $f"
        FAIL=1
    fi
done

# 反过来也要查: 那四个包**不该**再出现在 .app 里。
# 漏掉的话公证照样失败, 而失败信息是一长串 Apple 的归档路径, 很难一眼看出
# "是打包配置没改干净"。
for f in cpython-3.11.15-embed.tar.gz hermes-agent-bundle.tar.gz \
         node-embed.tar.gz chromium-embed.tar.gz; do
    if [ -f "$RES/$f" ]; then
        echo "  ❌ $f 不该打进 .app (公证会因它失败) —— 检查 tauri.<arch>.conf.json"
        FAIL=1
    fi
done

if [ "$FAIL" = "1" ]; then
    echo ""
    echo "  资源没被打进去 —— 多半是 build 时没带架构配置覆盖。"
    echo "  用 npm run tauri:build:arm64 或 npm run tauri:build:x64, 别直接 tauri build。"
    exit 1
fi

# 2 · uv 是 Mach-O 二进制, 直接看架构
UV_ARCH="$(file -b "$RES/uv" 2>/dev/null || true)"
if ! echo "$UV_ARCH" | grep -q "$WANT"; then
    echo "  ❌ uv 架构不符: 期望 $WANT, 实际 → $UV_ARCH"
    FAIL=1
else
    echo "  ✓ uv       $WANT"
fi


echo ""
if [ "$FAIL" = "1" ]; then
    echo "❌ 架构校验不过 —— 这个包不能发。"
    echo "   资源目录: src-tauri/resources/mac-$ARCH/"
    echo "   重新生成: bash scripts/build-mac-resources.sh $ARCH"
    exit 1
fi

echo "✅ 架构校验通过 · .app 内嵌运行时全是 $WANT"
