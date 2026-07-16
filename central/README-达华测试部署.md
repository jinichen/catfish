# 鲶鱼 Catfish · 达华智能测试部署手册

**版本**: v0.18.0 (2026-07-15)
**目标读者**: 达华 IT 部署人员
**预计时长**: 首次部署 30-45 分钟

---

## 前言 · 你收到 4 个文件

| 文件 | 大小 | 用途 |
|------|------|------|
| `catfish-central-images-mac.tar` | ~2.5 GB | 8 个服务的 Docker 镜像 (mac / Windows / Ubuntu 通用) |
| `catfish-central-config-mac.tar.gz` | ~50 KB | 部署配置 + 本手册 |
| `Catfish Companion_0.18.0_aarch64.dmg` | ~15 MB | **员工 mac 桌面客户端** (Apple Silicon M1/M2/M3/M4) |
| `Catfish Companion_0.18.0_x64_zh-CN.msi` | ~15 MB | **员工 Windows 桌面客户端** (x64 中文 UI) |

**关键**: **服务端 Docker 镜像已在鲶鱼团队 mac 上编译打包**, 达华无需在本地编译.
直接 `docker load` 加载即可运行, 支持 mac / Windows / Ubuntu 任意环境.

---

## 1. 环境准备 (5 分钟, 3 选 1)

### 1.A · macOS (Apple Silicon, 推荐测试环境)

**硬件**: Mac M1/M2/M3/M4, macOS 12+, 内存 ≥ 8 GB (推荐 16), 磁盘 ≥ 20 GB

**装 Docker Desktop for Mac (Apple Silicon)**:
1. 下载: https://www.docker.com/products/docker-desktop/
2. 装完启动, 图标 → **Settings → Resources**:
   - CPU: 4 (推荐 6)
   - Memory: 6 GB (推荐 8)
   - Disk: 40 GB
3. **Apply & Restart**

### 1.B · Windows 10/11 Professional (24H2+)

**硬件**: Intel/AMD x64, 内存 ≥ 8 GB, 磁盘 ≥ 20 GB, 虚拟化已开启 (BIOS)

**装 Docker Desktop for Windows**:
1. 下载: https://www.docker.com/products/docker-desktop/
2. 装时勾选 **"Use WSL 2 based engine"** (推荐, 比 Hyper-V 稳)
3. 装完启动, Settings → **Resources → WSL Integration**:
   - Memory: 6 GB
   - Disk: 40 GB
4. 右下角 Docker 图标显示 running

**⚠️ Windows 常见坑**:
- 若装的是 Windows Home 版, 无 Hyper-V, 需要用 WSL 2 backend (Docker Desktop 支持)
- 若公司统一装 360/腾讯管家, 可能拦截 Docker 网络, 加白名单

### 1.C · Ubuntu 22.04 / 24.04 (推荐生产环境)

**硬件**: x64, 内存 ≥ 4 GB (推荐 8), 磁盘 ≥ 20 GB

**装 Docker**:
```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER   # 免 sudo, 需重登生效
newgrp docker
docker --version                  # verify
```

---

## 2. 加载 catfish-central (通用命令 · 5 分钟)

**建工作目录 + 解压 config + 加载镜像**.

### macOS / Ubuntu (Terminal)

```bash
mkdir -p ~/catfish-central && cd ~/catfish-central

# 解压 config (~50 KB, 秒完)
tar -xzf ~/Downloads/catfish-central-config-mac.tar.gz

# 加载 8 个 Docker 镜像 (2-3 分钟)
docker load -i ~/Downloads/catfish-central-images-mac.tar
```

### Windows (PowerShell, 以 `E:\catfish-central` 为例)

```powershell
mkdir E:\catfish-central
cd E:\catfish-central

# 假设 3 个文件放在 Downloads
tar -xzf $env:USERPROFILE\Downloads\catfish-central-config-mac.tar.gz

docker load -i $env:USERPROFILE\Downloads\catfish-central-images-mac.tar
```

