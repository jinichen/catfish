#!/bin/sh
# catfish-web runtime config 注入 (P3.5.79+ 7/22 达华 POC 血案后加)
#
# nginx image 官方 /docker-entrypoint.d/ hook — 按文件名数字排序执行, 完毕启 nginx.
# 这脚本从容器 env 读 CATFISH_OIDC_* + CATFISH_WEB_* · 生成 /usr/share/nginx/html/config.js
# · 前端 index.html 里 <script src="/config.js"> 先加载 · 挂到 window.__CATFISH_CONFIG__.
#
# 老 build-arg VITE_OIDC_ISSUER 模式已废: 客户每台 server IP 不同 · 不能每客户重编
# image · runtime 模式一份 image 到处部署 · 换 IP 只改 env + `docker compose restart web`.
#
# 默认值 (env 未设时) 用 127.0.0.1:8998 · dev/单机测试兼容 · **生产客户必设**.

set -eu

OUT=/usr/share/nginx/html/config.js
ISSUER="${CATFISH_OIDC_ISSUER:-http://127.0.0.1:8998}"
CLIENT_ID="${CATFISH_OIDC_CLIENT_ID:-catfish-companion}"
SCOPE="${CATFISH_OIDC_SCOPE:-openid email profile}"
# 10/14: 员工接入落地页 (/connect.html) 的 http 端口 —— compose 把 CATFISH_HTTP_PORT 传进来
HTTP_PORT="${CATFISH_PUBLIC_HTTP_PORT:-}"

# 11/08 (IP + 域名 / 公网映射同时用): 全部 issuer —— 主地址 (IP) 在前, 其余是 setup 按
# PUBLIC_HOST 算出来的。前端用跟 window.location.origin 一致的那个 (lib/issuer.ts),
# 对不上用第一个。没配 CATFISH_OIDC_ISSUERS 就只有 oidcIssuer 一个, 跟以前一样。
# 只收 http(s)://主机[:端口] 这种形状, 别的 (引号 / 反斜杠 / 空格) 一律跳过, 不往 JS 里塞。
ISSUERS_JS=""
set -f
OLDIFS=$IFS; IFS=','
for iss in $ISSUER ${CATFISH_OIDC_ISSUERS:-}; do
    IFS=$OLDIFS
    iss=$(printf '%s' "$iss" | tr -d ' \t\r\n'); iss="${iss%/}"
    case "$iss" in
        http://*|https://*) ;;
        *) [ -n "$iss" ] && echo "[catfish-web-config] ⚠ 跳过不像地址的 issuer: $iss"; continue ;;
    esac
    case "${iss#*://}" in ''|*[!A-Za-z0-9.:-]*) echo "[catfish-web-config] ⚠ 跳过不像地址的 issuer: $iss"; continue ;; esac
    case ",$ISSUERS_JS," in *",\"$iss\","*) continue ;; esac
    ISSUERS_JS="${ISSUERS_JS:+$ISSUERS_JS,}\"$iss\""
done
IFS=$OLDIFS
set +f

cat > "$OUT" <<EOF
// runtime config · docker-entrypoint 从容器 env 生成 · 换 env 需 restart 容器.
// 前端 lib/env.ts 读 window.__CATFISH_CONFIG__ · fallback vite build-time env (dev 用).
window.__CATFISH_CONFIG__ = {
  oidcIssuer: "$ISSUER",
  oidcIssuers: [$ISSUERS_JS],
  oidcClientId: "$CLIENT_ID",
  oidcScope: "$SCOPE",
  publicHttpPort: "$HTTP_PORT",
};
EOF

echo "[catfish-web-config] /config.js 生成 · oidcIssuer=$ISSUER · 全部: [$ISSUERS_JS]"
