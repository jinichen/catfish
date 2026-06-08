#!/usr/bin/env bash
# 鲶鱼中央服务 · 一键正式部署 (6/9 鸿波 BL-F10 后).
#
# 干啥:
#   1. 体检 .env (必填字段全填了 + 密码不是占位)
#   2. 体检宿主机 (docker 装了, 端口没冲突, 资源够)
#   3. docker compose build + up -d
#   4. 等所有 service 健康 (有超时, 防卡死脚本)
#   5. smoke test (curl healthcheck + 1 个真请求路径)
#   6. 汇报最终状态 + 给员工 IT 的 URL 清单
#
# 用法:
#   cd central/
#   cp .env.production.example .env       # 第一次
#   nano .env                              # 填密码/OIDC/API key
#   bash deploy.sh                         # 部署
#   bash deploy.sh status                  # 看现状, 不动 stack
#   bash deploy.sh logs gateway            # tail gateway log
#   bash deploy.sh down                    # 关 stack (保留 volume)
#
# 跟之前部署文档区别:
#   - DEPLOYMENT-RUNBOOK.md 是给客户 IT 的纸面 runbook (10+ 步手写命令)
#   - PRODUCTION-DEPLOYMENT.md 是给 docs reader 的指南
#   - 这个 deploy.sh 把 runbook 自动化, 防客户漏步 / 装错顺序
#
# 跟 manifesto 对齐:
#   - 不出公司: 全部 docker stack 跑客户机房, 不调外网 (公网 LLM API key 留空也能跑)
#   - 数据零出端: PG 只本机暴露 (127.0.0.1:5432), gateway 走 nginx SSL
#   - 员工主权: 没员工数据存中央 (gateway 只存 metadata audit / quota event, 不存 prompt)

set -euo pipefail

# ── 颜色 (输出可读) ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

err() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
ok() { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
info() { echo -e "${BLUE}[INFO]${NC} $*"; }
step() { echo -e "\n${BOLD}══ $* ══${NC}"; }

# ── cd 到 script 所在目录 (能在任何地方调用) ──
cd "$(dirname "$0")"
COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env"

# ── 子命令 ──
SUBCMD="${1:-deploy}"

case "$SUBCMD" in
    status)
        docker compose -f "$COMPOSE_FILE" ps
        exit 0
        ;;
    logs)
        SVC="${2:-gateway}"
        docker compose -f "$COMPOSE_FILE" logs --tail=100 -f "$SVC"
        exit 0
        ;;
    down)
        warn "关 stack (保留 volume, PG 数据安全)"
        docker compose -f "$COMPOSE_FILE" down
        ok "stack 已关. 数据 volume (pgdata/gateway_data/hubdata) 保留, 下次 up -d 自动接续."
        exit 0
        ;;
    nuke)
        # 危险: 删 volume = 删 PG 数据 + audit log + 文件 facts
        warn "${BOLD}危险${NC}: 这会删所有 volume (PG 数据 + audit log + facts), 不可恢复"
        read -p "确认要 nuke? 输 'nuke' 继续: " confirm
        if [ "$confirm" != "nuke" ]; then
            info "取消"
            exit 0
        fi
        docker compose -f "$COMPOSE_FILE" down -v
        ok "stack + volume 全删. 下次跑 deploy.sh 从空开始."
        exit 0
        ;;
    deploy|"")
        # 主流程, 继续往下
        ;;
    *)
        cat <<EOF
用法:
  $0 [deploy]    部署 (默认)
  $0 status      看 service 状态
  $0 logs [svc]  tail 日志 (svc 默认 gateway)
  $0 down        关 stack (保留 volume)
  $0 nuke        删 stack + 所有 volume (危险, 删 PG 数据)
EOF
        exit 1
        ;;
esac


# ── Step 1: 体检宿主机 ──
step "1/6  宿主机体检"

# 1.1 docker / docker compose 装了?
if ! command -v docker >/dev/null 2>&1; then
    err "docker 没装. 装: https://docs.docker.com/engine/install/"
    exit 1
fi
ok "docker: $(docker --version | head -1)"

if ! docker compose version >/dev/null 2>&1; then
    err "docker compose v2 没装 (docker-compose v1 也不行, 必须 plugin 形式)"
    err "  Ubuntu/Debian: sudo apt install docker-compose-plugin"
    exit 1
fi
ok "docker compose: $(docker compose version --short)"

