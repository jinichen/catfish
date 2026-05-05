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
