# 鲶鱼 Catfish 中央服务栈部署手册

**版本**: 0.18.0 (2026-07-14)
**目标读者**: 客户 IT 部署人员
**适用环境**: Ubuntu 22.04+ / Windows Server 2019+ / Windows 11 Pro
**服务架构**: 8 服务 Docker Compose 栈 (7 应用 + 1 反代)

---

## 目录

1. [架构概览](#1-架构概览)
2. [前置要求](#2-前置要求)
3. [Phase A: 制作部署包 (macOS 开发机)](#3-phase-a-制作部署包-macos-开发机)
4. [Phase B: 传输到目标机](#4-phase-b-传输到目标机)
5. [Phase C: 目标机部署](#5-phase-c-目标机部署)
6. [Phase D: 首次启动 + verify](#6-phase-d-首次启动--verify)
7. [员工端接入 (Companion msi)](#7-员工端接入-companion-msi)
8. [日常运维](#8-日常运维)
9. [升级流程](#9-升级流程)
10. [数据备份](#10-数据备份)
11. [故障排查](#11-故障排查)
12. [附录 A: 端口清单](#附录-a-端口清单)
13. [附录 B: `.env` 字段说明](#附录-b-env-字段说明)
14. [附录 C: 数据位置](#附录-c-数据位置)

---

## 1. 架构概览

### 8 服务清单

| # | Service | 端口 | 用途 | 依赖 |
|---|---------|------|------|------|
| 1 | **postgres** | 5432 (仅内网) | 数据库 | - |
| 2 | **identity** | 8998 | OIDC / SSO 认证 | postgres |
| 3 | **gateway** | 8999 | LLM 网关 (核心) | postgres, identity |
| 4 | **skills-hub** | 8997 | Skills 发布 / 分发 | postgres |
| 5 | **wiki-hub** | 8994 | 部门 Wiki 共享 | postgres |
| 6 | **mcp-registry** | 8996 | MCP 工具注册 | postgres |
| 7 | **web** | 内网 | Web UI (SPA) | - |
| 8 | **nginx** | 80 / 443 | 反代 + SSL 卸载 | 所有上层 service |

### 数据流

```
员工 Windows Companion → https://catfish.zdff.com.cn (nginx 443)
                          ├── /sso/    → identity  (SSO 登录)
                          ├── /v1/     → gateway   (LLM 调用)
                          ├── /hub/    → skills-hub
                          ├── /wiki/   → wiki-hub  (走 gateway 反代)
                          ├── /mcp/    → mcp-registry
                          └── /        → web (SPA UI)
```

### 数据本地化 (符合客户合规)

- **员工数据不出企业**: 所有 8 service 跑在**客户机房 Docker 主机**上, 员工数据 (对话 / wiki / skill) 全存客户 PG.
- **PG 只本机绑定** (`127.0.0.1:5432`): 防外网直连数据库.
- **OAuth token 不上中央** (P3.4.1 改造): 员工连 Jira / GitLab 等 MCP 的 OAuth token 存员工本机, 不进中央 secret-broker.
- **可断外网运行**: 若使用私有 LLM (内网 Qwen), 全栈可**断外网跑**. 公网 LLM (Gemini / DashScope) API key 留空即禁用.

---

## 2. 前置要求

### 目标机硬件 (客户机房)

| 项 | 最低 | 推荐 | 备注 |
|----|------|------|------|
| CPU | 4 vCPU | 8 vCPU | Gateway 默认 4 uvicorn worker |
| 内存 | 4 GB | 8 GB | 1000 员工峰值 ~1GB app + PG buffer |
| 磁盘 | 20 GB | 100 GB | image ~2GB, PG 数据增长 (员工对话 audit) |
| 网络 | 内网 | 内网 + 公网出口 | 员工连内网, 拉 Docker image 需公网 (或用本手册预 load 方案) |

### 目标机操作系统

**推荐**: Ubuntu Server 22.04 LTS  
**支持**: Windows Server 2019+ / Windows 11 Pro (走 Docker Desktop)

### 软件

**目标机**:
- Docker Engine 24.0+ 或 Docker Desktop 4.30+
- Docker Compose v2 (`docker compose` 命令, 不是 `docker-compose`)
- Bash (Ubuntu 自带 / Windows 装 Git for Windows 附赠 Git Bash)

**开发机 (macOS)**:
- Docker Desktop 4.30+
- 若 Apple Silicon (M1-M4), Docker Desktop 里可 build linux/amd64 (跨架构支持内置)

---

## 3. Phase A: 制作部署包 (macOS 开发机)

### 3.1 build 所有 image (Apple Silicon 强制 amd64)

```bash
cd ~/person_task/catfish/central

# 若 Apple Silicon, 装 QEMU 支持跨架构 (一次即可)
docker run --rm --privileged tonistiigi/binfmt --install all

# 强制 build 成 amd64 (Ubuntu server 是 x86_64)
export DOCKER_DEFAULT_PLATFORM=linux/amd64

# build 6 个 catfish 服务
docker compose build

# 拉 base image (postgres + nginx, 走 Hub)
docker pull --platform linux/amd64 postgres:16-alpine
docker pull --platform linux/amd64 nginx:1.27-alpine

# verify 8 个 image 都在
docker images | grep -E 'catfish|postgres|nginx'
```

**首次 build**: ~40-60 min (Apple Silicon QEMU 慢) / ~20 min (Intel Mac).

### 3.2 打包所有 image → tar

```bash
docker save -o ~/catfish-all-images.tar \
    catfish-identity:0.1.0 \
    catfish-gateway:0.1.0 \
    catfish-skills-hub:0.1.0 \
    catfish-wiki-hub:0.1.0 \
    catfish-mcp-registry:0.1.0 \
    catfish-web:0.1.0 \
    postgres:16-alpine \
    nginx:1.27-alpine

ls -lh ~/catfish-all-images.tar
# 预期: 1.5-2.5 GB
```

### 3.3 打包最小配置包 → tar

```bash
cd ~/person_task/catfish/central

tar -czf ~/catfish-central-minimal.tar.gz \
    docker-compose.yml \
    deploy.sh \
    .env.production.example \
    nginx.conf.example \
    identity-server/config/ \
    llm-gateway/config/

ls -lh ~/catfish-central-minimal.tar.gz
# 预期: 30-80 KB
```

**清单**:
- `docker-compose.yml` — 编排 8 service
- `deploy.sh` — 一键部署脚本
- `.env.production.example` — 环境变量模板
- `nginx.conf.example` — Nginx 反代模板
- `identity-server/config/` — identity 服务 config (clients/database/users yaml)
- `llm-gateway/config/` — gateway config (models/quotas/roles yaml)

**总传输量**: 约 **1.5-2.5 GB** (2 个 tar 文件).

---

## 4. Phase B: 传输到目标机

### 4.1 传输方式 (选一)

**方式 A: SCP (Ubuntu 目标机, 装了 SSH)**:
```bash
scp ~/catfish-all-images.tar user@target-ip:/tmp/
scp ~/catfish-central-minimal.tar.gz user@target-ip:/tmp/
```

**方式 B: rsync (带进度条)**:
```bash
rsync -avz --progress ~/catfish-all-images.tar user@target-ip:/tmp/
rsync -avz --progress ~/catfish-central-minimal.tar.gz user@target-ip:/tmp/
```

**方式 C: U 盘 / 云盘**:
- U 盘 8GB+ 就够
- 阿里云盘 / 百度云 (内网快)
- **微信文件传输**: 单文件 100MB 上限, **image tar 不行**, 仅 minimal 包可用

**方式 D: 内网 SMB 共享** (Windows 服务器):
- Windows 目标机开共享目录
- macOS Finder → "连接服务器" → `smb://target-ip/share/`

---

## 5. Phase C: 目标机部署

### 5.1 Ubuntu 目标机

#### 装 Docker (若没装)

```bash
# Ubuntu / Debian
curl -fsSL https://get.docker.com | sudo bash
sudo usermod -aG docker $USER
newgrp docker

# verify
docker --version
docker compose version
```

#### 建目录 + 解压

```bash
sudo mkdir -p /opt/catfish/central
sudo chown $USER /opt/catfish/central
cd /opt/catfish/central

# 解压最小配置包
tar -xzf /tmp/catfish-central-minimal.tar.gz

# load 所有 image
docker load -i /tmp/catfish-all-images.tar

# verify 8 image 都 loaded
docker images | grep -E 'catfish|postgres|nginx' | wc -l
# 期望: 8
```

### 5.2 Windows 目标机

#### 装 Docker Desktop

- 下载 https://www.docker.com/products/docker-desktop
- 装完启动, **保持 Linux containers 模式** (默认, 不要切 Windows)
- Windows 11 Pro 24H2 建议**Hyper-V backend** (Settings → General → 取消勾选 "Use the WSL 2 based engine")

#### 装 Git for Windows (为 Git Bash)

- https://git-scm.com/download/win
- 完事打开 **Git Bash**

#### 建目录 + 解压

Git Bash 里 (若你从 macOS scp 到 Windows C:\):

```bash
mkdir -p /c/catfish/central
cd /c/catfish/central

tar -xzf /c/catfish-central-minimal.tar.gz
docker load -i /c/catfish-all-images.tar
docker images | grep -E 'catfish|postgres|nginx'
```

### 5.3 配 `.env` (Ubuntu / Windows 通用)

```bash
cp .env.production.example .env
nano .env    # Ubuntu, 或 notepad .env (Windows)
```

**必改 4 项** (其他保持默认):

```bash
# 1. PostgreSQL 密码 (至少 16 位强密码)
PG_PASSWORD=xxxxxxxxxxxxxxxx

# 2. 内网 LLM API key (公司自申请)
INTERNAL_LLM_KEY=xxxxxxxxxx

# 3. OIDC issuer (公司自己的 SSO, 若走本机 identity 默认走 http://identity:8998)
CATFISH_OIDC_ISSUER=https://catfish.zdff.com.cn/sso

# 4. Skills Hub 共享 token (32+ 位随机)
SKILLS_HUB_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**生成随机字符串** (Ubuntu / Git Bash):
```bash
openssl rand -base64 24    # PG_PASSWORD 用
openssl rand -hex 32       # SKILLS_HUB_TOKEN 用
```

### 5.4 配 `nginx.conf` (若走 SSL)

```bash
cp nginx.conf.example nginx.conf
nano nginx.conf   # 改 server_name 为你的域名 (如 catfish.zdff.com.cn)

# 建 certs 目录
mkdir -p certs

# 放 SSL 证书 (Let's Encrypt 或公司自签)
# certs/cert.pem
# certs/key.pem
```

**首次跑无 SSL** (临时): 直接跳过 nginx.conf / certs, 走 http (docker-compose.yml 里 nginx 依赖 cert 会挂, 需临时注释掉 nginx service).

---

## 6. Phase D: 首次启动 + verify

### 6.1 一键部署 (推荐)

```bash
cd /opt/catfish/central     # Ubuntu
# 或 cd /c/catfish/central  # Windows Git Bash

bash deploy.sh
```

`deploy.sh` 会自动:
1. 体检 `.env` 必填项
2. 检查 Docker 状态 + 端口冲突 (80/443/8994/8996/8997/8998/8999)
3. `docker compose up -d --no-build` (用 loaded image, 不 rebuild)
4. 等所有 service healthcheck 绿 (超时 120s)
5. Smoke test (curl 各 endpoint)
6. 报最终状态

### 6.2 手工启动 (若不走 deploy.sh)

```bash
docker compose up -d --no-build

# 等 30-60s 让所有 service 起
docker compose ps
```

### 6.3 verify

```bash
# 看 service 状态
docker compose ps

# 期望: 8 container 全 running / healthy
# NAME                    STATUS                    PORTS
# catfish-postgres        Up 2 minutes (healthy)    127.0.0.1:5432->5432/tcp
# catfish-identity        Up 2 minutes (healthy)    127.0.0.1:8998->8998/tcp
# catfish-gateway         Up 2 minutes (healthy)    127.0.0.1:8999->8999/tcp
# catfish-skills-hub      Up 2 minutes (healthy)    127.0.0.1:8997->8997/tcp
# catfish-wiki-hub        Up 2 minutes (healthy)    127.0.0.1:8994->8994/tcp
# catfish-mcp-registry    Up 2 minutes (healthy)    127.0.0.1:8996->8996/tcp
# catfish-web             Up 2 minutes
# catfish-nginx           Up 2 minutes              0.0.0.0:80->80, 0.0.0.0:443->443

# 手工 healthcheck
curl http://localhost:8998/.well-known/openid-configuration  # identity
curl http://localhost:8999/healthz                            # gateway
curl http://localhost:8997/healthz                            # skills-hub
curl http://localhost:8994/healthz                            # wiki-hub
curl http://localhost:8996/health                             # mcp-registry
curl -k https://localhost/                                    # web via nginx (若配了 SSL)
```

**所有 curl 返 200/JSON = 部署成功**.

---

## 7. 员工端接入 (Companion msi)

### 7.1 分发 msi 给员工

从 CI (CircleCI / GitHub Actions) 下载最新 `Catfish Companion_0.18.0_x64_en-US.msi`, 通过公司 IT 分发系统 (SCCM / Intune) 或员工邮件推送.

### 7.2 员工首次启动配置

员工双击 msi 装完 (无需管理员权限), 首次启动 Companion, 会**弹出中央服务器地址填写框**:

```
请输入公司 Catfish 服务器地址:
[  https://catfish.zdff.com.cn  ]

□ 使用 SSL (推荐)

[取消]  [连接]
```

- **服务器地址**: 填客户机房部署的域名 (如 `https://catfish.zdff.com.cn`), 或直接 IP (`http://192.168.x.x`)
- 点 **连接**
- 会跳 SSO 登录页 (公司 IdP), 员工输公司账号密码

登录成功后, Companion 自动生成 `%LOCALAPPDATA%\Catfish Companion\.env`, 缓存服务器地址. **员工再次启动不需要重填**.

### 7.3 员工基础功能 verify

- 打开 Chat, 发一句 "你好", 应有 AI 回复 (走 gateway → 内网 LLM)
- 打开 Dashboard, 应看到 6 个卡片 (Services / Skills / Wiki / Todos / Style / Recent)
- Skills Hub 卡片能列出中央 skill (走 skills-hub)
- Wiki Hub 卡片能列出部门 wiki (走 gateway → wiki-hub)

---

## 8. 日常运维

### 8.1 常用命令

```bash
cd /opt/catfish/central   # 或 Windows 对应路径

# 看现状
docker compose ps
bash deploy.sh status

# 看某 service 日志 (tail -f)
docker compose logs -f gateway
docker compose logs -f identity | tail -50

# 重启某 service (不停别的)
docker compose restart gateway

# 关全栈 (保留 volume)
docker compose down
bash deploy.sh down

# 起全栈
docker compose up -d --no-build
```

### 8.2 监控关键指标

```bash
# 容器资源占用
docker stats

# PG 磁盘用量
docker exec catfish-postgres psql -U catfish -d catfish -c "SELECT pg_size_pretty(pg_database_size('catfish'));"

# nginx access log (若配了)
docker exec catfish-nginx tail -100 /var/log/nginx/access.log
```

### 8.3 日志轮转

Docker 默认 log driver 是 `json-file`, 长期跑会占磁盘. 建议配 `docker daemon.json`:

Ubuntu `/etc/docker/daemon.json`:
```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "5"
  }
}
```

改完 `sudo systemctl restart docker`.

---

## 9. 升级流程

### 9.1 只改代码, 不改架构

**macOS 上重 build 变更的 service** (只 build 一个更快):
```bash
cd ~/person_task/catfish/central
docker compose build gateway    # 只 rebuild gateway
```

**save 只这一个 image**:
```bash
docker save -o ~/catfish-gateway-v0.19.tar catfish-gateway:0.1.0
```

**传目标机 load + restart**:
```bash
scp ~/catfish-gateway-v0.19.tar user@target:/tmp/
```

Target:
```bash
docker load -i /tmp/catfish-gateway-v0.19.tar
docker compose up -d gateway   # 只重启 gateway, 其他服务不受影响
```

**传输量**: 单 image ~300MB (vs 全栈 2GB).

### 9.2 加新 service / 改 docker-compose

需要**重传 `catfish-central-minimal.tar.gz`** (含新的 docker-compose.yml):
```bash
tar -xzf catfish-central-minimal.tar.gz -C /opt/catfish/central/ --overwrite
docker compose up -d --no-build   # 应用新 compose
```

### 9.3 升级 base image (postgres / nginx)

在 macOS 上 `docker pull` 新版本, save 传目标机 load, 重启对应 service. 注意 postgres 大版本升级 (16 → 17) 需要 `pg_upgrade`, **别直接切 image**, 会数据损坏.

---

## 10. 数据备份

### 10.1 什么数据要备份

| Volume | 内容 | 大小估算 | 优先级 |
|--------|------|----------|--------|
| `pgdata` | PostgreSQL 所有数据 | 1-50 GB | 🔴 **最高** |
| `hubdata` | Skills Hub 上传的 skill 包 | 100 MB - 5 GB | 🟡 中 |
| `wikidata` | Wiki Hub FS 兜底 | 10-500 MB | 🟢 低 (PG 里也有) |
| `gateway_data` | Gateway 审计日志 | 100 MB - 2 GB | 🟡 中 |

### 10.2 备份命令

```bash
# 找 volume 物理路径
docker volume inspect pgdata
# "Mountpoint": "/var/lib/docker/volumes/pgdata/_data"

# 打包 (需要停 postgres 保证一致性)
docker compose stop postgres
sudo tar -czf /backup/pgdata-$(date +%Y%m%d).tar.gz \
    -C /var/lib/docker/volumes/pgdata _data
docker compose start postgres

# 或用 pg_dump (不用停机, 增量)
docker exec catfish-postgres pg_dump -U catfish -Fc catfish \
    > /backup/catfish-$(date +%Y%m%d).dump
```

### 10.3 自动化备份

`/etc/cron.daily/catfish-backup`:
```bash
#!/bin/bash
DATE=$(date +%Y%m%d)
BACKUP_DIR=/backup/catfish

mkdir -p $BACKUP_DIR
docker exec catfish-postgres pg_dump -U catfish -Fc catfish \
    > $BACKUP_DIR/pg-$DATE.dump

# 保留最近 30 天
find $BACKUP_DIR -name "pg-*.dump" -mtime +30 -delete
```

`chmod +x /etc/cron.daily/catfish-backup`.

### 10.4 恢复

```bash
docker exec -i catfish-postgres pg_restore -U catfish -d catfish \
    < /backup/pg-20260714.dump
```

---

## 11. 故障排查

### 11.1 常见 blocker

| 错误 | 原因 | 解法 |
|------|------|------|
| `docker: command not found` | Docker 没装 | 走 [Phase C 5.1](#51-ubuntu-目标机) 装 Docker |
| `port 8999 already in use` | 端口冲突 | `sudo lsof -i :8999` 找占用, 关掉或改 docker-compose ports |
| `no matching manifest for linux/amd64` | image 架构不匹配 | macOS 上 `docker pull --platform linux/amd64` 重 pull + 重 save |
| `catfish-postgres unhealthy` | PG 密码错 / 磁盘满 | `docker compose logs postgres` 看具体 |
| `gateway 起不来, connection refused to identity` | identity 没起 / OIDC config 错 | 先看 identity `docker compose logs identity` |
| `nginx exited (1)` | `nginx.conf` 或 cert 缺 | 临时注释 nginx service, 或 `cp nginx.conf.example nginx.conf` |
| `403 forbidden` on Docker Hub pull | 大陆网络 | 走本手册**预 load image tar** 方案, 不再 pull Hub |
| `员工 Companion 连不上` | 服务器地址 / SSO / 防火墙 | 员工机 `curl https://catfish.zdff.com.cn/healthz` 验通 |

### 11.2 全栈 debug

```bash
# 看 8 service 状态
docker compose ps
# 若有 "unhealthy" 或 "exited"

# 具体 log
docker compose logs <service-name> --tail 100

# 进 container debug
docker compose exec gateway sh
# 里面 curl / cat 配置

# 完整 stack down + 重来
docker compose down
docker compose up -d --no-build
```

### 11.3 数据完全清空重来 (⚠️ 慎用)

```bash
docker compose down -v          # -v 删所有 volume
docker system prune -a -f       # 删所有 image (含 base)
# 然后从 Phase C 重来
```

---

## 附录 A: 端口清单

| 端口 | Service | 暴露范围 | 用途 |
|------|---------|----------|------|
| 5432 | postgres | 127.0.0.1 (本机) | 数据库 |
| 8994 | wiki-hub | 127.0.0.1 (本机) | Wiki 服务 |
| 8996 | mcp-registry | 127.0.0.1 (本机) | MCP 注册 |
| 8997 | skills-hub | 127.0.0.1 (本机) | Skills 分发 |
| 8998 | identity | 127.0.0.1 (本机) | OIDC / SSO |
| 8999 | gateway | 127.0.0.1 (本机) | LLM 网关 |
| 80 | nginx | 0.0.0.0 (公网) | HTTP → 301 HTTPS |
| 443 | nginx | 0.0.0.0 (公网) | HTTPS 员工访问 |

**外部只暴露 80/443**, 其他都走 docker network 内部访问 或 127.0.0.1 (调试). **无 8997/8998/8999 暴露到公网**.

---

## 附录 B: `.env` 字段说明

**必填**:
```bash
PG_PASSWORD=xxx                  # postgres 密码, ≥ 16 位
INTERNAL_LLM_KEY=xxx             # 内网 LLM API key
CATFISH_OIDC_ISSUER=https://...  # SSO issuer URL
SKILLS_HUB_TOKEN=xxx             # Skills 发布 token (manager+ 用)
```

**可选**:
```bash
# uvicorn 并发调优 (BL-F10 实测)
GATEWAY_WORKERS=4            # 默认 4, 8 vCPU 机改 8
IDENTITY_WORKERS=2           # 默认 2, 5000+ 员工改 4

# 公网 LLM API key (可留空禁用)
DASHSCOPE_API_KEY=           # 阿里云 dashscope
GEMINI_API_KEY=              # Google Gemini

# 域名 (nginx 反代用)
DOMAIN=catfish.example.com   # 改成你公司域名
```

**详细字段**: 见 `central/.env.production.example` 每行注释.

---

## 附录 C: 数据位置

| 数据 | 存储 | 位置 (Ubuntu Docker 默认) |
|------|------|--------------------------|
| PostgreSQL 数据 | `pgdata` volume | `/var/lib/docker/volumes/pgdata/_data` |
| Skills Hub 包 | `hubdata` volume | `/var/lib/docker/volumes/hubdata/_data` |
| Wiki FS 兜底 | `wikidata` volume | `/var/lib/docker/volumes/wikidata/_data` |
| Gateway 审计 | `gateway_data` volume | `/var/lib/docker/volumes/gateway_data/_data` |
| Nginx 日志 | container 内 | `docker exec catfish-nginx cat /var/log/nginx/access.log` |
| Nginx 证书 | `./certs/` (bind mount) | `/opt/catfish/central/certs/` |

**Windows Docker Desktop**: volume 在 WSL2 内 (WSL2 backend) 或 VHDX 里 (Hyper-V backend). `docker volume inspect <name>` 查具体路径.

---

## 附录 D: 快速命令备忘

```bash
# 部署
bash deploy.sh                       # 一键部署 (含体检 + smoke test)

# 状态
docker compose ps                    # service 状态
docker stats                         # 实时资源

# 日志
docker compose logs -f gateway       # tail gateway
docker compose logs --tail 100       # 全 service 最近 100 行

# 重启
docker compose restart gateway       # 单 service
docker compose down && docker compose up -d --no-build   # 全栈

# 数据库
docker exec -it catfish-postgres psql -U catfish -d catfish   # PG shell

# 备份
docker exec catfish-postgres pg_dump -U catfish -Fc catfish > backup.dump

# 恢复
docker exec -i catfish-postgres pg_restore -U catfish -d catfish < backup.dump
```

---

## 联系方式

**产品负责**: 中电福富 catfish 项目组  
**技术支持**: 提交 issue 到 https://github.com/jinichen/catfish/issues  
**紧急联系**: (公司内部联系方式)

---

**文档版本**: 2026-07-14 v0.18.0  
**上次更新**: P3.3.18-cleanup wiki-hub 补齐, P3.4.1-cleanup2 secret-broker 清理
