import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { bootstrapEndpoints } from "./lib/env";
import { fetchViaProxy } from "./lib/http_proxy";
import { installFetchProxy } from "./lib/fetchProxyPolicy";
import { checkTimeoutChain } from "./lib/timeouts";
import {
  StartupErrorBoundary,
  installStartupDiagnostics,
} from "./lib/startupDiagnostics";
import "./styles/tokens.css";
import "./styles/globals.css";
// highlight.js 主题 —— github 风格,在浅/深色都看得清
import "highlight.js/styles/github.css";
// 8/7 UI REFRESH 覆盖层 — 必须最后 import (同名类靠源顺序覆盖 globals.css)
import "./styles/refresh.css";

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
// 8/10: 超时链自检。这条链跨 Companion / hermes / gateway 三个进程, 而当天
// 的事故正是"界面上改了 gateway 的 180 秒, 实际卡住的是 Companion 里写死的
// 30 秒"。同进程内这几层的顺序至少要能自己查出来 —— 配反了不该是静默的。
(function assertTimeoutChain() {
  for (const p of checkTimeoutChain()) {
    console.error("[timeouts] 超时链配置有问题:", p);
  }
})();

// 判据和安装逻辑都在 fetchProxyPolicy.ts —— **别在这里内联**。
//
// 8/14 Windows 白屏就是这段曾经内联在这里的判据干的: 原来写的是
// `url.startsWith("http://")`, 而 Windows 上 Tauri **自己的 IPC**
// 就是 http://ipc.localhost/<cmd> (macOS 上是 ipc://localhost/<cmd>),
// 于是每次 invoke 都被劫进代理, 代理内部又 invoke, 无限递归到爆 V8
// 字符串上限。它当时没被任何测试覆盖, 正是因为锁在这个 IIFE 里不可 import。
installFetchProxy(fetchViaProxy);

// 装错误钩子 —— 必须在 render 之前, 否则 render 期间出事照样是白屏。
installStartupDiagnostics();

// BL-WIN9 / DEPLOY1 (5/8): 启动前先从 Rust backend 拿 yaml 配置的 endpoints,
// 替换 build-time 默认 gatewayUrl. 这样客户改 ~/.catfish/companion.yaml 重启
// 就能切到任何网关地址 (中央服务器部署), 不需要重新打包 .app/.exe.
//
// 8/4 (鸿波 Windows 启动白屏) 加超时:
//   bootstrapEndpoints 内部两块都有 try/catch, 所以**抛异常**逃不出来 —— 但里面
//   4 个 `await invoke(...)` 一个超时都没有。invoke 挂住 (不 resolve 也不 reject)
//   时, 这个 promise 永远不 settle, 下面 .finally 里的 render **永远不执行**,
//   表现就是一整片白, 且没有任何异常可抓。
//
//   .finally 能兜住 rejection, 兜不住"不返回"。所以这里必须是 race 而不是 catch。
//
//   5 秒: 这几个 invoke 是读本地 yaml + 拼 auth header, 正常是毫秒级。5 秒没回来
//   就是卡住了, 而卡住的后果只是 endpoints 回退到 build-time 默认 —— 界面能起来,
//   员工还能进设置改。**永远比白屏强。**
const BOOTSTRAP_TIMEOUT_MS = 5000;

function renderApp() {
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <StartupErrorBoundary>
        <App />
      </StartupErrorBoundary>
    </React.StrictMode>,
  );
}

Promise.race([
  bootstrapEndpoints(),
  new Promise<void>((resolve) =>
    setTimeout(() => {
      // eslint-disable-next-line no-console
      console.warn(
        `[startup] bootstrapEndpoints ${BOOTSTRAP_TIMEOUT_MS}ms 未返回, 先渲染界面; ` +
          `endpoints 回退到 build-time 默认, 可在设置里改。`,
      );
      resolve();
    }, BOOTSTRAP_TIMEOUT_MS),
  ),
])
  .catch(() => {
    /* bootstrapEndpoints 内部已全 try/catch, 这里只是保证 render 一定发生 */
  })
  .then(renderApp);
