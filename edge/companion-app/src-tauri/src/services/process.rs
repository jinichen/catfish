//! 跨平台子进程管理。
//!
//! 实现策略：
//!   - spawn 完全脱离父进程（detached）：父进程 Companion 退了子进程不死
//!     - Unix: `process_group(0)` 让子进程在新的 process group
//!     - Windows: CREATE_NO_WINDOW + DETACHED_PROCESS
//!   - 日志重定向到文件（stdout+stderr 合流到一个 .log）
//!   - kill: SIGTERM → 等 800ms → 还活就 SIGKILL（Unix），Windows 走 taskkill /F
//!   - is_alive: `kill -0 <pid>` (Unix) / tasklist (Windows)
//!
//! 不依赖 libc/nix —— 用标准库 Command 包装系统命令，跨平台够用且少一层 crate。

use std::path::PathBuf;
use std::process::{Command, Stdio};

#[derive(Debug, Clone)]
pub struct SpawnConfig {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub log_path: PathBuf,
    pub working_dir: PathBuf,
    pub env: Vec<(String, String)>,
}

#[derive(Debug)]
pub struct SpawnHandle {
    pub pid: u32,
}

pub fn spawn_detached(cfg: SpawnConfig) -> anyhow::Result<SpawnHandle> {
    if let Some(parent) = cfg.log_path.parent() {
        std::fs::create_dir_all(parent)?;
    }

    let log_file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&cfg.log_path)?;
    let log_clone = log_file.try_clone()?;

    let mut cmd = Command::new(&cfg.program);
    cmd.args(&cfg.args)
        .current_dir(&cfg.working_dir)
        .stdin(Stdio::null())
        .stdout(Stdio::from(log_file))
        .stderr(Stdio::from(log_clone));

    for (k, v) in &cfg.env {
        cmd.env(k, v);
    }

    // Unix: 起新 process group 让子进程脱离 Companion 信号波及
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }

    // Windows: 不弹 cmd 黑窗 + 脱离 Companion
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        const DETACHED_PROCESS: u32 = 0x0000_0008;
        cmd.creation_flags(CREATE_NO_WINDOW | DETACHED_PROCESS);
    }

    let child = cmd.spawn()?;
    Ok(SpawnHandle { pid: child.id() })
}

pub fn kill(pid: u32) -> anyhow::Result<()> {
    if !is_alive(pid) {
        return Ok(()); // 已经死了，幂等返回
    }

    #[cfg(unix)]
    {
        // SIGTERM
        let _ = Command::new("kill").arg(pid.to_string()).output();
        // 等 800ms 让进程优雅退
        std::thread::sleep(std::time::Duration::from_millis(800));
        if is_alive(pid) {
            // 还活 → SIGKILL
            let _ = Command::new("kill")
                .args(["-9", &pid.to_string()])
                .output();
        }
    }
    #[cfg(windows)]
    {
        let _ = Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/F"])
            .output();
    }
    Ok(())
}

pub fn is_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    #[cfg(unix)]
    {
        // kill -0 检查存在性，不发信号
        Command::new("kill")
            .args(["-0", &pid.to_string()])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    }
    #[cfg(windows)]
    {
        Command::new("tasklist")
            .args(["/FI", &format!("PID eq {}", pid), "/NH"])
            .output()
            .ok()
            .and_then(|o| String::from_utf8(o.stdout).ok())
            .map(|s| s.contains(&pid.to_string()))
            .unwrap_or(false)
    }
}

/// 从 PID 文件读出 PID 并验活；文件不存在或进程已死返回 None
pub fn read_pid_file_alive(path: &std::path::Path) -> Option<u32> {
    let s = std::fs::read_to_string(path).ok()?;
    let pid: u32 = s.trim().parse().ok()?;
    if is_alive(pid) {
        Some(pid)
    } else {
        None
    }
}
