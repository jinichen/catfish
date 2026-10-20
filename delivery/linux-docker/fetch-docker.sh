#!/usr/bin/env bash
# 取 Linux 用的 Docker 离线安装文件 (10/21), 给中央端交付包带上。
#
#   bash delivery/linux-docker/fetch-docker.sh <amd64|arm64> <输出目录>
#
# 产出 (<输出目录> = 包里的 delivery/catfish-poc/linux/):
#   docker.tgz       Docker 官方静态包 (dockerd / containerd / runc / docker CLI …)
#   docker-compose   compose v2 插件 (单个静态二进制)
#   docker-version.txt
#
# 为什么要带: 国央企的服务器多半不通外网, 而 setup.sh 要求先有 Docker。原来交付包只给 Windows
# 带了 Docker (WSL 发行版里), Linux 服务器得客户 IT 自己想办法 —— 内网源里不一定有 docker-ce,
# 麒麟 / 统信这类系统的软件源里甚至可能只有 podman。
#
# 为什么用静态包而不是 deb / rpm: 客户系统有 Ubuntu / CentOS / 麒麟 / 统信, 包管理器各不一样,
# 一份 deb 一份 rpm 也盖不全。官方静态包不依赖发行版, 配三个 systemd 单元就能跑
# (官方文档 docs.docker.com/engine/install/binaries/ 就是这条路)。
#
# 版本跟 WSL 发行版里的对齐 (delivery/wsl/rootfs 打出来是 Docker 29.9.1 / Compose v5.6.0), 两边
# 装出来是同一个引擎。要升级时改下面的版本和 sha256。
#   · compose 的 sha256 取自 GitHub release 自带的 .sha256 文件
#   · docker 静态包官方不发校验值; 这里的值是 10/21 从 download.docker.com 和清华镜像
#     各下一份、两边一致后钉的
set -euo pipefail

DOCKER_VERSION="29.9.1"
COMPOSE_VERSION="5.6.0"

ARCH="${1:?用法: fetch-docker.sh <amd64|arm64> <输出目录>}"
OUT_DIR="${2:?用法: fetch-docker.sh <amd64|arm64> <输出目录>}"
case "$ARCH" in
    amd64) DARCH=x86_64
           DOCKER_SHA256="c158ecc55adb3afb01bbd3539ed3b997d20c26dbdc418080506cc1bf5243ff57"
           COMPOSE_SHA256="40343e21ca777173e69cff5dbafeb37c6f81f3b0d57d9e597f036e95eb63e76a" ;;
    arm64) DARCH=aarch64
           DOCKER_SHA256="3e93cc6d1a686456a3b18eefdde26467a837a7a2b257af22e27cba1dbc1b2018"
           COMPOSE_SHA256="733ec76717ceb59052a9609b9dadfb523b2df8eab57a54212872d10a58078ea2" ;;
    *) echo "❌ 架构只能是 amd64 / arm64: $ARCH" >&2; exit 2 ;;
esac

DOCKER_NAME="docker-$DOCKER_VERSION-$DARCH.tgz"
COMPOSE_NAME="docker-compose-$COMPOSE_VERSION-linux-$DARCH"
# 官方源在前, 清华镜像兜底 (构建机在国内, download.docker.com 偶尔很慢)
DOCKER_URLS="https://download.docker.com/linux/static/stable/$DARCH/docker-$DOCKER_VERSION.tgz
https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/static/stable/$DARCH/docker-$DOCKER_VERSION.tgz"
COMPOSE_URLS="https://github.com/docker/compose/releases/download/v$COMPOSE_VERSION/docker-compose-linux-$DARCH"

CACHE="${CATFISH_LINUX_DOCKER_CACHE:-$HOME/.cache/catfish-linux-docker}"
mkdir -p "$OUT_DIR" "$CACHE"

ok_sha() { [ -f "$1" ] && [ "$(shasum -a 256 "$1" | cut -d' ' -f1)" = "$2" ]; }

# fetch <缓存文件名> <sha256> <候选 URL 列表>
fetch() {
    local name="$1" sha="$2" urls="$3" url try
    ok_sha "$CACHE/$name" "$sha" && return 0
    for url in $urls; do
        echo "→ 下载 $name ← $url"
        rm -f "$CACHE/$name.part"
        for try in 1 2 3; do
            # 构建机代理偶发断流: -C - 断点续传
            if curl -fL --retry 3 --connect-timeout 20 -C - -o "$CACHE/$name.part" "$url"; then break; fi
            echo "  第 $try 次下载失败, 20 秒后重试" >&2
            sleep 20
        done
        if ok_sha "$CACHE/$name.part" "$sha"; then
            mv "$CACHE/$name.part" "$CACHE/$name"
            return 0
        fi
        echo "  ⚠ $name sha256 不对 (下载不完整或被篡改), 换下一个源" >&2
    done
    rm -f "$CACHE/$name.part"
    echo "❌ $name 取不到 (所有源都失败或 sha256 不对)" >&2
    return 1
}

fetch "$DOCKER_NAME" "$DOCKER_SHA256" "$DOCKER_URLS"
fetch "$COMPOSE_NAME" "$COMPOSE_SHA256" "$COMPOSE_URLS"

# 包里该有的几个二进制都在, 不然到客户机上解开才发现缺东西
for must in docker/dockerd docker/docker docker/containerd docker/containerd-shim-runc-v2 docker/runc docker/docker-proxy docker/docker-init; do
    tar tzf "$CACHE/$DOCKER_NAME" | grep -qx "$must" || { echo "❌ $DOCKER_NAME 里缺 $must" >&2; exit 1; }
done

cp "$CACHE/$DOCKER_NAME" "$OUT_DIR/docker.tgz"
cp "$CACHE/$COMPOSE_NAME" "$OUT_DIR/docker-compose"
chmod 0755 "$OUT_DIR/docker-compose"
{
    echo "docker=$DOCKER_VERSION"
    echo "compose=$COMPOSE_VERSION"
    echo "arch=$ARCH"
} > "$OUT_DIR/docker-version.txt"
echo "✓ Docker $DOCKER_VERSION + Compose v$COMPOSE_VERSION ($ARCH) → $OUT_DIR ($(du -sh "$OUT_DIR" | cut -f1))"
