#!/bin/bash
# ⚠️ 已废弃 (P3.5.83 · 7/29) —— 不要再用这个脚本。
#
# 它的做法是把 resources/mac/ 改名成 mac.arm64.bak, 再往同一个位置写 x86_64
# 资源。于是"当前 resources/mac 里是哪个架构"完全靠人记, 而且 build 完必须
# 手工还原。
#
# 7/16 跑过这个脚本之后没有还原, 到 7/29 才发现 —— 中间十三天打出的每一个
# aarch64 dmg 内嵌的都是 x86 运行时。macOS 有 Rosetta 2 透明转译, 所以全程
# 没有任何报错, 只是"慢"; 企业若禁装 Rosetta 则 hermes 直接起不来。
#
# 现在两个架构的资源各有各的目录, 同时存在、互不覆盖:
#
#   # 1. 生成对应架构的资源 (只需在资源有更新时跑)
#   bash scripts/build-mac-resources.sh aarch64    # → resources/mac-aarch64/
#   bash scripts/build-mac-resources.sh x64        # → resources/mac-x64/
#
#   # 2. 打包 (自带架构校验, 不符直接失败, 不会出错包)
#   npm run tauri:build:arm64
#   npm run tauri:build:x64
#
# 保留本文件只为记录这段历史。要看正确做法见上面两条命令。
exit_deprecated() {
    echo "❌ 本脚本已废弃 · 会污染 resources/mac/ 导致架构错配"
    echo "   改用: bash scripts/build-mac-resources.sh x64 && npm run tauri:build:x64"
    exit 1
}
exit_deprecated

# ─────────────────────────────────────────────────────────────────
# 以下为历史实现, 已不执行 (上面 exit_deprecated 会先返回)
# ─────────────────────────────────────────────────────────────────
# BL-INTEL-DMG (7/16): 给 Intel Mac 员工 build 单独 dmg (Companion 桌面 app).
#
# 前提: Apple Silicon dmg 已 build 好 (~/Downloads/catfish-达华交付-0715/*aarch64.dmg).
# 这个 script 只补 x86_64 dmg. 后台跑 30-60 min.
#
# 用法:
#   nohup bash build-intel-dmg.sh > /tmp/intel-dmg.log 2>&1 &
#   disown
#
# 睡醒 verify:
#   grep -E "===" /tmp/intel-dmg.log
#   ls -lh ~/Downloads/catfish-达华交付-0715/*x64.dmg

set -euo pipefail

COMPANION="$HOME/person_task/catfish/edge/companion-app"
DELIVERY="$HOME/Downloads/catfish-达华交付-0715"
RESOURCES="$COMPANION/src-tauri/resources"

echo "=== Step 1 · rustup 加 x86_64-apple-darwin target ==="
rustup target add x86_64-apple-darwin

echo ""
echo "=== Step 2 · Swift binary 编 universal (lipo 合并双架构) ==="
cd "$COMPANION/src-tauri/swift"

# backup 老 arm64 binary
if [ -f catfish-calendar ]; then
    cp catfish-calendar catfish-calendar.arm64.bak 2>/dev/null || true
fi

# swiftc 多个 -target 不生成 universal, 只用最后一个. 必须分别编译 + lipo 合并.
swiftc catfish-calendar.swift \
       -framework EventKit -framework Foundation \
       -target x86_64-apple-macos11.0 \
       -O -o catfish-calendar-x64

swiftc catfish-calendar.swift \
       -framework EventKit -framework Foundation \
       -target arm64-apple-macos11.0 \
       -O -o catfish-calendar-arm

# lipo 合并
lipo -create -output catfish-calendar catfish-calendar-x64 catfish-calendar-arm
rm catfish-calendar-x64 catfish-calendar-arm

# verify universal
echo "Swift binary 架构:"
file catfish-calendar

echo ""
echo "=== Step 3 · Backup arm64 mac resources + 建 x86_64 版 ==="
cd "$RESOURCES"

# backup arm64 resources
if [ -d mac ]; then
    rm -rf mac.arm64.bak
    mv mac mac.arm64.bak
fi

mkdir mac
cd mac

