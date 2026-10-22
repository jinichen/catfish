#!/usr/bin/env bash
# 服务器证书自动续期 (10/22)。每天跑一次 + 开机跑一次, 重复跑没副作用。
#
#   bash renew-cert.sh
#
# 服务器证书只有 397 天 (macOS 不认超过 398 天的 TLS 证书), 原来要人记得在到期前重跑 setup.sh;
# 一年没人管, 到期那天员工的 Companion 就连不上中央。
#
# 做的事跟 setup.sh 第 4 步同一份逻辑 (tools/certgen.py, 跑在 identity 镜像里):
#   · 剩不到 30 天 / SAN 里没有当前 IP → 重签服务器证书; 否则什么都不做
#   · 内部 CA 不动 —— 员工机器上的 ca.pem 不用换
#   · 真重签了才让 web 容器的 nginx 重新加载证书 (不重启, 在线连接不断)
#
# 谁调它:
#   · Linux: setup.sh 装机时写进当前用户的 crontab (每天 03:17 + 开机)
#   · Windows WSL2: catfish-wsl-boot.ps1 的常驻循环每天调一次
#   · Windows + Docker Desktop: 走 renew-cert.ps1 (同逻辑的 PowerShell 版)
set -uo pipefail
cd "$(dirname "$0")"

say() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

[ -f .env ] || { say "没有 .env (还没装机), 跳过"; exit 0; }
ISSUER=$(grep -E '^CATFISH_OIDC_ISSUER=' .env | tail -1 | cut -d= -f2- | tr -d "\"'" | tr -d '\r')
case "$ISSUER" in
    https://*) ;;
    *) say "HTTP 模式 (没用证书), 跳过"; exit 0 ;;
esac
HOSTPORT="${ISSUER#https://}"
SERVER_IP="${HOSTPORT%%[:/]*}"
if ! [[ "$SERVER_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    say "❌ 从 .env 的 CATFISH_OIDC_ISSUER ($ISSUER) 取不到服务器 IP"
    exit 1
fi
[ -f certs/cert.pem ] || { say "certs/cert.pem 不存在 (证书还没生成过, 由 setup.sh 生成), 跳过"; exit 0; }

# 用正在跑的 identity 容器的镜像 (就是 setup.sh 生成证书用的那个); 没在跑就按 compose 里写的
IDENTITY_IMAGE=$(docker inspect catfish-identity --format '{{.Config.Image}}' 2>/dev/null || true)
if [ -z "$IDENTITY_IMAGE" ]; then
    IDENTITY_IMAGE=$(grep -E '^\s*image:\s*catfish-identity:' docker-compose.yml | head -1 | awk '{print $2}')
fi
[ -n "$IDENTITY_IMAGE" ] || { say "❌ 找不到 catfish-identity 镜像"; exit 1; }

hash() { sha256sum certs/cert.pem 2>/dev/null | cut -c1-64; }
BEFORE=$(hash)
if ! OUT=$(docker run --rm \
        --user "$(id -u):$(id -g)" \
        -v "$PWD/certs:/certs" \
        -v "$PWD/tools:/tools:ro" \
        "$IDENTITY_IMAGE" \
        python3 /tools/certgen.py --ip "$SERVER_IP" --out /certs 2>&1); then
    say "❌ 证书检查 / 重签失败:"
    echo "$OUT"
    exit 1
fi
if [ "$(hash)" = "$BEFORE" ]; then
    say "证书还有效, 不用续 ($(echo "$OUT" | grep -o '[0-9]* 天后到期' | tail -1))"
    exit 0
fi
echo "$OUT"
if docker exec catfish-web nginx -s reload >/dev/null 2>&1; then
    say "✓ 服务器证书已续期, 门户已换上新证书 (内部 CA 没变, 员工电脑不用动)"
else
    say "⚠ 服务器证书已续期, 但 web 容器的 nginx 没能重新加载 —— 重启一下: docker restart catfish-web"
    exit 1
fi