**期望**: 看到 8 行 `Loaded image: catfish-identity:0.1.0` 等.

---

## 3. 配置 .env (5 分钟)

```bash
# macOS / Ubuntu
cp .env.production.example .env
open -e .env     # mac 用 TextEdit; Ubuntu 用 nano/vim

# Windows PowerShell
Copy-Item .env.production.example .env
notepad .env
```

### 至少改这 3 项 (其他保持默认)

```env
# ── 1. PG 数据库密码 (16+ 位强密码, 达华自定, 一次性设置) ──
PG_PASSWORD=DahuaTest2026@Strong

# ── 2. OIDC issuer (客户端 + 服务端共享, 必须一致) ──
# 单机部署 (mac/Win/Ubuntu 上 Docker Desktop): 用 host.docker.internal
CATFISH_IDENTITY_ISSUER=http://host.docker.internal:8998
CATFISH_OIDC_ISSUER=http://host.docker.internal:8998

# 若达华用 Linux 生产环境 + 有域名 + SSL, 改成:
# CATFISH_IDENTITY_ISSUER=https://catfish.dahua.com
# CATFISH_OIDC_ISSUER=https://catfish.dahua.com
# (同时改 nginx.conf.example → nginx.conf, 加 SSL 证书到 ./certs/)

# ── 3. 内网 LLM 端点 (若达华有部署) ──
# 若达华没自建内网 LLM, 用 dummy 占位 (公网 model 仍可用)
INTERNAL_LLM_KEY=dahua-test-secret
INTERNAL_LLM_BASE_QWEN_MAIN=http://127.0.0.1:9998/v1
INTERNAL_LLM_BASE_QWEN_VISION=http://127.0.0.1:9998/v1
INTERNAL_LLM_BASE_BGE_M3=http://127.0.0.1:9998/v1

# ── 4. (可选) 公网 LLM key (员工 fallback 用) ──
DASHSCOPE_API_KEY=              # 阿里通义千问, 达华拿到后填
GEMINI_API_KEY=                 # Google Gemini, 可选
```

**保存注意** (Windows): 记事本另存为时 **编码选 UTF-8**, 不要 UTF-16 / ANSI.

---

## 3.5. 建 users.yaml seed 员工账号 (5 分钟, 首次必做)

**目的** · Identity 首次启动时若 PG.users 表空, 会读 `users.yaml` seed 灌进 PG. 之后 PG 是权威, yaml 忽略. **不做这步 → 员工登录 401**.

### Step 1 · 建 users.yaml (从模板)

```bash
# mac / Ubuntu
cp identity-server/config/users.yaml.example identity-server/config/users.yaml
open -e identity-server/config/users.yaml     # 或 nano/vim

# Windows PowerShell
Copy-Item identity-server\config\users.yaml.example identity-server\config\users.yaml
notepad identity-server\config\users.yaml
```

### Step 2 · 生成 admin 密码 hash

**先启动 identity** (postgres 也要一起):

```bash
docker compose up -d postgres identity
sleep 20

# 生成 hash (换成你的强密码)
docker compose exec identity python -c \
  "from catfish_identity.users import hash_password; print(hash_password('DahuaAdmin2026!'))"
```

**输出**类似 · **完整 copy 那一整串** (从 `$2b$12$` 开始):
```
$2b$12$abcXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

### Step 3 · 编辑 users.yaml 填 hash

```yaml
users:
  - email: admin@dahua.com
    password_hash: $2b$12$你刚生成的完整hash     # ← 粘贴这里
    name: 达华管理员
    department: IT
    role: sysadmin

  # 后续员工可以 vim 加 · 或用 admin API
```

保存关闭.

### Step 4 · 让 seed 触发 (TRUNCATE users + restart identity)

```bash
# 若之前 up 过, PG.users 可能已有 (空) 记录 · 清空让 seed 再跑
docker compose exec postgres psql -U catfish -d catfish -c "TRUNCATE users CASCADE;"

# restart identity · 自动 seed
docker compose restart identity
sleep 15

