#!/usr/bin/env bash
# 达华 POC · Catfish 中央服务一键装机脚本 (P3.5.79+ 7/23 血案后写)
#
# 用途:
#   客户 IT 拿到 delivery tar 后 · 一条命令完成:
#     1. 探测 server IP (或指定)
#     2. 从 .env.example 生成 .env · 自动替换 <server-ip>
#     3. 装 image tar (若未装)
#     4. 生成自签 cert (若走 HTTPS · 可选)
#     5. docker compose up · verify
#
# 用法:
#   # 自动探测 IP · 走 HTTP (dev/test 用)
#   bash setup.sh
#
#   # 显式指定 IP + 自签 cert (推荐 · 生产 POC 走 HTTPS)
#   SERVER_IP=192.168.100.50 ENABLE_HTTPS=1 bash setup.sh
#
#   # 只重生成 .env (不动 docker · 已装好想改 IP 时用)
#   REGEN_ENV_ONLY=1 SERVER_IP=192.168.100.50 bash setup.sh
#
# 前置:
#   - Ubuntu 22.04+ / CentOS 8+ · x86_64
#   - Docker 24+ + docker compose plugin
#   - 4 vCPU / 8GB RAM / 100GB disk (推荐)
#   - 内网可访问 (不需公网)
#
# 装完验证 (脚本尾自动跑):
#   - identity discovery 返新 issuer
#   - gateway /healthz 200
#   - web /healthz 200
#   - 浏览器打 http://<server-ip>:5173 能看到登录页

set -euo pipefail

# ── 参数 · env 可 override ──────────────────────────────
SERVER_IP="${SERVER_IP:-}"
ENABLE_HTTPS="${ENABLE_HTTPS:-0}"
REGEN_ENV_ONLY="${REGEN_ENV_ONLY:-0}"
IMAGE_TAR="${IMAGE_TAR:-}"   # 若未装 image · 指到 image tar 路径

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "═══════════════════════════════════════════════════════"
echo "  Catfish 中央服务一键装机 · 达华 POC (P3.5.79+ 7/23)"
echo "═══════════════════════════════════════════════════════"

# ── 1. 探测 / 确认 server IP ─────────────────────────────
if [ -z "$SERVER_IP" ]; then
    # 探测: 取第一条非 loopback / 非 docker 的 IPv4
    SERVER_IP=$(hostname -I 2>/dev/null | tr ' ' '\n' | \
        grep -vE '^(127\.|172\.1[7-9]\.|172\.2[0-9]\.|172\.3[0-1]\.|169\.254\.)' | \
        head -1)
    if [ -z "$SERVER_IP" ]; then
        echo "❌ 无法自动探测 server IP · 请显式指定:"
        echo "   SERVER_IP=192.168.x.x bash setup.sh"
        exit 1
    fi
    echo "→ 自动探测 server IP: $SERVER_IP"
    read -rp "  确认? (y/n · 回车 = y): " confirm
    if [ "${confirm:-y}" != "y" ] && [ "${confirm:-y}" != "Y" ]; then
        echo "  请显式指定: SERVER_IP=192.168.x.x bash setup.sh"
        exit 1
    fi
else
    echo "→ 使用指定 IP: $SERVER_IP"
fi

# ── 2. 从 .env.example 生成 .env ──────────────────────────
if [ ! -f .env.example ]; then
    echo "❌ 找不到 .env.example · 请确认在 delivery/dahua-poc/ 目录跑"
    exit 1
fi

# HTTP vs HTTPS
if [ "$ENABLE_HTTPS" = "1" ]; then
    ISSUER_URL="https://$SERVER_IP"
    WEB_URL="https://$SERVER_IP"
else
    ISSUER_URL="http://$SERVER_IP:8998"
    WEB_URL="http://$SERVER_IP:5173"
fi

if [ -f .env ]; then
    echo "→ .env 已存在 · 备份到 .env.bak.$(date +%s)"
    cp .env ".env.bak.$(date +%s)"
fi

