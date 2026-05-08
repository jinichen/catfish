import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { bootstrapEndpoints } from "./lib/env";
import "./styles/tokens.css";
import "./styles/globals.css";
// highlight.js 主题 —— github 风格,在浅/深色都看得清
import "highlight.js/styles/github.css";

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
