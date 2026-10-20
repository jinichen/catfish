#!/usr/bin/env bash
# 取 WSL 离线安装包 (10/20), 给 Windows 中央端交付包带上。
#
#   bash delivery/wsl/fetch-wsl-msi.sh <输出目录>
#
# 为什么要带: Windows Server 2022 / 老一些的 Win10 自带的是「收件箱版」WSL, 内核老、`wsl --version`
# 都没有; 新版 WSL 平时走 Microsoft Store / `wsl --install` 联网装, 客户内网机装不了。
# 微软在 GitHub 发布同一个 MSI (MIT 开源, 可再分发), 离线 `msiexec /i` 即可。
#
# 版本钉死 + 校验 sha256 (取自 GitHub release 的 digest), 不跟「最新」:
# 3.0 是 10 月才出的新大版本, 先用 2.x 线最后一版; 要升级时改下面两行并在 Windows 上实测。
set -euo pipefail

WSL_VERSION="2.7.14"
WSL_SHA256="db084e536279a59e90a26ec598d8aa8a4dff8309f41d078fd06242953ac1ebcd"
NAME="wsl.${WSL_VERSION}.0.x64.msi"
URL="https://github.com/microsoft/WSL/releases/download/${WSL_VERSION}/${NAME}"

OUT_DIR="${1:?用法: fetch-wsl-msi.sh <输出目录>}"
CACHE="${CATFISH_WSL_CACHE:-$HOME/.cache/catfish-wsl}"
mkdir -p "$OUT_DIR" "$CACHE"

ok_sha() { [ -f "$1" ] && [ "$(shasum -a 256 "$1" | cut -d' ' -f1)" = "$WSL_SHA256" ]; }

if ! ok_sha "$CACHE/$NAME"; then
    echo "→ 下载 $NAME"
    rm -f "$CACHE/$NAME.part"
    for try in 1 2 3; do
        # 构建机代理偶发断流: -C - 断点续传
        if curl -fL --retry 3 --connect-timeout 20 -C - -o "$CACHE/$NAME.part" "$URL"; then break; fi
        echo "  第 $try 次下载失败, 20 秒后重试" >&2
        sleep 20
    done
    mv "$CACHE/$NAME.part" "$CACHE/$NAME"
    ok_sha "$CACHE/$NAME" || { echo "❌ $NAME sha256 不对 (下载不完整或被篡改)" >&2; rm -f "$CACHE/$NAME"; exit 1; }
fi
cp "$CACHE/$NAME" "$OUT_DIR/wsl.x64.msi"
echo "$WSL_VERSION" > "$OUT_DIR/wsl-msi-version.txt"
echo "✓ WSL $WSL_VERSION MSI → $OUT_DIR/wsl.x64.msi ($(du -h "$OUT_DIR/wsl.x64.msi" | cut -f1))"
