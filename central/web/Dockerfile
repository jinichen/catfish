# catfish-web Dockerfile (BL-ARCH1 5/10).
#
# multi-stage:
#   Stage 1: node 20 build vite dist/
#   Stage 2: nginx-alpine 服 dist/ + 反代 gateway/identity (sidecar 模式)
#
# 用法:
#   docker build -t catfish-web:0.1.0 .
#   docker run -p 80:80 -e GATEWAY_URL=http://gateway:8999 catfish-web:0.1.0

FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --frozen-lockfile || npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# 拷构建产物
COPY --from=builder /app/dist /usr/share/nginx/html
# 拷反代配置
COPY nginx.conf.example /etc/nginx/conf.d/default.conf

# 真上线: docker-compose 内网 gateway / identity 名解析为 host
# 这里默认 127.0.0.1, sidecar 部署 (跟 gateway / identity 同一台) 直接行
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