# 1.2 端口冲突? 6/9 BL-WEB+MCP+BROKER-DEPLOY 加 8996 mcp-registry.
# 注: secret-broker 8995 跟 web :80 (容器内) 都不绑宿主机, 不检查.
check_port() {
    local port=$1
    local svc=$2
    if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE ":${port}\$|:${port}$"; then
        err "端口 ${port} 已被占用 (${svc} 需要). 用 'ss -tlnp | grep :${port}' 找占用进程"
        return 1
    fi
    return 0
}
PORT_OK=true
for p in 80 443 8996 8997 8998 8999; do
    case $p in
        80)   svc="nginx http" ;;
        443)  svc="nginx https" ;;
        8996) svc="mcp-registry" ;;
        8997) svc="skills-hub" ;;
        8998) svc="identity" ;;
        8999) svc="gateway" ;;
    esac
    check_port "$p" "$svc" || PORT_OK=false
done
if ! $PORT_OK; then
    err "端口冲突, 改 docker-compose.yml ports 或关占用进程"
    exit 1
fi
ok "端口 80 / 443 / 8997 / 8998 / 8999 全空"

# 1.3 资源够? (至少 4 vCPU + 4GB free RAM, BL-F10 实测最低)
TOTAL_MEM_MB=$(free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo "0")
if [ "$TOTAL_MEM_MB" -gt 0 ] && [ "$TOTAL_MEM_MB" -lt 4096 ]; then
    warn "总内存 ${TOTAL_MEM_MB}MB < 4GB. BL-F10 实测 1000 user 峰值 ~1GB, 算上 PG+identity+nginx 推荐 ≥ 4GB"
fi
CPU_COUNT=$(nproc 2>/dev/null || echo "0")
if [ "$CPU_COUNT" -gt 0 ] && [ "$CPU_COUNT" -lt 4 ]; then
    warn "vCPU 只 $CPU_COUNT (推荐 ≥ 4). gateway 默认 4 worker, < 4 vCPU 会过分配."
    warn "  改 .env: GATEWAY_WORKERS=$CPU_COUNT IDENTITY_WORKERS=1"
fi
ok "宿主机: ${CPU_COUNT} vCPU / ${TOTAL_MEM_MB}MB mem"


# ── Step 2: 体检 .env ──
step "2/6  .env 体检"

if [ ! -f "$ENV_FILE" ]; then
    err ".env 不存在. 先跑: cp .env.production.example .env, 然后改密码/OIDC/API key"
    exit 1
fi
ok ".env 存在"

# 6/9 BL-BROKER-DEPLOY: CATFISH_SECRET_MASTER_KEY 自动生成 (首次部署)
# 检测占位 → openssl rand -base64 32 生成 → sed 写回 .env. 避免客户 IT 漏步.
MK_VAL=$(grep -E "^CATFISH_SECRET_MASTER_KEY=" "$ENV_FILE" | head -1 | sed 's/^CATFISH_SECRET_MASTER_KEY=//' || echo "")
if [ -z "$MK_VAL" ] || [ "$MK_VAL" = "CHANGE_ME_RUN_DEPLOY_SH" ] || [ "$MK_VAL" = "CHANGE_ME" ]; then
    info "首次部署: 自动生成 CATFISH_SECRET_MASTER_KEY (AES-256 主密钥)..."
    if ! command -v openssl >/dev/null 2>&1; then
        err "openssl 没装, 无法生成 master key. 手工: openssl rand -base64 32 改 .env"
        exit 1
    fi
    NEW_KEY=$(openssl rand -base64 32)
    # 跨平台 sed (GNU sed vs BSD sed). 用临时文件最稳.
    TMP_ENV=$(mktemp)
    awk -v new_key="CATFISH_SECRET_MASTER_KEY=$NEW_KEY" '
        /^CATFISH_SECRET_MASTER_KEY=/ { print new_key; next }
        { print }
    ' "$ENV_FILE" > "$TMP_ENV"
    mv "$TMP_ENV" "$ENV_FILE"
    chmod 600 "$ENV_FILE"   # .env 含密码 / master key, 严控权限
    ok "CATFISH_SECRET_MASTER_KEY 已生成并写回 .env (chmod 600)"
    warn "**备份 .env 文件**: master key 丢了所有员工 OAuth 凭据不可解密"
fi