# verify seed 成功
docker compose logs identity --tail 20 | grep -E "seed|users 加载"
```

**期望 log**:
```
PG users 首次 seed: 从 yaml 灌 1 条
PG users 加载: 1 个用户 (覆盖 yaml)
```

### Step 5 · verify PG 里真有 user

```bash
docker compose exec postgres psql -U catfish -d catfish -c "SELECT email, name, role FROM users;"
```

**期望** · 显示 admin@dahua.com 条目.

### 后续加员工 3 种方式 (v0.18 Phase 1)

**方式 A · psql 直接 INSERT** (最快):
```bash
# 生成新员工 hash
docker compose exec identity python -c "from catfish_identity.users import hash_password; print(hash_password('EmployeePasswd'))"

# psql insert
docker compose exec postgres psql -U catfish -d catfish -c \
  "INSERT INTO users (email, password_hash, name, department, tier, role, managed_departments) VALUES ('zhang.san@dahua.com', '刚生成的hash', '张三', '研发', 'employee', 'employee', '[]'::jsonb);"
```

**方式 B · vim yaml + wipe + restart** (适合首次批量 seed):
```bash
vim identity-server/config/users.yaml         # 追加员工
docker compose exec postgres psql -U catfish -d catfish -c "TRUNCATE users CASCADE;"
docker compose restart identity
```

**方式 C · Companion Admin API** (Phase 2 加, 现在没 UI):
- Companion 里 admin 用户看到 "员工管理" 面板, 点鼠标建
- Phase 2 未实现

---

## 4. 启动全栈 + 验证 (5 分钟)

### 4.1 一键启

```bash
# 启 7 个 service (nginx 跳过, 需要域名 + SSL 才启)
docker compose up -d postgres identity gateway skills-hub wiki-hub mcp-registry web
```

### 4.2 等 60 秒 warm up

```bash
sleep 60             # mac/Ubuntu
Start-Sleep 60       # Windows PowerShell
```

### 4.3 验证 service 状态

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
catfish-web            Up (unhealthy)    ← 已知, 只是 healthcheck 路径问题, 功能正常
```

### 4.4 verify 关键接口

```bash
# identity OIDC 服务发现
curl http://host.docker.internal:8998/.well-known/openid-configuration

# gateway 健康检查
curl http://host.docker.internal:8999/healthz

# web SPA 首页
# 浏览器打开: http://localhost:5173
```

**期望**: 4.4.1 返回 JSON (含 `"issuer":"http://host.docker.internal:8998"`), 4.4.2 返回 `{"status":"ok"}`, 4.4.3 浏览器看到 catfish web 首页.

---

## 5. 装 Companion 员工端 (每员工 3 分钟)

### 5.1 macOS 员工机

```bash
open ~/Downloads/Catfish\ Companion_0.18.0_aarch64.dmg
```
- 拖 **Catfish Companion.app** → **Applications**
- 首次启动: **右键 → 打开** (绕过 macOS 未签名警告)

### 5.2 Windows 员工机

- 双击 `Catfish Companion_0.18.0_x64_en-US.msi`
- 若 Windows Defender 弹窗, 点 **更多信息 → 仍要运行**
- 装完从开始菜单启动

---

## 6. Companion Onboarding (员工首次启动, 5 分钟)

**8 步引导**:

| Step | 内容 | 操作 |
|------|------|------|
| 0 | Welcome | 点 **开始 →** |
| **1** | **🌐 连接服务器 (关键!)** | 填 Gateway URL + Identity URL (见下) |
| 2 | 起名 + 选人设 | 例: 起名"小鲶", 选温柔人设 |
| 3 | SSO 登录 | 用 seed 账号 (见 6.2) |
| 4 | 选默认模型 | 推荐 `catfish-private-main` 或 `catfish-public-qwen` |
| 5 | Curator (画像同意) | 同意 或 跳过 |
| 6 | 文书目录 | 选员工的项目文件夹 或 跳过 |
| 7 | 试聊一句 | 输 "帮我总结今天的邮件" |

### 6.1 Step 1 服务器地址 (根据部署位置填)

