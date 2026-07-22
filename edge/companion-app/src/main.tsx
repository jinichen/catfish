import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { bootstrapEndpoints } from "./lib/env";
import { fetchViaProxy } from "./lib/http_proxy";
import "./styles/tokens.css";
import "./styles/globals.css";
// highlight.js 主题 —— github 风格,在浅/深色都看得清
import "highlight.js/styles/github.css";

// BL-CSP-PROXY-GLOBAL (7/18 鸿波): 全局 monkey-patch window.fetch → fetchViaProxy.
//
// 背景:
//   Tauri CSP connect-src 严格 ('self' + localhost:* + 127.0.0.1:*), 员工输达华 IP
//   (远端 gateway URL) 时 build 版所有 direct fetch 被 WebView 拦, 返 TypeError: Load failed
//   (Task #63/64 铁证). 之前 Step 6 逐个改 5 处 direct fetch → fetchViaProxy, 未来加新 fetch
//   会漏, 靠 code review 抓.
//
// 方案:
//   在**所有前端 code 之前**替换 window.fetch. 拦截 http:// https:// 请求, 转 fetchViaProxy
//   走 Rust reqwest (CSP 严格无所谓). 内部 scheme (tauri: / ipc: / blob: / data: / relative)
//   走原生 fetch (CSP 允许 self, 无需代理).
//
// 优点:
//   - 一次 patch, 全项目 fetch 自动生效. 未来加新 fetch call 无需改.
//   - Step 6 里 5 处硬编码 fetchViaProxy 可保留 (双保险) 或撤 (代码更 clean).
//
// 风险:
//   - vite HMR / 内部库若 direct fetch localhost:1420 · 走原生 (CSP 允许 localhost). OK.
//   - 若 build 后 · 内部库 fetch 远端 URL · 也自动走代理. OK.
(function installFetchProxy() {
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    // 提取 URL string
    let url: string;
    if (typeof input === "string") {
      url = input;
    } else if (input instanceof URL) {
      url = input.href;
    } else if (input && typeof (input as Request).url === "string") {
      url = (input as Request).url;
    } else {
      // 不认识的 input 类型 · 用原生 · 保守
      return originalFetch(input, init);
    }

    // 只 patch http:// https:// (external). 其他 scheme (tauri: ipc: blob: data: file: 相对路径)
    // 都走原生 · CSP self 允许.
    if (url.startsWith("http://") || url.startsWith("https://")) {
      return fetchViaProxy(url, init);
    }
    return originalFetch(input, init);
  };
})();

// BL-WIN9 / DEPLOY1 (5/8): 启动前先从 Rust backend 拿 yaml 配置的 endpoints,
// 替换 build-time 默认 gatewayUrl. 这样客户改 ~/.catfish/companion.yaml 重启
// 就能切到任何网关地址 (中央服务器部署), 不需要重新打包 .app/.exe.
bootstrapEndpoints().finally(() => {
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
});
