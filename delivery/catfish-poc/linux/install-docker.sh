#!/usr/bin/env bash
# 离线装 Docker 引擎 + compose 插件 (10/21) —— Linux 服务器没有 Docker、又不能上外网时用。
#
#   sudo bash linux/install-docker.sh
#
# setup.sh 发现这台机器没有 Docker 时会自动调它 (用 root 跑 setup.sh 的话); 也可以先单独跑。
#
# 装的是同目录下的 Docker 官方静态包 (docker.tgz) 和 compose 插件 (docker-compose),
# 版本见 docker-version.txt。不依赖发行版的软件源, Ubuntu / Debian / CentOS / RHEL /
# 麒麟 / 统信 都是同一套 (官方文档 docs.docker.com/engine/install/binaries/ 的做法):
#   · 二进制 → /usr/bin
#   · compose → /usr/local/lib/docker/cli-plugins/docker-compose
#   · systemd 单元 containerd.service / docker.service / docker.socket → /etc/systemd/system
#   · 建 docker 组, 用 sudo 跑时把当前用户加进去 (重新登录后生效)
#   · 设开机自启并启动
#
# 不会碰已有的 Docker:
#   · 已经有 docker 命令 → 不装引擎 (系统包装的 docker-ce / docker.io 不覆盖, 免得跟包管理器打架)
#     只在缺 compose 插件时补一个插件
#   · `docker` 其实是 podman (麒麟 / 统信 / RHEL 上常见的 podman-docker) → 停下说明, 不往下装
# 可以反复跑: 装过了就只检查、启动。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR=/usr/local/lib/docker/cli-plugins
UNIT_DIR=/etc/systemd/system

say() { echo "$@"; }
die() {
    echo "❌ $1" >&2
    shift
    for line in "$@"; do echo "   $line" >&2; done
    exit 1
}

[ "$(uname -s)" = "Linux" ] || die "这个脚本只给 Linux 服务器用 (当前: $(uname -s))"
[ "$(id -u)" = "0" ] || die "要用 root 跑:" "sudo bash $0"

# ── 架构要对得上 ─────────────────────────────────────────────
WANT_ARCH=$(sed -n 's/^arch=//p' "$HERE/docker-version.txt" 2>/dev/null || true)
case "$(uname -m)" in
    x86_64|amd64)  HOST_ARCH=amd64 ;;
    aarch64|arm64) HOST_ARCH=arm64 ;;
    *)             HOST_ARCH="$(uname -m)" ;;
esac
if [ -n "$WANT_ARCH" ] && [ "$WANT_ARCH" != "$HOST_ARCH" ]; then
    die "交付包是 $WANT_ARCH 的, 这台服务器是 $HOST_ARCH —— 装不了" \
        "x86_64 服务器用 catfish-poc-FULL-amd64-*.tar.gz" \
        "ARM 服务器 (鲲鹏 / 飞腾 / Graviton) 用 catfish-poc-FULL-arm64-*.tar.gz"
fi

install_compose_plugin() {
    [ -f "$HERE/docker-compose" ] || die "缺 $HERE/docker-compose (交付包不完整)"
    mkdir -p "$PLUGIN_DIR"
    install -m 0755 "$HERE/docker-compose" "$PLUGIN_DIR/docker-compose"
    say "  ✓ compose 插件 → $PLUGIN_DIR/docker-compose"
}

wait_docker() {
    local i
    for i in $(seq 1 30); do
        docker info >/dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
}

# ── 已经有 docker 命令: 不装引擎 ─────────────────────────────
if command -v docker >/dev/null 2>&1; then
    if docker --version 2>/dev/null | grep -qi podman; then
        die "这台机器上的 docker 命令其实是 podman ($(command -v docker))" \
            "中央服务要的是 Docker 引擎 (compose 网络 / 健康检查跟 podman 不完全一样)。" \
            "先卸掉 podman-docker 再重跑, 例如:" \
            "  yum remove -y podman-docker      (CentOS / RHEL / 麒麟)" \
            "  apt-get remove -y podman-docker  (Ubuntu / Debian / 统信)"
    fi
    say "→ 已经有 Docker: $(docker --version 2>/dev/null || echo "$(command -v docker)")"
    if ! docker compose version >/dev/null 2>&1; then
        say "→ 缺 compose 插件, 补上"
        install_compose_plugin
    fi
    if ! docker info >/dev/null 2>&1 && command -v systemctl >/dev/null 2>&1; then
        say "→ Docker 引擎没在跑, 启动"
        systemctl enable --now docker >/dev/null 2>&1 || true
        wait_docker || die "Docker 引擎起不来" "看原因: journalctl -u docker --no-pager | tail -50"
    fi
    say "✓ Docker 就绪: $(docker compose version 2>/dev/null | head -1)"
    exit 0
fi

