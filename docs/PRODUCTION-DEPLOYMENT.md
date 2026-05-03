# 鲶鱼中央服务 · 生产部署指南

> 五一 sprint 5/2 收尾, BL-F7 MVP. **客户 IT 拿这个文档 30 分钟可以装好**.
>
> 适用: 私有部署 (客户机房 / 私有云). catfish-cloud SaaS 部署是另一份文档 (后续).

---

## 你需要准备

- **1 台 Linux 服务器** (推荐 Ubuntu 22.04+, CentOS 7+ 也行), 至少 4 vCPU + 8GB RAM
- **域名 + SSL 证书** (Let's Encrypt 免费, 客户内网用自签也行)
- **Docker + docker-compose** (`docker --version` ≥ 24, `docker compose version` ≥ 2.20)
- **网络**: 出站 443 通 (调 LLM API), 入站 80/443 通 (员工 Companion 连)

---

## 部署 5 步 (~15 分钟)

### 1. 拉源码

```bash
git clone https://github.com/jinichen/catfish.git
cd catfish/central
```

### 2. 配置 `.env`

```bash
cp .env.production.example .env
vim .env
```

必须改:
- `PG_PASSWORD` — 至少 16 位强密码 (生产别用默认!)
- `DOMAIN` — 你的域名 (例 `catfish.acme.com`)
- `CATFISH_OIDC_ISSUER` — 客户 SSO issuer URL (飞书 / 钉钉 / 自建 OIDC)
- `DASHSCOPE_API_KEY` / `GEMINI_API_KEY` — 公网 LLM key (按需)
- `SKILLS_HUB_TOKEN` — 32 位随机, 内部分发给 manager

### 3. 配置 nginx

```bash
cp nginx.conf.example nginx.conf
# 改 server_name 成你的域名
sed -i 's/catfish.example.com/catfish.acme.com/g' nginx.conf

# 申请 Let's Encrypt cert (一次性):
mkdir -p certs
sudo certbot certonly --standalone -d catfish.acme.com
sudo cp /etc/letsencrypt/live/catfish.acme.com/fullchain.pem certs/
sudo cp /etc/letsencrypt/live/catfish.acme.com/privkey.pem certs/
```

如果是**内网部署没公网**, 用自签 cert:
```bash
mkdir -p certs && cd certs
openssl req -x509 -newkey rsa:4096 -keyout privkey.pem -out fullchain.pem \
  -days 3650 -nodes -subj "/CN=catfish.acme.com"
cd ..
```

### 4. 启动

```bash
docker compose up -d
docker compose ps
# 4 个容器都该 up + healthy:
#   catfish-postgres    Up (healthy)
#   catfish-identity    Up (healthy)
#   catfish-gateway     Up (healthy)
#   catfish-skills-hub  Up (healthy)
#   catfish-nginx       Up
```

### 5. 验证

```bash
curl -k https://catfish.acme.com/healthz
# {"status":"ok","service":"catfish-gateway"}

curl -k https://catfish.acme.com/sso/.well-known/openid-configuration
# OIDC 配置 JSON

curl -k https://catfish.acme.com/hub/healthz
# {"status":"ok","service":"catfish-skills-hub"}
```

---

## 升级

```bash
cd catfish && git pull origin main
cd central
docker compose pull       # 拉新镜像 (如果走镜像仓库)
docker compose build      # 或者本地重 build
docker compose up -d      # 滚动重启, alembic 自动跑 schema migration
```

**downtime**: ~10 秒 (gateway 重启). 不影响员工长期使用 (Companion 自动重连).

---

## 监控 (推荐, 不强制)

健康检查 endpoints:
- `GET /healthz` (gateway) → JSON
- `GET /sso/.well-known/openid-configuration` (identity) → JSON
- `GET /hub/healthz` (skills-hub) → JSON

接 Prometheus / Grafana / Zabbix 用任何 HTTP 监控都行. 推荐:
- 监控 5xx 错误率
- 监控 PG 容器状态 + 磁盘 (audit jsonl + quota_events 会涨)
- 监控容器 healthcheck (docker compose ps)

---

## 备份 (重要!)

```bash
# 每天 cron 跑 (员工数据全在 PG):
0 2 * * * docker exec catfish-postgres pg_dump -U catfish catfish | gzip > /backup/catfish-$(date +\%F).sql.gz

# 保留 30 天:
0 3 * * * find /backup -name 'catfish-*.sql.gz' -mtime +30 -delete

# Skills Hub 文件 (额外):
0 2 * * * tar czf /backup/hub-$(date +\%F).tar.gz -C /var/lib/docker/volumes/central_hubdata/_data .
```

---

## Companion 怎么连这个生产 gateway

员工本机 Companion build 时注入域名:

```bash
cd edge/companion-app
VITE_CATFISH_GATEWAY_URL=https://catfish.acme.com \
  npm run tauri:build

# 装 .app 给员工
```

或者员工本机 `~/.catfish/config.yaml`:

```yaml
gateway:
  url: https://catfish.acme.com
```

(Companion 优先级: env > config > 默认 localhost:8999)

---

## 多 gateway 实例 (HA, ~50+ 员工)

`docker-compose.yml` 加 `deploy.replicas: 2` (Swarm) 或换 k8s. PG 共享, gateway 无状态.
nginx upstream 加多个 backend 自动负载均衡.

```yaml
gateway:
  deploy:
    replicas: 2  # 双实例
```

---

## 常见问题

**Q: 启动后 gateway healthcheck 红?**
A: `docker compose logs gateway` 看错. 大概率 alembic 跑不过, 检查 PG_PASSWORD / CATFISH_DB_URL 对不对.

**Q: 客户 IT 不让用 docker?**
A: 我们也支持 systemd + venv 部署, 但维护成本高很多. 强烈建议 docker. 如果坚持 systemd, 联系 catfish 团队要 systemd unit 文件.

**Q: 升级会丢数据吗?**
A: 不会. PG / Skills Hub / quota.db 都在 docker volume, 升级不动数据. alembic migration 是 idempotent (`CREATE TABLE IF NOT EXISTS`).

**Q: 客户没自己 SSO 怎么办?**
A: 用本机 catfish-identity (默认配的). 配 `CATFISH_OIDC_ISSUER=http://identity:8998` 自包含, 然后用 `central/identity-server/config/users.yaml` 加员工.

**Q: SSL cert 过期?**
A: certbot 自动续, 客户 IT 加 cron `0 0 * * * certbot renew --quiet && docker compose restart nginx`.

---

## 技术细节

部署架构:
```
                   ┌─────────────────────────────┐
   员工 Companion  │   nginx (443/80)            │
   ─────HTTPS────► │   ↓ ↓ ↓                     │
                   │   gateway   identity   hub  │
                   │   :8999     :8998      :8997│
                   │       ↓ ↓                   │
                   │       PostgreSQL :5432      │
                   │       (quota + users)       │
                   └─────────────────────────────┘
```

5 容器 (postgres / identity / gateway / skills-hub / nginx),
全部 healthcheck + restart unless-stopped.

资源 (典型 50 员工):
- gateway: 200-500MB RAM, ~10% CPU
- identity: 100MB RAM, < 1% CPU
- postgres: 200MB RAM
- skills-hub: 100MB RAM
- nginx: 50MB RAM
- **总计 < 2GB RAM, ~15% CPU** (4 vCPU 服务器够用)