# 必填 + 不能是占位
declare -A REQUIRED=(
    [PG_PASSWORD]="CHANGE_ME_TO_STRONG_PASSWORD"
    [CATFISH_OIDC_ISSUER]="https://catfish.yourcompany.com/sso"
    [SKILLS_HUB_TOKEN]="CHANGE_ME_RANDOM_32_CHARS"
    [INTERNAL_LLM_KEY]="CHANGE_ME"
    # CATFISH_SECRET_MASTER_KEY 上面已自动生成 + 兜底检查, 不进 REQUIRED
)
PLACEHOLDER_FOUND=()
for key in "${!REQUIRED[@]}"; do
    placeholder="${REQUIRED[$key]}"
    val=$(grep -E "^${key}=" "$ENV_FILE" | head -1 | sed "s/^${key}=//" || echo "")
    if [ -z "$val" ] || [ "$val" = "$placeholder" ]; then
        PLACEHOLDER_FOUND+=("$key")
    fi
done
if [ ${#PLACEHOLDER_FOUND[@]} -gt 0 ]; then
    err ".env 里这些字段还是占位 / 空, 必须改成真值:"
    for k in "${PLACEHOLDER_FOUND[@]}"; do
        echo "    - $k"
    done
    exit 1
fi
ok ".env 必填字段全填了 (密码/OIDC/SKILLS_HUB_TOKEN/INTERNAL_LLM_KEY/SECRET_MASTER_KEY)"

# PG_PASSWORD 强度 (≥ 16 位)
PG_PASS=$(grep -E "^PG_PASSWORD=" "$ENV_FILE" | head -1 | sed 's/^PG_PASSWORD=//')
if [ "${#PG_PASS}" -lt 16 ]; then
    warn "PG_PASSWORD 只 ${#PG_PASS} 位, 推荐 ≥ 16 位 + 含大小写+数字+符号"
fi


# ── Step 3: build + up ──
step "3/6  build + up -d"

info "build (首次 ~3min, 之后 layer cache 命中 < 30s)..."
docker compose -f "$COMPOSE_FILE" build --pull

info "up -d (起 pg → identity → secret-broker → gateway → mcp-registry → skills-hub → web → nginx)..."
docker compose -f "$COMPOSE_FILE" up -d
ok "stack 已起, 进入健康检查"


# ── Step 4: 等 healthy ──
step "4/6  等所有 service healthy (超时 120s)"

wait_healthy() {
    local svc=$1
    local timeout=${2:-120}
    local elapsed=0
    while [ $elapsed -lt $timeout ]; do
        status=$(docker inspect --format='{{.State.Health.Status}}' "catfish-${svc}" 2>/dev/null || echo "missing")
        case "$status" in
            healthy)
                ok "${svc}: healthy (${elapsed}s)"
                return 0
                ;;
            unhealthy)
                err "${svc}: unhealthy, 看 log: docker compose logs ${svc} | tail -30"
                return 1
                ;;
            starting|"")
                # 还在启动
                ;;
            missing)
                # nginx 没 healthcheck, skip
                if [ "$svc" = "nginx" ]; then
                    ok "${svc}: (无 healthcheck, 跳过)"
                    return 0
                fi
                ;;
        esac
        sleep 3
        elapsed=$((elapsed + 3))
    done
    err "${svc}: ${timeout}s 内未变 healthy. 看 log: docker compose logs ${svc} | tail -50"
    return 1
}

ALL_HEALTHY=true
for svc in postgres identity gateway skills-hub; do
    wait_healthy "$svc" 120 || ALL_HEALTHY=false
done

if ! $ALL_HEALTHY; then
    err "至少一个 service 未变 healthy. 排查:"
    err "  docker compose -f $COMPOSE_FILE ps"
    err "  docker compose -f $COMPOSE_FILE logs gateway | tail -50"
    exit 1
fi


# ── Step 5: smoke test ──
step "5/6  smoke test (真打 healthcheck endpoint)"

smoke_curl() {
    local url=$1
    local desc=$2
    if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then
        ok "${desc}: 200 OK"
        return 0
    else
        err "${desc} (${url}): 失败"
        return 1
    fi
}

