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
    // P3.5.29 Phase 6.2 (6/17 鸿波): main bundle 1.17 MB → chunkSizeWarningLimit
    // 设 800 KB 降 warning 噪音, 真code split** 见 manualChunks.
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      input: {
        main: "index.html",
        pet: "pet.html",
      },
      output: {
        // P3.5.29 Phase 6.2 (6/17 鸿波) — vendor chunk split.
        // 老 main bundle 1,173 kB / gzip 363 kB — 第三方 lib 全打一起.
        // chunk 拆:
        //   - react-vendor: react / react-dom / scheduler — UI core, 缓存友好
        //   - tauri-vendor: @tauri-apps/* — 桌面端 API, 切版本时只 invalidate 自己
        //   - state-vendor: zustand — store 框架
        //   - markdown-vendor: react-markdown / remark-* / rehype-* / micromark/mdast/unist —
        //     最大 chunk, 只 ChatMessage 渲染时用
        //   - utils-vendor: uuid / date-fns / 等小 util
        // 预期: main bundle ~600 KB, react ~150 KB, markdown ~250 KB, tauri ~80 KB.
        // 真实测见 npm run build 输出**.
        manualChunks: (id: string) => {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("/react/") || id.includes("/react-dom/") || id.includes("/scheduler/")) {
            return "react-vendor";
          }
          if (id.includes("/@tauri-apps/")) {
            return "tauri-vendor";
          }
          if (id.includes("/zustand/")) {
            return "state-vendor";
          }
          if (
            id.includes("/react-markdown/")
            || id.includes("/remark-")
            || id.includes("/rehype-")
            || id.includes("/micromark")
            || id.includes("/mdast-")
            || id.includes("/unist-")
            || id.includes("/hast-")
          ) {
            return "markdown-vendor";
          }
          // 其他 node_modules 进默认 vendor chunk (rollup 自动)
          return undefined;
        },
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
