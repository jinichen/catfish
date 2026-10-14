#!/bin/bash
# 从 GitHub Release 取 Windows 会议转写组件包 (10/14)。
#
#   bash scripts/fetch-meeting-asr-release.sh <版本, 如 1.1.0> [输出目录, 默认 ~/catfish-components]
#
# build-meeting-asr-pack.yml 把包切成 1900MB 一段发到 Release `meeting-asr-<版本>` (单个文件上限 2GB),
# 附 .parts (分段清单) 和 .sha256。这里走 lib-download.sh (默认经 ghfast.top 加速, GH_PROXY="" 直连)
# 逐段下载、拼回、核对 sha256。比 gh run download 快: CI 构建产物要登录, 走不了加速代理 (实测 ~0.3MB/s)。
set -euo pipefail

VERSION="${1:?用法: $0 <版本> [输出目录]}"
OUT_DIR="${2:-$HOME/catfish-components}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/lib-download.sh"

REPO="jinichen/catfish"
F="meeting-asr-$VERSION-windows-x64.tar.gz"
BASE="https://github.com/$REPO/releases/download/meeting-asr-$VERSION"
WORK="$OUT_DIR/.fetch-$VERSION"
mkdir -p "$WORK"

download_with_retry "$BASE/$F.parts" "$WORK/$F.parts"
download_with_retry "$BASE/$F.sha256" "$WORK/$F.sha256"
while IFS= read -r part; do
    part="$(basename "$(printf '%s' "$part" | tr -d '\r')")"
    [ -n "$part" ] || continue
    # 已下完的段 (断点重跑) 不重下 —— 大小对不对由最后的 sha256 兜底
    [ -s "$WORK/$part" ] && { echo "  · 已有 $part"; continue; }
    download_with_retry "$BASE/$part" "$WORK/$part"
done < "$WORK/$F.parts"

echo "→ 拼接 + 核对 sha256"
tr -d '\r' < "$WORK/$F.parts" | xargs -n1 basename | (cd "$WORK" && xargs cat) > "$WORK/$F"
want="$(awk '{print $1}' "$WORK/$F.sha256")"
got="$(shasum -a 256 "$WORK/$F" | awk '{print $1}')"
[ "$want" = "$got" ] || { echo "❌ sha256 不对: 期望 $want, 实际 $got (删 $WORK 重下)"; exit 1; }
mv "$WORK/$F" "$OUT_DIR/$F"
rm -rf "$WORK"
echo "✓ $OUT_DIR/$F · $(du -h "$OUT_DIR/$F" | cut -f1) · sha256 $got"
