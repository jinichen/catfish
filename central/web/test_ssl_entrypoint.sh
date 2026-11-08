#!/usr/bin/env bash
# web 容器 41-catfish-ssl.sh (11/08): HTTPS 开 + 证书齐 → 启用 443 和 8080 跳转; 端口改过跳转带端口; 证书缺 / 没开 → 都不启用。
# 跑法: bash central/web/test_ssl_entrypoint.sh
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
PASS=0; FAIL=0
ok()  { echo "  ✓ $1"; PASS=$((PASS+1)); }
bad() { echo "  ✗ $1"; FAIL=$((FAIL+1)); }

setup() {   # $1 = 有没有证书, $2 = official → 另外放一套正式证书
    N=$(mktemp -d); mkdir -p "$N/conf.d" "$N/certs"; OFF="$N/conf.d/catfish-ssl-official.conf"
    cp catfish-ssl.conf "$N/catfish-ssl.conf.disabled"
    cp catfish-ssl-official.conf "$N/catfish-ssl-official.conf.disabled"
    cp catfish-http-redirect.conf "$N/catfish-http-redirect.conf.disabled"
    [ "$1" = yes ] && { echo c > "$N/certs/cert.pem"; echo k > "$N/certs/key.pem"; }
    [ "${2:-}" = official ] && { mkdir -p "$N/certs/official"; echo c > "$N/certs/official/fullchain.pem"; echo k > "$N/certs/official/privkey.pem"; }
    return 0
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

# ── 11/08 IP + 域名 / 公网映射 ────────────────────────────────────
R() { cat "$N/conf.d/catfish-http-redirect.conf" 2>/dev/null; }

echo "── 没配域名 (跟以前一样) ──"
setup yes official; CATFISH_ENABLE_HTTPS=1 run
[ ! -f "$OFF" ] && ok "没有 CATFISH_PUBLIC_HOSTS: 正式证书块不启用 (只有 IP)" || bad "没配域名却启用了正式证书块"
[ "$(R | grep -c 'listen 8080')" = 1 ] && ok "跳转只有默认那一块" || bad "多出了跳转块: $(R)"
grep -q 'listen 443 ssl default_server;' "$N/conf.d/catfish-ssl.conf" && ok "内部证书那块是 443 的 default_server" || bad "内部证书块不是 default_server"

echo "── 域名 + 内部证书 ──"
setup yes; CATFISH_ENABLE_HTTPS=1 CATFISH_PUBLIC_HOSTS=catfish.example.com run
[ ! -f "$OFF" ] && ok "没有正式证书: 只一个 443 块 (内部证书的 SAN 带着域名)" || bad "没有正式证书却生成了正式证书块"
R | grep -A3 'server_name catfish.example.com;' | grep -q 'return 301 https://catfish.example.com$request_uri;' \
    && ok "http://域名 → https://域名" || bad "域名跳转不对: $(R)"

echo "── 域名 + 正式证书 (两个 443 块, 各用各的证书) ──"
setup yes official
CATFISH_ENABLE_HTTPS=1 CATFISH_PUBLIC_HOSTS='Catfish.Example.com,203.0.113.7:8443,m.example.com' run
[ -f "$OFF" ] && ok "正式证书块已生成" || bad "正式证书块没生成"
grep -q 'server_name catfish.example.com m.example.com;' "$OFF" 2>/dev/null \
    && ok "正式证书块 server_name = 域名 (小写, 不含公网 IP)" || bad "server_name 不对: $(grep server_name "$OFF" 2>/dev/null)"
grep -q 'ssl_certificate     /etc/nginx/certs/official/fullchain.pem;' "$OFF" 2>/dev/null \
    && grep -q 'ssl_certificate_key /etc/nginx/certs/official/privkey.pem;' "$OFF" \
    && ok "正式证书块用 certs/official/ 的证书" || bad "正式证书块证书路径不对"
grep -q 'ssl_certificate     /etc/nginx/certs/cert.pem;' "$N/conf.d/catfish-ssl.conf" \
    && ok "默认块 (IP) 仍用内部证书 cert.pem" || bad "默认块证书被改了"
! grep -qE '^[[:space:]]*listen .*default_server' "$OFF" && ok "正式证书块不是 default_server (IP 不会拿到域名证书)" || bad "正式证书块抢了 default_server"
grep -q 'include /etc/nginx/catfish-locations.conf;' "$OFF" && grep -q 'location = /catfish-ca.pem' "$OFF" \
    && ok "正式证书块也带全部反代 + /catfish-ca.pem (接入码照样能用)" || bad "正式证书块缺反代 / ca.pem"
grep -q '__SERVER_NAMES__' "$OFF" && bad "占位符没换掉" || ok "server_name 占位符换掉了"

echo "── 公网映射 (NAT): 跳到对外端口 ──"
R | grep -A3 'server_name 203.0.113.7;' | grep -q 'return 301 https://203.0.113.7:8443$request_uri;' \
    && ok "http://203.0.113.7 → https://203.0.113.7:8443 (用对外端口, 不是内部 443)" || bad "NAT 跳转不对: $(R)"
R | grep -A3 'listen 8080 default_server' | grep -q 'return 301 https://$host$request_uri;' \
    && ok "内网 IP 还是走默认块, 行为不变" || bad "默认跳转块变了"

setup yes; CATFISH_ENABLE_HTTPS=1 CATFISH_HTTPS_PORT=8443 CATFISH_PUBLIC_HOSTS='catfish.example.com,203.0.113.7:443' run
R | grep -A3 'server_name catfish.example.com;' | grep -q 'return 301 https://catfish.example.com:8443$request_uri;' \
    && ok "域名没写端口: 跟内部 HTTPS 端口 (8443)" || bad "默认对外端口不对: $(R)"
R | grep -A3 'server_name 203.0.113.7;' | grep -q 'return 301 https://203.0.113.7$request_uri;' \
    && ok "写了 :443 的映射项: 不带端口" || bad ":443 映射项不对"

echo "── 只有公网 IP + 正式证书 → 不启用正式证书块 (公网 CA 不给 IP 签) ──"
setup yes official; CATFISH_ENABLE_HTTPS=1 CATFISH_PUBLIC_HOSTS='203.0.113.7:8443' run
[ ! -f "$OFF" ] && ok "没有域名就不启用正式证书块" || bad "只有 IP 却启用了正式证书块"

echo "── 怪东西不往配置里塞 ──"
setup yes official; CATFISH_ENABLE_HTTPS=1 CATFISH_PUBLIC_HOSTS='ok.example.com,evil.com;return 500,x.com:80a,*.example.com' run
! R | grep -vE '^[[:space:]]*#' | grep -E 'server_name|return 301' | grep -qE 'evil|return 500|x\.com|\*' && ok "非法项全部跳过" || bad "非法项进了配置: $(R)"
grep -q 'server_name ok.example.com;' "$OFF" && ok "合法项照常" || bad "合法项丢了"

setup yes official; CATFISH_ENABLE_HTTPS=0 CATFISH_PUBLIC_HOSTS=catfish.example.com run
[ ! -f "$OFF" ] && ok "HTTP 模式: 正式证书块也不启用" || bad "HTTP 模式启用了正式证书块"

echo ""
echo "通过 $PASS · 失败 $FAIL"
[ "$FAIL" -eq 0 ] || exit 1
