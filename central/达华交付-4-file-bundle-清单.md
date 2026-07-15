# 达华测试交付 · 4-file bundle 打包清单

**目标**: 一次性给达华 4 个 file, 支持 macOS + Windows 员工端 · Docker 中央端

## 4 个 file

| # | File | 大小 | 来源 |
|---|---|---|---|
| 1 | `Catfish Companion_0.18.0_aarch64.dmg` | ~129 MB | 本机 build (`~/person_task/catfish/edge/companion-app/src-tauri/target/release/bundle/dmg/`) |
| 2 | `catfish-companion_0.18.0_x64_en-US.msi` | ~147 MB | CircleCI Build #12 artifacts |
| 3 | `catfish-central-images.tar.gz` | ~2 GB (gzip 后) | `docker save` 打 7 个 image |
| 4 | `catfish-central-config.tar.gz` | ~10 MB | central 配置 + .env.production.example + 部署手册 |

## 打包命令 (明早跑, 磁盘要 5GB 空闲)

### File 1 · dmg (已 ready)

```bash
ls -lh "$HOME/person_task/catfish/edge/companion-app/src-tauri/target/release/bundle/dmg/Catfish Companion_0.18.0_aarch64.dmg"
```

### File 2 · msi (从 CircleCI 下)

```bash
# CircleCI 界面找 Build #12 (或最新绿) 的 Artifacts, 下 catfish-companion_0.18.0_x64_en-US.msi
# 或用 CircleCI CLI:
# circleci artifacts fetch <build-id> --output ~/Downloads/catfish-companion_0.18.0_x64_en-US.msi
```

### File 3 · docker images tar

**先 build 全部 image** (若已 build 过, docker compose build 会 cache):

```bash
cd ~/person_task/catfish/central
docker compose build   # 5-10 min, build 6 个 custom image
docker pull postgres:16-alpine
```

**save + 压缩** (磁盘要 5GB 临时空间, 最终 gzip 后 ~2GB):

```bash
cd ~/person_task/catfish/central
docker save \
  postgres:16-alpine \
  catfish-identity:0.1.0 \
  catfish-gateway:0.1.0 \
  catfish-skills-hub:0.1.0 \
  catfish-web:0.1.0 \
  catfish-mcp-registry:0.1.0 \
  catfish-wiki-hub:0.1.0 \
  | gzip > ~/Downloads/catfish-central-images.tar.gz

ls -lh ~/Downloads/catfish-central-images.tar.gz
```

**达华解包命令** (对方跑):

```bash
gunzip -c catfish-central-images.tar.gz | docker load
# 或
docker load -i catfish-central-images.tar.gz  # 若不 gzip
```

### File 4 · config tar

**只带 config, 排除 venv/node_modules/logs/db** (军规 #58 · 不带源码):

```bash
cd ~/person_task/catfish
tar czf ~/Downloads/catfish-central-config.tar.gz \
  --exclude='*/venv' \
  --exclude='*/node_modules' \
  --exclude='*/__pycache__' \
  --exclude='*/.git' \
  --exclude='*/dist' \
  --exclude='*.log' \
  --exclude='*.db' \
  --exclude='*.env' \
  central/docker-compose.yml \
  central/.env.production.example \
  central/identity-server/config/ \
  central/llm-gateway/config/ \
  central/skills-hub/config/ \
  central/mcp-registry/config/ \
  central/wiki-hub/config/ \
  central/README.md \
  central/README-达华测试部署.md 2>/dev/null || echo "部分目录可能不存在, 跳过"

ls -lh ~/Downloads/catfish-central-config.tar.gz
tar tzf ~/Downloads/catfish-central-config.tar.gz | head -30
```

## 交付 · 达华那边的 4 步部署

```bash
# 1. 装 Companion (mac 员工)
open Catfish\ Companion_0.18.0_aarch64.dmg

# 1. 装 Companion (Windows 员工)
msiexec /i catfish-companion_0.18.0_x64_en-US.msi

# 2. 装中央服务端 (达华 IT 一台 Ubuntu/CentOS 服务器)
mkdir -p ~/catfish && cd ~/catfish
tar xzf catfish-central-config.tar.gz
gunzip -c catfish-central-images.tar.gz | docker load
cp central/.env.production.example central/.env
vim central/.env      # 改 PG_PASSWORD / OIDC issuer / API key
cd central && docker compose up -d

# 3. verify (等 30s 服务健康)
curl http://localhost:8999/healthz    # gateway
curl http://localhost:8998/.well-known/openid-configuration
curl http://localhost:8997/healthz    # skills-hub

# 4. Companion 首启, Onboarding Step 1 填 gateway URL: http://<服务器IP>:8999
```

## 军规 checklist

- ✅ 不带 src 源码 (只带 config + Docker image binary)
- ✅ .env 里 secret 用 `.env.production.example` 模板, 空值达华填
- ✅ postgres 用官方 image, 不自己 fork
- ✅ 各服务 image 里 seed 数据留白 (第一个用户 sign up 时创建 admin)
- ⚠️ 若达华 IT 环境国内 registry blocked, docker pull postgres 可能挂 → 需 `postgres:16-alpine` 已在 tar 里 (上面 save 命令已含)

## 预估时间 (打包侧)

- File 1: 0 (已有)
- File 2: 5 min (CircleCI download)
- File 3: 15 min (build + save + gzip)
- File 4: 1 min (tar)

**总计 · 明早 20-30 min 打完 · 通过网盘 or SFTP 发达华**.
