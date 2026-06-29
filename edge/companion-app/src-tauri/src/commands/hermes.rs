//! P3.5.125 (6/26 鸿波 catch "catfish 对 hermes 进程挂没监控"): hermes 进程
//! 健康监控 + hang 时自动重启.
//!
//! hermes 默认 launchd KeepAlive 在 *crash 退出* 时拉, 但 hang (GIL deadlock /
//! IO block) launchd 不知道 — 进程 alive 但不响应. Companion 主动:
//!
//!   1. `hermes_status`: TCP 8642 探活 + GET /healthz 探健康
//!   2. `hermes_kill`: useServiceStatus 连续 3 次 unhealthy 时调 — kill -9 触发
//!      launchd 自动重启
//!
//! 不在 catfish 端 spawn hermes (跟 gateway 不同 — gateway 是 Companion 起的,
//! hermes 是员工 brew install + launchd 起的). kill 后等 launchd 拉.

use std::time::Duration;

use crate::commands::types::ServiceStatus;
use crate::services::hermes_api_config;

const TCP_TIMEOUT: Duration = Duration::from_millis(800);
const HTTP_TIMEOUT: Duration = Duration::from_secs(2);

/// 解析 hermes URL 真 host + port (从 hermes_api_config 真 url 提取).
/// 默认 http://localhost:8642 → ("localhost", 8642).
fn parse_hermes_endpoint() -> (String, u16) {
    let cfg = hermes_api_config::hermes_api_config();
    let url = &cfg.url;
    // 真简单 parse: http://<host>:<port> 或 http://<host>:<port>/...
    let stripped = url
        .trim_start_matches("http://")
        .trim_start_matches("https://");
    let path_idx = stripped.find('/').unwrap_or(stripped.len());
    let authority = &stripped[..path_idx];
    if let Some(colon) = authority.find(':') {
        let host = authority[..colon].to_string();
        let port: u16 = authority[colon + 1..].parse().unwrap_or(8642);
        (host, port)
    } else {
        (authority.to_string(), 8642)
    }
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

async fn probe_healthz(base: &str) -> bool {
    let client = match reqwest::Client::builder()
        .timeout(HTTP_TIMEOUT)
        .no_proxy()
        .build()
    {
        Ok(c) => c,
        Err(_) => return false,
    };
    let url = format!("{base}/healthz");
    client
        .get(&url)
        .send()
        .await
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

#[tauri::command]
pub async fn hermes_status() -> Result<ServiceStatus, String> {
    let (host, port) = parse_hermes_endpoint();

    // 1. TCP 探活
    let tcp_alive = probe_tcp(&host, port).await;
    if !tcp_alive {
        return Ok(ServiceStatus::down(
            Some(port),
            "hermes 未启动 — 检查 brew services list hermes (launchd 应自动拉)",
        ));
    }

    // 2. TCP 通 → 探 /healthz (hang detection — TCP 通但 /healthz 超时 = GIL/IO block)
    let cfg = hermes_api_config::hermes_api_config();
    let healthy = probe_healthz(&cfg.url).await;

    Ok(ServiceStatus {
        running: true,
        healthy,
        // hermes PID 真在 launchd 控制, Companion 不持有 PID file. 0 = 未知.
        pid: None,
        port: Some(port),
        message: Some(if healthy {
            format!("hermes 已连接 :{port}")
        } else {
            "⚠ TCP 通但 /healthz 超时 — 进程可能 hang (GIL/IO block), 连续 3 次将自动重启".into()
        }),
    })
}

/// hang 自动恢复: kill -9 hermes 进程, 触发 launchd KeepAlive 拉新进程.
/// : 由 useServiceStatus 真连续 3 次 unhealthy 时调**.
#[tauri::command]
pub async fn hermes_kill() -> Result<(), String> {
    // : 真:** 真找 hermes 进程 — : ps -ef | grep hermes : 真:** : :
    // : : : : : : : : : : : : : : : : : : : : : : : :
    let output = std::process::Command::new("pgrep")
        .args(["-f", "hermes_cli.gateway run"])
        .output()
        .map_err(|e| format!("pgrep 失败: {e}"))?;

    if !output.status.success() || output.stdout.is_empty() {
        return Err("pgrep 找不到 hermes 进程 (它可能已经死了, 等 launchd 拉)".to_string());
    }

    let pids: Vec<i32> = String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|l| l.trim().parse().ok())
        .collect();

    if pids.is_empty() {
        return Err("pgrep 输出无有效 PID".to_string());
    }

    let mut killed = 0;
    let mut errs = Vec::new();
    for pid in &pids {
        let status = std::process::Command::new("kill")
            .args(["-9", &pid.to_string()])
            .status();
        match status {
            Ok(s) if s.success() => killed += 1,
            Ok(s) => errs.push(format!("kill -9 {pid} exit={s}")),
            Err(e) => errs.push(format!("kill -9 {pid} err={e}")),
        }
    }

    log::warn!(
        "P3.5.125 hermes hang 自动重启: killed {} hermes PIDs ({:?}). \
         launchd 应在 2-5s 后自动拉新进程. errs={:?}",
        killed,
        pids,
        errs
    );

    if killed == 0 {
        return Err(format!("0 PID killed: {errs:?}"));
    }
    Ok(())
}