# ── 下面是全新安装 ───────────────────────────────────────────
[ -f "$HERE/docker.tgz" ] || die "缺 $HERE/docker.tgz (交付包不完整)"
for f in /usr/bin/dockerd /usr/bin/containerd; do
    # 没有 docker 命令却有引擎二进制: 多半是装了一半的系统包。不覆盖, 让人先看清楚
    [ -e "$f" ] && die "没有 docker 命令, 但 $f 已经存在 —— 像是装了一半的 Docker" \
        "先确认它是哪来的 (rpm -qf $f 或 dpkg -S $f), 卸干净再重跑。"
done

# systemd: 开机自启和 socket 激活靠它。没有 systemd 的机器 (极少见) 不在这个脚本的范围
command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ] || \
    die "这台机器没有 systemd, 这个脚本没法设开机自启" \
        "可以手工跑: 解开 $HERE/docker.tgz 到 /usr/bin, 再后台起 containerd 和 dockerd"

# iptables: dockerd 靠它做端口映射和容器出网 (网关要出网调大模型 API)。缺了 dockerd 起不来
if ! command -v iptables >/dev/null 2>&1; then
    die "没有 iptables 命令 —— Docker 做端口映射和容器出网要用它" \
        "从内网软件源装上再重跑:" \
        "  yum install -y iptables        (CentOS / RHEL / 麒麟)" \
        "  apt-get install -y iptables    (Ubuntu / Debian / 统信)"
fi

# overlay 文件系统: 没有的话 Docker 退回 vfs 存储, 能跑但每个容器整份拷镜像, 又慢又占盘
if ! grep -qw overlay /proc/filesystems 2>/dev/null; then
    modprobe overlay 2>/dev/null || true
    grep -qw overlay /proc/filesystems 2>/dev/null || \
        say "  ⚠ 内核没有 overlay 文件系统, Docker 会用 vfs 存储 (能跑, 但慢且占磁盘)"
fi

say "→ 安装 Docker $(sed -n 's/^docker=//p' "$HERE/docker-version.txt" 2>/dev/null) (离线静态包)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
tar xzf "$HERE/docker.tgz" -C "$TMP"
for bin in "$TMP"/docker/*; do
    install -m 0755 "$bin" "/usr/bin/$(basename "$bin")"
done
say "  ✓ 二进制 → /usr/bin ($(ls "$TMP/docker" | tr '\n' ' '))"
install_compose_plugin

# systemd 单元: 照 moby / containerd 上游仓库里的官方单元写 (contrib/init/systemd/ 与 containerd.service)
cat > "$UNIT_DIR/containerd.service" <<'EOF'
[Unit]
Description=containerd container runtime
Documentation=https://containerd.io
After=network.target local-fs.target dbus.service

[Service]
ExecStartPre=-/sbin/modprobe overlay
ExecStart=/usr/bin/containerd
Type=notify
Delegate=yes
KillMode=process
Restart=always
RestartSec=5
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
OOMScoreAdjust=-999

[Install]
WantedBy=multi-user.target
EOF

cat > "$UNIT_DIR/docker.socket" <<'EOF'
[Unit]
Description=Docker Socket for the API

[Socket]
ListenStream=/run/docker.sock
SocketMode=0660
SocketUser=root
SocketGroup=docker

[Install]
WantedBy=sockets.target
EOF

cat > "$UNIT_DIR/docker.service" <<'EOF'
[Unit]
Description=Docker Application Container Engine
Documentation=https://docs.docker.com
After=network-online.target nss-lookup.target docker.socket firewalld.service containerd.service time-set.target
Wants=network-online.target containerd.service
Requires=docker.socket

[Service]
Type=notify
ExecStart=/usr/bin/dockerd -H fd:// --containerd=/run/containerd/containerd.sock
ExecReload=/bin/kill -s HUP $MAINPID
TimeoutStartSec=0
RestartSec=2
Restart=always
StartLimitBurst=3
StartLimitInterval=60s
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
Delegate=yes
KillMode=process
OOMScoreAdjust=-500

[Install]
WantedBy=multi-user.target
EOF
say "  ✓ systemd 单元 → $UNIT_DIR/{containerd.service,docker.service,docker.socket}"

getent group docker >/dev/null 2>&1 || groupadd -r docker
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    usermod -aG docker "$SUDO_USER"
    say "  ✓ 用户 $SUDO_USER 加进 docker 组 (重新登录后生效, 之后不加 sudo 也能用 docker)"
fi

systemctl daemon-reload
systemctl enable --now containerd.service docker.socket docker.service >/dev/null 2>&1 || true
if ! wait_docker; then
    die "Docker 装好了但引擎起不来" \
        "看原因: journalctl -u docker -u containerd --no-pager | tail -50" \
        "常见: SELinux 拦截 (getenforce 是 Enforcing 时临时 setenforce 0 试一下) / 内核太老 (要 3.10+, 推荐 4.x+)"
fi
say "✓ Docker 就绪: $(docker --version) · $(docker compose version | head -1) · 已设开机自启"
