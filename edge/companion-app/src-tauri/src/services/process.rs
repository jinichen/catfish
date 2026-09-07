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

use std::ffi::OsStr;
use std::path::PathBuf;
use std::process::{Command, Stdio};

use fs2::FileExt;

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

/// Companion 进程级单实例锁。保留打开的文件句柄即可让锁覆盖整个进程生命周期。
pub struct InstanceGuard {
    _file: std::fs::File,
}

/// 创建不会在 Windows GUI 应用旁弹出控制台窗口的短期子进程。
///
/// `catfish-email.exe`、Python、PowerShell、tasklist/wmic 都属于 console
/// subsystem。Companion 本身是 windows subsystem，直接 `Command::new` 它们会
/// 让系统临时创建黑框。其它平台保持标准 `Command` 行为。
pub fn background_command<S: AsRef<OsStr>>(program: S) -> Command {
    let command = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let mut command = command;
        command.creation_flags(CREATE_NO_WINDOW);
        command
    }
    #[cfg(not(windows))]
    {
        command
    }
}

/// Tokio 版本的无窗口短期子进程构造器。
pub fn background_tokio_command<S: AsRef<OsStr>>(program: S) -> tokio::process::Command {
    let command = tokio::process::Command::new(program);
    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let mut command = command;
        command.creation_flags(CREATE_NO_WINDOW);
        command
    }
    #[cfg(not(windows))]
    {
        command
    }
}

/// 获取指定路径的跨平台独占锁；已有 Companion 时返回 None，不等待也不重复启动。
pub fn try_acquire_instance_lock(path: &std::path::Path) -> anyhow::Result<Option<InstanceGuard>> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let file = std::fs::OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .open(path)?;
    match file.try_lock_exclusive() {
        Ok(()) => Ok(Some(InstanceGuard { _file: file })),
        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => Ok(None),
        Err(error) => Err(error.into()),
    }
}

/// Companion 启动时使用的锁路径。放在用户配置目录，不依赖安装目录和盘符。
pub fn try_acquire_companion_instance_lock() -> anyhow::Result<Option<InstanceGuard>> {
    let home = crate::util::paths::home_env()?;
    try_acquire_instance_lock(
        &std::path::PathBuf::from(home)
            .join(".catfish")
            .join("companion-instance.lock"),
    )
}

/// 单个日志文件的上限, 超过就轮转。
const LOG_MAX_BYTES: u64 = 32 * 1024 * 1024;
/// 保留几个历史文件 (.1 .2 .3)。
const LOG_KEEP: u32 = 3;

/// 子进程日志轮转 —— 在 spawn 前做, 所以每次重启都是一次检查点。
///
/// # 为什么 (8/4 查 tool-bridge 反复重启时看到的)
///
/// 实测鸿波机器: `gateway.log` **462 MB**, `tool-bridge.log` 46 MB, 从来不轮转。
/// tool-bridge 每天重启 ~100 次, 每次把整个启动序列 (70+ 行 patch 日志) 重写一遍,
/// 一直涨到磁盘满为止。
///
/// 更实际的伤害是**查不了问题**: 出事想看日志, 先得跟一个几百 MB 的文件搏斗,
/// 而真正有用的最后几十行埋在最底下。日志留着是为了被读, 读不了就等于没有。
///
/// 放在 spawn 前而不是起个后台线程定时轮转: 这里天然是安全点 —— 旧进程已经不再
/// 写了, 新进程还没开始写, 不存在"轮转时有人正持有 fd 往里写"的竞态。
fn rotate_if_needed(path: &std::path::Path) {
    rotate_with_limit(path, LOG_MAX_BYTES, LOG_KEEP);
}

/// 阈值/代数做成参数, 测试才不用真写 32MB × 6 次。
fn rotate_with_limit(path: &std::path::Path, max_bytes: u64, keep: u32) {
    let Ok(meta) = std::fs::metadata(path) else {
        return; // 还没有这个文件 —— 首次启动, 无事可做
    };
    if meta.len() < max_bytes {
        return;
    }
    // 最老的先删, 然后 .2→.3 .1→.2 当前→.1
    let nth = |i: u32| path.with_extension(format!(
        "{}.{i}",
        path.extension().and_then(|s| s.to_str()).unwrap_or("log")
    ));
    let _ = std::fs::remove_file(nth(keep));
    for i in (1..keep).rev() {
        let _ = std::fs::rename(nth(i), nth(i + 1));
    }
    match std::fs::rename(path, nth(1)) {
        Ok(()) => log::info!(
            "日志轮转: {} 超过 {} MB, 已转存为 .1 (保留 {} 份)",
            path.display(),
            max_bytes / 1024 / 1024,
            keep
        ),
        // 轮转失败不能挡住服务启动 —— 日志太大是小问题, 服务起不来是大问题
        Err(e) => log::warn!("日志轮转失败 ({}), 继续追加写: {e}", path.display()),
    }
}

