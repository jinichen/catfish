//! Read-only Hermes API health. A failed probe must not kill the gateway.

use crate::commands::types::ServiceStatus;
use crate::services::hermes_api_config;

#[tauri::command]
pub async fn hermes_status() -> Result<ServiceStatus, String> {
    let cfg = hermes_api_config::hermes_api_config();
    let url = reqwest::Url::parse(&cfg.url).map_err(|e| format!("Hermes 地址无效: {e}"))?;
    crate::util::service_probe::http_health(
        &cfg.url, "/health", url.port_or_known_default(), false,
    ).await
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
