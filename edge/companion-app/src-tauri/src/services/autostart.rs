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

use super::autostart_deps::check_runtime_deps;
use super::autostart_mcp::ensure_catfish_tools_mcp_registered;

/// autostart 入口 —— Tauri setup hook 里 spawn 一次, 起 3 个本地客户端服务.
///
/// 5/22 鸿波拍: gateway 解耦 (launchctl 管), 这层只管 3 个 client-side service.
pub fn schedule_autostart() {
    tauri::async_runtime::spawn(async move {
        ensure_catfish_tools_mcp_registered();
        ensure_tool_bridge_running().await;
        ensure_local_search_running().await;
        ensure_chrome_running().await;
        check_runtime_deps().await;
    });
}

// 5/22 gateway 解耦: ensure_gateway_running 删 (~70 行 spawn 逻辑), gateway 由
// launchctl/客户 IT 管. P39 收尾: gateway_start/stop/get_dev_token 3 command 也
// 删, 只留 gateway_status 探活. 见 git blame.

// ============================================================
// tool-bridge
// ============================================================

pub async fn ensure_tool_bridge_running() {
    // P3.5.196 (7/7 鸿波军规审判): 每次都 pkill+spawn, 保证 tool-bridge 加载最新代码.
    //
    // # 老逻辑的问题
    // 老 ensure: pid_file 那个 PID 活着就 early return. 副作用跟 local-search 一样 (见
    // ensure_local_search_running P3.4.2 comment):
    //   - 老 tool-bridge 不知道 catfish plugin.py / tool schema 更新了
    //   - 老 tool-bridge 不知道 hermes API 变了 (P25 monkey-patch 签名对不上)
    //   - PID alive ≠ 服务健康 (进程 alive 但 tool 调用 TypeError, watchdog 检测不到)
    //
    // 鸿波 7/7 实测撞过: 早上改了 catfish plugin.py (P3.5.192 has_host_access fix)
    // + 加了 email_read/attachment tool (P3.5.194), 但 tool-bridge 从 12:04 就没重启,
    // 加载的是改动前 code. 员工反馈"chat 未知错误", 原因是 monkey-patch 用老签名调
    // 新 hermes API. 手动 pkill + hermes restart 才好. 军规: 让 Companion 冷启动就
    // 自动重启 tool-bridge, 不依赖员工记忆.
    //
    // # 新逻辑
    // 每次 Companion 启动 pkill -f 'catfish_tool_bridge --socket' 清孤儿, 再 spawn 新的.
    // 启动慢 3-5s (初始 import hermes), 可预测.
    //
    // caller:
    //   - autostart (schedule_autostart): app 冷启动时调, pkill 上次残留 + fresh spawn
    //   - watchdog: 5s tick 时 !is_alive 才调 (进程真死了), pkill 无匹配静默无害
    pkill_tool_bridge();

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
    // P3.4.2 (6/15 鸿波): 强制清旧进程后重启 — 不再"在跑就 return".
    //
    // # 老逻辑的问题
    // 老 ensure: pid_file 那个 PID 活着就 early return. 副作用:
    //   - 老 watcher 不知道 ~/.catfish/search-scope.yaml 改了 (没 SIGHUP / reload)
    //   - 老 watcher 不知道 catfish_search 包代码更新了 (Python module 已载入)
    //   - pid_file 只记 1 个 PID, 历史 race 留下的孤儿进程不被清理
    //
    // 鸿波本机实测撞过: 进程 5/8 起跑 1 个多月, 后续 yaml 加 ~/Documents 实时索引
    // 不生效, 因为老 watcher 用 5/8 的 yaml.
    //
    // # 新逻辑
    // 每次 Companion 启动 pkill -f 'catfish_search.cli watch' 清孤儿, 再 spawn 新的.
    //
    // BL-SEARCH-NO-BOOTSTRAP (7/27 鸿波实盘): 这里原来写着"启动慢 5-15s
    // (初始 reconcile)" —— **那是假的**. watcher.py:run_watch 只做
    // observer.schedule() + 处理文件变化事件, 全文没有一处调 run_index,
    // 从来就没有 reconcile.
    //
    // 后果: local_search 从装上那天起没做过全量索引. 索引库里只有 watcher
    // 运行期间碰巧被改动过的文件 —— 鸿波 yaml 里配了 ~/Documents / ~/Desktop /
    // ~/Downloads / ~/.catfish/uploads, 四个各 0 条, 60332 条全来自
    // ~/person_task (活跃开发目录, git checkout / npm ci 天天动文件).
    //
    // 修法(7/27): 不在这里加 reconcile —— 每次开 Companion 都全量 stat 判重
    // 太慢且绝大多数情况白跑. 改成 watch 自己在**库为空**时 bootstrap 一次
    // (watcher.py:_bootstrap_if_empty). 员工加目录 / 手动重建走
    // commands::local_search::local_search_index.
    //
    // pkill 仅 macOS/Linux 有, Windows 暂不支持 (Companion 当前只 macOS 发布).
    pkill_local_search_watchers();

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

/// P3.4.2 (6/15 鸿波): pkill 所有 catfish_search.cli watch 进程 (含孤儿).
///
/// 跟 pid_alive 配套使用: 既然不能 reload yaml / 不能热更代码, 干脆每次 Companion
/// 启动都把所有 watcher 都杀掉, 然后由 ensure_local_search_running spawn 新的.
///
/// 用 pkill -f 模糊匹配 cmdline. 'catfish_search.cli watch' 这串够特异, 不会
/// 误杀别的进程. 失败静默 (没 pkill 命令 / 没匹配进程都不算错).
///
/// macOS / Linux only. Windows 暂不处理 (Companion 当前只 macOS).
///
/// pub: commands/local_search.rs:local_search_start 也调 (UI 重启路径).
pub fn pkill_local_search_watchers() {
    #[cfg(any(target_os = "macos", target_os = "linux"))]
    {
        let out = std::process::Command::new("pkill")
            .args(["-f", "catfish_search.cli watch"])
            .output();
        match out {
            Ok(o) if o.status.success() => {
                log::info!(
                    "autostart: pkill 清掉旧 local-search watcher (确保新进程用最新 yaml + 代码)"
                );
                // pkill 完后 fsnotify subscribe 释放需要一小段, 给 0.5s 缓冲
                std::thread::sleep(std::time::Duration::from_millis(500));
            }
            Ok(_) => {
                // pkill 返非 0 通常是"无匹配进程" (exit 1), 这是正常情况
                log::debug!("autostart: pkill local-search 无匹配进程 (首次启动 / 已清干净)");
            }
            Err(e) => {
                log::warn!("autostart: pkill local-search 失败 (不阻塞 spawn): {e}");
            }
        }
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        log::debug!("autostart: pkill_local_search_watchers 跳过 (非 unix)");
    }
}

/// P3.5.196 (7/7 鸿波军规审判): pkill 所有 catfish_tool_bridge --socket 进程 (含孤儿).
///
/// 跟 pid_alive 配套使用: 跟 pkill_local_search_watchers 同 pattern (P3.4.2).
/// 用途:
///   - autostart 冷启动前 pkill 上次残留进程, 保证新 spawn 加载最新 code
///   - 未来可用于 UI restart 按钮 (commands 里 wrap 一下即可)
///
/// 用 pkill -f 模糊匹配 cmdline. 'catfish_tool_bridge --socket' 这串够特异 (跟
/// hermes 里 mcp_server 子进程 'catfish_tool_bridge.mcp_server' 区分开), 不误杀.
/// 失败静默 (没 pkill 命令 / 无匹配都不算错).
///
/// macOS / Linux only. Windows 暂不处理 (Companion 当前只 macOS).
///
/// pub: 让 commands/ 也能调 (未来 UI restart 按钮).
pub fn pkill_tool_bridge() {
    #[cfg(any(target_os = "macos", target_os = "linux"))]
    {
        let out = std::process::Command::new("pkill")
            .args(["-f", "catfish_tool_bridge --socket"])
            .output();
        match out {
            Ok(o) if o.status.success() => {
                log::info!(
                    "autostart: pkill 清掉旧 tool-bridge (确保新进程用最新 catfish code + hermes 版本)"
                );
                // pkill 完后 unix socket 释放需要一小段, 给 0.5s 缓冲
                std::thread::sleep(std::time::Duration::from_millis(500));
            }
            Ok(_) => {
                // pkill 返非 0 通常是"无匹配进程" (exit 1), 首次启动或已清干净都正常
                log::debug!("autostart: pkill tool-bridge 无匹配进程 (首次启动 / 已清干净)");
            }
            Err(e) => {
                log::warn!("autostart: pkill tool-bridge 失败 (不阻塞 spawn): {e}");
            }
        }
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        log::debug!("autostart: pkill_tool_bridge 跳过 (非 unix)");
    }
}

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
