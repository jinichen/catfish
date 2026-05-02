//! 应用启动时自动拉起关键服务 —— 让员工不需要手动按"启动"。
//!
//! 设计:
//!   - 关键链路服务 (gateway / tool-bridge): 后台静默拉起, 失败也不阻塞 UI 启动
//!   - 已在跑的服务: 静默 no-op, 不当错处理
//!   - 失败 / panic: tauri::async_runtime::spawn 隔离, 主线程继续
//!
//! 为什么不直接复用 commands::*::*_start?
//!   它们是给前端 invoke 用的, 已在跑时返回 Err("已在跑"), 在 autostart 上下文里
//!   这其实是 "happy path"。包一层 ensure_running 把 "已在跑" 转成 Ok, 真异常才记 warn。

use std::path::Path;

use crate::services::{catfish_paths, endpoints, process};

/// autostart 入口 —— 在 Tauri setup hook 里 spawn 一次, 顺序拉起 gateway + tool-bridge。
///
/// 为啥要顺序: tool-bridge spawn 时会 import hermes 整个 toolset, ~3-5 秒;
///             gateway 是独立 FastAPI, 几乎瞬间就绪。先起 gateway 让前端早点
///             看到 catalog, 再背景起 tool-bridge。
pub fn schedule_autostart() {
    tauri::async_runtime::spawn(async move {
        ensure_gateway_running().await;
        ensure_tool_bridge_running().await;
    });
}

// ============================================================
// gateway
// ============================================================

pub async fn ensure_gateway_running() {
    if pid_alive(catfish_paths::gateway_pid_file().as_deref(), "catfish_gateway") {
        log::info!("autostart: gateway already running");
        return;
    }

    let dir = match catfish_paths::gateway_dir() {
        Some(d) => d,
        None => {
            log::warn!("autostart: gateway dir not found, skipping (员工可能没装)");
            return;
        }
    };
    let python = match catfish_paths::gateway_python() {
        Some(p) => p,
        None => {
            log::warn!("autostart: gateway python not found");
            return;
        }
    };
    let log_path = match catfish_paths::gateway_log_path() {
        Some(p) => p,
        None => return,
    };
    let pid_file = match catfish_paths::gateway_pid_file() {
        Some(p) => p,
        None => return,
    };

    let ep = endpoints::endpoints();
    // 五一 sprint 5/2 修: 显式传 CATFISH_ENV=dev, 让 dev 多账号 + /api/dev/users 可用.
    // 不显式传时 Companion 父进程若有 CATFISH_ENV=prod (Mac.app 启动环境继承),
    // 会污染 gateway, /api/dev/users 返 404, 切换器拉空.
    // 客户生产部署是另起 gateway (不通过 Companion autostart), 互不干扰.
    let cfg = process::SpawnConfig {
        program: python,
        args: vec!["-m".into(), "catfish_gateway.app".into()],
        log_path,
        working_dir: dir,
        env: vec![
            ("PORT".into(), ep.gateway_port.to_string()),
            ("CATFISH_ENV".into(), "dev".into()),
        ],
    };

    match process::spawn_detached(cfg) {
        Ok(handle) => {
            if let Err(e) = std::fs::write(&pid_file, handle.pid.to_string()) {
                log::warn!("autostart: gateway started but failed to write PID: {e}");
            } else {
                log::info!("autostart: gateway started (PID {})", handle.pid);
            }
        }
        Err(e) => {
            log::warn!("autostart: gateway spawn failed: {e}");
        }
    }
}

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
