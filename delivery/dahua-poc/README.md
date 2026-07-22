# 达华 POC · 鲶鱼 (Catfish) 中央服务器 · 部署 SOP

**版本**: 2026-07-18  ·  **交付**: 鲶鱼团队 → 达华智能 IT

---

## 一、前置检查

Linux 服务器 (Ubuntu 22.04+ / Debian 12+ / CentOS 8+) · Docker + Compose 已装:

```bash
docker --version         # >= 24
docker compose version   # >= v2

# 架构
uname -m
# x86_64  → 用 amd64 包
# aarch64 → 用 arm64 包
```

**资源建议**:
- CPU: 4 core+
- RAM: 8 GB+
- Disk: 30 GB+
- 网络: 员工电脑能访问服务器 8998 · 8999 端口

---

## 二、安装

### 1. 解压对应架构包

```bash
# amd64 服务器
tar xzf dahua-poc-central-amd64-20260718.tar.gz -C /opt/catfish-central/
# 或 arm64 服务器
# tar xzf dahua-poc-central-arm64-20260718.tar.gz -C /opt/catfish-central/

cd /opt/catfish-central/
ls
# 期望: images/  docker-compose.yml  .env.example  README.md
```

### 2. Docker load 全部 image

```bash
for f in images/*.tar; do
    echo "loading $f ..."
    docker load < "$f"
done

# verify
docker images | grep -E "catfish|postgres"
# 期望: 6 catfish image + postgres:16-alpine
```

### 3. 配置 .env

```bash
cp .env.example .env
vim .env  # 或 nano

# 必填:
#   PG_PASSWORD          → 随机字符串 · openssl rand -hex 24
#   JWT_SIGNING_KEY      → openssl rand -hex 32
#
# 可选 (客户 IT 自己有 LLM provider 才填):
#   DASHSCOPE_API_KEY / GEMINI_API_KEY / 私网 LLM 配置
#
# OIDC (默认 host.docker.internal · POC 测试可保留)
#   CATFISH_OIDC_ISSUER=http://<server-ip>:8998   # 员工机能访问的 URL
```

### 4. 起服务

```bash
docker compose up -d

# verify 全部 healthy (等 60 秒)
sleep 60
docker compose ps
# 期望: 全部 `Up (healthy)`

# 检查关键 log
docker compose logs gateway --tail 20 | grep -iE "auth:|OIDCProvider 初始化"
# 期望: `auth: env=prod → Composite[OIDC(...), DevToken]`
```

---

## 三、Verify 服务器就绪

```bash
# identity 8998
curl -s http://<server-ip>:8998/health
# 期望: {"status":"ok",...}

# gateway 8999
curl -s http://<server-ip>:8999/healthz
# 期望: {"status":"ok"}

# 从员工机测 (若在同一内网)
curl -s http://<server-ip>:8998/.well-known/jwks.json
```

---

## 四、员工 Companion 配置

员工装完 Companion dmg 后:
1. 打开 Companion → **仪表盘** → **服务器配置** → **改**
2. 填:
   - **Gateway URL**: `http://<server-ip>:8999`
   - **Identity URL**: `http://<server-ip>:8998`
3. **保存** → 提示重启
4. 关掉 Companion 重新打开
5. 首次点 chat · 弹浏览器 SSO · 输 email + 密码
6. 通了 · POC 就绪

---

## 四点五、员工 Companion 装完后 · **邮件 tab 手工装 CLI** (Task #8)

Companion 邮件 tab 若提示 "catfish-email CLI 没装" · IT 每台员工机跑一次 (5 min · 永久):

```bash
# 员工 mac 上
export PATH=~/.hermes/hermes-agent/venv/bin:$PATH
mkdir -p ~/.local/bin
ln -sf ~/.hermes/hermes-agent/venv/bin/catfish-email ~/.local/bin/catfish-email
which catfish-email  # verify
```

或员工 POC 期只用 chat · 邮件 tab 不用. 长期 fix (Task #8): rebuild dmg bundle email-agent · 员工机零手工.

---

## 五、常见问题

### Q1: `docker compose up` 起来 · gateway 挂 · log 报 `No inference provider`
A: .env 里 provider key 缺 (DASHSCOPE_API_KEY / GEMINI_API_KEY / etc). 客户 IT 自己配 LLM.

### Q2: 员工 chat 401 invalid token
A: 常见:
- `.env` 里 `CATFISH_ENV=dev` (应 `prod`) — docker-compose.yml default 已 prod
- `.env` 里 `CATFISH_OIDC_AUDIENCE` 单值 — docker-compose.yml default 已双值 `catfish-companion,catfish-gateway`
- identity restart 后员工需重新 SSO (rm ~/.catfish/oauth · 重开 Companion)

### Q3: chat 500 · litellm APIConnectionError orjson
A: image `catfish-gateway:0.1.1` 已含 orjson (Task #68 fix). 若挂 · `docker exec catfish-gateway pip show orjson` verify.

### Q4: 员工 Companion 提示 CATFISH_ENV=prod 但 gateway 起不来
A: 检查 · `docker compose logs gateway --tail 50` · 若 · alembic 挂 · postgres 未 healthy · 等 60 秒 · postgres 起来后 gateway 会自动重连.

---

## 六、升级

新版本包 · 停服务 → 覆盖 → 起:

```bash
docker compose down
# 覆盖 (保 .env · 保 volumes)
tar xzf dahua-poc-central-<arch>-<新日期>.tar.gz --exclude='.env*' -C /opt/catfish-central/
for f in /opt/catfish-central/images/*.tar; do docker load < "$f"; done
docker compose up -d
```

---

## 七、备份

```bash
# Postgres 数据 volume
docker exec catfish-postgres pg_dump -U catfish catfish > backup-$(date +%Y%m%d).sql

# ~/.catfish/ (员工机数据 · 客户 IT 无需管)
```

---

## 八、卸载

```bash
docker compose down -v   # -v 清 volume (postgres 数据没了)
docker rmi $(docker images | grep -E "catfish|postgres:16" | awk '{print $3}')
rm -rf /opt/catfish-central/
```

---

**技术支持**: 陈鸿波 <chenhongbo@ffcs.cn>
**紧急**: 客户 IT 遇挂 · Slack / 微信 @陈鸿波
