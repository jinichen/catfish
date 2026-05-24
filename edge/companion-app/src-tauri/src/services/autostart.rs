//! 应用启动时自动拉起关键服务 —— 让员工不需要手动按"启动"。
//!
//! 设计:
//!   - tool-bridge: client-side MCP server, 跟 Companion lifecycle 绑定 → Companion 管
//!   - gateway: 服务端基础设施, **不归 Companion 管** (5/22 鸿波拍板)
//!
//! 5/22 BL-COMPANION-DECOUPLE-GATEWAY (鸿波): 服务端 vs 客户端职责清分.
//!   - **服务端** (gateway): 由 launchctl plist (本机 dev) 或客户 IT (生产) 管.
//!     Companion 不 spawn / 不 watchdog. UI health check + 提示员工.
//!   - **客户端** (3 个本地服务, Companion 全管):
//!     - tool-bridge (MCP server, hermes-cli socket)
//!     - local-search (员工文件 FTS watcher)
//!     - chrome (隔离 Chrome 实例 + CDP for LLM 浏览器操作)
//!
//!   解决的老问题:
//!     - autostart + watchdog 撞 PID file / 多进程 (5/22 17561+57521 同跑)
//!     - 50 员工 Mac 各跑独立 gateway 资源浪费
//!     - 职责倒置 (UI 进程拉服务端基础设施)
//!
//!   chrome autostart 决策 (5/22 鸿波拍 "默认 autostart"):
//!   LLM 操作浏览器是 catfish 核心场景 (EIS / 周报 / 资质等), 员工不该手动
//!   每次点 'chrome 启动' 按钮. 默认 spawn, 窗口可手动最小化.

use std::path::Path;

use crate::services::{catfish_paths, endpoints, process};

/// autostart 入口 —— Tauri setup hook 里 spawn 一次, 起 3 个本地客户端服务.
///
/// 5/22 鸿波拍: gateway 解耦 (launchctl 管), 这层只管 3 个 client-side service.
pub fn schedule_autostart() {
    tauri::async_runtime::spawn(async move {
        ensure_tool_bridge_running().await;
        ensure_local_search_running().await;
        ensure_chrome_running().await;
    });
}

// 5/22 BL-COMPANION-DECOUPLE-GATEWAY (鸿波): ensure_gateway_running 函数删 —
// schedule_autostart 不再调它, watchdog 也不再调它. 员工想手动启停 gateway
// 走 commands/gateway.rs:gateway_start (前端 UI 按钮). 真生产部署用 launchctl
// plist 或客户 IT 流程, Companion 不操心.
//
// 删的是 ~70 行 spawn 逻辑, 跟 commands/gateway.rs:gateway_start 重复. 真要恢复
// git blame.

// ============================================================
// tool-bridge
// ============================================================

pub async fn ensure_tool_bridge_running() {
    if pid_alive(catfish_paths::tool_bridge_pid_file().as_deref(), "catfish_tool_bridge") {
        log::info!("autostart: tool-bridge already running");
        return;
    }

    let dir = match catfish_paths::tool_bridge_dir() {
        Some(d) => d,
        None => {
            log::warn!("autostart: tool-bridge dir not found, skipping");
            return;
        }
    };
    let python = match catfish_paths::tool_bridge_python() {
        Some(p) => p,
        None => {
            log::warn!("autostart: hermes venv python not found — \
                tool-bridge can't start (聊天将无法调工具, Gemini 会退化到 tool_code)");
            return;
        }
    };
    let log_path = match catfish_paths::tool_bridge_log_path() {
        Some(p) => p,
        None => return,
    };
    let pid_file = match catfish_paths::tool_bridge_pid_file() {
        Some(p) => p,
        None => return,
    };
    let socket_path = match catfish_paths::tool_bridge_socket() {
        Some(p) => p,
        None => return,
    };

    // socket 文件残留清理 —— 上次没正常 stop 会留死文件
    let _ = std::fs::remove_file(&socket_path);

    let pythonpath = dir.join("src").to_string_lossy().to_string();
    let cfg = process::SpawnConfig {
        program: python,
        args: vec![
            "-m".into(),
            "catfish_tool_bridge".into(),
            "--socket".into(),
            socket_path.to_string_lossy().to_string(),
        ],
        log_path,
        working_dir: dir,
        env: vec![("PYTHONPATH".into(), pythonpath)],
    };

    match process::spawn_detached(cfg) {
        Ok(handle) => {
            if let Err(e) = std::fs::write(&pid_file, handle.pid.to_string()) {
                log::warn!("autostart: tool-bridge started but failed to write PID: {e}");
            } else {
                log::info!("autostart: tool-bridge started (PID {})", handle.pid);
            }
        }
        Err(e) => {
            log::warn!("autostart: tool-bridge spawn failed: {e}");
        }
    }
}

