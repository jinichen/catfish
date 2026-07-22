# catfish-web Dockerfile (BL-ARCH1 5/10, BL-WEB-DEPLOY 6/9 重构).
#
# multi-stage:
#   Stage 1: node 20 build vite dist/ (清华 npm mirror 绕 Clash)
#   Stage 2: nginx-alpine 服 dist/ (只服 SPA, 不反代 — 反代留给中央 nginx)
#
# 用法 (docker-compose 集成):
#   docker compose build web
#   docker compose up -d web
#   中央 nginx 反代 / → web:80 (内网 service 名)
#
# 用法 (standalone, 给客户参考):
#   见 nginx.conf.example (含 SSL + 完整反代, 单容器跑)

FROM node:20-alpine AS builder
WORKDIR /app
# 6/9 BL-F10 撞过: Clash 拦 npm 默认 registry. 客户机房同样大概率撞. 切清华 mirror.
RUN npm config set registry https://registry.npmmirror.com
COPY package.json package-lock.json* ./
RUN npm install --frozen-lockfile || npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# 拷构建产物
COPY --from=builder /app/dist /usr/share/nginx/html
# 6/9: 用 docker 专用 conf (只服 SPA, 中央 nginx 反代 API). standalone 部署看
# nginx.conf.example.
COPY nginx.docker.conf /etc/nginx/conf.d/default.conf

# ── P3.5.79+ (7/22 达华 POC 血案) runtime config 注入 ──
# 老 build-arg VITE_OIDC_ISSUER 路子行不通 · 客户每台 server IP 不同 · 每客户重编
# image = 不 scale · image tar 也不可能"一份到处装". 改成 SPA 标准 runtime config:
#   1. 容器启动时 · docker-entrypoint.d 里 script 从容器 env 生成 /config.js
#   2. index.html <script src="/config.js"> 在 main bundle 前加载 · 挂 window.__CATFISH_CONFIG__
#   3. 前端 lib/env.ts 优先读 window.__CATFISH_CONFIG__ · fallback vite env (dev 兼容)
# 客户改 CATFISH_OIDC_ISSUER env + `docker compose restart web` = 秒生效 · 不重编.
#
# nginx image 有自带的 /docker-entrypoint.d/ hook (会按数字排序执行 *.sh · 完后启 nginx).
# 我们塞 40-catfish-config.sh 生成 /usr/share/nginx/html/config.js.
COPY docker-entrypoint-catfish-config.sh /docker-entrypoint.d/40-catfish-config.sh
RUN chmod +x /docker-entrypoint.d/40-catfish-config.sh

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://localhost/healthz || exit 1
CMD ["nginx", "-g", "daemon off;"]
