# 鲶鱼 Catfish · 达华服务端一键部署 SOP

**版本**: v0.18.1 (2026-07-18 · 7/18 code fix 全 mirror)
**目标读者**: 达华 IT 部署人员 (Linux / macOS / Windows Docker Desktop 均可)
**预计时长**: 首次 20-40 分钟 (含 image load + 配置 + 首次启动 warm up)

**你会拿到 2 个 tar.gz** (根据服务器 CPU 架构选一个):

| 文件 | 大小 | 架构 | 内容 |
|------|------|------|------|
| `dahua-poc-central-amd64-<date>.tar.gz` | ~601 MB | x86_64 (99% 政企/云服务器) | 7 image + docker-compose.yml + .env.example + README.md |
| `dahua-poc-central-arm64-<date>.tar.gz` | ~604 MB | aarch64 (鲲鹏/ARM) | 同上 · arm64 image |

**员工 Companion dmg/msi 分开分发** (见 `达华POC-3台mac-分发SOP.md` · 员工机独立分发到 3 台 mac).

**7/18 关键 fix 已 mirror 到本 bundle**:
- ✅ Task #61: `CATFISH_ENV=prod` (docker-compose.yml default 已 prod)
- ✅ Task #66: `CATFISH_OIDC_AUDIENCE=catfish-companion,catfish-gateway` 双值 (default 已双值 · 员工 Companion id_token 通)
- ✅ Task #68: `catfish-gateway:0.1.1` 含 orjson (litellm mcp code path 不再 502)

---

## 服务栈架构 · 8 个 Docker service