# 7/16 BL-INTEL-DMG 国内下 GitHub Release 慢/不通 · 加 ghfast.top 代理.
# 若你在国外环境可以清空 GH_PROXY 直连.
GH_PROXY="${GH_PROXY:-https://ghfast.top/}"

# 统一下载函数 · retry 10 次 + 断点续传 + verify tar 完整性
download_with_retry() {
    local url="$1"
    local output="$2"
    local url_proxied="${GH_PROXY}${url}"
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        echo "[attempt $attempt/10] $output"
        # -C - 断点续传, --retry 3 内部小 retry
        if curl -fL --retry 3 --retry-delay 5 --retry-max-time 900 --continue-at - \
                -o "$output" "$url_proxied"; then
            # verify tar/zip 完整
            if [[ "$output" == *.gz ]] || [[ "$output" == *.tar.gz ]]; then
                if gzip -t "$output" 2>/dev/null; then
                    echo "  ✓ 下载完整 (gzip verify OK)"
                    return 0
                else
                    echo "  ✗ tar 坏 · 删了重下"
                    rm -f "$output"
                fi
            else
                echo "  ✓ 下载完整"
                return 0
            fi
        fi
        # 第 5 次后换直连试试
        if [ $attempt -eq 5 ] && [ -n "$GH_PROXY" ]; then
            echo "  switch to direct (no proxy)"
            url_proxied="$url"
        fi
        sleep 10
    done
    echo "!!! 下 10 次都挂: $url"
    return 1
}

# --- 3.1 · x86_64 uv (走 ghfast.top 加速) ---
UV_VERSION="0.4.30"
UV_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-apple-darwin.tar.gz"
download_with_retry "$UV_URL" "/tmp/uv-x64.tar.gz" || exit 1
tar -xzf /tmp/uv-x64.tar.gz -C /tmp/
cp /tmp/uv-x86_64-apple-darwin/uv ./uv
chmod +x ./uv
ls -lh ./uv

# --- 3.2 · x86_64 cpython (走 ghfast.top 加速 · 大 26MB · 国内网易断) ---
PYTHON_VERSION="3.11.15"
PYTHON_BUILD_TAG="20260623"
PY_FILE="cpython-${PYTHON_VERSION}+${PYTHON_BUILD_TAG}-x86_64-apple-darwin-install_only.tar.gz"
CPYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD_TAG}/${PY_FILE}"
download_with_retry "$CPYTHON_URL" "cpython-${PYTHON_VERSION}-embed.tar.gz" || exit 1
ls -lh "cpython-${PYTHON_VERSION}-embed.tar.gz"

# --- 3.3 · install.sh 跟架构无关, 从 arm64 backup copy ---
cp "$RESOURCES/mac.arm64.bak/install.sh" ./install.sh
chmod +x ./install.sh

# --- 3.4 · hermes-agent-bundle.tar.gz 是 Python 源码, 跟架构无关 ---
cp "$RESOURCES/mac.arm64.bak/hermes-agent-bundle.tar.gz" ./hermes-agent-bundle.tar.gz

echo "x86_64 mac resources 就绪:"
ls -lh "$RESOURCES/mac/"

echo ""
echo "=== Step 4 · Tauri build --target x86_64-apple-darwin (30-60 min) ==="
cd "$COMPANION"
npm run tauri build -- --target x86_64-apple-darwin 2>&1

INTEL_DMG="$COMPANION/src-tauri/target/x86_64-apple-darwin/release/bundle/dmg/Catfish Companion_0.18.0_x64.dmg"

if [ -f "$INTEL_DMG" ]; then
    echo ""
    echo "=== Step 5 · Copy Intel dmg 到 delivery ==="
    cp "$INTEL_DMG" "$DELIVERY/"
    ls -lh "$DELIVERY/Catfish Companion_0.18.0_x64.dmg"
else
    echo "!!! Intel dmg 没生成 · 检查 log 找 error"
    exit 1
fi

echo ""
echo "=== Step 6 · 恢复 arm64 resources (防污染 Apple Silicon build 环境) ==="
cd "$RESOURCES"
rm -rf mac
mv mac.arm64.bak mac

echo ""
echo "=== DONE ==="
echo "delivery 里 3 个 client:"
ls -lh "$DELIVERY/"*.dmg "$DELIVERY/"*.msi 2>&1
