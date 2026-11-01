import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 界面代码在 central/web/src/mobile (网页版 /m 同一份); 这里只多一层 iOS 平台 (src/platform_ios.ts)。
// 那边的文件 import react / tweetnacl 时会就近找 central/web/node_modules —— 两份 React 会让 hooks 直接崩,
// 所以全部钉到本工程的 node_modules。
const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    dedupe: ["react", "react-dom", "tweetnacl"],
    alias: {
      react: here("./node_modules/react"),
      "react-dom": here("./node_modules/react-dom"),
      tweetnacl: here("./node_modules/tweetnacl"),
    },
  },
  server: { fs: { allow: [here("../.."), here(".")] } },
  build: { outDir: "dist", target: "es2022", sourcemap: false },
});