**若员工机 = 服务端同机** (mac 单机, Docker Desktop):
- Gateway URL: `http://host.docker.internal:8999`
- Identity URL: `http://host.docker.internal:8998`

**若员工机 = 服务端异机** (客户端连远程 Ubuntu 服务器):
- Gateway URL: `http://<服务器 IP>:8999` (例: `http://192.168.10.20:8999`)
- Identity URL: `http://<服务器 IP>:8998`

**生产环境有域名 + SSL**:
- Gateway URL: `https://catfish.dahua.com`
- Identity URL: `https://catfish.dahua.com`

### 6.2 Step 3 登录用的 seed 账号

内置 2 个测试账号 (从 `identity-server/config/users.yaml` 首次启动自动 seed 到 PG):

| Email | 密码 | 角色 |
|-------|------|------|
| `chenhongbo@ffcs.cn` | `catfish123` | sysadmin (系统管理员) |
| `demo@ffcs.cn` | (需查 users.yaml 或改) | employee (普通员工) |

**建议**: 达华管理员用 sysadmin 账号登入 web UI (http://localhost:5173), 加达华专版账号后再让员工用. 详见第 11 节.

---

## 7. 测试功能 checklist (员工体验 4-6 周)

### 7.1 基础对话 (P0)
- [ ] 打开 Companion 主界面
- [ ] 输 "你好, 帮我总结今天邮件"
- [ ] 等待 AI 回复 (5-30 秒)

### 7.2 邮件集成 (P0)
- [ ] Companion "邮件" tab
- [ ] 连接邮箱 (IMAP / Exchange / Outlook 365)
- [ ] AI 自动分类 / 总结 / 起草回复

### 7.3 知识库 (P1)
- [ ] Companion "知识" tab
- [ ] 上传 markdown / PDF 文档
- [ ] 后续对话可以引用该文档

### 7.4 技能 (P1)
- [ ] Companion "技能" tab
- [ ] 浏览 Skills Hub 里可用技能
- [ ] 应用一个, 观察对话中效果

### 7.5 主动提醒 (P2)
- [ ] 让 Catfish 记 "明天 3 点客户会议"
- [ ] 到时应主动弹出提醒

### 7.6 MCP 连接器 (P2)
- [ ] Companion "连接" tab
- [ ] 订阅 Jira / GitLab / Filesystem
- [ ] AI 能调用外部工具

### 7.7 Web 管理端 (P1, 达华管理员用)
- [ ] 浏览器 http://localhost:5173 (mac) 或 http://服务器 IP:5173 (远程)
- [ ] 用 sysadmin 账号登录
- [ ] 查看: 员工列表 / 使用统计 / 审计日志

---

## 8. 故障排查

### 8.1 `docker compose ps` 显示 gateway/identity Restarting

```bash
docker compose logs gateway --tail 50
docker compose logs identity --tail 50
```

**常见根因 3 种**:
- `.env` 里 `INTERNAL_LLM_BASE_QWEN_MAIN` 等未设 → gateway config 加载挂 → 用第 3 节的 dummy 值
- `PG_PASSWORD` 首次启动后改过 → **pgdata volume 保留旧密码**, gateway 连不上 → 重置数据库 (见 8.4)
- `CATFISH_IDENTITY_ISSUER` 环境未配 → identity 返回 `0.0.0.0` 浏览器打不开 → 检查 `.env`

### 8.2 Companion 登录失败 "无法连接服务器"

- 检查 Step 1 填的 URL 是否正确 (mac 单机用 `host.docker.internal`, 远程用 IP)
- 或打开 Companion → **Dashboard → 服务器配置卡** → 改 URL, 重启 Companion

### 8.3 Companion 登录报 "密码错误"

- 确认账号 `chenhongbo@ffcs.cn` (含 @ffcs.cn)
- 密码 `catfish123` (纯小写, 无空格)
- 若仍挂: 查 identity log, 是否 seed 成功
  ```bash
  docker compose logs identity | grep seed
  # 期望: "PG 首次 seed 2 个用户从 yaml"
  ```

### 8.4 重置数据库 (Nuclear reset, 会丢所有数据)

**⚠️ 仅测试环境用**, 会删所有对话/员工数据:

```bash
docker compose down
docker volume rm catfish-central_pgdata      # 或 central_pgdata, 看具体名字
docker compose up -d
```

### 8.5 收集 log 报错给鲶鱼团队

```bash
docker compose logs > logs-$(date +%Y%m%d-%H%M).txt
```

发给 鲶鱼团队联系人 快速定位.

---

## 9. 日常运维

### 9.1 停止 (保留数据)

```bash
docker compose stop
```

### 9.2 重启

```bash
docker compose start
```

### 9.3 完全删除 (不保留数据, 需重新 load image 才能再跑)

```bash
docker compose down --volumes    # 危险, 删所有数据
```

### 9.4 查看日志

```bash
# 实时看 gateway 日志
docker compose logs -f gateway

# 看某段时间的日志
docker compose logs --since=1h gateway
```

### 9.5 数据备份 (每周)

```bash
# 备份 PG (员工数据都在这)
docker compose exec postgres pg_dump -U catfish catfish > backup-$(date +%Y%m%d).sql

# 备份 gateway_data (audit / quota / facts)
docker run --rm -v catfish-central_gateway_data:/data -v $(pwd):/backup \
  alpine tar -czf /backup/gateway_data-$(date +%Y%m%d).tar.gz -C /data .
```

---

## 10. 生产升级 · IP → 域名切换

测试期用 IP 简单快速. 达华正式上线时需要切域名 + SSL, 30 分钟窗口:

### 准备工作
1. 达华 IT 拿子域名 (例 `catfish.dahua.com`) + DNS A 记录指向服务器 IP
2. 申请 SSL 证书:
   - **Let's Encrypt** (免费, 需公网 80/443 可达): `certbot certonly`
   - 或**达华内部 CA**
   - 或**商业 cert** (阿里云 SSL / DigiCert)
3. 证书放 `~/catfish-central/certs/fullchain.pem` + `privkey.pem`
4. `cp nginx.conf.example nginx.conf`, 改 server_name

### 切换步骤

```bash
cd ~/catfish-central

# 1. 停 identity + gateway
docker compose stop identity gateway

# 2. 改 .env issuer
# CATFISH_IDENTITY_ISSUER=https://catfish.dahua.com
# CATFISH_OIDC_ISSUER=https://catfish.dahua.com

# 3. 启 nginx + identity + gateway
docker compose up -d nginx identity gateway

# 4. 通知员工重登
```

**员工端切换**:
- 打开 Companion → Dashboard → **服务器配置卡**
- URL 改成 `https://catfish.dahua.com`
- 保存 → 重启 Companion → 重新登录 (旧 token 因 issuer 变了自动失效)

---

## 11. 加达华专版账号

内置的 `chenhongbo@ffcs.cn` 是鲶鱼团队测试账号. 达华上线前应加自己账号.

### 方法 A · 改 users.yaml (适合首次 seed, 未启动过 central)

编 `~/catfish-central/identity-server/config/users.yaml`:

```yaml
users:
  # 保留 chenhongbo 让鲶鱼团队远程支持用
  - email: chenhongbo@ffcs.cn
    password_hash: $2b$12$lurtJgClagy68sidF76/uux2DvJwbYzNlbOBMHOIvmJNQG2N2WvM2
    name: 陈鸿波 (鲶鱼团队支持)
    department: engineering
    tier: sysadmin

  # 达华管理员
  - email: admin@dahua.com
    password_hash: <bcrypt hash, 见下方生成命令>
    name: 达华管理员
    department: management
    tier: sysadmin

  # 达华员工
  - email: user01@dahua.com
    password_hash: <bcrypt hash>
    name: 达华员工 01
    department: sales
    tier: employee
```

**生成 bcrypt 密码 hash**:

```bash
docker compose exec identity python -c \
  "from catfish_identity.users import hash_password; print(hash_password('YOUR_PASSWORD'))"
```

改完 `docker compose restart identity`, 若 PG 首次 seed 会自动灌入.

### 方法 B · Web UI 加账号 (适合已启动, 想在线加)

- 浏览器 http://localhost:5173 (或 http://服务器 IP:5173)
- 用 `chenhongbo@ffcs.cn` / `catfish123` 登录
- 进 **管理** tab → **员工列表** → **添加员工**
- 填 email / 密码 / 部门 / 角色, 保存

---

## 12. 反馈 + 支持

**试用期建议**:
- 首批 10-20 名种子员工, 4-6 周
- 每周内部反馈会 1 次
- 鲶鱼团队每 2 周现场/远程支持 1 次
- 试用满收集: 满意度问卷 + 使用数据 + 改进需求

**联系鲶鱼团队**:
- 邮箱: [鲶鱼团队邮箱]
- 微信: [鲶鱼团队联系人]

**紧急故障响应 SLA**:
- P0 (系统全挂): 1 小时响应
- P1 (核心功能挂): 4 小时响应
- P2 (咨询 / 优化): 2 工作日响应

---

## 附录 A · 端口清单

| 端口 | 服务 | 用途 |
|------|------|------|
| 5432 | postgres | 数据库 (只本机可访问) |
| 8994 | wiki-hub | 企业知识库 API |
| 8996 | mcp-registry | MCP 连接器 API |
| 8997 | skills-hub | 技能库 API |
| **8998** | **identity** | **OIDC SSO 登录, 员工浏览器需可访问** |
| **8999** | **gateway** | **主 API, 员工 Companion 直连** |
| 5173 | web | 管理 UI (SPA) |
| 80 / 443 | nginx | 反代 (生产, 需要域名 + SSL 才启) |

**员工机需要能访问**: 8999 (Gateway) + 8998 (Identity). 生产走 443 nginx.

---

## 附录 B · .env 关键字段

```env
# 数据库
PG_USER=catfish
PG_PASSWORD=<16+ 位强密码>
PG_DB=catfish

# OIDC (客户端 + 服务端共享, 必须一致)
CATFISH_IDENTITY_ISSUER=<URL, 员工浏览器 + gateway container 双向可达>
CATFISH_OIDC_ISSUER=<同上>
CATFISH_OIDC_AUDIENCE=catfish-gateway

# 内网 LLM (若达华有部署, 填真实地址)
INTERNAL_LLM_KEY=<达华内网 LLM 平台 API key>
INTERNAL_LLM_BASE_QWEN_MAIN=<达华内网 Qwen 主模型 URL>
INTERNAL_LLM_BASE_QWEN_VISION=<达华内网 Qwen 多模态 URL>
INTERNAL_LLM_BASE_BGE_M3=<达华内网 embedding URL>

# 公网 LLM (可选 fallback)
DASHSCOPE_API_KEY=<阿里通义千问 key, 可选>
GEMINI_API_KEY=<Google Gemini key, 可选>

# uvicorn worker 数 (按 CPU 核数调)
GATEWAY_WORKERS=4               # 4 vCPU 用 4, 8 vCPU 用 8
IDENTITY_WORKERS=2              # 一般 2 够, 1000+ 员工用 4
```

---

## 附录 C · 数据存储位置

| 数据 | 位置 | 备份优先级 |
|------|------|-----------|
| PG 数据库 | volume `pgdata` | P0 (每天) |
| Gateway audit / quota / facts | volume `gateway_data` | P1 (每周) |
| Skills Hub 上传文件 | volume `hubdata` | P1 (每周) |
| Wiki 知识文档 | volume `wikidata` | P0 (每天) |
| 员工端对话历史 | 员工电脑本地 (数据本地化) | 员工自己备份 |

**核心承诺**: **员工个人数据 (对话 / 邮件 / 文件) 全存员工本机**, 中央零留存. 达华信安审计只需管中央 3 个 volume + PG.

---

**祝试用顺利! 有问题随时联系.**
