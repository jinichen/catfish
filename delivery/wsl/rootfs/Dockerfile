# Windows 中央端的 WSL2 发行版 (10/20): Debian 12 + Docker 引擎, 客户机不用装 Docker Desktop。
#
# 为什么不用 Docker Desktop: 员工 > 250 人或年收入 > 1000 万美元的公司要买订阅 (客户多半在这个范围),
# 而且它的离线安装包要单独分发。WSL2 是 Windows 自带的, 这里只带一个装好 Docker 引擎的 Linux。
#
# 产物 = `docker export` 出来的根文件系统 tar.gz, 客户机上 `wsl --import` 导入 (见 build-rootfs.sh)。
# 中央服务本身照旧用交付包里的镜像 + setup.sh, 跟 Linux 服务器同一套, 不另维护。
FROM debian:12-slim

ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn

# 构建机在国内, 官方源慢且常断 —— 走清华镜像 (debian + docker-ce 都有)
RUN sed -i "s#deb.debian.org#${APT_MIRROR}#g" /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg iproute2 iptables procps python3 openssl tar gzip less \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL "https://${APT_MIRROR}/docker-ce/linux/debian/gpg" -o /etc/apt/keyrings/docker.asc \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://${APT_MIRROR}/docker-ce/linux/debian bookworm stable" \
      > /etc/apt/sources.list.d/docker.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-compose-plugin \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# WSL2 内核的 nftables 兼容性不稳, Docker 官方 WSL 文档建议切 iptables-legacy, 否则 dockerd 建网桥会失败
RUN update-alternatives --set iptables /usr/sbin/iptables-legacy \
 && update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy

COPY wsl.conf /etc/wsl.conf
COPY catfish-start.sh /usr/local/bin/catfish-start.sh
RUN chmod 0755 /usr/local/bin/catfish-start.sh \
 && mkdir -p /opt/catfish /var/log/catfish \
 && docker --version > /etc/catfish-wsl-versions \
 && docker compose version >> /etc/catfish-wsl-versions \
 && containerd --version >> /etc/catfish-wsl-versions
