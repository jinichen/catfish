# Catfish Companion App

鲶鱼平台的桌面伴侣应用 —— 让员工在浏览器和终端之外，
有一个常驻桌面入口管理本地 Catfish 组件、看会话状态、查个人配额。

## 定位

这不是 Hermes Agent 本身（Hermes 仍在终端里跑），
也不是 Catfish 中央 Web 控制台（那是浏览器里给管理员看的）。

Companion App 是给**员工本人**用的桌面工具，单窗口三 tab：

1. **Console（控制台）** —— 一键启停本地 Catfish 组件
   （llm-gateway / Catfish Chrome / local-search），实时日志，
   状态点亮红或绿
2. **Sessions（会话）** —— 看当前所有 Hermes 会话，最近的输出、
   token 占用，从这里拉起新会话
3. **Dashboard（仪表盘）** —— 当前身份（SOUL / skin / SSO）、
   本月配额、可用模型清单、装了哪些 skills/MCP

## 设计哲学

> 员工不应该 care HTTPS_PROXY 这种系统级状态。
>
> Companion App 的责任是把 Catfish 各组件的复杂度（端口、进程、
> 代理状态、上游可达性）藏进单个桌面应用里，员工打开看到的应该是
> "✓ 一切正常，可以干活了" 或者 "✗ Clash 没起，点这里修"。

## 技术栈

- **Tauri 2.0**（Rust 后端 + WebView 前端，包体 ~10MB，内存 ~50MB）
- **React 18 + TypeScript + Vite**
- **Zustand**（轻量状态管理，避开 Redux 样板）
- **macOS + Windows** 双平台

## 项目结构

```
companion-app/
├── src-tauri/        # Rust 后端
│   └── src/
│       ├── commands/        # 暴露给前端的 invoke 命令
│       │   ├── gateway.rs       # llm-gateway 启停/状态
│       │   ├── chrome.rs        # Catfish Chrome (9222)
│       │   ├── local_search.rs  # local-search MCP
│       │   ├── health.rs        # 调 gateway HTTP
│       │   ├── logs.rs          # tail 日志 → 事件流
│       │   ├── sessions.rs      # 读 Hermes 会话目录
│       │   └── system.rs        # 通知/内存/自启动
│       ├── services/        # 内部进程管理（前端不可见）
│       └── tray/            # menubar 托盘
├── src/              # React 前端
│   ├── tabs/
│   │   ├── Console/
│   │   ├── Sessions/
│   │   └── Dashboard/
│   ├── components/
│   ├── lib/
│   ├── hooks/
│   ├── store/
│   └── types/
└── docs/
    ├── ARCHITECTURE.md  # 进程模型 / IPC 约定
    └── BRANDING.md      # 品牌色 / 字体 / 间距
```

## 开发

```bash
npm install
npm run tauri:dev       # 开发模式（前端热更新 + Rust 热编译）
npm run tauri:build     # 打包（macOS .dmg / Windows .msi）
```

详见 `docs/ARCHITECTURE.md`。

## 状态

🚧 **骨架阶段** —— 目录已建，配置文件占位，业务代码尚未填充。

下一步：填 `src-tauri/src/commands/gateway.rs`（最简单的 health 探测）作为第一个能跑的 invoke 路径，验证 Rust ↔ React 通路。
