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

    // Python 子进程在 stdout 重定向到文件时默认变 block buffering (4-8KB)，
    // 这会让日志看上去"卡很久才出现"。强制 unbuffered 让每行立刻写盘。
    // 对非 Python 进程 (如 Chrome) 这个 env 变量被忽略,无副作用。
    cmd.env("PYTHONUNBUFFERED", "1");

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

/// 从 PID 文件读出 PID 并验活；文件不存在或进程已死返回 None.
///
/// 注意: 这是**宽松版**, 只看 PID 存活, 不验进程是不是我们的. macOS / Linux 上
/// PID 会被 OS 回收复用, 旧 catfish 服务死掉后那个 PID 可能被分给别的进程
/// (比如 ssh / docker), 这个函数会误判"还在跑". 想准确请用
/// `read_pid_file_alive_strict(path, cmdline_substr)`.
///
/// 留着是因为以后可能给"我不在乎是谁的进程, 反正 PID 占了就别动" 类场景用.
/// 现在所有调用点都换成 strict 版了, 暂时 dead code.
#[allow(dead_code)]
pub fn read_pid_file_alive(path: &std::path::Path) -> Option<u32> {
    let s = std::fs::read_to_string(path).ok()?;
    let pid: u32 = s.trim().parse().ok()?;
    if is_alive(pid) {
        Some(pid)
    } else {
        None
    }
}

/// 严格版: PID 存活 **且** cmdline 含指定 substring 才算"还在跑".
///
/// 解决 macOS / Linux 上 PID 复用导致 Companion 永远启动不起来子进程的 bug:
///   旧 tool-bridge PID 死掉 → OS 回收 → 分给别的进程 → kill -0 还成功 →
///   Companion 误以为 tool-bridge 还在 → 拒绝 spawn 新的 → UI 卡黄.
///
/// 自愈: 检测到 PID 复用 (PID 活但 cmdline 不匹配) 时自动删 stale PID file,
///       下次调用就直接 None, 上层的 start 路径会 fresh spawn.
///
/// 保守策略: cmdline 拿不到 (ps 失败 / 输出空) 时**默认认为是我们的进程**,
///           跟旧 `read_pid_file_alive` 行为一致, 不引入新风险.
pub fn read_pid_file_alive_strict(
    path: &std::path::Path,
    cmdline_substr: &str,
) -> Option<u32> {
    let s = std::fs::read_to_string(path).ok()?;
    let pid: u32 = s.trim().parse().ok()?;
    if !is_alive(pid) {
        // PID 死了 → stale, 清文件 (省得下次再误读)
        let _ = std::fs::remove_file(path);
        return None;
    }
    match cmdline_matches(pid, cmdline_substr) {
        Some(false) => {
            // 确认 PID 复用 (是别人家进程) → 清 stale 文件
            let _ = std::fs::remove_file(path);
            None
        }
        Some(true) | None => Some(pid),
    }
}

/// 检查 PID 的 cmdline 是否含 substr.
/// `Some(true)` = 含, `Some(false)` = 不含, `None` = 拿不到 (保守处理).
fn cmdline_matches(pid: u32, substr: &str) -> Option<bool> {
    #[cfg(unix)]
    {
        let output = Command::new("ps")
            .args(["-p", &pid.to_string(), "-o", "command="])
            .output()
            .ok()?;
        let s = String::from_utf8(output.stdout).ok()?;
        if s.trim().is_empty() {
            return None;
        }
        Some(s.contains(substr))
    }
    #[cfg(windows)]
    {
        let output = Command::new("wmic")
            .args([
                "process",
                "where",
                &format!("ProcessId={}", pid),
                "get",
                "CommandLine",
            ])
            .output()
            .ok()?;
        let s = String::from_utf8(output.stdout).ok()?;
        if s.trim().is_empty() {
            return None;
        }
        Some(s.contains(substr))
    }
}
