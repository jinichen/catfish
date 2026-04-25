//! catfish-tool-bridge 子进程管理 + JSON-RPC 客户端。
//!
//! 启动: spawn `<hermes-venv-python> -m catfish_tool_bridge --socket <path>`
//! 通信: Unix domain socket newline-delimited JSON-RPC 2.0
//! 复用 hermes-agent 的 venv，因为 tool 依赖（markitdown / browser /
//! mcp / ...）都在那个 venv 里装好了。

use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixStream;

use crate::commands::types::ServiceStatus;
use crate::services::{catfish_paths, process};

const RPC_TIMEOUT: Duration = Duration::from_secs(30);

/// 跟 Python tool-bridge 协议对齐 —— 字段都是 snake_case,
/// 不用 rename_all="camelCase" 否则 input_schema 会被改成 inputSchema 跟 Python 错位。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolInfo {
    pub name: String,
    pub description: String,
    pub input_schema: Value,
    pub emoji: String,
    pub toolset: String,
    pub available: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallResult {
    pub ok: bool,
    pub tool: String,
    pub result: Option<Value>,
    pub error: Option<String>,
}

// ============================================================
// 生命周期 (start / stop / status)
// ============================================================

#[tauri::command]
pub async fn tool_bridge_start() -> Result<(), String> {
    if let Some(pid_file) = catfish_paths::tool_bridge_pid_file() {
        if let Some(pid) = process::read_pid_file_alive(&pid_file) {
            return Err(format!("tool-bridge 已在跑（PID {pid}）"));
        }
    }

    let dir = catfish_paths::tool_bridge_dir().ok_or_else(|| {
        "找不到 tool-bridge 目录 — 设 CATFISH_TOOL_BRIDGE_DIR 或确保 \
         ~/person_task/catfish/edge/tool-bridge 存在"
            .to_string()
    })?;
    let python = catfish_paths::tool_bridge_python()
        .ok_or_else(|| "找不到 hermes venv 里的 Python".to_string())?;
    let log_path = catfish_paths::tool_bridge_log_path()
        .ok_or_else(|| "找不到日志路径".to_string())?;
    let pid_file = catfish_paths::tool_bridge_pid_file()
        .ok_or_else(|| "找不到 PID 文件路径".to_string())?;
    let socket_path = catfish_paths::tool_bridge_socket()
        .ok_or_else(|| "找不到 socket 路径".to_string())?;

    // 让 Python 能 import catfish_tool_bridge —— 通过 PYTHONPATH 指向 src/
    let pythonpath = dir.join("src").to_string_lossy().to_string();

    let cfg = process::SpawnConfig {
        program: python,
        args: vec![
            "-m".into(),
            "catfish_tool_bridge".into(),
            "--socket".into(),
            socket_path.to_string_lossy().to_string(),
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
pub async fn tool_bridge_stop() -> Result<(), String> {
    let pid_file = catfish_paths::tool_bridge_pid_file()
        .ok_or_else(|| "找不到 PID 文件路径".to_string())?;
    if !pid_file.exists() {
        return Err("tool-bridge 没由 Companion 启动过（无 PID 文件）".into());
    }
    let pid: u32 = std::fs::read_to_string(&pid_file)
        .map_err(|e| format!("读 PID 文件失败: {e}"))?
        .trim()
        .parse()
        .map_err(|e| format!("PID 文件格式错误: {e}"))?;
    process::kill(pid).map_err(|e| format!("kill 失败: {e}"))?;
    let _ = std::fs::remove_file(&pid_file);
    // 清掉 socket 文件残留
    if let Some(sock) = catfish_paths::tool_bridge_socket() {
        let _ = std::fs::remove_file(sock);
    }
    Ok(())
}

#[tauri::command]
pub async fn tool_bridge_status() -> Result<ServiceStatus, String> {
    let pid = catfish_paths::tool_bridge_pid_file()
        .and_then(|p| process::read_pid_file_alive(&p));

    if pid.is_none() {
        return Ok(ServiceStatus::down(
            None,
            "未启动 — 点 \"启动\" 拉起（让 Companion 调 hermes 35 tools）",
        ));
    }

    // 进程在 → 试 RPC 探活
    let healthy = match call_rpc("health", json!(null)).await {
        Ok(v) => v
            .get("ok")
            .and_then(|b| b.as_bool())
            .unwrap_or(false),
        Err(_) => false,
    };

    Ok(ServiceStatus {
        running: true,
        healthy,
        pid,
        port: None, // unix socket 没端口
        message: Some(if healthy {
            "已就绪（unix socket）".into()
        } else {
            "进程在但 RPC 未响应 — 可能还在 import tools（首次启动 ~5s）".into()
        }),
    })
}

// ============================================================
// JSON-RPC 客户端
// ============================================================

#[tauri::command]
pub async fn tool_bridge_list_tools() -> Result<Vec<ToolInfo>, String> {
    let val = call_rpc("tools/list", json!(null)).await?;
    serde_json::from_value::<Vec<ToolInfo>>(val)
        .map_err(|e| format!("解析 tools/list 失败: {e}"))
}

#[tauri::command]
pub async fn tool_bridge_call_tool(
    name: String,
    args: Value,
) -> Result<ToolCallResult, String> {
    let params = json!({ "name": name, "args": args });
    let val = call_rpc("tools/dispatch", params).await?;
    serde_json::from_value::<ToolCallResult>(val)
        .map_err(|e| format!("解析 tools/dispatch 失败: {e}"))
}

// ============================================================
// internal: unix socket NDJSON RPC
// ============================================================

async fn call_rpc(method: &str, params: Value) -> Result<Value, String> {
    let socket_path = catfish_paths::tool_bridge_socket()
        .ok_or_else(|| "找不到 socket 路径".to_string())?;
    if !socket_path.exists() {
        return Err("tool-bridge socket 不存在 — 还没启动？".into());
    }

    // 连接 + 读写都要 timeout，避免 RPC 挂死把 Companion 卡住
    let stream = tokio::time::timeout(
        Duration::from_secs(2),
        UnixStream::connect(&socket_path),
    )
    .await
    .map_err(|_| "连接 tool-bridge 超时".to_string())?
    .map_err(|e| format!("连接失败: {e}"))?;

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
    let n = tokio::time::timeout(RPC_TIMEOUT, reader.read_line(&mut line))
        .await
        .map_err(|_| format!("RPC {method} 超时（>{}s）", RPC_TIMEOUT.as_secs()))?
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
        return Err(format!("tool-bridge 错误: {msg}"));
    }

    Ok(resp.get("result").cloned().unwrap_or(Value::Null))
}
