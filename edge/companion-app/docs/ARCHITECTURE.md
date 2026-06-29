# Companion App 架构

## 进程模型

```
┌─────────────────────────────────────────────────┐
│  Companion App (Tauri 进程)                      │
│                                                 │
│  ┌────────────────┐       ┌──────────────────┐  │
│  │  WebView       │       │  Rust 主进程     │  │
│  │  (React 前端)  │ ←IPC→ │  (Tauri runtime) │  │
│  └────────────────┘       └────────┬─────────┘  │
│                                    │             │
│                    spawn / kill ───┤             │
└────────────────────────────────────┼─────────────┘
                                     ▼
   ┌──────────────┬─────────────────┬────────────────┐
   ▼              ▼                 ▼                ▼
catfish-      Catfish Chrome    local-search      其他
gateway       (--port 9222)     MCP server       MCP servers
(:8999)
```

Companion App 是个**控制器**，不是路由器。它起停子进程、读它们的健康状态/日志，
但实际的 LLM 流量、浏览器调试流量都不经它转发。

## IPC 约定

前端调 Rust 命令通过 `src/lib/tauri.ts`，约定：

1. **命名**：`<service>_<op>`，例如 `gateway_start`、`chrome_status`
2. **参数**：camelCase 在 TS 端，Rust 端用 `#[serde(rename_all = "camelCase")]` 接收
3. **返回**：`Result<T, String>` —— Rust 端的 `Err` 字符串直接传给前端 `catch`
4. **流式**：日志 / 进度通过 `app_handle.emit("log:gateway", payload)`，前端 `listen("log:gateway", ...)`

## 命令清单（src-tauri/src/commands/）

每个文件聚焦一个职责，避免单文件超过 200 行：

- `gateway.rs` —— `gateway_start` / `gateway_stop` / `gateway_status`
- `chrome.rs` —— `chrome_launch` / `chrome_kill` / `chrome_status`
- `local_search.rs` —— `local_search_start` / `local_search_stop` / `local_search_status`
- `health.rs` —— `healthz` / `catalog` (调 gateway HTTP)
- `logs.rs` —— `tail` (订阅日志事件流)
- `sessions.rs` —— `sessions_list` / `sessions_get` (读 ~/.hermes/sessions/)
- `system.rs` —— `notify` / `create_reminder` / `list_reminder_lists` / `create_calendar_event` / `list_calendars` / `get_hermes_version` (P3.5.141 6/29: 砍 `open_terminal` — Sessions tab 已并入工作台 sidebar, 终端入口下架)

## 状态管理

前端用 Zustand 三个 store：

- `services.ts` —— 三个服务的最新状态（每 N 秒由 hook 轮询写入）
- `identity.ts` —— 当前员工身份 / SOUL / skin / SSO
- `ui.ts` —— activeTab / dark mode 等纯 UI 状态

刻意不上 Redux —— MVP 状态量小，Zustand 30 行能搭完。

## 安全

- Tauri CSP 设为 `null` 是因为前端调用都走 `invoke`，不开 `webview` 任何外站权限
- Rust 端 `commands::` 是唯一可被前端调用的入口，`services::` 是内部模块
- 子进程的 PID 写到本地状态文件 `$CATFISH_HOME/companion-state.json`，只 kill 自己起的实例

## 开发

```bash
cd companion-app
npm install
npm run tauri:dev       # 同时启动 Vite (1420) + cargo run
```

## 打包

- macOS：`./scripts/build-mac.sh` → `.dmg` (universal binary)
- Windows：`./scripts/build-win.sh` → `.msi`

第一版不开自动更新；等核心功能稳定后接入 `tauri-plugin-updater`。
