//! Catfish Chrome 的启停 + 状态查询。
//!
//! Catfish Chrome 是 Companion 起的**独立**调试 Chrome 实例（隔离 user-data-dir），
//! 不复用员工日常浏览器，避免污染 cookies / 误退他们的 Chrome 主窗口。
//!
//! 启动：`<chrome-bin> --remote-debugging-port=9222 --user-data-dir=<隔离 profile>`
//! kill：只 kill PID 文件里记的那个进程，绝不动员工日常 Chrome。

use std::time::Duration;

use crate::commands::types::ServiceStatus;
use crate::services::{catfish_paths, endpoints, process};

const TCP_TIMEOUT: Duration = Duration::from_millis(800);
const HTTP_TIMEOUT: Duration = Duration::from_secs(2);

#[tauri::command]
pub async fn chrome_launch() -> Result<(), String> {
    if let Some(pid_file) = catfish_paths::chrome_pid_file() {
        if let Some(pid) = process::read_pid_file_alive(&pid_file) {
            return Err(format!("Chrome 已在跑（PID {pid}）"));
        }
    }

    let chrome_bin = catfish_paths::find_chrome().ok_or_else(|| {
        "找不到 Chrome — 请先安装 Google Chrome / Chromium".to_string()
    })?;
    let user_data_dir = catfish_paths::chrome_user_data_dir()
        .ok_or_else(|| "找不到 Chrome 隔离 profile 路径".to_string())?;
    let log_path = catfish_paths::chrome_log_path()
        .ok_or_else(|| "找不到日志路径".to_string())?;
    let pid_file = catfish_paths::chrome_pid_file()
        .ok_or_else(|| "找不到 PID 文件路径".to_string())?;
    let working_dir = user_data_dir
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| std::env::temp_dir());

    let chrome_port = endpoints::endpoints().chrome_port;
    let cfg = process::SpawnConfig {
        program: chrome_bin,
        args: vec![
            format!("--remote-debugging-port={chrome_port}"),
            format!("--user-data-dir={}", user_data_dir.display()),
            "--no-first-run".into(),
            "--no-default-browser-check".into(),
            "--disable-features=DialMediaRouteProvider".into(), // 减少后台噪音
            "about:blank".into(), // 默认开空白页
        ],
        log_path,
        working_dir,
        env: vec![],
    };

    let handle = process::spawn_detached(cfg).map_err(|e| format!("启动失败: {e}"))?;

    std::fs::write(&pid_file, handle.pid.to_string())
        .map_err(|e| format!("写 PID 文件失败: {e}"))?;

    Ok(())
}

#[tauri::command]
pub async fn chrome_kill() -> Result<(), String> {
    let pid_file = catfish_paths::chrome_pid_file()
        .ok_or_else(|| "找不到 PID 文件路径".to_string())?;

    if !pid_file.exists() {
        return Err("Catfish Chrome 没有由 Companion 启动过（无 PID 文件）".into());
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
pub async fn chrome_status() -> Result<ServiceStatus, String> {
    let ep = endpoints::endpoints();
    let host = ep.chrome_host.clone();
    let port = ep.chrome_port;
    let tcp_alive = tokio::time::timeout(
        TCP_TIMEOUT,
        tokio::net::TcpStream::connect((host.as_str(), port)),
    )
    .await
    .map(|r| r.is_ok())
    .unwrap_or(false);

    if !tcp_alive {
        return Ok(ServiceStatus::down(
            Some(port),
            "未启动 — 点 \"启动\" 拉起 Catfish Chrome",
        ));
    }

    // 9222 通了再 GET /json/version 确认是 DevTools 协议
    let chrome_base = ep.chrome_base();
    let healthy = match reqwest::Client::builder()
        .timeout(HTTP_TIMEOUT)
        .no_proxy()
        .build()
    {
        Ok(c) => c
            .get(format!("{chrome_base}/json/version"))
            .send()
            .await
            .map(|r| r.status().is_success())
            .unwrap_or(false),
        Err(_) => false,
    };

    let pid = catfish_paths::chrome_pid_file()
        .and_then(|p| process::read_pid_file_alive(&p));

    Ok(ServiceStatus {
        running: true,
        healthy,
        pid,
        port: Some(port),
        message: Some(if healthy {
            format!("DevTools 已就绪 :{port}")
        } else {
            "TCP 通但非 Chrome DevTools — 端口被别的进程占了？".into()
        }),
    })
}
