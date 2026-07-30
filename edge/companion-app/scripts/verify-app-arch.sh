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

# 1 · 首启速度优先：七件东西都必须随架构包内嵌。归档来自固定版本资源
# 流水线；发布阶段仍须完成 Developer ID 签名、公证与 Gatekeeper 验证。
#
# 7/30 加 catfish-email-dist.tar.gz —— 之前它压根没被打包, 员工装完
# 邮件 tab 直接挂, 界面还提示去跑一个他机器上不存在的 install.sh。
# 现在缺它就不让发包。
for f in install.sh uv cpython-3.11.15-embed.tar.gz \
         hermes-agent-bundle.tar.gz node-embed.tar.gz chromium-embed.tar.gz \
         catfish-email-dist.tar.gz; do
    if [ ! -f "$RES/$f" ]; then
        echo "  ❌ 缺 $f"
        FAIL=1
    fi
done

for f in cpython-3.11.15-embed.tar.gz hermes-agent-bundle.tar.gz \
         node-embed.tar.gz chromium-embed.tar.gz; do
    if [ -f "$RES/$f" ] && ! gzip -t "$RES/$f" 2>/dev/null; then
        echo "  ❌ $f gzip 校验失败"
        FAIL=1
    fi
done

if [ -f "$RES/install.sh" ] && ! bash -n "$RES/install.sh"; then
    echo "  ❌ install.sh 语法校验失败"
    FAIL=1
fi

if [ "$FAIL" = "1" ]; then
    echo ""
    echo "  资源没被打进去 —— 多半是 build 时没带架构配置覆盖。"
    echo "  用 npm run tauri:build:arm64 或 npm run tauri:build:x64, 别直接 tauri build。"
    exit 1
fi

# 2 · 主程序、helper 与 uv 都必须包含目标架构。
MAIN_BIN="$APP/Contents/MacOS/catfish-companion-app"
CALENDAR_BIN="$APP/Contents/Resources/catfish-calendar"
for label_path in "主程序|$MAIN_BIN" "日历 helper|$CALENDAR_BIN" "uv|$RES/uv"; do
    label="${label_path%%|*}"
    path="${label_path#*|}"
    actual="$(file -b "$path" 2>/dev/null || true)"
    if ! echo "$actual" | grep -q "$WANT"; then
        echo "  ❌ $label 架构不符: 期望 $WANT, 实际 → $actual"
        FAIL=1
    else
        echo "  ✓ $label    $WANT"
    fi
done

# 3 · 抽取三个归档内的代表性 Mach-O 再检查，防止 arm64 包混入 x86 运行时。
check_tar_binary() {
    local archive="$1"
    local entry="$2"
    local label="$3"
    local temp
    temp="$(mktemp "${TMPDIR:-/tmp}/catfish-arch.XXXXXX")" || return 1
    if ! tar -xOzf "$archive" "$entry" > "$temp" 2>/dev/null; then
        echo "  ❌ $label 无法从归档读取: $entry"
        rm -f "$temp"
        FAIL=1
        return
    fi
    local actual
    actual="$(file -b "$temp" 2>/dev/null || true)"
    rm -f "$temp"
    if ! echo "$actual" | grep -q "$WANT"; then
        echo "  ❌ $label 架构不符: 期望 $WANT, 实际 → $actual"
        FAIL=1
    else
        echo "  ✓ $label    $WANT"
    fi
}

PY_ENTRY="python/bin/python3.11"
NODE_ENTRY="$(tar -tzf "$RES/node-embed.tar.gz" 2>/dev/null | awk '/\/bin\/node$/ {print; exit}')"
CHROME_ENTRY="$(tar -tzf "$RES/chromium-embed.tar.gz" 2>/dev/null | awk '/Google Chrome for Testing\.app\/Contents\/MacOS\/Google Chrome for Testing$/ {print; exit}')"

check_tar_binary "$RES/cpython-3.11.15-embed.tar.gz" "$PY_ENTRY" "Python"
check_tar_binary "$RES/node-embed.tar.gz" "$NODE_ENTRY" "Node.js"
check_tar_binary "$RES/chromium-embed.tar.gz" "$CHROME_ENTRY" "Chromium"

# Hermes 归档是源码主包；至少确认真正的源码入口存在。
if ! tar -tzf "$RES/hermes-agent-bundle.tar.gz" 2>/dev/null | \
     awk '$0 == "hermes-agent-src/pyproject.toml" { found=1 } END { exit !found }'; then
    echo "  ❌ Hermes 归档缺 pyproject.toml"
    FAIL=1
else
    echo "  ✓ Hermes 源码归档"
fi

# 旧的 uv-only 检查保留变量名兼容下游日志解析。
UV_ARCH="$(file -b "$RES/uv" 2>/dev/null || true)"


echo ""
if [ "$FAIL" = "1" ]; then
    echo "❌ 架构校验不过 —— 这个包不能发。"
    echo "   资源目录: src-tauri/resources/mac-$ARCH/"
    echo "   重新生成: bash scripts/build-mac-resources.sh $ARCH"
    exit 1
fi

echo "✅ 架构校验通过 · .app 内嵌运行时全是 $WANT"
