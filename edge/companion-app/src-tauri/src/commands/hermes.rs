//! P3.5.125 (6/26 鸿波 catch "catfish 对 hermes 进程挂没监控"): hermes 进程
//! 健康监控 + hang 时自动重启.
//!
//! hermes 默认 launchd KeepAlive 在 *crash 退出* 时拉, 但 hang (GIL deadlock /
//! IO block) launchd 不知道 — 进程 alive 但不响应. Companion 主动:
//!
//!   1. `hermes_status`: TCP 8642 探活 + GET /health 探健康
//!   2. `hermes_kill`: useServiceStatus 连续 3 次 unhealthy 时调 — kill -9 触发
//!      launchd 自动重启
//!
//! 不在 catfish 端 spawn hermes (跟 gateway 不同 — gateway 是 Companion 起的,
//! hermes 是员工 brew install + launchd 起的). kill 后等 launchd 拉.
//!
//! # P3.5.198.h fix (7/8 鸿波军规审判)
//!
//! 原 probe URL 是 `/healthz`, 但 hermes v0.18 route table (api_server.py:4519-
//! 4521) 只挂了 `/health` 和 `/v1/health`, **没有 `/healthz`**. 探测长期返 404
//! → useServiceStatus 一直 unhealthy → Dashboard 服务状态显 "TCP 通但
//! /healthz 超时 — 进程可能 hang", 连续 3 次误触发 hermes_kill (**误 kill**
//! 好好的 hermes). P32 auto-restart 也撞这 bug → 20s poll 全 false → 超时.
//! 改成 `/health` 一处修完.
//!
//! 注: gateway 8999 侧走 `/healthz` 是对的 (catfish gateway app.py:485-486
//! 同时挂了 `/health` + `/healthz`), commands/gateway.rs 不用改.

use std::time::Duration;

use crate::commands::types::ServiceStatus;
use crate::services::hermes_api_config;

const TCP_TIMEOUT: Duration = Duration::from_millis(800);
const HTTP_TIMEOUT: Duration = Duration::from_secs(2);

/// 解析 hermes URL 真 host + port (从 hermes_api_config 真 url 提取).
/// 默认 http://localhost:8642 → ("localhost", 8642).
fn parse_hermes_endpoint() -> (String, u16) {
    let cfg = hermes_api_config::hermes_api_config();
    let url = &cfg.url;
    // 真简单 parse: http://<host>:<port> 或 http://<host>:<port>/...
    let stripped = url
        .trim_start_matches("http://")
        .trim_start_matches("https://");
    let path_idx = stripped.find('/').unwrap_or(stripped.len());
    let authority = &stripped[..path_idx];
    if let Some(colon) = authority.find(':') {
        let host = authority[..colon].to_string();
        let port: u16 = authority[colon + 1..].parse().unwrap_or(8642);
        (host, port)
    } else {
        (authority.to_string(), 8642)
    }
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

async fn probe_healthz(base: &str) -> bool {
    let client = match reqwest::Client::builder()
        .timeout(HTTP_TIMEOUT)
        .no_proxy()
        .build()
    {
        Ok(c) => c,
        Err(_) => return false,
    };
    // P3.5.198.h (7/8 鸿波): hermes v0.18 没 /healthz, 用 /health.
    // hermes api_server.py:4519 `router.add_get("/health", self._handle_health)`
    // — 无 auth 保护 (line 1157 handler 里没调 _check_auth), 匿名 GET 即可.
    let url = format!("{base}/health");
    client
        .get(&url)
        .send()
        .await
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

#[tauri::command]
pub async fn hermes_status() -> Result<ServiceStatus, String> {
    let (host, port) = parse_hermes_endpoint();

    // 1. TCP 探活
    let tcp_alive = probe_tcp(&host, port).await;
    if !tcp_alive {
        // 探不到 —— 先分清是"装了但没起来"还是"压根没装上", 这两件事的
        // 处置方式完全不同。
        //
        // 原来这里一律报"检查 brew services list hermes (launchd 应自动拉)",
        // 把人往 launchd 的方向带。但达华现场的真实情况是首启装机就失败了,
        // 根本没有 hermes 可供 launchd 拉 —— 照这条提示查一晚上也查不出来。
        let msg = if let Some(err) = crate::commands::hermes_install::last_bootstrap_error() {
            format!("hermes 装机失败, 服务起不来 — {err}")
        } else if !crate::commands::hermes_install::hermes_agent_installed() {
            "hermes 没装上 (~/.hermes/hermes-agent 不存在) — \
             Dashboard 点重新安装; 内网机器需先把 catfish-runtime-<arch>.tar.gz \
             解压到 ~/.catfish/runtime/"
                .to_string()
        } else {
            "hermes 未启动 — 检查 brew services list hermes (launchd 应自动拉)".to_string()
        };
        return Ok(ServiceStatus::down(Some(port), msg));
    }

    // 2. TCP 通 → 探 /health (hang detection — TCP 通但 /health 超时 = GIL/IO block)
    let cfg = hermes_api_config::hermes_api_config();
    let healthy = probe_healthz(&cfg.url).await;

    Ok(ServiceStatus {
        running: true,
        healthy,
        // hermes PID 真在 launchd 控制, Companion 不持有 PID file. 0 = 未知.
        pid: None,
        port: Some(port),
        message: Some(if healthy {
            format!("hermes 已连接 :{port}")
        } else {
            "⚠ TCP 通但 /health 超时 — 进程可能 hang (GIL/IO block), 连续 3 次将自动重启".into()
        }),
    })
}

/// hang 自动恢复: kill -9 hermes 进程, launchd KeepAlive 自动拉起.
///
/// # 设计意图 (P3.5.125 6/26 鸿波)
///
/// hermes hang 场景 (GIL deadlock / IO block): 进程 alive 但 /health 20s
/// 超时 → useServiceStatus 连续 3 次 unhealthy → 调 hermes_kill → kill -9
/// → launchd KeepAlive (plist `<KeepAlive>true</>`) 自动拉起新进程.
///
/// hang 状态下 SIGUSR1 (graceful drain) 可能 handler 没响应 (event loop 卡),
/// 只有 kill -9 强杀可靠. 场景跟 P38 wechat 扫码后主动切 credential 完全
/// 不同 (那里用 SIGUSR1 drain), 不共用.
///
/// # P3.5.198.k (7/8 鸿波军规审判 — pgrep pattern bug)
///
/// 老 pattern `"hermes_cli.gateway run"` 3 天 latent 不 match, 因 hermes
/// launchd 起的 argv 是 `python -m hermes_cli.main gateway run --replace`,
/// 中间 `.main` 挡住老 pattern. 改成 `"hermes_cli.*gateway.*run"` 通配.
///
/// # P3.5.201 撤 P34 sh -lc start (7/8 鸿波)
///
/// P34 曾加 `sh -lc 'hermes gateway start'` 兜底假设 launchd KeepAlive 不可
/// 靠. 但员工机 plutil verify plist `<KeepAlive>true</>` + SIGUSR1 test 3s
/// 拉起 verify KeepAlive 正常. 之前误诊 launchd 501 是 hermes CLI 内部 unload
/// +reload+start 处理, 不是 KeepAlive 挂. 撤回主动 start, 回归 P3.5.125 简
/// 洁设计 (kill 完 return, launchd 自己拉起).
#[tauri::command]
pub async fn hermes_kill() -> Result<(), String> {
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
