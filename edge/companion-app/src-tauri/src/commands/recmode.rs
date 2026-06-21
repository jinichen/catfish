//! P3.5.45 (鸿波 6/20 拍 'A: 砍录屏 HTTP path') — RecMode Tauri commands
//! 直调 tool-bridge unix socket, bypass gateway HTTP 全套.
//!
//! # 为啥
//!
//! 老链路 (4 跳, 多 2 个 HTTP 中转):
//!   Companion → HTTP → hermes 8642 P7 catch-all → HTTP → gateway 8999
//!                                                      → unix sock → tool-bridge
//!
//! gateway /api/learn/* 11 个 endpoint 全是 thin proxy → tool-bridge JSON-RPC
//! (`gateway/app.py:619-1100` 每个 endpoint 一行 `_tb_rpc.call("recmode/...")`).
//! 走 HTTP 是历史包袱 + cascade fail 风险 (P7 plugin SERVICE_TOKEN 30 天过期就挂).
//!
//! 新链路 (1 跳, 0 token 验签):
//!   Companion (Tauri) → unix sock → tool-bridge
//!
//! # 为啥跟 chat 不同
//!
//! chat 必经 hermes (LLM 调用走 LiteLLM, hermes 直接打 deepseek/qwen 公网 API).
//! 录屏纯本机操作 (CDP keyframes / screenshot / events jsonl / fs IO), tool-bridge
//! 已在 Companion 同台机器, 没必要绕 HTTP. 跟 chat 彻底解耦, 0 共享 token.

use serde_json::{json, Value};
use std::time::Duration;

use crate::services::{oauth, tool_bridge_rpc};

/// 走 LLM 综合的 analyze 给 10 分钟超时 (vision model 多张截图慢).
const ANALYZE_TIMEOUT_SECS: u64 = 600;

/// 其他 endpoint 给 60s.
const DEFAULT_TIMEOUT_SECS: u64 = 60;

/// save_skill 可能 copytree 整个录屏目录, 1 分钟够.
const SAVE_SKILL_TIMEOUT_SECS: u64 = 60;

/// 通用 recmode RPC 入口 — 前端 invoke("recmode_rpc", {method, params}).
///
/// method 必须 "recmode/" 前缀, 防止前端绕过白名单调任意 tool-bridge 方法.
///
/// 自动注入 catfish_home (gateway 老端原本注的). 前端不用自己传.
#[tauri::command]
pub async fn recmode_rpc(
    method: String,
    params: Option<Value>,
) -> Result<Value, String> {
    if !method.starts_with("recmode/") {
        return Err(format!(
            "method 必须 'recmode/' 前缀, 收到 {method:?}. \
             非 recmode 路径请用对应 Tauri command (e.g. tool_bridge_call_tool)."
        ));
    }

    // 默认空 dict, 给注入 catfish_home / auth_token 用
    let mut params = params.unwrap_or_else(|| json!({}));

    // 自动注入 catfish_home env (gateway 老端 app.py 每个 endpoint 都做的)
    if let Value::Object(ref mut map) = params {
        if !map.contains_key("catfish_home") {
            let catfish_home = std::env::var("CATFISH_HOME").unwrap_or_default();
            map.insert("catfish_home".to_string(), json!(catfish_home));
        }

        // P3.5.45 follow-up (鸿波 6/20 实跑出 'aggregate_session 没 auth token'):
        // 老 gateway HTTP path 自动把 client Authorization header 抽出来塞
        // auth_token 字段转发给 tool-bridge (server.py _handle_recmode_analyze
        // 接收 auth_token); 现在 Companion 直调 sock 跳过 HTTP 那段 → 需要 Rust
        // 端自己注入. tool-bridge aggregator.call_llm 用 auth_token 调 gateway
        // loopback /v1/chat/completions 跑 vision LLM.
        //
        // 走 oauth::ensure_fresh_access_token 拿真 token (P3.5.42.10 silent refresh
        // 路径). 拿不到不强塞 (caller 可能用不到 LLM 的 endpoint, e.g.
        // start_recording / stop_recording 纯本机, 不需要 token).
        if !map.contains_key("auth_token") {
            if let Some(tok) = oauth::ensure_fresh_access_token().await {
                map.insert("auth_token".to_string(), json!(tok));
            }
        }
    }

    let timeout = match method.as_str() {
        "recmode/analyze" => Duration::from_secs(ANALYZE_TIMEOUT_SECS),
        "recmode/save_skill" => Duration::from_secs(SAVE_SKILL_TIMEOUT_SECS),
        _ => Duration::from_secs(DEFAULT_TIMEOUT_SECS),
    };

    tool_bridge_rpc::call_with_timeout(&method, params, timeout).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn rejects_non_recmode_method() {
        // 防止前端调任意 tool-bridge 方法绕过白名单
        let err = recmode_rpc("tools/list".to_string(), None).await.unwrap_err();
        assert!(err.contains("recmode/"), "err: {err}");

        let err = recmode_rpc("memory/query".to_string(), None).await.unwrap_err();
        assert!(err.contains("recmode/"), "err: {err}");
    }

    #[tokio::test]
    async fn rejects_empty_method() {
        let err = recmode_rpc("".to_string(), None).await.unwrap_err();
        assert!(err.contains("recmode/"), "err: {err}");
    }

    // 注: 真 RPC 测要起 tool-bridge mock sock, 留 integration test ship (Companion build 时跑).
    // 这里只测白名单守卫. recmode/* 真路径都会进 call_with_timeout, 那是 sock 测的范畴.
}
