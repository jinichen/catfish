//! 直接调 gateway HTTP 的命令（不是探进程，是探"业务面活着"）。
//!
//! 给前端的仪表盘 / 控制台用 —— "进程在跑" ≠ "服务能用"，
//! 比如 gateway 进程活着但 .env 没加载、dev token 错配，/healthz 仍可能 200。

use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::services::{endpoints, hermes_api_config};

const HTTP_TIMEOUT: Duration = Duration::from_secs(2);

/// BL-AUTH-DECOUPLE-A5 Phase 2 (5/19): 返 Companion 真"后端 API 入口" base URL.
/// hermes_api.enabled + has_key 时 = hermes 8642 (hermes Phase 1 proxy 转 gateway),
/// 否则 = gateway 8999 (灰度回退). 跟前端 config.backendUrl 同语义.
fn backend_base() -> String {
    let h = hermes_api_config::hermes_api_config();
    if h.enabled && h.key.is_some() {
        return h.url.trim_end_matches('/').to_string();
    }
    endpoints::endpoints().gateway_base()
}

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
    let base = backend_base();
    let resp = client
        .get(format!("{base}/healthz"))
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
    let base = backend_base();
    let resp = client
        .get(format!("{base}/v1/catalog"))
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
