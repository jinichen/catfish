#!/usr/bin/env bash
# web 容器 41-catfish-ssl.sh (11/08): HTTPS 开 + 证书齐 → 启用 443 和 8080 跳转; 端口改过跳转带端口; 证书缺 / 没开 → 都不启用。
# 跑法: bash central/web/test_ssl_entrypoint.sh
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
PASS=0; FAIL=0
ok()  { echo "  ✓ $1"; PASS=$((PASS+1)); }
bad() { echo "  ✗ $1"; FAIL=$((FAIL+1)); }

setup() {   # $1 = 有没有证书
    N=$(mktemp -d); mkdir -p "$N/conf.d" "$N/certs"
    cp catfish-ssl.conf "$N/catfish-ssl.conf.disabled"
    cp catfish-http-redirect.conf "$N/catfish-http-redirect.conf.disabled"
    [ "$1" = yes ] && { echo c > "$N/certs/cert.pem"; echo k > "$N/certs/key.pem"; }
}
run() { CATFISH_NGINX_DIR="$N" sh docker-entrypoint-catfish-ssl.sh >/dev/null 2>&1; }

setup yes; CATFISH_ENABLE_HTTPS=1 run
[ -f "$N/conf.d/catfish-ssl.conf" ] && ok "HTTPS 开: 443 配置启用" || bad "443 没启用"
grep -q 'return 301 https://$host$request_uri;' "$N/conf.d/catfish-http-redirect.conf" 2>/dev/null \
    && ok "443: 跳到 https://\$host (不带端口)" || bad "跳转配置不对: $(cat "$N/conf.d/catfish-http-redirect.conf" 2>/dev/null)"
grep -q '__HTTPS_PORT__' "$N/conf.d/catfish-http-redirect.conf" 2>/dev/null && bad "占位符没换掉" || ok "占位符换掉了"

setup yes; CATFISH_ENABLE_HTTPS=1 CATFISH_HTTPS_PORT=8443 run
grep -q 'return 301 https://$host:8443$request_uri;' "$N/conf.d/catfish-http-redirect.conf" 2>/dev/null \
    && ok "改过端口: 跳到 https://\$host:8443" || bad "8443 没带上"

setup yes; CATFISH_ENABLE_HTTPS=1 CATFISH_HTTPS_PORT='443;evil' run
grep -q 'return 301 https://$host$request_uri;' "$N/conf.d/catfish-http-redirect.conf" 2>/dev/null \
    && ok "端口不是数字: 当 443, 不往配置里塞怪东西" || bad "非数字端口没挡住"

setup no; CATFISH_ENABLE_HTTPS=1 run
[ ! -f "$N/conf.d/catfish-http-redirect.conf" ] && ok "证书缺: 不启用跳转 (跳过去也打不开)" || bad "证书缺还启用了跳转"

setup yes; CATFISH_ENABLE_HTTPS=0 run
[ ! -f "$N/conf.d/catfish-http-redirect.conf" ] && [ ! -f "$N/conf.d/catfish-ssl.conf" ] && ok "HTTP 模式: 都不启用" || bad "HTTP 模式启用了"

echo ""
echo "通过 $PASS · 失败 $FAIL"
[ "$FAIL" -eq 0 ] || exit 1
