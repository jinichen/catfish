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

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://localhost/healthz || exit 1
CMD ["nginx", "-g", "daemon off;"]
