/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri expects a fixed port and disables HMR over the websocket layer it controls.
// See: https://v2.tauri.app/start/frontend/vite/
const host = process.env.TAURI_DEV_HOST;

export default defineConfig(async () => ({
  plugins: [react()],

  // 5/5 BL-E27 spike: 多入口 (主 Companion + 桌宠副窗 pet.html)
  build: {
    rollupOptions: {
      input: {
        main: "index.html",
        pet: "pet.html",
      },
    },
  },

  // C1 (6/6 鸿波 marathon CI matrix audit): vitest 4 严格 — store/*.test.ts 是
  // 自定义 console.log mini runner (npx tsx 单跑, 不走 vitest), 没用 describe/it,
  // vitest collect 报 "No test suite found" 整 job 挂. 排除这 3 个走 npm run
  // test:store 单跑. lib/ 真用 describe/it 的 vitest test 仍跑.
  test: {
    exclude: [
      "**/node_modules/**",
      "**/dist/**",
      "src/store/auto_continue.test.ts",
      "src/store/queue.test.ts",
      "src/store/recmode.test.ts",
      "src/store/email.test.ts",   // 也是自定义 runner (待 audit, 先排除防 CI 挂)
    ],
  },

  // Prevent vite from obscuring rust errors
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      // Tauri watches src-tauri itself; ignore here to avoid double rebuilds
      ignored: ["**/src-tauri/**"],
    },
  },
}));
