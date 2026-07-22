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

cat > "$OUT" <<EOF
// runtime config · docker-entrypoint 从容器 env 生成 · 换 env 需 restart 容器.
// 前端 lib/env.ts 读 window.__CATFISH_CONFIG__ · fallback vite build-time env (dev 用).
window.__CATFISH_CONFIG__ = {
  oidcIssuer: "$ISSUER",
  oidcClientId: "$CLIENT_ID",
  oidcScope: "$SCOPE",
};
EOF

echo "[catfish-web-config] /config.js 生成 · oidcIssuer=$ISSUER"
