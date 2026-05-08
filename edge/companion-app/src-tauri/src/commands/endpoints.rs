//! BL-WIN9 / DEPLOY1 (5/8): 暴露当前 endpoints 给前端.
//!
//! 前端默认通过 import.meta.env.VITE_CATFISH_GATEWAY_URL 拿网关地址, 但 vite
//! 是 build-time 静态替换 — 一旦 .app/.exe 打包好, 这个值就锁死了, 客户改不了.
//!
//! 解法: 启动时前端 invoke('get_runtime_endpoints'), Rust 这边读 yaml +
//! env (跟 services/endpoints.rs 同一份逻辑) 返动态 URL, 前端把 config
//! 里的 gatewayUrl 替换掉. 这样客户改 ~/.catfish/companion.yaml 重启
//! Companion 就能切到任意网关地址, 不需要重新打包.

use serde::Serialize;

use crate::services::endpoints;

#[derive(Debug, Clone, Serialize)]
pub struct RuntimeEndpoints {
    /// 完整 URL (e.g. "http://10.10.40.50:8999"). 前端 fetch() 直接拼 path 用.
    pub gateway_url: String,
    /// Chrome CDP base URL.
    pub chrome_debug_url: String,
}

/// 给前端拉当前 endpoints. 优先级 yaml > env > default (跟 Rust 端 services::endpoints 一致).
#[tauri::command]
pub fn get_runtime_endpoints() -> Result<RuntimeEndpoints, String> {
    let ep = endpoints::endpoints();
    Ok(RuntimeEndpoints {
        gateway_url: ep.gateway_base(),
        chrome_debug_url: ep.chrome_base(),
    })
}
