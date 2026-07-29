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

# P3.5.80 (7/30 达华现场): 归一化权限, 否则 public/ 里的静态资源可能 403。
#
# 现象: 页面能开、JS/CSS 都正常, 但顶栏 logo 是个坏图标。
#       curl -kI https://.../catfish-logo.svg → **403**, 不是 404。
#
# 真因: dist/ 里两类文件来路不同 ——
#   dist/assets/*.js|css   容器内 `npm run build` 生成 → 644 → nginx 读得到
#   dist/catfish-logo.svg  来自 public/, 经 `COPY . .` 从**宿主机**带进来,
#                          vite 原样拷进 dist, **保留宿主机的权限位**
# 构建机 umask 若是 077, 这些文件就是 600, 而 nginx worker 跑的是非 root 的
# `nginx` 用户 → 读不了 → 403。
#
# 阴在这: git 只记录 644/755 两种 mode, 所以**全新 clone 出来的仓库是 644,
# 这个问题复现不了**; 只有在那台 umask 特殊的构建机上才会出。CI 绿、别人
# 机器上也绿, 唯独交付出去的镜像坏。
#
# a+rX 的大写 X = 只给目录加执行位, 不会把普通文件变成可执行。
RUN chmod -R a+rX /usr/share/nginx/html
# web 容器是员工浏览器唯一入口, 自己扛 SPA + 反代 (+ 可选 SSL).
# P3.5.80 (7/28): 反代规则抽到 catfish-locations.conf, 由 :80 / :443 两个
# server block 共用, 免得写两遍走岔.
COPY nginx.docker.conf      /etc/nginx/conf.d/default.conf
COPY catfish-locations.conf /etc/nginx/catfish-locations.conf

# ── P3.5.80 (7/28) HTTPS 条件启用 ──
# catfish-ssl.conf 落到 **.disabled** 后缀, 不在 conf.d/ 下, 默认不生效.
# 41-catfish-ssl.sh 在 CATFISH_ENABLE_HTTPS=1 **且**证书真实存在时才拷进 conf.d/.
# 若无条件写死 443 block 而证书不存在, nginx 加载证书失败会直接起不来 ——
# 等于为了没启用的 HTTPS 把默认 HTTP 路径搞坏.
COPY catfish-ssl.conf /etc/nginx/catfish-ssl.conf.disabled

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
COPY docker-entrypoint-catfish-ssl.sh    /docker-entrypoint.d/41-catfish-ssl.sh
RUN chmod +x /docker-entrypoint.d/40-catfish-config.sh \
             /docker-entrypoint.d/41-catfish-ssl.sh

EXPOSE 80 443
# healthcheck 走 :80 —— HTTPS 是可选的, 而且自签证书下 wget 要 --no-check-certificate,
# 徒增噪音. :80 始终在跑 (HTTPS 模式下它只做 301 跳转以外的静态服务也照常),
# 用它判活最稳.
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1/healthz || exit 1
CMD ["nginx", "-g", "daemon off;"]