cp .env.example .env
sed -i \
    -e "s|<server-ip>|$SERVER_IP|g" \
    -e "s|^CATFISH_OIDC_ISSUER=.*|CATFISH_OIDC_ISSUER=$ISSUER_URL|" \
    -e "s|^CATFISH_IDENTITY_ISSUER=.*|CATFISH_IDENTITY_ISSUER=$ISSUER_URL|" \
    -e "s|^CATFISH_IDENTITY_CORS_ORIGINS=.*|CATFISH_IDENTITY_CORS_ORIGINS=$WEB_URL|" \
    .env

echo "→ 生成 .env · 关键字段:"
grep -E "^CATFISH_(OIDC|IDENTITY)_" .env | sed 's/^/    /'

# PG_PASSWORD 若空 · 生成随机
if grep -qE "^PG_PASSWORD=$|^PG_PASSWORD= *$" .env; then
    RAND_PW=$(openssl rand -hex 16 2>/dev/null || head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 24)
    sed -i "s|^PG_PASSWORD=.*|PG_PASSWORD=$RAND_PW|" .env
    echo "→ PG_PASSWORD 空 · 已生成随机: $RAND_PW  ← ★ 记好 · 数据库唯一密码"
fi

# JWT_SIGNING_KEY 若空 · 生成随机
if grep -qE "^JWT_SIGNING_KEY=$" .env; then
    JWT_KEY=$(openssl rand -hex 32 2>/dev/null || head -c 64 /dev/urandom | base64 | tr -d '/+=' | head -c 64)
    sed -i "s|^JWT_SIGNING_KEY=.*|JWT_SIGNING_KEY=$JWT_KEY|" .env
    echo "→ JWT_SIGNING_KEY 空 · 已生成随机 (64 字符)"
fi

# ── 若只重生 .env · 到此为止 ───────────────────────────
if [ "$REGEN_ENV_ONLY" = "1" ]; then
    echo ""
    echo "✓ .env 已重生 · REGEN_ENV_ONLY=1 模式退出"
    echo "  下一步 · 重启涉及服务:"
    echo "    docker compose up -d --force-recreate identity gateway web"
    exit 0
fi

# ── 2.5 · 生成 users.yaml 含 admin (P3.5.79+ 7/23 199 blood catch) ──────
# delivery tar 里只带 users.yaml.example (真 hash 不进 tar · 安全 · 军规).
# 装机时用 docker load 好的 identity image 里的 bcrypt 生成真 hash · 写 users.yaml.
# 首次 identity 启动 · seed_pg_from_yaml_if_empty() 读 yaml · 灌 admin 进 PG.
# 后续 identity 从 PG 读 · yaml 忽略. 员工首次登进后立即改密.
IDENTITY_CFG="$SCRIPT_DIR/identity-server/config"
mkdir -p "$IDENTITY_CFG"
ADMIN_PW="${ADMIN_PASSWORD:-catfish_2026}"
ADMIN_HASH=""   # P3.5.79+ (7/23): 显式初始化 · 防 set -u 未定义变量挂

if [ ! -f "$IDENTITY_CFG/users.yaml" ]; then
    # 用 identity image 里的 python + bcrypt 生成 hash (host 不需 pip install bcrypt)
    if docker image inspect catfish-identity:0.1.0 >/dev/null 2>&1; then
        ADMIN_HASH=$(docker run --rm catfish-identity:0.1.0 python3 -c \
            "from catfish_identity.users import hash_password; print(hash_password('$ADMIN_PW'))" 2>/dev/null)
    fi
    if [ -z "$ADMIN_HASH" ]; then
        # fallback · catfish_2026 硬编 bcrypt hash (若 image 未 load · 兜底)
        ADMIN_HASH='$2b$12$vN9eb7i7voFcdwzM8W5SOuVmjV3ETViwyA9DDAVQXz1JXhUBFzV2m'
        [ "$ADMIN_PW" != "catfish_2026" ] && \
            echo "  ⚠ image 未 load · 用兜底 hash · 密码强制为 catfish_2026"
        ADMIN_PW="catfish_2026"
    fi

    cat > "$IDENTITY_CFG/users.yaml" <<EOF
# 装机时 setup.sh 自动生成 · $(date '+%Y-%m-%d %H:%M')
# admin 首次登进后立即改密 (Companion 内建改密 UI · 或 sysadmin 面板)
users:
  - email: admin@catfish.com
    password_hash: $ADMIN_HASH
    name: 系统管理员
    department: IT
    role: sysadmin
