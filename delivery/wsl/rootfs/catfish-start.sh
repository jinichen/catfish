#!/usr/bin/env bash
# WSL 发行版里拉起中央服务 (10/20)。Windows 端开机任务和 setup-wsl.ps1 都调它, 重复调没副作用。
#
#   catfish-start.sh <服务器IP>
#
# 1. 把服务器 IP (Windows 的局域网 IP) 绑到本发行版的 lo 上。
#    setup.sh 自检用 http://<服务器IP>:8999 之类的地址, 容器里也有回连服务器 IP 的 (OIDC issuer)。
#    WSL2 是 NAT, 不绑的话这些请求要绕出去经 Windows 端口转发再回来 —— 依赖防火墙放行虚拟网卡,
#    各机器不一样。绑到 lo 上就在发行版里直接到 docker 发布的端口, 跟 Windows 怎么配无关。
# 2. dockerd 没在跑就拉起来 (没开 systemd, 见 /etc/wsl.conf), 等到能用为止。
#    容器全是 restart: unless-stopped, dockerd 起来它们就跟着起来, 这里不用 compose up。
set -euo pipefail

SERVER_IP="${1:-}"
LOG=/var/log/catfish/dockerd.log
mkdir -p /var/log/catfish

if [ -n "$SERVER_IP" ]; then
    if ! [[ "$SERVER_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        echo "❌ 服务器 IP 格式不对: $SERVER_IP" >&2
        exit 2
    fi
    if ! ip -4 addr show dev lo | grep -q " $SERVER_IP/"; then
        ip addr add "$SERVER_IP/32" dev lo
        echo "✓ 服务器 IP $SERVER_IP 已绑到发行版 lo"
    fi
fi

# cgroup v2 嵌套: 根 cgroup 里还有进程、又没往下放控制器时, runc 建容器报
#   "cannot enter cgroupv2 /sys/fs/cgroup/docker with domain controllers -- it is in threaded mode"。
# 有 systemd 的系统由 systemd 做这一步; 我们没开 systemd (见 /etc/wsl.conf), 新版 WSL 内核又只有 cgroup v2。
# 做法照官方 docker:dind 的入口脚本: 根里的进程挪进 /init 子组, 再把控制器全放给子组。只在这个状态下才动。
CG=/sys/fs/cgroup
if [ -f "$CG/cgroup.controllers" ] && [ -z "$(cat "$CG/cgroup.subtree_control")" ] && [ -n "$(cat "$CG/cgroup.controllers")" ]; then
    mkdir -p "$CG/init"
    xargs -rn1 < "$CG/cgroup.procs" > "$CG/init/cgroup.procs" 2>/dev/null || true
    sed -e 's/ / +/g' -e 's/^/+/' < "$CG/cgroup.controllers" > "$CG/cgroup.subtree_control"
    echo "✓ cgroup v2 控制器已下放 (无 systemd 环境)"
fi

if ! docker info >/dev/null 2>&1; then
    # 上次没正常退出 (Windows 重启 / wsl --shutdown) 留下的 pid 文件:
    # 重启后 PID 会被别的进程复用, dockerd 看到 containerd.pid 里的号「还活着」就干等它, 15 秒后放弃
    # ("failed to start containerd: timeout waiting for containerd to start")。10/20 模拟重启实测。
    # 只在 dockerd / containerd 都确实没在跑时清, 不碰正在用的。
    if ! pgrep -x dockerd >/dev/null && ! pgrep -x containerd >/dev/null; then
        rm -f /var/run/docker.pid /var/run/docker/containerd/containerd.pid
    fi
    if ! pgrep -x dockerd >/dev/null; then
        nohup dockerd >>"$LOG" 2>&1 &
    fi
    for _ in $(seq 1 60); do
        docker info >/dev/null 2>&1 && break
        sleep 1
    done
    if ! docker info >/dev/null 2>&1; then
        echo "❌ dockerd 60 秒还没起来, 最后几行日志:" >&2
        tail -20 "$LOG" >&2 || true
        exit 1
    fi
fi
echo "✓ Docker 引擎就绪: $(docker version --format '{{.Server.Version}}')"
