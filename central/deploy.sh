#!/usr/bin/env bash
# 鲶鱼中央服务 · 一键正式部署 (6/9 鸿波 BL-F10 后).
#
# 干啥:
#   1. 体检 .env (必填字段全填了 + 密码不是占位)
#   1.5 自动生成纯随机的密钥 (主密钥 / SKILLS_HUB_TOKEN / PG 密码),
#       **已有的绝不覆盖** —— 改主密钥 = 所有存库的 API key 解不开
#   2. 体检宿主机 (docker 装了, 端口没冲突, 资源够)
#   3. docker compose build + up -d
#   4. 等所有 service 健康 (有超时, 防卡死脚本)
#   5. smoke test (curl healthcheck + 1 个真请求路径)
#   6. 汇报最终状态 + 给员工 IT 的 URL 清单
#
# 用法:
#   cd central/
#   cp .env.production.example .env       # 第一次
#   nano .env                              # 只填客户自己的: 内网端点 / INTERNAL_LLM_KEY / OIDC
#                                          # (主密钥、PG 密码、HUB token 由脚本生成)
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

# 1.2 端口冲突? 6/9 BL-WEB+MCP-DEPLOY 加 8996 mcp-registry.
# 注: web :80 (容器内) 不绑宿主机, 不检查.
# P3.4.1-cleanup2 (7/14): secret-broker 8995 已砍 (P3.4.1 6/13), 端口清单也一起清.
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
# P3.3.18-cleanup (7/14) 加 8994 wiki-hub
for p in 80 443 8994 8996 8997 8998 8999; do
    case $p in
        80)   svc="nginx http" ;;
        443)  svc="nginx https" ;;
        8994) svc="wiki-hub" ;;
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

# P3.4.1-cleanup2 (7/14): 砍 CATFISH_SECRET_MASTER_KEY 自动生成段.
# P3.4.1 (6/13) 已砍 secret-broker 服务, master_key 不再需要
# (OAuth token 改 Companion 本机存, 无需中央 AES 加密).


# ── Step 2.5: 自动生成纯随机的密钥 ──────────────────────────────
#
# 8/1 恢复。7/14 (P3.4.1-cleanup2) 砍掉自动生成是对的 —— 当时 secret-broker
# 服务已经没了, 主密钥确实用不上。但 8/1 供应商拆分把 CATFISH_SECRET_KEY
# 重新变成必需 (API key 加密存库), 而**只在 SOP 里写了一行手工命令**,
# 自动生成没跟着回来。
#
# 这三项跟别的必填项性质不同:
#
#   PG_PASSWORD / SKILLS_HUB_TOKEN / CATFISH_SECRET_KEY   纯随机, 只要够随机
#   INTERNAL_LLM_KEY / 三个内网端点 / 公网 API key         只有客户知道
#
# 前者让客户"自己想一个"没有任何好处, 只会得到三种结果: 想一个弱的、
# 照抄文档里的示例值 (SOP 里 SKILLS_HUB_TOKEN 就给了个真值, 照抄的话
# **所有客户共用同一个 token**)、或者干脆忘了改。
#
# ⚠⚠ 最重要的一条: **已经有合法值就绝对不碰**。
#    覆盖 CATFISH_SECRET_KEY = 所有存库的供应商 API key 全部解不开, 只能
#    逐个去各家后台重新申请。这个脚本会被重跑 (改配置、升级、排障),
#    所以"重跑安全"不是锦上添花, 是硬要求。
step "2.5/6  密钥自动生成 (只填空的, 已有的一个字不改)"

# Fernet 密钥 = 32 字节随机的 url-safe base64。
# 用 openssl 而不是 python -c "from cryptography..." —— 宿主机不一定装了
# cryptography (它在容器里), 而 openssl 装 docker 的机器上都有。
# 实测 openssl 产出的值 Fernet 直接接受。
gen_fernet_key() { openssl rand -base64 32 | tr '+/' '-_'; }
gen_hex_token()  { openssl rand -hex 32; }

# 把 KEY=VALUE 写回 .env (原地改, 不追加重复行)。
# 用 awk 而不是 sed -i: 生成的值里有 / 和 - 等字符, sed 的分隔符会被撞;
# 而且 macOS 和 GNU 的 sed -i 参数不一样。
set_env_var() {
    local key="$1" val="$2"
    awk -v k="$key" -v v="$val" '
        BEGIN { done = 0 }
        $0 ~ "^" k "=" { print k "=" v; done = 1; next }
        { print }
        END { if (!done) print k "=" v }
    ' "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
}

# 当前值是不是"还没填"(空 / CHANGE_ME 开头)。
env_needs_value() {
    local key="$1"
    local val
    val=$(grep -E "^${key}=" "$ENV_FILE" | head -1 | sed "s/^${key}=//" || echo "")
    [ -z "$val" ] || [[ "$val" == CHANGE_ME* ]]
}

GENERATED=()

# ── 主密钥 ──
if env_needs_value CATFISH_SECRET_KEY; then
    set_env_var CATFISH_SECRET_KEY "$(gen_fernet_key)"
    GENERATED+=("CATFISH_SECRET_KEY")
    ok "已生成 CATFISH_SECRET_KEY (供应商 API key 的加密主密钥)"
else
    info "CATFISH_SECRET_KEY 已有值, 不动 (改了会让所有存库的 API key 解不开)"
fi

# ── Skills Hub token ──
if env_needs_value SKILLS_HUB_TOKEN; then
    set_env_var SKILLS_HUB_TOKEN "$(gen_hex_token)"
    GENERATED+=("SKILLS_HUB_TOKEN")
    ok "已生成 SKILLS_HUB_TOKEN (manager 发布 skill 用)"
