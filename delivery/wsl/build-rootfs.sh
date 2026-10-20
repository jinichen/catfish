#!/usr/bin/env bash
# 打 Windows 中央端用的 WSL 发行版根文件系统 (10/20)。
#
#   bash delivery/wsl/build-rootfs.sh [amd64|arm64] [输出目录]
#
# 产出: <输出目录>/catfish-wsl-rootfs-<arch>.tar.gz  (+ .sha256)
#   客户机: wsl --import CatfishCentral <装机目录> catfish-wsl-rootfs-amd64.tar.gz --version 2
#
# Windows 客户机都是 x64, 交付只用 amd64; arm64 只为在 Apple Silicon 上原生跑一遍整套装机做验证
# (amd64 在 Mac 上走 QEMU, 里面再跑 dockerd + 六个服务太慢)。两个架构同一个 Dockerfile。
set -euo pipefail

ARCH="${1:-amd64}"
OUT_DIR="${2:-$HOME/Downloads}"
case "$ARCH" in amd64|arm64) ;; *) echo "❌ 架构只能是 amd64 / arm64: $ARCH" >&2; exit 2 ;; esac

HERE="$(cd "$(dirname "$0")" && pwd)"
TAG="catfish-wsl-rootfs:$ARCH"
OUT="$OUT_DIR/catfish-wsl-rootfs-$ARCH.tar.gz"
mkdir -p "$OUT_DIR"

echo "→ 构建 $TAG (linux/$ARCH)"
# 构建机网络抖 (清华源偶发断开), 失败重试 3 次
ok=""
for try in 1 2 3; do
    if docker buildx build --platform "linux/$ARCH" --load -t "$TAG" "$HERE/rootfs"; then ok=1; break; fi
    echo "  构建第 $try 次失败, 30 秒后重试" >&2
    sleep 30
done
[ -n "$ok" ] || { echo "❌ 构建失败" >&2; exit 1; }

echo "→ 导出根文件系统"
cid=$(docker create --platform "linux/$ARCH" "$TAG" /bin/true)
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT
docker export "$cid" | gzip -9 > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"

# 打出来的包自己验一遍: 关键文件都在, 不然 Windows 那边导入后才发现缺东西
for must in etc/wsl.conf usr/local/bin/catfish-start.sh usr/bin/dockerd usr/bin/docker \
            usr/libexec/docker/cli-plugins/docker-compose usr/sbin/iptables-legacy etc/catfish-wsl-versions; do
    tar tzf "$OUT" | grep -qx "$must" || { echo "❌ 根文件系统里缺 $must" >&2; rm -f "$OUT"; exit 1; }
done
(cd "$OUT_DIR" && shasum -a 256 "$(basename "$OUT")" > "$(basename "$OUT").sha256")
echo "✓ $OUT ($(du -h "$OUT" | cut -f1))"
tar xzOf "$OUT" etc/catfish-wsl-versions | sed 's/^/   /'
