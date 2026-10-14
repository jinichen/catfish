//! 装机 / 升级换 hermes 目录前先停网关, 装完再起来 (10/14)。
//!
//! # 病
//!
//! 10/14 20:42 鸿波本机 Companion 1.0.132 把 hermes 0.21 换成 0.22: 旧目录 (连同它的 Python 环境)
//! 被挪成备份时, 旧网关进程还在跑。正好进来一条对话, 旧进程去读已经被挪走的证书文件
//! (`certifi points to a missing CA bundle ... venv/lib/python3.11/...`), 这条对话失败;
//! 一分钟后网关重启才恢复。Windows 更糟: 网关占着文件, 挪旧目录直接失败
//! ("挪开旧安装 ... 有程序占着它? 先退出 Companion 和 hermes")。
//!
//! # 做法 (各平台用各自的服务管理方式)
//!
//! - macOS: 网关是 launchd 服务 `ai.hermes.gateway` (KeepAlive, 只 kill 会被马上拉起来,
//!   拉起来的正是装到一半的新目录)。换目录前 `launchctl bootout` 卸载, 装完 (成功或回滚)
//!   用原 plist `launchctl bootstrap` 装回来。
//! - Windows: 用**旧**安装自己的 `hermes gateway stop` 停, 等端口放开; 装完用新安装的
//!   `hermes gateway start`。
//!
//! 只在原来确实在跑时才恢复; 停 / 起失败只记日志, 不挡装机 (装机本身失败才是要报的错)。

use std::time::{Duration, Instant};

use super::hermes_install_state::BootstrapPaths;

/// 停网关的结果: 装完要不要恢复。
#[derive(Debug, Default)]
pub(crate) struct StoppedGateway {
    resume: bool,
}

#[cfg(target_os = "macos")]
const LAUNCHD_LABEL: &str = "ai.hermes.gateway";

#[cfg(target_os = "macos")]
fn launchd_target() -> Option<(String, std::path::PathBuf)> {
    let out = std::process::Command::new("/usr/bin/id").arg("-u").output().ok()?;
    let uid = String::from_utf8_lossy(&out.stdout).trim().to_string();
    let home = crate::util::paths::home_env().ok()?;
    let plist = std::path::PathBuf::from(home)
        .join("Library/LaunchAgents")
        .join(format!("{LAUNCHD_LABEL}.plist"));
    (!uid.is_empty()).then(|| (format!("gui/{uid}"), plist))
}

/// 换 hermes 目录之前调。
pub(crate) fn stop_for_install(paths: &BootstrapPaths) -> StoppedGateway {
    #[cfg(target_os = "macos")]
    {
        let _ = paths;
        let Some((domain, _plist)) = launchd_target() else {
            return StoppedGateway::default();
        };
        let service = format!("{domain}/{LAUNCHD_LABEL}");
        let loaded = std::process::Command::new("/bin/launchctl")
            .args(["print", &service])
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false);
        if !loaded {
            return StoppedGateway::default();
        }
        match std::process::Command::new("/bin/launchctl").args(["bootout", &service]).output() {
            Ok(out) if out.status.success() => {
                wait_until_down();
                log::info!("[hermes-install] 换目录前已停 hermes 网关 ({service})");
                StoppedGateway { resume: true }
            }
            Ok(out) => {
                log::warn!(
                    "[hermes-install] 停 hermes 网关失败 ({}): {} —— 继续装, 期间进来的对话可能失败",
                    out.status,
                    String::from_utf8_lossy(&out.stderr).trim()
                );
                StoppedGateway::default()
            }
            Err(e) => {
                log::warn!("[hermes-install] 起不了 launchctl ({e}) —— 继续装");
                StoppedGateway::default()
            }
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        // 旧安装自己的 CLI (这时候还没换目录)
        let cli = crate::services::catfish_paths::hermes_venv_tool(&paths.install_dir, "hermes");
        if !cli.exists() || !super::codex_gateway::hermes_api_ready() {
            return StoppedGateway::default();
        }
        match crate::services::process::python_command(&cli)
            .args(["gateway", "stop"])
            .env("HERMES_HOME", &paths.hermes_home)
            .output()
        {
            Ok(out) if out.status.success() => {
                wait_until_down();
                log::info!("[hermes-install] 换目录前已停 hermes 网关");
                StoppedGateway { resume: true }
            }
            Ok(out) => {
                log::warn!(
                    "[hermes-install] `hermes gateway stop` 失败 ({}): {}",
                    out.status,
                    String::from_utf8_lossy(&out.stderr).trim()
                );
                StoppedGateway::default()
            }
            Err(e) => {
                log::warn!("[hermes-install] 起不了旧 hermes CLI ({e})");
                StoppedGateway::default()
            }
        }
    }
}

/// 装完 (成功或回滚之后) 调: 原来在跑的就重新起来。
pub(crate) fn resume_after_install(paths: &BootstrapPaths, stopped: StoppedGateway) {
    if !stopped.resume {
        return;
    }
    #[cfg(target_os = "macos")]
    {
        let _ = paths;
        let Some((domain, plist)) = launchd_target() else { return };
        if !plist.is_file() {
            log::warn!("[hermes-install] {} 不在了, 没法把网关装回 launchd", plist.display());
            return;
        }
        match std::process::Command::new("/bin/launchctl")
            .arg("bootstrap")
            .arg(&domain)
            .arg(&plist)
            .output()
        {
            Ok(out) if out.status.success() => log::info!("[hermes-install] 装完已重新启动 hermes 网关"),
            Ok(out) => log::warn!(
                "[hermes-install] 重新启动 hermes 网关失败 ({}): {} —— 手动: launchctl bootstrap {domain} {}",
                out.status,
                String::from_utf8_lossy(&out.stderr).trim(),
                plist.display()
            ),
            Err(e) => log::warn!("[hermes-install] 起不了 launchctl ({e})"),
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let cli = crate::services::catfish_paths::hermes_venv_tool(&paths.install_dir, "hermes");
        if !cli.exists() {
            log::warn!("[hermes-install] {} 不在, 网关没法重新启动", cli.display());
            return;
        }
        match crate::services::process::python_command(&cli)
            .args(["gateway", "start"])
            .env("HERMES_HOME", &paths.hermes_home)
            .output()
        {
            Ok(out) if out.status.success() => log::info!("[hermes-install] 装完已重新启动 hermes 网关"),
            Ok(out) => log::warn!(
                "[hermes-install] `hermes gateway start` 失败 ({}): {}",
                out.status,
                String::from_utf8_lossy(&out.stderr).trim()
            ),
            Err(e) => log::warn!("[hermes-install] 起不了新 hermes CLI ({e})"),
        }
    }
}

/// 等 API 端口不再响应 (最多 60 秒) —— 进程真退出了, 文件才放开。
/// 10/14 实测: v0.22 收到停止信号会先把手头的对话做完, 本机要 20 多秒才退。
fn wait_until_down() {
    let started = Instant::now();
    while super::codex_gateway::hermes_api_ready() {
        if started.elapsed() >= Duration::from_secs(60) {
            log::warn!("[hermes-install] 网关 60 秒还没退出, 继续装 (期间进来的对话可能失败)");
            return;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
}