EOF
    chmod 600 "$IDENTITY_CFG/users.yaml"
    echo "→ users.yaml 生成 · sysadmin: admin@catfish.com / $ADMIN_PW"
    echo "  ⚠ 首次登进立即改密 (delivery/docs 里 SOP 有指引)"
else
    echo "→ users.yaml 已存在 · skip (跨装机保留)"
fi

# ── 2.6 · gateway config 存在性 check (P3.5.79+ 7/23 199 blood catch v2) ─
# gateway 启动读 /app/config/models.yaml + roles.yaml. 若 delivery tar 里没打进
# llm-gateway/config · docker mount 空目录 · gateway worker startup 挂反复 die.
# 检 · 若空 · 明报 error 让 IT 从 delivery tar / rsync 补齐.
GATEWAY_CFG="$SCRIPT_DIR/llm-gateway/config"
mkdir -p "$GATEWAY_CFG"
if [ ! -f "$GATEWAY_CFG/models.yaml" ] || [ ! -f "$GATEWAY_CFG/roles.yaml" ]; then
    echo "❌ llm-gateway/config 缺 models.yaml 或 roles.yaml · gateway 启动会挂"
    echo "   fix (2 选 1):"
    echo "     A) 重解压 delivery tar (最新版含 config)"
    echo "     B) 从 catfish 源码 rsync:"
    echo "        rsync -av <mac>:person_task/catfish/central/llm-gateway/config/ $GATEWAY_CFG/"
    echo ""
    read -rp "  确认已补齐后回车继续? (Ctrl+C 中止): "
    if [ ! -f "$GATEWAY_CFG/models.yaml" ]; then
        echo "❌ 仍缺 models.yaml · 中止"
        exit 1
    fi
fi
echo "→ gateway config OK ($(ls "$GATEWAY_CFG" | wc -l | tr -d ' ') files)"

# ── 3. HTTPS 自签 cert (若 ENABLE_HTTPS=1) ────────────────
if [ "$ENABLE_HTTPS" = "1" ]; then
    mkdir -p certs
    if [ ! -f certs/cert.pem ] || [ ! -f certs/key.pem ]; then
        echo "→ 生成自签 cert (含 IP $SERVER_IP)..."
        openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
            -keyout certs/key.pem -out certs/cert.pem \
            -subj "/CN=$SERVER_IP" \
            -addext "subjectAltName=IP:$SERVER_IP,DNS:catfish.local" \
            2>/dev/null
        chmod 600 certs/key.pem
        echo "  ✓ certs/cert.pem · certs/key.pem 已生成"
    else
        echo "→ 自签 cert 已存在 · skip"
    fi
fi

# ── 4. 装 image tar (若指定 / 若 images/ 里有) ──────────
# P3.5.79+ (7/23 199 blood catch v2): IMAGE_TAR 未传 · 自动探 images/*.tar.gz.
# 用户命令 `\ ` 续行错时 · IMAGE_TAR 没进 env · 老版直接 skip load · 后面 docker
# compose up 去 docker.io pull · 内网挂. 现在自动探 · 兜底更稳.
if [ -z "$IMAGE_TAR" ]; then
    AUTO_TAR=$(ls "$SCRIPT_DIR/images/"*.tar.gz 2>/dev/null | head -1)
    if [ -n "$AUTO_TAR" ]; then
        echo "→ 自动发现 image tar: $(basename "$AUTO_TAR") (IMAGE_TAR 未传 · 兜底)"
        IMAGE_TAR="$AUTO_TAR"
    fi
fi

