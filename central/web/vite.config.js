import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// catfish-web Vite 配置 (BL-ARCH1 5/10).
//
// dev: vite --port 5173 直跑, 走代理转 gateway :8999.
// prod: vite build 出静态 dist/, 由 nginx 服务并反代 /v1/* /api/* 到 gateway.
export default defineConfig({
    plugins: [react()],
    server: {
        // BL-ARCH2 fix4 (5/10): 显式 host + strictPort.
        //   host 0.0.0.0 → 同时听 127.0.0.1 + localhost + 局域网 (Companion 默认开 127.0.0.1)
        //   strictPort → 5173 被占就报错而不是偷偷换 5174 (Companion 永远跳 5173 一致)
        host: "0.0.0.0",
        port: 5173,
        strictPort: true,
        // dev 时浏览器 fetch /api /v1 → 代理到 gateway, 单 origin 简化 CORS + cookie.
        proxy: {
            "/api": {
                target: "http://127.0.0.1:8999",
                changeOrigin: true,
            },
            "/v1": {
                target: "http://127.0.0.1:8999",
                changeOrigin: true,
            },
        },
    },
    build: {
        outDir: "dist",
        sourcemap: true,
        target: "es2022",
    },
});
