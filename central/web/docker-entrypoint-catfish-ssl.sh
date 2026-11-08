#!/bin/sh
# catfish-web · 条件启用 HTTPS (P3.5.80 · 7/28 鸿波)
#
# nginx 官方镜像会按数字顺序执行 /docker-entrypoint.d/*.sh 再启 nginx.
# 本脚本编号 41, 排在 40-catfish-config.sh (生成 config.js) 之后.
#
# 干的事: 满足条件就把 443 server block 拷进 conf.d/ 让它生效.
#
# 两个条件缺一不可:
#   1. CATFISH_ENABLE_HTTPS=1
#   2. 证书文件真实存在且非空
#
# 为啥要判第 2 条: nginx 加载不到 ssl_certificate 会**启动失败**.
# 若只判 env, 客户设了 ENABLE_HTTPS=1 但证书没挂进来 (compose 漏了 volume /
# setup.sh 没跑到生成那步), web 容器就会反复重启, 而且报错藏在容器日志里.
# 判文件 + 明确日志, 让问题在启动那一刻就说清楚.

set -eu

NGX="${CATFISH_NGINX_DIR:-/etc/nginx}"   # 只给测试换目录用 (central/web/test_ssl_entrypoint.sh)
SRC=$NGX/catfish-ssl.conf.disabled
DST=$NGX/conf.d/catfish-ssl.conf
CERT=$NGX/certs/cert.pem
KEY=$NGX/certs/key.pem

if [ "${CATFISH_ENABLE_HTTPS:-0}" != "1" ]; then
    echo "[catfish-web-ssl] CATFISH_ENABLE_HTTPS != 1 · 只跑 HTTP (:80)"
    exit 0
fi

if [ ! -s "$CERT" ] || [ ! -s "$KEY" ]; then
    echo "[catfish-web-ssl] ❌ CATFISH_ENABLE_HTTPS=1 但证书缺失或为空:"
    echo "[catfish-web-ssl]    cert: $CERT $([ -e "$CERT" ] && echo '(存在但为空)' || echo '(不存在)')"
    echo "[catfish-web-ssl]    key : $KEY  $([ -e "$KEY"  ] && echo '(存在但为空)' || echo '(不存在)')"
    echo "[catfish-web-ssl]    → 检查 compose 是否挂了 ./certs:/etc/nginx/certs:ro"
    echo "[catfish-web-ssl]    → 检查宿主机 certs/ 下是否有 setup.sh 生成的 cert.pem / key.pem"
    echo "[catfish-web-ssl]    本次降级为只跑 HTTP (:80) · 不让容器反复重启"
    exit 0
fi

cp "$SRC" "$DST"
echo "[catfish-web-ssl] ✓ HTTPS 已启用 (:443) · 证书 $CERT"

# 11/08: 容器 8080 一律 301 跳 https (宿主机 80 映射过来)。HTTPS 改过端口就带上, 不是数字的当 443。
HP="${CATFISH_HTTPS_PORT:-443}"
case "$HP" in ''|*[!0-9]*) HP=443 ;; esac
SUFFIX=""
[ "$HP" != "443" ] && SUFFIX=":$HP"
sed "s/__HTTPS_PORT__/$SUFFIX/" "$NGX/catfish-http-redirect.conf.disabled" > "$NGX/conf.d/catfish-http-redirect.conf"
echo "[catfish-web-ssl] ✓ :8080 → https://<host>$SUFFIX 跳转已启用 (宿主机 80 映射到这里)"