if [ -n "$IMAGE_TAR" ]; then
    if [ ! -f "$IMAGE_TAR" ]; then
        echo "❌ IMAGE_TAR=$IMAGE_TAR 找不到"
        exit 1
    fi
    # 若关键 image 本地已在 (幂等 · 二次跑不重 load) · skip
    if docker image inspect catfish-gateway:0.1.1 >/dev/null 2>&1; then
        echo "→ image 本地已存 · skip load (若要强 load · docker rmi 后重跑)"
    else
        echo "→ load image tar: $IMAGE_TAR (~5-15 min)"
        if [[ "$IMAGE_TAR" =~ \.gz$ ]]; then
            gunzip -c "$IMAGE_TAR" | docker load
        else
            docker load < "$IMAGE_TAR"
        fi
        echo "  ✓ 装完"
    fi
    echo "  现有 image:"
    docker images | grep -E "catfish|postgres:16-alpine" | sed 's/^/    /'
fi

# ── 4.5 · verify 关键 image 本地存 (fail loud · 别让 docker compose 去 pull 挂) ──
MISSING_IMG=""
for img in catfish-identity:0.1.0 catfish-gateway:0.1.1 catfish-web:0.1.0 \
           catfish-skills-hub:0.1.0 catfish-mcp-registry:0.1.0 catfish-wiki-hub:0.1.0 \
           postgres:16-alpine; do
    if ! docker image inspect "$img" >/dev/null 2>&1; then
        MISSING_IMG="$MISSING_IMG $img"
    fi
done
if [ -n "$MISSING_IMG" ]; then
    echo ""
    echo "❌ 本地缺 image ·$MISSING_IMG"
    echo "   fix · 指定 IMAGE_TAR 或放 tar 到 images/ 目录:"
    echo "     IMAGE_TAR=./images/dahua-poc-central-<arch>-<date>.tar.gz bash setup.sh"
    echo "   (内网机不能连 docker.io · 必须本地 load)"
    exit 1
fi

# ── 5. docker compose up ──────────────────────────────
# P3.5.79+ (7/23 199 blood catch): 用 --force-recreate 强重建 container · 让新 .env
# 里的 GATEWAY_WORKERS / CATFISH_OIDC_ISSUER / CATFISH_IDENTITY_CORS_ORIGINS 立即生效.
# `docker compose up -d` 不加 --force-recreate 时 · 若 container 已存 · 只 restart ·
# env 是创 container 时读的 · 老值不换 · IT 反复怀疑 "为啥 workers 还是 4 / CORS 还挂".
echo ""
echo "→ docker compose up --force-recreate (不含 nginx · 若走 HTTPS 后续再加)..."
if [ "$ENABLE_HTTPS" = "1" ]; then
    docker compose up -d --force-recreate
else
    docker compose up -d --force-recreate $(docker compose config --services | grep -v '^nginx$')
fi

# ── 6. verify ─────────────────────────────────────────
echo ""
echo "→ 等 30s · 服务启动..."
sleep 30

echo ""
echo "── verify ──"
echo -n "  identity discovery: "
if curl -sf "$ISSUER_URL/.well-known/openid-configuration" -H "Origin: $WEB_URL" > /dev/null 2>&1; then
    curl -s "$ISSUER_URL/.well-known/openid-configuration" | grep -oE '"issuer":"[^"]+"' | head -1
else
    echo "❌ 挂 · 看 docker logs catfish-identity"
fi

echo -n "  gateway healthz:    "
if curl -sf "http://$SERVER_IP:8999/healthz" > /dev/null 2>&1; then
    echo "✓ 200"
else
    echo "❌ 挂 · 看 docker logs catfish-gateway"
fi

echo -n "  web /config.js:     "
if curl -sf "$WEB_URL/config.js" 2>/dev/null | grep -q "$SERVER_IP"; then
    echo "✓ oidcIssuer 含 $SERVER_IP"
else
    echo "⚠ 待查 · 看 docker exec catfish-web cat /usr/share/nginx/html/config.js"
fi

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  装机完毕 · 员工浏览器打:"
echo "    $WEB_URL"
echo ""
echo "  默认 sysadmin: admin@catfish.com / catfish_2026"
echo "  ★ 首次登录后立刻改密"
echo ""
if [ "$ENABLE_HTTPS" != "1" ]; then
    echo "  ⚠ 走 HTTP · Chrome/Edge 需 chrome://flags 加 unsafely-treat-insecure-origin-as-secure"
    echo "     · Safari 无此 flag · 生产建议 ENABLE_HTTPS=1 重跑"
fi
echo "═══════════════════════════════════════════════════════"
