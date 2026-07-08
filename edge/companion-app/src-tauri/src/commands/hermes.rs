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
        return Ok(ServiceStatus::down(
            Some(port),
            "hermes 未启动 — 检查 brew services list hermes (launchd 应自动拉)",
        ));
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

/// hang 自动恢复: kill -9 hermes 进程 + 主动 `hermes gateway start` 拉起.
///
/// # P3.5.198.j (7/8 鸿波军规审判 — hermes_kill 只 kill 不拉的历史坑)
///
/// 原设计假设 launchd KeepAlive 自动拉起, 但员工 mac 上多次撞
/// `Could not find service "ai.hermes.gateway" in domain for user gui: 501` —
/// launchd job 状态出问题, KeepAlive 不生效. hermes_kill 只 kill 结果 hermes
/// 死了没人管, useServiceStatus / P32 auto-restart 一直 unhealthy 到 timeout.
///
/// 改成: pgrep + kill -9 后跑 `hermes gateway start` shell 命令强制拉起.
/// hermes CLI 自己内部会处理 launchd job unload + reload + start 那套
/// (从员工命令行验证过是可靠的).
///
/// PATH 兜底: Tauri 进程的 PATH 是 LaunchServices 给的系统 PATH (`/usr/bin:
/// /bin`), 不含 brew 装的 hermes. 用 login shell (`sh -lc`) 加载员工 profile
/// 让 hermes CLI 找得到.
///
/// # P3.5.198.k (7/8 鸿波军规审判 — pgrep pattern bug)
///
/// 老 pattern `"hermes_cli.gateway run"` 期望 match 类似 `hermes_cli gateway run`
/// 的命令行, 但**实际 hermes launchd 起的 argv 是**:
///   python -m hermes_cli.main gateway run --replace
/// 中间是 `hermes_cli.main gateway run` — `.main` 挡住老 pattern (`.` 在 regex
/// 里虽是任意字符, 但要求 `hermes_cli.` 之后紧跟 `gateway`, 而实际是 `.main `).
/// pgrep NO MATCH → kill 一次没执行 → hermes 老 process 不死 → sh -lc start
/// no-op (hermes 已在跑) → 老 credential 一直不换.
///
/// 改成 `"hermes_cli.*gateway.*run"` 通配 python -m hermes_cli.<any> gateway run.
/// 从员工机上 pgrep 验证过 match 上唯一 hermes 主进程 (PID 34109 场景).
#[tauri::command]
pub async fn hermes_kill() -> Result<(), String> {
    // step 1: pgrep + kill -9 老进程
    let output = std::process::Command::new("pgrep")
        .args(["-f", "hermes_cli.*gateway.*run"])
        .output()
        .map_err(|e| format!("pgrep 失败: {e}"))?;

    let mut killed = 0;
    let mut errs = Vec::new();
    if output.status.success() && !output.stdout.is_empty() {
        let pids: Vec<i32> = String::from_utf8_lossy(&output.stdout)
            .lines()
            .filter_map(|l| l.trim().parse().ok())
            .collect();
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
    } else {
        // 没找到 hermes 进程 — 可能已经死了, 仍继续跑 start 拉起
        log::info!("hermes_kill: pgrep 没找到 hermes 进程 (pattern=hermes_cli.*gateway.*run), 直接跳到 start 拉起");
    }

    // step 2: 主动跑 `hermes gateway start` 强制拉起, 不靠 launchd KeepAlive
    // (它在员工 mac 上不可靠, 见函数顶部注释). 用 login shell 让 hermes 在
    // PATH 里 (员工 zshenv / bash_profile 里有 brew shellenv).
    let start_output = std::process::Command::new("sh")
        .args(["-lc", "hermes gateway start"])
        .output();

    match start_output {
        Ok(o) if o.status.success() => {
            log::info!(
                "P3.5.198.j hermes_kill: killed {} PIDs + hermes gateway start OK. \
                 stdout={} errs={:?}",
                killed,
                String::from_utf8_lossy(&o.stdout).trim(),
                errs,
            );
            Ok(())
        }
        Ok(o) => {
            let stderr = String::from_utf8_lossy(&o.stderr).trim().to_string();
            let stdout = String::from_utf8_lossy(&o.stdout).trim().to_string();
            log::warn!(
                "hermes_kill: killed {} PIDs 但 hermes gateway start 退出码 {}: \
                 stdout={stdout} stderr={stderr}",
                killed, o.status,
            );
            // hermes gateway start 可能已经在跑 (idempotent), 或者第一次撞 501 domain
            // 但 CLI 内部会 unload+reload+start 修好. 只要退出码非 0 就明确报错.
            Err(format!("hermes gateway start 失败: exit={}, stderr={stderr}", o.status))
        }
        Err(e) => {
            log::warn!("hermes_kill: 无法 exec sh -lc 'hermes gateway start': {e}");
            Err(format!("exec hermes gateway start 失败: {e}"))
        }
    }
}