// ============================================================
// local-search (5/22 鸿波: 加进 autostart, 文件 FTS watcher)
// ============================================================

pub async fn ensure_local_search_running() {
    if pid_alive(catfish_paths::local_search_pid_file().as_deref(), "catfish_search") {
        log::info!("autostart: local-search already running");
        return;
    }

    let dir = match catfish_paths::local_search_dir() {
        Some(d) => d,
        None => {
            log::warn!("autostart: local-search dir not found, skipping");
            return;
        }
    };
    let python = match catfish_paths::local_search_python() {
        Some(p) => p,
        None => {
            log::warn!("autostart: local-search python not found");
            return;
        }
    };
    let log_path = match catfish_paths::local_search_log_path() {
        Some(p) => p,
        None => return,
    };
    let pid_file = match catfish_paths::local_search_pid_file() {
        Some(p) => p,
        None => return,
    };

    // 跟 commands/local_search.rs:local_search_start 同款 spawn: src/ → PYTHONPATH,
    // 避免员工跑 pip install -e .
    let pythonpath = dir.join("src").to_string_lossy().to_string();
    let cfg = process::SpawnConfig {
        program: python,
        args: vec![
            "-m".into(),
            "catfish_search.cli".into(),
            "watch".into(),
        ],
        log_path,
        working_dir: dir,
        env: vec![("PYTHONPATH".into(), pythonpath)],
    };

    match process::spawn_detached(cfg) {
        Ok(handle) => {
            if let Err(e) = std::fs::write(&pid_file, handle.pid.to_string()) {
                log::warn!("autostart: local-search started but failed to write PID: {e}");
            } else {
                log::info!("autostart: local-search started (PID {})", handle.pid);
            }
        }
        Err(e) => {
            log::warn!("autostart: local-search spawn failed: {e}");
        }
    }
}

// ============================================================
// chrome (5/22 鸿波: 默认 autostart, LLM 浏览器场景核心)
// ============================================================

pub async fn ensure_chrome_running() {
    if pid_alive(catfish_paths::chrome_pid_file().as_deref(), "remote-debugging-port") {
        log::info!("autostart: chrome already running");
        return;
    }

    let chrome_bin = match catfish_paths::find_chrome() {
        Some(p) => p,
        None => {
            log::warn!(
                "autostart: 找不到 Chrome / Chromium, 跳过 chrome autostart \
                 (LLM 浏览器操作场景不可用, 装 Google Chrome 后 Companion 重启自动起)"
            );
            return;
        }
    };
    let user_data_dir = match catfish_paths::chrome_user_data_dir() {
        Some(p) => p,
        None => return,
    };
    let log_path = match catfish_paths::chrome_log_path() {
        Some(p) => p,
        None => return,
    };
    let pid_file = match catfish_paths::chrome_pid_file() {
        Some(p) => p,
        None => return,
    };
    let working_dir = user_data_dir
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_else(std::env::temp_dir);

    let chrome_port = endpoints::endpoints().chrome_port;
    // 跟 commands/chrome.rs:chrome_launch 同款 spawn (隔离 user-data-dir + CDP 端口).
    // 5/22 鸿波: 默认 autostart 时窗口仍会显示, 员工可手动最小化. 不上 headless,
    // 因为 LLM 操作时员工要看屏幕确认 (5 内部初衷"催 不代行" — 透明可监督).
    let cfg = process::SpawnConfig {
        program: chrome_bin,
        args: vec![
            format!("--remote-debugging-port={chrome_port}"),
            format!("--user-data-dir={}", user_data_dir.display()),
            "--no-first-run".into(),
            "--no-default-browser-check".into(),
            "--disable-features=DialMediaRouteProvider".into(),
            // Chrome 138+ 默认 CDP WebSocket Origin 白名单, hermes browser tool 默认空 Origin
            "--remote-allow-origins=*".into(),
            "about:blank".into(),
        ],
        log_path,
        working_dir,
        env: vec![],
    };

    match process::spawn_detached(cfg) {
        Ok(handle) => {
            if let Err(e) = std::fs::write(&pid_file, handle.pid.to_string()) {
                log::warn!("autostart: chrome started but failed to write PID: {e}");
            } else {
                log::info!("autostart: chrome started (PID {})", handle.pid);
            }
        }
        Err(e) => {
            log::warn!("autostart: chrome spawn failed: {e}");
        }
    }
}

// ============================================================
// helpers
// ============================================================

fn pid_alive(pid_file: Option<&Path>, cmdline_substr: &str) -> bool {
    pid_file
        .and_then(|p| {
            if p.exists() {
                process::read_pid_file_alive_strict(p, cmdline_substr)
            } else {
                None
            }
        })
        .is_some()
}
