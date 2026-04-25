//! local-search 后台 watcher 的启停 + 状态查询。
//!
//! 区分两个独立场景：
//!
//! 1. **stdio MCP (`catfish-search-mcp`)** —— Hermes 在对话里要查文件时，
//!    自己 fork 一个 MCP 进程，stdin/stdout 用完就退。这里 Companion 不管。
//!
//! 2. **后台 watcher (`catfish-search watch`)** —— 监听员工文件系统事件，
//!    增量更新 SQLite FTS 索引。这是真长期跑的服务，Companion 管启停。
//!
//! 后台 watcher 没有端口可探，状态完全靠 PID 文件 + is_alive 判断。

use crate::commands::types::ServiceStatus;
use crate::services::{catfish_paths, process};

#[tauri::command]
pub async fn local_search_start() -> Result<(), String> {
    if let Some(pid_file) = catfish_paths::local_search_pid_file() {
        if let Some(pid) = process::read_pid_file_alive(&pid_file) {
            return Err(format!("Local Search watcher 已在跑（PID {pid}）"));
        }
    }

    let dir = catfish_paths::local_search_dir().ok_or_else(|| {
        "找不到 local-search 目录 — 设 CATFISH_LOCAL_SEARCH_DIR 或确保 \
         ~/person_task/catfish/edge/local-search 存在"
            .to_string()
    })?;
    let python = catfish_paths::local_search_python()
        .ok_or_else(|| "找不到 Python 解释器".to_string())?;
    let log_path = catfish_paths::local_search_log_path()
        .ok_or_else(|| "找不到日志路径".to_string())?;
    let pid_file = catfish_paths::local_search_pid_file()
        .ok_or_else(|| "找不到 PID 文件路径".to_string())?;

    // 把 src/ 加到 PYTHONPATH，免去员工跑 `pip install -e .` 的额外步骤
    // （catfish-local-search 只有 pyyaml + watchdog 这两个核心 runtime 依赖）
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

    let handle = process::spawn_detached(cfg).map_err(|e| format!("启动失败: {e}"))?;

    std::fs::write(&pid_file, handle.pid.to_string())
        .map_err(|e| format!("写 PID 文件失败: {e}"))?;

    Ok(())
}

#[tauri::command]
pub async fn local_search_stop() -> Result<(), String> {
    let pid_file = catfish_paths::local_search_pid_file()
        .ok_or_else(|| "找不到 PID 文件路径".to_string())?;

    if !pid_file.exists() {
        return Err("Local Search watcher 没有由 Companion 启动过（无 PID 文件）".into());
    }

    let pid: u32 = std::fs::read_to_string(&pid_file)
        .map_err(|e| format!("读 PID 文件失败: {e}"))?
        .trim()
        .parse()
        .map_err(|e| format!("PID 文件格式错误: {e}"))?;

    process::kill(pid).map_err(|e| format!("kill 失败: {e}"))?;

    let _ = std::fs::remove_file(&pid_file);
    Ok(())
}

#[tauri::command]
pub async fn local_search_status() -> Result<ServiceStatus, String> {
    let pid = catfish_paths::local_search_pid_file()
        .and_then(|p| process::read_pid_file_alive(&p));

    match pid {
        Some(pid) => Ok(ServiceStatus {
            running: true,
            healthy: true, // 进程活着就算健康；FS watcher 没业务面探测点
            pid: Some(pid),
            port: None,
            message: Some("Watcher 在跑（FS 事件 → 增量索引）".into()),
        }),
        None => Ok(ServiceStatus::down(
            None,
            "未启动 — 点 \"启动\" 拉起 watcher（监听文件变化更新索引）",
        )),
    }
}