fi

# ── PG 密码 ──
#
# ⚠ 只在**首次部署**生成。postgres 的密码是 volume 初始化那一刻写进去的,
# 之后改 .env 不会改库里的密码, 只会导致连不上 (SOP 故障排查第 3 条就是
# 这个)。所以 volume 已经存在时, 哪怕 .env 里还是占位符也不生成 ——
# 那种情况得人工处理, 不能让脚本把一个能用的库搞成连不上。
PG_VOLUME="$(basename "$(pwd)")_pgdata"
if env_needs_value PG_PASSWORD; then
    if docker volume inspect "$PG_VOLUME" >/dev/null 2>&1; then
        err "PG_PASSWORD 还是占位, 但 postgres volume ($PG_VOLUME) 已经存在了。"
        echo "    库里的密码是第一次启动时定下的, 现在生成一个新的只会连不上。"
        echo "    要么填回原来那个密码, 要么 docker compose down -v 重来 (会删数据)。"
        exit 1
    fi
    # 只用字母数字: PG 密码会出现在 CATFISH_DB_URL 这个 URL 里
    # (postgresql://user:PASS@host/db), 带 @ : / 这些字符要转义, 不值当。
    # 32 位字母数字 ≈ 165 bit, 比"16 位含符号"强得多。
    set_env_var PG_PASSWORD "$(openssl rand -hex 16)$(openssl rand -base64 12 | tr -dc 'A-Za-z0-9')"
    GENERATED+=("PG_PASSWORD")
    ok "已生成 PG_PASSWORD (首次部署)"
fi

if [ ${#GENERATED[@]} -gt 0 ]; then
    echo
    echo -e "${BOLD}${YELLOW}══════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  刚生成了 ${#GENERATED[@]} 个密钥, 已写进 .env${NC}"
    echo -e "${BOLD}${YELLOW}══════════════════════════════════════════════════════════${NC}"
    for k in "${GENERATED[@]}"; do
        echo "    $k=$(grep -E "^${k}=" "$ENV_FILE" | head -1 | sed "s/^${k}=//")"
    done
    echo
    if printf '%s\n' "${GENERATED[@]}" | grep -qx CATFISH_SECRET_KEY; then
        echo -e "${RED}${BOLD}  ⚠ CATFISH_SECRET_KEY 现在就存进公司密码管理器。${NC}"
        echo "    它丢了的话, 所有存在数据库里的供应商 API key 都解不开 ——"
        echo "    只能逐个去各家后台重新申请、再在界面上重填一遍。代码兜不住。"
        echo "    (备份 .env 本身也算, 但别只依赖服务器上那一份。)"
        echo
    fi
    echo "  .env 权限已收紧到 600。"
fi

# .env 里有明文密码, 别让同机的其他用户读到。
chmod 600 "$ENV_FILE" 2>/dev/null || true

# 必填 + 不能是占位
# ⚠ CATFISH_SECRET_KEY 也在这里 —— 8/1 之前它不在, 于是客户忘了改的话
# deploy.sh 会**放行**, 部署"成功", 但供应商页面上永远存不了 key
# (界面报"服务器还没有配 CATFISH_SECRET_KEY")。部署脚本说 OK 而功能是坏的,
# 正是最难查的那种。现在上面那一步会自动生成, 这里是兜底。
declare -A REQUIRED=(
    [PG_PASSWORD]="CHANGE_ME_TO_STRONG_PASSWORD"
    # 占位符跟 .env.production.example 里的值必须对得上。8/1 之前 example
    # 里写的就是这个 yourcompany.com 的值, 于是"照文档 cp 一份"必然被拒 ——
    # 而 SOP 表格里标着它可选。现在 example 给的是能直接用的默认值。
    [CATFISH_OIDC_ISSUER]="CHANGE_ME_OIDC_ISSUER_URL"
    [SKILLS_HUB_TOKEN]="CHANGE_ME_RANDOM_32_CHARS"
    [INTERNAL_LLM_KEY]="CHANGE_ME"
    [CATFISH_SECRET_KEY]="CHANGE_ME_RUN_THE_COMMAND_ABOVE"
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
ok ".env 必填字段全填了 (主密钥/PG密码/OIDC/SKILLS_HUB_TOKEN/INTERNAL_LLM_KEY)"

# PG_PASSWORD 强度 (≥ 16 位)
PG_PASS=$(grep -E "^PG_PASSWORD=" "$ENV_FILE" | head -1 | sed 's/^PG_PASSWORD=//')
if [ "${#PG_PASS}" -lt 16 ]; then
    warn "PG_PASSWORD 只 ${#PG_PASS} 位, 推荐 ≥ 16 位 + 含大小写+数字+符号"
fi


# ── Step 3: build + up ──
step "3/6  build + up -d"

info "build (首次 ~3min, 之后 layer cache 命中 < 30s)..."
docker compose -f "$COMPOSE_FILE" build --pull

info "up -d (起 pg → identity → gateway → mcp-registry → skills-hub → web → nginx)..."
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
# P3.3.18-cleanup (7/14) 加 wiki-hub 到 healthy 循环
for svc in postgres identity gateway skills-hub wiki-hub; do
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
# 6/9 BL-WEB+MCP-DEPLOY 加: 2 个新服务 smoke
# P3.4.1-cleanup2 (7/14): 砍 secret-broker smoke (服务已删 P3.4.1 6/13)
smoke_curl "http://127.0.0.1:8996/health" "mcp-registry /health" || SMOKE_OK=false
# P3.3.18-cleanup (7/14) 加 wiki-hub smoke
smoke_curl "http://127.0.0.1:8994/healthz" "wiki-hub /healthz" || SMOKE_OK=false
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
