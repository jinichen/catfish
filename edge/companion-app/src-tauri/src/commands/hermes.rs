//! Read-only Hermes API health. A failed probe must not kill the gateway.

use crate::commands::types::ServiceStatus;

/// 10/29: 改用 hermes 自己的就绪检查 (/health/detailed), 见 services::hermes_probe。
#[tauri::command]
pub async fn hermes_status() -> Result<ServiceStatus, String> {
    crate::services::hermes_probe::hermes_status().await
}

/// 10/19: 服务状态里 Hermes 那一行的「重启」(鸿波「hermes 也是本地服务, 为什么只读」)。
/// 跟切模型同一条路: `hermes gateway restart`, 等新进程稳定就绪才返回 (mac 走 launchd, Windows 走 CLI)。
#[tauri::command]
pub async fn hermes_restart() -> Result<(), String> {
    tokio::task::spawn_blocking(crate::commands::codex_gateway::restart_hermes_gateway)
        .await
        .map_err(|e| format!("重启 Hermes 失败: {e}"))?
}

/// Legacy explicit recovery entrypoint. Health polling never calls this.
/// Windows has no launchd restart guarantee; do not run Unix pgrep/kill there.
#[tauri::command]
pub async fn hermes_kill() -> Result<(), String> {
    #[cfg(not(unix))]
    { Err("此平台不支持通过强制终止来重启 Hermes，请使用服务管理器".into()) }
    #[cfg(unix)]
    {
    // pgrep + kill -9. hermes 死后 launchd KeepAlive 自动拉起.
    let output = std::process::Command::new("pgrep")
        .args(["-f", "hermes_cli.*gateway.*run"])
        .output()
        .map_err(|e| format!("pgrep 失败: {e}"))?;

    if !output.status.success() || output.stdout.is_empty() {
        return Err("pgrep 找不到 hermes 进程 (它可能已经死了, launchd 应正在拉)".to_string());
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
        "hermes hang 自动重启: killed {} hermes PIDs ({:?}). \
         launchd KeepAlive 应在 2-5s 后自动拉新进程. errs={:?}",
        killed, pids, errs,
    );

    if killed == 0 {
        return Err(format!("0 PID killed: {errs:?}"));
    }
    Ok(())
    }
}
