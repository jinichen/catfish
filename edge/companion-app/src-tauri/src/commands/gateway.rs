//! llm-gateway 状态查询.
//!
//! P39 (5/22 gateway 解耦收尾): start/stop/get_dev_token 3 个 Rust command 删了.
//! 生产员工机 gateway 由 launchctl/客户 IT 管, Companion 不 spawn 也不管 dev_token.
//! 保留 gateway_status 做纯 TCP + /healthz 探活, 给 Console 卡片显状态点.
//!
//! catfish_paths::{gateway_dir, gateway_python, gateway_log_path, gateway_pid_file}
//! 4 个 helper 保留 (被 local_search_python / logs.rs / gateway_status 用).

use std::time::Duration;

use crate::commands::types::ServiceStatus;
use crate::services::{catfish_paths, endpoints, process};

const TCP_TIMEOUT: Duration = Duration::from_millis(800);
const HTTP_TIMEOUT: Duration = Duration::from_secs(2);

#[tauri::command]
pub async fn gateway_status() -> Result<ServiceStatus, String> {
    let ep = endpoints::endpoints();

    // 1. 先 TCP 探活
    let tcp_alive = probe_tcp(&ep.gateway_host, ep.gateway_port).await;
    if !tcp_alive {
        return Ok(ServiceStatus::down(
            Some(ep.gateway_port),
            "未启动 — 点 \"启动\" 按钮拉起",
        ));
    }

    // 2. TCP 通 → 探 /healthz
    let healthy = probe_healthz().await;

    // 3. 读 PID 文件确认是不是 Companion 起的
    let pid = catfish_paths::gateway_pid_file()
        .and_then(|p| process::read_pid_file_alive_strict(&p, "catfish_gateway"));

    Ok(ServiceStatus {
        running: true,
        healthy,
        pid,
        port: Some(ep.gateway_port),
        message: Some(if healthy {
            format!("已连接 :{}", ep.gateway_port)
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
    // P3.5.80 (7/28): 中央端可能是自签 HTTPS, 挂上 ~/.catfish/server-ca.pem 的信任.
    let client = match crate::util::http_client::trust_central(
        reqwest::Client::builder().timeout(HTTP_TIMEOUT).no_proxy(),
    )
    .build()
    {
        Ok(c) => c,
        Err(_) => return false,
    };
    let base = endpoints::endpoints().gateway_base();
    client
        .get(format!("{base}/healthz"))
        .send()
        .await
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}