SMOKE_OK=true
smoke_curl "http://127.0.0.1:8999/healthz" "gateway /healthz" || SMOKE_OK=false
smoke_curl "http://127.0.0.1:8998/.well-known/openid-configuration" "identity OIDC discovery" || SMOKE_OK=false
smoke_curl "http://127.0.0.1:8997/healthz" "skills-hub /healthz" || SMOKE_OK=false
# 6/9 BL-WEB+MCP+BROKER-DEPLOY 加: 3 个新服务 smoke
smoke_curl "http://127.0.0.1:8996/health" "mcp-registry /health" || SMOKE_OK=false
# secret-broker 不绑宿主机, 走 docker exec curl 容器内
if docker compose -f "$COMPOSE_FILE" exec -T secret-broker curl -fsS "http://localhost:8995/health" >/dev/null 2>&1; then
    ok "secret-broker /health (容器内): 200"
else
    err "secret-broker /health (容器内): 失败. master_key 没生成 / PG 连不上"
    SMOKE_OK=false
fi
# web 不绑宿主机, 走中央 nginx (HTTP 80 / 80 → 301 → 443) 看 / 返 200
# 跳过 SSL 验证 (deploy 时可能还没 cert)
if curl -fskS -o /dev/null -w "%{http_code}" -L "http://127.0.0.1/" 2>/dev/null | grep -qE "200|301"; then
    ok "web SPA via nginx /: 200/301"
else
    warn "web SPA via nginx /: 验证不过 — 可能 nginx.conf 还没改 server_name / cert 没配"
fi

# uvicorn 多 worker 验证 (BL-F10 真根因 fix)
GATEWAY_WORKERS_ACTUAL=$(docker compose -f "$COMPOSE_FILE" logs gateway 2>&1 | grep -c "Started server process" || echo "0")
EXPECTED_WORKERS=$(grep -E "^GATEWAY_WORKERS=" "$ENV_FILE" | sed 's/^GATEWAY_WORKERS=//' || echo "4")
if [ "$GATEWAY_WORKERS_ACTUAL" -lt "$EXPECTED_WORKERS" ]; then
    warn "gateway worker 起了 ${GATEWAY_WORKERS_ACTUAL} 个 (期望 ${EXPECTED_WORKERS}). 检 src/catfish_gateway/app.py 是否 uvicorn.run(workers=...) 真传"
else
    ok "gateway uvicorn workers: ${GATEWAY_WORKERS_ACTUAL} (BL-F10 多 worker fix 生效)"
fi

if ! $SMOKE_OK; then
    err "smoke test 失败. 看 service log 排查"
    exit 1
fi


# ── Step 6: 汇报 ──
step "6/6  部署完成"

DOMAIN=$(grep -E "^DOMAIN=" "$ENV_FILE" | sed 's/^DOMAIN=//' | head -1)
DOMAIN=${DOMAIN:-catfish.example.com}

cat <<EOF

${GREEN}${BOLD}✓ 部署成功${NC}

${BOLD}service 状态:${NC}
$(docker compose -f "$COMPOSE_FILE" ps --format "table {{.Service}}\t{{.Status}}\t{{.Ports}}")

${BOLD}内部访问 (本机 curl):${NC}
  gateway:     http://127.0.0.1:8999/healthz
  identity:    http://127.0.0.1:8998/.well-known/openid-configuration
  skills-hub:  http://127.0.0.1:8997/healthz

${BOLD}对外访问 (员工 Companion 用):${NC}
  https://${DOMAIN}/         → gateway (chat/quota/audit)
  https://${DOMAIN}/sso/     → identity (OIDC)
  https://${DOMAIN}/hub/     → skills-hub (skill 分发)
  ${YELLOW}前提${NC}: nginx.conf 配了 DOMAIN, SSL cert 装在 ./certs/cert.pem + key.pem

${BOLD}日常运维:${NC}
  bash deploy.sh status         # 看 service 状态
  bash deploy.sh logs gateway   # tail gateway 日志
  bash deploy.sh down           # 关 stack (保数据)
  docker compose pull && bash deploy.sh   # 升级

${BOLD}BL-F10 性能基准 (4 vCPU / 4 worker):${NC}
  - 1000 并发员工: P50=6.5s / P95=14s / P99=15s
  - quota PG 写: 0 race (5 用户 200+ 并发同 user_email)
  - 单实例承载: 1000-1500 employee 内, 之上要横向扩多 gateway

${BOLD}下一步:${NC}
  1. 把 https://${DOMAIN} 发员工
  2. Companion app 配这 URL (catfish_central_url)
  3. 让员工 SSO 登一次验链路
EOF
