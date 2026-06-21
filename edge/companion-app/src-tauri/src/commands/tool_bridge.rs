//! catfish-tool-bridge 子进程管理 + JSON-RPC 客户端。
//!
//! 启动: spawn `<hermes-venv-python> -m catfish_tool_bridge --socket <path>`
//! 通信: Unix domain socket newline-delimited JSON-RPC 2.0
//! 复用 hermes-agent 的 venv，因为 tool 依赖（markitdown / browser /
//! mcp / ...）都在那个 venv 里装好了。

use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::commands::types::ServiceStatus;
use crate::services::{catfish_paths, process, tool_bridge_rpc};

/// 老 callers (list_tools / call_tool / chat_approval) 沿用 30s timeout 保持行为.
/// P3.5.45: call_rpc 私有 fn 砍, 改 thin-wrap 调 services::tool_bridge_rpc 公共
/// helper (跟 commands/recmode.rs 复用同一条 unix sock RPC 实现).
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
        if let Some(pid) = process::read_pid_file_alive_strict(&pid_file, "catfish_tool_bridge") {
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

    // BL-D3 Phase 3.1 (5/9): 注入 MCP registry / 员工身份 env, 让 tool-bridge
    // 启动时能调 gateway /v1/mcp/subscribed 拉员工真订阅, 自动 spawn jira/gitlab
    // 等 mcp servers. 没登录 (没 token) 时 tool-bridge fallback 走硬编码
    // autostart (CATFISH_MCP_AUTOSTART='time').
    //
    // P3.4.1 (6/13 hb): 砍 CATFISH_SECRET_BROKER_URL — secret-broker 中央服务
    // 已删, mcp OAuth token 改 Companion 本机存
    // (~/.catfish/mcp/oauth-tokens/<ref>). tool-bridge 跑 mcp 时直接读本机文件,
    // 不再走 secret-broker.
    let ep = crate::services::endpoints::endpoints();
    let gateway_url = std::env::var("CATFISH_GATEWAY_URL").unwrap_or_else(|_| ep.gateway_base());
    let mut env_pairs: Vec<(String, String)> = vec![
        ("PYTHONPATH".into(), pythonpath),
        ("CATFISH_MCP_REGISTRY_URL".into(), gateway_url.clone()),
    ];
    // 员工身份 (登录后才有, 没登录时不传 → tool-bridge fallback 路径 2)
    if let Some(token) = crate::services::oauth::current_access_token() {
        env_pairs.push(("CATFISH_USER_JWT".into(), token));
    }
    if let Some(sub) = crate::services::oauth::current_user_sub() {
        env_pairs.push(("CATFISH_USER_SUB".into(), sub));
    }

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
        env: env_pairs,
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
        .and_then(|p| process::read_pid_file_alive_strict(&p, "catfish_tool_bridge"));

    if pid.is_none() {
        // BL-TOOL-BRIDGE-SOCK-FALLBACK (6/1): 兜底外部托管场景 — 别的工具 / 员工
        // 手动 / dev 残留起的 tool-bridge socket 模式进程, 没经过 autostart 写
        // pid 文件. 走 socket health RPC 探活 — 探到 → 标 running, 不误报"未启动".
        //
        // 历史: 6/1 加这 fallback 时误诊了一次 — 我们以为 autostart spawn 后 fs::write
        // pid 失败, 但**真因是 catfish_task_schemas.py:1044 syntax bug** (同 commit
        // 修了): tool-bridge 启动立刻 exit, read_pid_file_alive_strict 检测 PID 死
        // 自动删 stale pid, 看上去像"pid 文件没写". syntax 修后正常路径完全 work.
        // fallback 留着对真外部托管场景仍有兜底价值, 没副作用.
        if let Some(sock) = catfish_paths::tool_bridge_socket() {
            if sock.exists() {
                let healthy = matches!(
                    tool_bridge_rpc::call_with_timeout("health", json!(null), RPC_TIMEOUT).await,
                    Ok(v) if v.get("ok").and_then(|b| b.as_bool()).unwrap_or(false)
                );
                if healthy {
                    return Ok(ServiceStatus {
                        running: true,
                        healthy: true,
                        pid: None,
                        port: None,
                        message: Some(
                            "已就绪 (无 pid 文件 — 外部托管或 autostart 异常)".into(),
                        ),
                    });
                }
            }
        }
        return Ok(ServiceStatus::down(
            None,
            "未启动 — 点 \"启动\" 拉起（让 Companion 调 hermes 35 tools）",
        ));
    }

    // 进程在 → 试 RPC 探活
    let healthy = match tool_bridge_rpc::call_with_timeout("health", json!(null), RPC_TIMEOUT).await {
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
    let val = tool_bridge_rpc::call_with_timeout("tools/list", json!(null), RPC_TIMEOUT).await?;
    serde_json::from_value::<Vec<ToolInfo>>(val)
        .map_err(|e| format!("解析 tools/list 失败: {e}"))
}

#[tauri::command]
pub async fn tool_bridge_call_tool(
    name: String,
    args: Value,
    session_id: Option<String>,
) -> Result<ToolCallResult, String> {
    // BL-TODO-BRIDGE-STORE (5/16): session_id 透传给 tool-bridge, 用于 per-session
    // stateful tool 注入 (hermes todo 等). 可选 — 不传 → backend 走 __default__ 全局 store.
    let mut params = json!({ "name": name, "args": args });
    if let Some(sid) = session_id {
        params["session_id"] = Value::String(sid);
    }
    let val = tool_bridge_rpc::call_with_timeout("tools/dispatch", params, RPC_TIMEOUT).await?;
    serde_json::from_value::<ToolCallResult>(val)
        .map_err(|e| format!("解析 tools/dispatch 失败: {e}"))
}

// P44 (6/5 鸿波 marathon): chat/completions approval — resolve pending block.
// session_key 来自 SSE event `hermes.tool.progress` (status=approval_pending)
// 的 approval_session_key 字段. choice ∈ {"once","session","always","deny"}.
// 调 plugin patched tool-bridge RPC `tools/chat_approval` →
// resolve_gateway_approval(session_key, choice).
#[tauri::command]
pub async fn tool_bridge_chat_approval(
    session_key: String,
    choice: String,
) -> Result<Value, String> {
    let params = json!({ "session_key": session_key, "choice": choice });
    tool_bridge_rpc::call_with_timeout("tools/chat_approval", params, RPC_TIMEOUT).await
}

// P3.5.45 follow-up: 老私有 call_rpc fn 砍 — 5 个 callers 全改调
// services::tool_bridge_rpc::call_with_timeout:
//   - tool_bridge_status (2 处 health 探活)
//   - tool_bridge_list_tools (tools/list)
//   - tool_bridge_call_tool (tools/dispatch)
//   - tool_bridge_chat_approval (tools/chat_approval)
// 公共 helper 跟 commands/recmode.rs 复用同一份 unix sock NDJSON RPC 实现.
