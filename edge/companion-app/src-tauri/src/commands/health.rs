//! 直接调 gateway HTTP 的命令（不是探进程，是探"业务面活着"）。
//!
//! 给前端的仪表盘 / 控制台用 —— "进程在跑" ≠ "服务能用"，
//! 比如 gateway 进程活着但 .env 没加载、dev token 错配，/healthz 仍可能 200。

use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;

const GATEWAY_BASE: &str = "http://127.0.0.1:8999";
const HTTP_TIMEOUT: Duration = Duration::from_secs(2);

/// 既要 Deserialize（reqwest 解 gateway 返回）也要 Serialize（送给前端）
#[derive(Debug, Serialize, Deserialize)]
pub struct HealthzResp {
    pub status: String,
    pub service: String,
}

/// GET /healthz —— 标准健康检查
#[tauri::command]
pub async fn healthz() -> Result<HealthzResp, String> {
    let client = build_client()?;
    let resp = client
        .get(format!("{GATEWAY_BASE}/healthz"))
        .send()
        .await
        .map_err(|e| format!("连接失败：{e}"))?;

    if !resp.status().is_success() {
        return Err(format!("HTTP {}", resp.status()));
    }

    resp.json::<HealthzResp>()
        .await
        .map_err(|e| format!("解析失败：{e}"))
}

/// GET /v1/catalog —— 公开的模型清单（匿名也能调）
#[tauri::command]
pub async fn catalog() -> Result<Value, String> {
    let client = build_client()?;
    let resp = client
        .get(format!("{GATEWAY_BASE}/v1/catalog"))
        .send()
        .await
        .map_err(|e| format!("连接失败：{e}"))?;

    if !resp.status().is_success() {
        return Err(format!("HTTP {}", resp.status()));
    }

    resp.json::<Value>()
        .await
        .map_err(|e| format!("解析失败：{e}"))
}

fn build_client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(HTTP_TIMEOUT)
        // 关键：localhost 永远不走代理，避免员工设了 HTTPS_PROXY 把 127.0.0.1 也劫了
        .no_proxy()
        .build()
        .map_err(|e| format!("HTTP client 构造失败：{e}"))
}
