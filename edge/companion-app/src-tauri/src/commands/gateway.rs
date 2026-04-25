//! llm-gateway 的启停 + 状态查询。
//!
//! 启动：`<gateway_python> -m catfish_gateway.app`，PORT=8999，工作目录在 gateway dir
//! 日志：stdout+stderr 合流到 `<companion-state>/gateway.log`
//! PID：写到 `<companion-state>/gateway.pid`，stop 读这个 kill

use std::time::Duration;

use crate::commands::types::ServiceStatus;
use crate::services::{catfish_paths, process};

const GATEWAY_PORT: u16 = 8999;
const GATEWAY_BASE: &str = "http://127.0.0.1:8999";
const TCP_TIMEOUT: Duration = Duration::from_millis(800);
const HTTP_TIMEOUT: Duration = Duration::from_secs(2);

#[tauri::command]
pub async fn gateway_start() -> Result<(), String> {
    // 1. 已经在跑就拒绝重启（防员工双击启动按钮起两个）
    if let Some(pid_file) = catfish_paths::gateway_pid_file() {
        if let Some(pid) = process::read_pid_file_alive(&pid_file) {
            return Err(format!("Gateway 已在跑（PID {pid}）"));
        }
    }

    // 2. 解析路径
    let dir = catfish_paths::gateway_dir().ok_or_else(|| {
        "找不到 gateway 目录 — 设 CATFISH_GATEWAY_DIR 或确保 \
         ~/person_task/catfish/central/llm-gateway 存在"
            .to_string()
    })?;
    let python = catfish_paths::gateway_python()
        .ok_or_else(|| "找不到 Python 解释器".to_string())?;
    let log_path = catfish_paths::gateway_log_path()
        .ok_or_else(|| "找不到日志路径".to_string())?;
    let pid_file = catfish_paths::gateway_pid_file()
        .ok_or_else(|| "找不到 PID 文件路径".to_string())?;

    // 3. spawn
    let cfg = process::SpawnConfig {
        program: python,
        args: vec!["-m".into(), "catfish_gateway.app".into()],
        log_path,
        working_dir: dir,
        env: vec![
            ("PORT".into(), GATEWAY_PORT.to_string()),
            // gateway 内部仍可走代理调外网模型，所以 HTTPS_PROXY 透传
            // gateway 自己的 network.py 会处理代理可达性
        ],
    };

    let handle = process::spawn_detached(cfg).map_err(|e| format!("启动失败: {e}"))?;

    // 4. 写 PID 文件
    std::fs::write(&pid_file, handle.pid.to_string())
        .map_err(|e| format!("写 PID 文件失败: {e}"))?;

    Ok(())
}

#[tauri::command]
pub async fn gateway_stop() -> Result<(), String> {
    let pid_file = catfish_paths::gateway_pid_file()
        .ok_or_else(|| "找不到 PID 文件路径".to_string())?;

    if !pid_file.exists() {
        return Err("Gateway 没有由 Companion 启动过（无 PID 文件）".into());
    }

    let pid: u32 = std::fs::read_to_string(&pid_file)
        .map_err(|e| format!("读 PID 文件失败: {e}"))?
        .trim()
        .parse()
        .map_err(|e| format!("PID 文件格式错误: {e}"))?;

    process::kill(pid).map_err(|e| format!("kill 失败: {e}"))?;

    // 删 PID 文件 —— 哪怕进程没干净，文件先删掉避免 stop 后状态卡住
    let _ = std::fs::remove_file(&pid_file);
    Ok(())
}

#[tauri::command]
pub async fn gateway_status() -> Result<ServiceStatus, String> {
    // 1. 先 TCP 探活
    let tcp_alive = probe_tcp("127.0.0.1", GATEWAY_PORT).await;
    if !tcp_alive {
        return Ok(ServiceStatus::down(
            Some(GATEWAY_PORT),
            "未启动 — 点 \"启动\" 按钮拉起",
        ));
    }

    // 2. TCP 通 → 探 /healthz
    let healthy = probe_healthz().await;

    // 3. 读 PID 文件确认是不是 Companion 起的
    let pid = catfish_paths::gateway_pid_file()
        .and_then(|p| process::read_pid_file_alive(&p));

    Ok(ServiceStatus {
        running: true,
        healthy,
        pid,
        port: Some(GATEWAY_PORT),
        message: Some(if healthy {
            format!("已连接 :{GATEWAY_PORT}")
        } else {
            "TCP 通但 /healthz 失败 — 检查 .env / dev token".into()
        }),
    })
}

async fn probe_tcp(host: &str, port: u16) -> bool {
    tokio::time::timeout(
        TCP_TIMEOUT,
        tokio::net::TcpStream::connect((host, port)),
    )
    .await
    .map(|r| r.is_ok())
    .unwrap_or(false)
}

async fn probe_healthz() -> bool {
    let client = match reqwest::Client::builder()
        .timeout(HTTP_TIMEOUT)
        .no_proxy()
        .build()
    {
        Ok(c) => c,
        Err(_) => return false,
    };
    client
        .get(format!("{GATEWAY_BASE}/healthz"))
        .send()
        .await
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}
