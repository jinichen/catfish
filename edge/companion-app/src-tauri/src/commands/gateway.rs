//! llm-gateway 的启停 + 状态查询。
//!
//! 启动：`<gateway_python> -m catfish_gateway.app`，PORT=8999，工作目录在 gateway dir
//! 日志：stdout+stderr 合流到 `<companion-state>/gateway.log`
//! PID：写到 `<companion-state>/gateway.pid`，stop 读这个 kill

use std::time::Duration;

use crate::commands::types::ServiceStatus;
use crate::services::{catfish_paths, endpoints, process};

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
    let ep = endpoints::endpoints();
    let cfg = process::SpawnConfig {
        program: python,
        args: vec!["-m".into(), "catfish_gateway.app".into()],
        log_path,
        working_dir: dir,
        env: vec![
            // gateway 自己读 PORT 环境变量决定监听端口; 我们把 endpoints 配置
            // 透传过去, 保证前后端口一致 (员工只用配一个 CATFISH_GATEWAY_PORT)
            ("PORT".into(), ep.gateway_port.to_string()),
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

/// 从 gateway 的 .env 读 CATFISH_DEV_TOKEN —— Companion 调 chat 接口需要带这个。
///
/// 设计:员工本机跑的 Companion 透明地读 gateway .env,员工不需要手动配 token。
/// SSO 上线后改成从 SSO session 取 access_token,弃用此 command。
#[tauri::command]
pub async fn gateway_get_dev_token() -> Result<String, String> {
    let dir = catfish_paths::gateway_dir()
        .ok_or_else(|| "找不到 gateway 目录".to_string())?;
    let env_file = dir.join(".env");
    if !env_file.exists() {
        return Err(format!("{} 不存在", env_file.display()));
    }
    let content = std::fs::read_to_string(&env_file)
        .map_err(|e| format!("读 .env 失败: {e}"))?;
    for line in content.lines() {
        let line = line.trim();
        if line.starts_with('#') || line.is_empty() {
            continue;
        }
        if let Some(rest) = line.strip_prefix("CATFISH_DEV_TOKEN=") {
            // 容忍 KEY="value" 这种带引号的写法
            return Ok(rest.trim().trim_matches(|c| c == '"' || c == '\'').to_string());
        }
    }
    Err("CATFISH_DEV_TOKEN 不在 gateway .env 里".to_string())
}

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
        .and_then(|p| process::read_pid_file_alive(&p));

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
    let client = match reqwest::Client::builder()
        .timeout(HTTP_TIMEOUT)
        .no_proxy()
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