| # | Service | Image | 端口 | 功能 |
|---|---------|-------|------|------|
| 1 | postgres | `postgres:16-alpine` | 5432 (127.0.0.1 only) | 主数据库 (users / quota / audit / facts) |
| 2 | identity | `catfish-identity:0.1.0` | **8998 (0.0.0.0)** | OIDC 认证服务 · 员工 SSO 登录 |
| 3 | gateway | `catfish-gateway:0.1.1` | **8999 (0.0.0.0)** | LLM 网关 · 员工 chat 走这 · fallback 链路由 (7/18 Task #68: 加 orjson 解 litellm 502) |
| 4 | skills-hub | `catfish-skills-hub:0.1.0` | 8997 (127.0.0.1) | 技能包托管 · manager 发布 skill |
| 5 | mcp-registry | `catfish-mcp-registry:0.1.0` | 8996 (127.0.0.1) | MCP 连接器仓库 · 员工订阅 Jira/GitLab |
| 6 | wiki-hub | `catfish-wiki-hub:0.1.0` | 8994 (127.0.0.1) | Wiki 中央 · 员工发布知识 |
| 7 | web | `catfish-web:0.1.0` | **5173 (0.0.0.0)** | admin 面板 (员工管理/审计) |
| 8 | nginx | `nginx:1.27-alpine` | 80 / 443 | 反代 · **需要域名 + SSL 证书, 测试期可跳过** |

**只 0.0.0.0 的 3 个** (identity 8998 / gateway 8999 / web 5173) 是员工浏览器 + Companion 访问. 别的 (skills-hub / mcp-registry / wiki-hub) 内部 network 用, 不对外.

---

## 部署 · 10 步 (每步 30 秒 - 5 分钟)

### 前置 · 服务器规格

- **OS**: Ubuntu 22.04+ / CentOS 8+ / Debian 11+ / macOS 12+ / Windows 10+ with WSL2
- **硬件**: 4 vCPU / 8 GB RAM (最小) · 8 vCPU / 16 GB RAM (推荐 1000+ 员工)
- **磁盘**: 20 GB 空闲 (image + PG data + audit + logs)
- **Docker**: Docker Engine 20+ 或 Docker Desktop
- **端口**: 5173 / 8998 / 8999 (员工可达) · 5432 / 8994 / 8996 / 8997 (只本机)

### Step 1 · 建部署根目录 (30 秒)

```bash
# Linux / macOS (推荐 /opt/catfish, 也可 ~/catfish)
sudo mkdir -p /opt/catfish
sudo chown $USER:$USER /opt/catfish
cd /opt/catfish

# Windows PowerShell (推荐 E:\catfish)
mkdir E:\catfish
cd E:\catfish
```

### Step 2 · 装 image tar (2-5 分钟, 视磁盘速度)

**⚠️ 装之前必查你服务器 CPU 架构** (Docker image 是**特定架构** · 装错架构直接跑不起来):

```bash
uname -m
```

**返回**:
- `x86_64` → **amd64** (99% 政企 Ubuntu/CentOS 是这个) · 用 `catfish-central-images-amd64.tar`
- `aarch64` → **arm64** (国产鲲鹏 / 部委服务器) · 用 `catfish-central-images-arm64.tar`
- `x86_64` 但 macOS/Windows Docker Desktop · 也用 amd64 tar

**若鲶鱼团队发给你的 tar 跟你架构不匹配** · 立即联系 catfish-support · 我们换正确架构的 tar 发你.

```bash
docker load -i /path/to/catfish-central-images-<你的架构>.tar

# verify 8 个 image 都在 (架构对)
docker images | grep -E "catfish-|postgres:16-alpine|nginx:1.27-alpine"

# 关键 verify 架构 (必须跟 uname -m 一致 or Docker 说"platform doesn't match" 警告)
docker inspect catfish-gateway:0.1.0 --format '{{.Architecture}}/{{.Os}}'
# 期望: amd64/linux 或 arm64/linux (跟 uname -m 一致)
```

**期望 8 行**:
```
catfish-gateway        0.1.0    xxx    494MB
catfish-identity       0.1.0    xxx    266MB
catfish-mcp-registry   0.1.0    xxx    250MB
catfish-skills-hub     0.1.0    xxx    232MB
catfish-web            0.1.0    xxx     49MB
catfish-wiki-hub       0.1.0    xxx    232MB
nginx                  1.27-alpine  xxx  49MB
postgres               16-alpine    xxx  288MB
```

### Step 3 · 解压 config tar (10 秒)

```bash
tar xzf /path/to/catfish-central-config.tar.gz

# 现在 /opt/catfish/ 下有 central/ 目录, 里面 17 个文件
ls central/
```

### Step 4 · cd 进 central 目录 (关键!)

```bash
cd /opt/catfish/central
```

**docker compose 必须在这里跑** · docker-compose.yml 里的 relative path (`./llm-gateway/config`, `./identity-server/config`) 都是相对这个目录.

### Step 5 · 建 .env (从 example 拷) + 改 6 项 (5-10 分钟)

```bash
cp .env.production.example .env
vim .env       # 或 nano/notepad
```

**必填 6 项** (下面详解, 找到对应行改掉 CHANGE_ME):

```env
# 1. Postgres 密码 (16+ 位强密码 · 大小写+数字+符号)
PG_PASSWORD=Dahua_PG_2026_STRONG_pwd

# 2. 内网 LLM API key (达华内网 qwen 平台申请)
INTERNAL_LLM_KEY=达华内网qwen平台的真key

# 3-5. 内网 LLM 3 个端点 URL (换成达华内网真实 IP:port)
INTERNAL_LLM_BASE_QWEN_MAIN=http://10.10.40.102:32730/openapi/xxx/v1
INTERNAL_LLM_BASE_QWEN_VISION=http://10.10.40.102:32730/openapi/yyy/v1
INTERNAL_LLM_BASE_BGE_M3=http://10.10.40.102:32730/openapi/zzz/v1

# 6. Skills Hub token (32 位随机 · manager 发布 skill 用)
SKILLS_HUB_TOKEN=aad226ad9ad83bacbe95e554a07864b0e4d9a215117696b79a161c726d522561
```

**可选 3 项** (公网 LLM · 测试期建议至少填 1 个 · fallback 用):

```env
DASHSCOPE_API_KEY=你的阿里dashscope key         # 申请 https://dashscope.console.aliyun.com/
DEEPSEEK_API_KEY=你的deepseek key               # 申请 https://platform.deepseek.com/api_keys
GEMINI_API_KEY=你的gemini key                   # 申请 https://aistudio.google.com/app/apikey
```

**默认可不改 3 项** (docker-compose.yml 已有 sensible default):

```env
GATEWAY_WORKERS=1              # 交付默认 1; 内网可达且资源充足时再改 4/8
IDENTITY_WORKERS=2             # 4 vCPU 用 2, 8 vCPU 改 4
# setup.sh 会按 SERVER_IP / ENABLE_HTTPS 自动写 OIDC issuer, 不要保留 host.docker.internal
```

**只有手工部署或使用域名时才需要手动改 OIDC**；一键装机脚本会自动生成 Linux 可访问地址:

```env
# Docker Desktop mac/Win → host.docker.internal 直接生效
# Linux 生产 (Ubuntu/CentOS) → 换成服务器 IP 或域名
CATFISH_OIDC_ISSUER=https://<你的服务器 IP>
# 或若有域名 + SSL 反代
CATFISH_OIDC_ISSUER=https://catfish.dahua.com/sso
```

### Step 6 · 建 identity 员工 seed (users.yaml) (5-10 分钟)

**先启动 identity + postgres** (只这两个, 建 admin 密码 hash 要用):

```bash
docker compose up -d postgres identity
sleep 20    # 等 postgres init + identity migrate
```

**生成 admin 密码 hash**:

```bash
# 换成你的强密码 (给达华管理员登录用)
docker compose exec identity python -c "from catfish_identity.users import hash_password; print(hash_password('Dahua_Admin_2026!'))"
```

**输出**类似:
```
$2b$12$abcXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

**Copy 整段 hash** (从 `$2b$12$` 开始整段).

**建 users.yaml**:

```bash
cp identity-server/config/users.yaml.example identity-server/config/users.yaml
vim identity-server/config/users.yaml
```

**替换里面的 `CHANGE_ME_...` 为刚生成的 hash**:

```yaml
users:
  - email: admin@dahua.com
    password_hash: $2b$12$abcXXXXXX...     # ← 粘贴 hash
    name: 达华管理员
    department: IT
    role: sysadmin
```

**Truncate PG.users 让 seed 触发** (第一次装, PG.users 是空的, seed 会自动跑; 若已 up 过一次, 需要 truncate):

```bash
docker compose exec postgres psql -U catfish -d catfish -c "TRUNCATE users CASCADE;"
docker compose restart identity
sleep 15

# verify · 期望 "PG users 首次 seed: 从 yaml 灌 1 条"
docker compose logs identity --tail 20 | grep -E "seed|users 加载"

# verify PG 里真有 user
docker compose exec postgres psql -U catfish -d catfish -c "SELECT email, name, role FROM users;"
```

### Step 7 · 启动全栈 (不含 nginx, 测试期不需要 SSL 反代)

```bash
docker compose up -d postgres identity gateway skills-hub wiki-hub mcp-registry web
```

### Step 8 · 等 60 秒 warm up

```bash
sleep 60           # Linux / macOS
Start-Sleep 60     # Windows PowerShell
```

### Step 9 · verify 7 服务健康

```bash
docker compose ps
```

**期望**:
```
NAME                   STATUS
catfish-postgres       Up (healthy)
catfish-identity       Up (healthy)
catfish-gateway        Up (healthy)
catfish-skills-hub     Up (healthy)
catfish-wiki-hub       Up (healthy)
catfish-mcp-registry   Up (healthy)
catfish-web            Up (unhealthy)    ← 已知 healthcheck 路径问题, 功能正常
```

### Step 10 · verify 关键 endpoint

```bash
# identity OIDC 服务发现 · 应返 JSON
curl http://localhost:8998/.well-known/openid-configuration

# gateway 健康 · 应返 {"status":"ok"}
curl http://localhost:8999/healthz

# web SPA 首页 · 浏览器打开
# http://<你服务器 IP>:5173
```

---

## 员工端连接 · Companion 里填服务器 URL

装 Companion 后 (dmg / msi), 首次启动:

**Step 1 · ServerSetupCard 弹出** (7/16 加, 首次装必填):

```
Gateway URL:     http://<服务器 IP>:8999
Identity URL:    http://<服务器 IP>:8998
```

点 "保存并测试" · 状态显示 🟢 认证服务 · 🟢 网关 · 自动切换到登录卡.

**Step 2 · 登录**:

- 邮箱: `admin@dahua.com`
- 密码: 你在 Step 6 用的强密码
- 点 "登录" · 浏览器打开 identity 登录页 · 再输一次同样账号提交

**Step 3 · Onboarding 8 步** (公司信息 · 员工画像 · 权限确认 · 试聊)

---

## 后续 · 加员工 3 种方式 (v0.18.0 Phase 1)

**方式 A · psql 直接 INSERT** (最快, 单个员工):

```bash
# 生成新员工 hash
docker compose exec identity python -c "from catfish_identity.users import hash_password; print(hash_password('Employee_2026'))"

# psql insert
docker compose exec postgres psql -U catfish -d catfish -c "
INSERT INTO users (email, password_hash, name, department, tier, role, managed_departments)
VALUES ('zhang.san@dahua.com', '刚生成的hash', '张三', '研发', 'employee', 'employee', '[]'::jsonb);
"
```

**方式 B · vim yaml + wipe + restart** (批量首次 seed):

```bash
vim identity-server/config/users.yaml            # 追加多条 user
docker compose exec postgres psql -U catfish -d catfish -c "TRUNCATE users CASCADE;"
docker compose restart identity
```

**方式 C · Companion Admin API** (Phase 2 未实现)

---

## 常见 6 个坑 · 排查

### 坑 1 · gateway 挂 · worker 循环 die

**症状**:
```
docker compose logs gateway | grep "Child process died"
```

**根因** · 3 种:

1. **models.yaml 引用了 .env 里没配的 env** (最常见) · 检查:
   ```bash
   docker compose logs gateway 2>&1 | grep "env variable"
   # 若报 "env variable ${INTERNAL_LLM_BASE_XXX} is not set"
   # → vim .env 补上对应变量
   ```

2. **volume 权限不对** · `/home/catfish/.catfish/facts` 写不进:
   ```bash
   docker compose exec --user root gateway chown -R catfish:catfish /home/catfish
   docker compose restart gateway
   ```

3. **PG 密码错** · `.env` 里 PG_PASSWORD 跟 postgres volume 里存的对不上:
   ```bash
   # 只测试期无数据可以 wipe
   docker compose down -v            # -v 删 volume
   docker compose up -d              # 重新 seed
   ```

### 坑 2 · identity users 加载 0 · 员工登录 401

**症状**:
```
docker compose logs identity | grep "users 加载"
# 期望: "PG users 加载: N 个用户", 若 N=0 → 没 seed
```

**Fix** · 参照 Step 6 · 建 users.yaml + Truncate + restart.

### 坑 3 · Companion 首启弹 CMD 框, 浏览器 URL 挂

**根因** · Windows 老版 msi (Build #12), 有 CMD & 截断 bug.

**Fix** · 换 CircleCI Build #25 之后的 msi (含 rundll32 fix).

### 坑 4 · web unhealthy 但能访问 · healthcheck 路径问题

已知无害 · 功能正常. 员工浏览器打开 `http://<IP>:5173` 能看到 catfish web 首页.

### 坑 5 · nginx 起不来 · 缺证书

**测试期 skip nginx** (Step 7 命令里没启 nginx 就行). 生产上域名 + Let's Encrypt 证书:

```bash
# 建 nginx.conf (从 example)
cp web/nginx.conf.example nginx.conf
vim nginx.conf           # 改 server_name 到达华域名

# 建 certs 目录 + 放 cert.pem + key.pem (Let's Encrypt certbot 生成)
mkdir certs
# 拷贝 cert.pem + key.pem 到 certs/

# 启 nginx
docker compose up -d nginx
```

### 坑 6 · docker compose 报 "path xxx not found"

**根因** · cwd 不在 `central/` 目录. compose 里 relative path 找不到.

**Fix**:

```bash
pwd                              # 应该显示 /opt/catfish/central
cd /opt/catfish/central
docker compose ps                # 再跑
```

---

## 关键参数速查

### .env 全字段 (含 default)

| 变量名 | 用途 | Default | 必填 |
|---|---|---|---|
| `PG_USER` | postgres 用户名 | `catfish` | ⭕ |
| `PG_PASSWORD` | postgres 密码 | 无 | ✅ **必填** |
| `PG_DB` | postgres 数据库名 | `catfish` | ⭕ |
| `GATEWAY_WORKERS` | gateway uvicorn worker 数 | `1` | ⭕ |
| `IDENTITY_WORKERS` | identity uvicorn worker 数 | `2` | ⭕ |
| `INTERNAL_LLM_KEY` | 内网 qwen API key | 无 | ✅ **必填** |
| `INTERNAL_LLM_BASE_QWEN_MAIN` | 内网 qwen chat 端点 | `http://127.0.0.1:9998/v1` | ✅ **必填** |
| `INTERNAL_LLM_BASE_QWEN_VISION` | 内网 qwen vision 端点 | 同上 | ✅ **必填** |
| `INTERNAL_LLM_BASE_BGE_M3` | 内网 embedding 端点 | 同上 | ✅ **必填** |
| `CATFISH_OIDC_ISSUER` | OIDC issuer URL | `https://<server-ip>` (setup 自动写入) | ⭕ |
| `CATFISH_OIDC_AUDIENCE` | OIDC audience | `catfish-gateway` | ⭕ |
| `DASHSCOPE_API_KEY` | 阿里 qwen-flash key | 空 (禁用) | ⭕ |
| `DEEPSEEK_API_KEY` | Deepseek key | 空 (禁用) | ⭕ |
| `GEMINI_API_KEY` | Google Gemini key | 空 (禁用) | ⭕ |
| `SKILLS_HUB_TOKEN` | Skills 发布 token (32 位随机) | 无 | ✅ **必填** |
| `DOMAIN` | nginx 域名 (生产) | `catfish.example.com` | ⭕ (nginx 时改) |

### docker compose 全命令速查

```bash
# 启全栈 (不含 nginx)
docker compose up -d postgres identity gateway skills-hub wiki-hub mcp-registry web

# 状态
docker compose ps

# log (最近 100 行 · 实时)
docker compose logs -f --tail 100 gateway
docker compose logs -f --tail 100 identity

# 重启单服务
docker compose restart gateway

# 停 · 保留数据
docker compose stop

# 启已有的 (不 pull)
docker compose start

# 停 + 删 container (保留 volume 数据)
docker compose down

# 停 + 删 container + 删 volume (⚠️ 数据全清! 测试期可用, 生产禁)
docker compose down -v

# 只 pull 新 image (若给了新版本 tar)
docker load -i /path/to/new-images.tar
docker compose up -d --force-recreate
```

### psql 常用命令

```bash
# 连 postgres
docker compose exec postgres psql -U catfish -d catfish

# 常用 SQL:
\dt                                                       # 列所有表
SELECT email, name, role FROM users;                      # 看员工
SELECT COUNT(*) FROM gateway_audit;                       # gateway 调用总数
SELECT COUNT(*) FROM users_audit;                         # 用户操作审计条数

# 改员工密码
UPDATE users SET password_hash='$2b$12$xxx', must_change_password=TRUE
WHERE email='zhang.san@dahua.com';

# 退出
\q
```

---

## 支持 · 联系鲶鱼团队

- 邮件: catfish-support@ai-catfish.com
- 附上 `docker compose ps` + `docker compose logs gateway --tail 100`

**测试期 7×12 支持** · 09:00 - 21:00.