pub fn spawn_detached(cfg: SpawnConfig) -> anyhow::Result<SpawnHandle> {
    if let Some(parent) = cfg.log_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    rotate_if_needed(&cfg.log_path);

    let log_file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&cfg.log_path)?;
    let log_clone = log_file.try_clone()?;

    let mut cmd = background_command(&cfg.program);
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
        let _ = background_command("taskkill")
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
        background_command("tasklist")
            .args(["/FI", &format!("PID eq {}", pid), "/NH"])
            .output()
            .ok()
            .and_then(|o| String::from_utf8(o.stdout).ok())
            .map(|s| s.contains(&pid.to_string()))
            .unwrap_or(false)
    }
}

// 7/17 BL-DEADCODE-SWEEP: 老 read_pid_file_alive (宽松版) 死代码已删.
// 全项目所有 caller 都换成 read_pid_file_alive_strict, 防 PID 复用导致的误判.

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
///           跟老宽松版一样 fall through 到 Some(pid), 不引入新风险.
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
        let output = background_command("wmic")
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

#[cfg(test)]
mod rotate_tests {
    use super::*;

    fn write(p: &std::path::Path, n: usize) {
        std::fs::write(p, vec![b'x'; n]).unwrap();
    }

    const LIMIT: u64 = 1024;
    const KEEP: u32 = 3;

    #[test]
    fn small_log_is_left_alone() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("a.log");
        write(&p, 100);
        rotate_with_limit(&p, LIMIT, KEEP);
        assert!(p.exists() && !d.path().join("a.log.1").exists());
        assert_eq!(std::fs::metadata(&p).unwrap().len(), 100);
    }

    #[test]
    fn oversized_log_moves_to_dot_1() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("a.log");
        write(&p, LIMIT as usize + 1);
        rotate_with_limit(&p, LIMIT, KEEP);
        // 当前文件让位, 内容进 .1; spawn 会重新建一个空的
        assert!(!p.exists(), "旧日志该被挪走");
        assert!(d.path().join("a.log.1").exists());
    }

    #[test]
    fn only_keeps_n_generations() {
        // 实测 gateway.log 462MB —— 轮转必须有上限, 否则只是把一个大文件
        // 变成一堆大文件, 磁盘照样满。
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("a.log");
        for _ in 0..(KEEP + 3) {
            write(&p, LIMIT as usize + 1);
            rotate_with_limit(&p, LIMIT, KEEP);
        }
        for i in 1..=KEEP {
            assert!(d.path().join(format!("a.log.{i}")).exists(), "少了第 {i} 代");
        }
        assert!(
            !d.path().join(format!("a.log.{}", KEEP + 1)).exists(),
            "代数超了上限, 磁盘还是会被撑满"
        );
    }

    #[test]
    fn missing_file_is_not_an_error() {
        let d = tempfile::tempdir().unwrap();
        rotate_with_limit(&d.path().join("never-existed.log"), LIMIT, KEEP); // 不该 panic
    }

    #[test]
    fn instance_lock_is_single_flight() {
        let d = tempfile::tempdir().unwrap();
        let path = d.path().join("companion.lock");
        let first = try_acquire_instance_lock(&path).unwrap();
        assert!(first.is_some());
        let second = try_acquire_instance_lock(&path).unwrap();
        assert!(second.is_none());
    }

    #[test]
    fn recurring_windows_console_commands_use_background_constructor() {
        // Windows GUI 程序直接 spawn console subsystem 的 exe，会短暂创建黑框。
        // 这几条都是启动时或定时执行的高频路径，不能再绕过统一入口。
        for (name, source, forbidden) in [
            (
                "commands/email.rs",
                include_str!("../commands/email.rs"),
                "Command::new(bin)",
            ),
            (
                "services/email_scheduler.rs",
                include_str!("email_scheduler.rs"),
                "Command::new(&bin)",
            ),
            (
                "services/autostart_deps.rs",
                include_str!("autostart_deps.rs"),
                "std::process::Command::new",
            ),
            (
                "commands/hermes_install_windows.rs",
                include_str!("../commands/hermes_install_windows.rs"),
                "Command::new(",
            ),
            (
                "commands/dream.rs",
                include_str!("../commands/dream.rs"),
                "Command::new(&python)",
            ),
            (
                "services/distill_scheduler.rs",
                include_str!("distill_scheduler.rs"),
                "std::process::Command::new",
            ),
        ] {
            assert!(
                !source.contains(forbidden),
                "{name} 仍直接启动 Windows console 子进程：{forbidden}"
            );
        }
        let source = include_str!("process.rs");
        for forbidden in ["Command::new(\"tasklist\")", "Command::new(\"wmic\")"] {
            assert!(
                !source.contains(forbidden),
                "进程巡检仍可能周期性弹黑框：{forbidden}"
            );
        }
        assert!(
            include_str!("email_scheduler.rs").contains("email_command(&bin)"),
            "后台扫描必须复用邮件页的 command builder，才能继承 foxmail_root"
        );
    }
}
