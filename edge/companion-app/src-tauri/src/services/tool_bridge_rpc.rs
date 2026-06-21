//! P3.5.45 — tool-bridge JSON-RPC over Unix socket (本机).
//!
//! 抽自 commands/tool_bridge.rs:247-325 (老 private fn call_rpc), 让 commands
//! 跨 module 复用 (e.g. commands/recmode.rs 接录屏 11 个 endpoint).
//!
//! # 为啥抽
//!
//! 鸿波 6/20 catch '录屏走 HTTP gateway 跟 chat 不同 path 造成混乱'. 治本:
//! 录屏 endpoint 砍 HTTP 改 Tauri command 直调 tool-bridge sock. 多个 commands
//! 用同一个 helper, 自然抽公共.
//!
//! tool_bridge.rs 老 call_rpc 留着 (现有 callers tool_bridge_list_tools /
//! tool_bridge_call_tool / tool_bridge_chat_approval 都依赖, 不动避免回归).
//! 后续 cleanup 让它 thin-wrap 调本 module.
//!
//! # 协议
//!
//! NDJSON over Unix socket (Windows: TCP, endpoint file 存端口号).
//! JSON-RPC 2.0 request/response. 一次 RPC = 一次连接 (短连, 简单).

use serde_json::{json, Value};
use std::path::PathBuf;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

#[cfg(not(unix))]
use tokio::net::TcpStream;
#[cfg(unix)]
use tokio::net::UnixStream;

use crate::services::catfish_paths;

/// 单次 RPC 默认超时. recmode/analyze 可能 LLM 调用较长, caller 自己加大.
pub const DEFAULT_RPC_TIMEOUT_SECS: u64 = 60;

/// 连 tool-bridge sock 的超时. 连不上 = sock 文件不在 / tool-bridge 没起.
const CONNECT_TIMEOUT_SECS: u64 = 2;

fn sock_path() -> Result<PathBuf, String> {
    catfish_paths::tool_bridge_socket()
        .ok_or_else(|| "找不到 tool-bridge endpoint 路径".to_string())
}

/// 调 tool-bridge JSON-RPC 一次. method 跟 params 透传, 不做 schema 验.
///
/// 错误:
///   - tool-bridge 没起 → "tool-bridge endpoint 不存在 — 还没启动？"
///   - 连接超时 / 失败
///   - 写入失败
///   - RPC 超时
///   - 响应非合法 JSON
///   - JSON-RPC error 字段 → "tool-bridge 错误: <message>"
pub async fn call(method: &str, params: Value) -> Result<Value, String> {
    call_with_timeout(method, params, Duration::from_secs(DEFAULT_RPC_TIMEOUT_SECS)).await
}

/// 跟 `call` 一样但可指定单次超时 (e.g. analyze 走 LLM 调用要 5min+).
pub async fn call_with_timeout(
    method: &str,
    params: Value,
    rpc_timeout: Duration,
) -> Result<Value, String> {
    let endpoint_path = sock_path()?;
    if !endpoint_path.exists() {
        return Err("tool-bridge endpoint 不存在 — 还没启动？".into());
    }

    #[cfg(unix)]
    let stream = tokio::time::timeout(
        Duration::from_secs(CONNECT_TIMEOUT_SECS),
        UnixStream::connect(&endpoint_path),
    )
    .await
    .map_err(|_| "连接 tool-bridge 超时".to_string())?
    .map_err(|e| format!("连接 tool-bridge 失败: {e}"))?;

    #[cfg(not(unix))]
    let stream = {
        // Windows: endpoint 文件存的是端口号
        let port_str = tokio::fs::read_to_string(&endpoint_path)
            .await
            .map_err(|e| format!("读 endpoint 文件失败: {e}"))?;
        let port: u16 = port_str
            .trim()
            .parse()
            .map_err(|e| format!("endpoint 文件内容不是端口号 ({port_str:?}): {e}"))?;
        let addr = format!("127.0.0.1:{port}");
        tokio::time::timeout(
            Duration::from_secs(CONNECT_TIMEOUT_SECS),
            TcpStream::connect(&addr),
        )
        .await
        .map_err(|_| format!("连接 tool-bridge {addr} 超时"))?
        .map_err(|e| format!("连接失败 ({addr}): {e}"))?
    };

    let (read_half, mut write_half) = stream.into_split();
    let mut reader = BufReader::new(read_half);

    let req = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    });
    let req_line = format!("{}\n", req);

    write_half
        .write_all(req_line.as_bytes())
        .await
        .map_err(|e| format!("写请求失败: {e}"))?;

    let mut line = String::new();
    let n = tokio::time::timeout(rpc_timeout, reader.read_line(&mut line))
        .await
        .map_err(|_| format!("RPC {method} 超时 (>{}s)", rpc_timeout.as_secs()))?
        .map_err(|e| format!("读响应失败: {e}"))?;

    if n == 0 {
        return Err("tool-bridge 关闭了连接".into());
    }

    let resp: Value = serde_json::from_str(line.trim())
        .map_err(|e| format!("响应非合法 JSON: {e}\n原文: {line}"))?;

    if let Some(err) = resp.get("error") {
        let msg = err
            .get("message")
            .and_then(|m| m.as_str())
            .unwrap_or("unknown");
        // 保留 tool-bridge 端 error code 给 caller decide (e.g. recmode 404 / 409)
        let code = err.get("code").and_then(|c| c.as_i64()).unwrap_or(-32000);
        return Err(format!("tool-bridge 错误 [{code}]: {msg}"));
    }

    Ok(resp.get("result").cloned().unwrap_or(Value::Null))
}
