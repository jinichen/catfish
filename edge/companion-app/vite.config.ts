/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri expects a fixed port and disables HMR over the websocket layer it controls.
// See: https://v2.tauri.app/start/frontend/vite/
const host = process.env.TAURI_DEV_HOST;

export default defineConfig(async () => ({
  plugins: [react()],

  // 11/15 A2UI 卡片试点: @a2ui/* 0.15 照 zod 3 写的 (直接用 zod 3 的内部方法), 而 zod 3 全系有 GHSA-mrhx-c7xc-m3w5
  // (超大数组校验耗尽内存, 只在 zod 4.6.6 修了), 安全审计不过。照上游 issue #4192 的临时办法: 装 zod 4.6.7,
  // 把裸的 "zod" 指到它自带的 v3 兼容入口 "zod/v3"。上游发了支持 zod 4 的版本就删掉这条 (见 package.json overrides)。
  // 另外卡片本身有大小上限 (lib/a2uiCard.ts CARD_MAX_CHARS), 撑不到那个漏洞要的超大数组。
  resolve: {
    alias: [{ find: /^zod$/, replacement: "zod/v3" }],
  },

  build: {
    // P3.5.29 Phase 6.2 (6/17 鸿波): main bundle 1.17 MB → chunkSizeWarningLimit
    // 设 800 KB 降 warning 噪音, 真code split** 见 manualChunks.
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      // 9/12: 桌宠副窗 pet.html 入口删了 (BL-E27), 回到单入口
      input: {
        main: "index.html",
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
          // 11/15: A2UI 卡片 (@a2ui/* 连带 lit / zod) 只在对话里出现卡片时按需加载 (tabs/Chat/A2uiCard 走 React.lazy),
          // 不归任何 vendor chunk —— 原来下面 "/react/" 把 @a2ui/react 也认成了 React, 整套塞进首屏预加载的 react-vendor (+460K)
          if (id.includes("/@a2ui/")) return undefined;
          if (id.includes("/node_modules/react/") || id.includes("/node_modules/react-dom/") || id.includes("/node_modules/scheduler/")) {
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

  // C1 (6/6 鸿波 marathon CI matrix audit): vitest 4 严格 — 下面这三个是
  // 自定义 console.log mini runner (npx tsx 单跑, 不走 vitest), 没用 describe/it,
  // vitest collect 报 "No test suite found" 整 job 挂, 所以排除。
  //
  // ⚠ 7/30 更正两处:
  //
  //   1. src/store/email.test.ts 曾一并排除, 标注"也是自定义 runner
  //      (待 audit, 先排除防 CI 挂)"。**那个判断是错的** —— 它
  //      `import { describe, it, expect } from "vitest"`, 是标准 vitest 测试,
  //      自己的文件注释还写着跑法就是 `vitest src/store/email.test.ts`。
  //      于是它从被排除那天起一次都没跑过, 而且没人会发现 (排除掉的测试
  //      不会红)。现在放回来。
  //
  //   2. 原注释说"走 npm run test:store 单跑" —— **package.json 里没有
  //      test:store 这个脚本**。也就是说剩下这三个自定义 runner 至今没有
  //      任何自动化入口, 只能手动 npx tsx 跑。
  //      它们是真的自定义 runner, 排除本身没错; 但"有别的地方在跑"这个
  //      印象是假的。要么补脚本要么改造成 vitest, 别让注释替不存在的机制背书。
  test: {
    // 11/15: 上面 zod → zod/v3 的别名要对 @a2ui/* (和它带的 zod-to-json-schema) 生效, 测试里得让它们过 vite
    server: { deps: { inline: [/@a2ui\//, /zod-to-json-schema/] } },
    exclude: [
      "**/node_modules/**",
      "**/dist/**",
      "src/store/auto_continue.test.ts",
      "src/store/queue.test.ts",
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
