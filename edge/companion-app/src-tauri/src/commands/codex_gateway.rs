//! Hermes 网关的健康检查与重启。
//!
//! 2026-08-15 从 codex_backend.rs 切出来。纯搬迁, 逻辑一行未改。
//!
//! ⚠ `hermes_api_ready` 上面那段注释 (地址必须走 hermes_api_config(), 不能
//! 写死 127.0.0.1:8642) 是一条真踩过的坑, 原样搬过来了 —— 别删。

use std::collections::HashSet;
use std::process::{Command, Stdio};
use std::time::Duration;

use super::codex_helper::prepend_path;
use super::codex_probe::{hermes_agent_root, hermes_python, hermes_root};

/// Hermes API server 通不通。
///
/// ⚠ 地址必须走 `hermes_api_config()`, 不能写死 `127.0.0.1:8642`。
///
/// 那一层已经支持 `~/.catfish/companion.yaml` 的 `hermes_api.url` 和环境变量
/// `CATFISH_HERMES_API_URL` 覆盖。写死的后果在改过端口的机器上是**双重**的:
///   · 这个函数恒 false → Codex 模型在 picker 里恒灰, 而理由说的是别的
///   · 每一次切模型 (含启动时的自动对齐) 都会走进 `restart_hermes_gateway()`
///     —— 一个最长约 80 秒的阻塞循环, 顺手把好好的 gateway 重启一遍
/// 而这台机器上 hermes 其实是好的, 只是不在默认端口。
pub(crate) fn hermes_api_ready() -> bool {
    use crate::services::hermes_api_config::hermes_api_config;

    let url = &hermes_api_config().url;
    // url 形如 http://localhost:8642。用 to_socket_addrs 而不是自己切字符串:
    // 主机名可能不是 IP (localhost / 内网 DNS 名), 直接 parse::<SocketAddr>
    // 对这些一律失败, 于是又变成恒 false。
    let Some(hostport) = url
        .split("://")
        .nth(1)
        .map(|rest| rest.split('/').next().unwrap_or(rest))
    else {
        log::warn!("hermes_api.url 不像个 URL, 无法探活: {url}");
        return false;
    };
    let with_port = if hostport.contains(':') {
        hostport.to_string()
    } else if url.starts_with("https") {
        format!("{hostport}:443")
    } else {
        format!("{hostport}:80")
    };
    use std::net::ToSocketAddrs;
    match with_port.to_socket_addrs() {
        Ok(mut addrs) => addrs.any(|value| {
            std::net::TcpStream::connect_timeout(&value, Duration::from_millis(500)).is_ok()
        }),
        Err(error) => {
            log::warn!("解析 hermes API 地址失败 ({with_port}): {error}");
            false
        }
    }
}

fn parse_gateway_pids(stdout: &[u8]) -> HashSet<u32> {
    String::from_utf8_lossy(stdout)
        .lines()
        .filter_map(|line| line.trim().parse::<u32>().ok())
        .collect()
}

/// 读取当前 Hermes gateway 进程代次。单看 8642 端口不够：`gateway restart`
/// 返回后，旧进程还会短暂继续监听；如果此时就让 picker 解锁，第一条消息会被
/// 旧 runtime 接走，表现为 DeepSeek/Codex 随机“未知错误”。
fn gateway_process_ids() -> HashSet<u32> {
    #[cfg(unix)]
    {
        return Command::new("pgrep")
            .args(["-f", "[h]ermes_cli\\.main gateway run"])
            .output()
            .ok()
            .map(|output| parse_gateway_pids(&output.stdout))
            .unwrap_or_default();
    }
    #[cfg(not(unix))]
    {
        HashSet::new()
    }
}

/// 仅用于异常兜底。API server 每条消息都会创建新 AIAgent 并重新读取 runtime，
/// 正常模型切换无需重启整个 gateway；只有 gateway 本来就不健康时才走这里。
pub(crate) fn restart_hermes_gateway() -> Result<(), String> {
    let old_pids = gateway_process_ids();
    let was_ready = hermes_api_ready();
    let python = hermes_python()?;
    let root = hermes_root()?;
    let agent_root = hermes_agent_root()?;
    let mut command = Command::new(&python);
    command
        .args(["-m", "hermes_cli.main", "gateway", "restart"])
        .env("HERMES_HOME", &root)
        .current_dir(&agent_root)
        .stdin(Stdio::null());
    if let Some(parent) = python.parent() {
        prepend_path(&mut command, &[parent.to_path_buf()]);
    }
    let output = command
        .output()
        .map_err(|error| format!("重启 Hermes gateway 失败: {error}"))?;
    if !output.status.success() {
        let details = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if details.is_empty() {
            format!("Hermes gateway 重启失败 (exit {})", output.status)
        } else {
            details
        });
    }
    let mut saw_unavailable = !was_ready;
    for _ in 0..320 {
        let ready = hermes_api_ready();
        if !ready {
            saw_unavailable = true;
        }
        let current_pids = gateway_process_ids();
        let generation_changed = if old_pids.is_empty() {
            // pgrep 不可用时退化为“端口确实掉过再恢复”，仍不能把旧 listener
            // 误认成新 gateway。
            saw_unavailable
        } else {
            !current_pids.is_empty() && old_pids.is_disjoint(&current_pids)
        };

        if ready && generation_changed {
            // 连续两次探活，防止 launchd 刚拉起又因配置错误立即退出。
            std::thread::sleep(Duration::from_millis(750));
            let stable_pids = gateway_process_ids();
            let stable_generation = old_pids.is_empty()
                || (!stable_pids.is_empty() && old_pids.is_disjoint(&stable_pids));
            if stable_generation && hermes_api_ready() {
                return Ok(());
            }
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    Err("Hermes gateway 切换超时：旧进程未完整退出或新进程未稳定就绪".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_gateway_pid_generation() {
        assert_eq!(
            parse_gateway_pids(b"123\nnot-a-pid\n456\n"),
            HashSet::from([123, 456])
        );
    }

    #[test]
    #[ignore = "restarts the developer's live Hermes gateway"]
    fn live_gateway_restart_waits_for_a_new_stable_generation() {
        assert!(hermes_api_ready(), "Hermes gateway must be running");
        let before = gateway_process_ids();
        assert!(!before.is_empty(), "could not identify the old gateway PID");
        restart_hermes_gateway().expect("gateway should restart and become stable");
        let after = gateway_process_ids();
        assert!(!after.is_empty(), "new gateway PID should exist");
        assert!(before.is_disjoint(&after), "gateway generation did not change");
        assert!(hermes_api_ready(), "new gateway should answer health checks");
    }
}
