//! Windows 首次启动安装器。
//!
//! MSI 只负责把 Companion 和离线资源落盘；Hermes 的长耗时准备在 GUI 启动后
//! 由这里后台执行。所有 PowerShell、tar、uv 子进程都没有窗口，输出统一写入
//! %LOCALAPPDATA%\hermes\logs\catfish-companion-bootstrap.log，前端通过
//! hermes-bootstrap-progress 显示阶段进度。
//!
//! 9/17: 这个模块在 mac 上也整个编译 —— 为了 `hermes_install_windows_tests.rs`
//! 能在 mac 上跑 (标记文件 / 健康判定 / 日志头这些纯逻辑)。但调用方
//! (hermes_install.rs 的 `#[cfg(target_os = "windows")]` 分支) 在 mac 上不存在,
//! 于是 bootstrap / run_hidden_powershell 等 9 个函数在 mac 构建里是"死代码",
//! 每次 cargo test 刷 10 条 warning。它们在 Windows 构建里全部活着, 不是真死 ——
//! 所以只在非 Windows 上静音, Windows 构建里真的没人用的函数照样会喊。
#![cfg_attr(not(target_os = "windows"), allow(dead_code))]

use anyhow::{Context, Result};
use std::ffi::OsString;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};

use super::hermes_install_base::{
    hermes_pinned_commit, hermes_pinned_tag, report, BootstrapProgressState, ProgressReporter,
    INSTALL_METHOD_MARKER,
};
use super::hermes_install_artifacts::{resolve_runtime_dir, RuntimeArtifacts};
use super::hermes_install_health::{core_health_problems, installed_hermes_commit_at};
use super::hermes_install_native;
use super::hermes_install_state::{write_bytes_atomic, write_completion_marker, BootstrapPaths};
use crate::services::catfish_paths::{hermes_venv_python, hermes_venv_tool};
use crate::services::process;

const TOTAL_STEPS: u8 = 7;

#[path = "hermes_install_windows_addons.rs"]
mod addons;
#[allow(unused_imports)] // Re-export Windows API for existing callers on all test platforms.
pub(crate) use addons::{ensure_optional_components, optional_components_ready};
use addons::install_optional_components;

#[cfg(all(test, unix))]
#[path = "hermes_install_windows_tests.rs"]
mod tests;

fn require_file(path: &Path, label: &str) -> Result<()> {
    let metadata = std::fs::metadata(path)
        .with_context(|| format!("{label}不存在: {}", path.display()))?;
    if !metadata.is_file() || metadata.len() == 0 {
        anyhow::bail!("{label}无效: {}", path.display());
    }
    Ok(())
}

fn open_bootstrap_log(paths: &BootstrapPaths) -> Result<std::fs::File> {
    let log_path = paths.hermes_home.join("logs/catfish-companion-bootstrap.log");
    if let Some(parent) = log_path.parent() {
        std::fs::create_dir_all(parent).with_context(|| format!("创建 {}", parent.display()))?;
    }
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .with_context(|| format!("打开 Windows Hermes 安装日志 {}", log_path.display()))
}

fn run_hidden_powershell(
    script: &Path,
    args: &[OsString],
    paths: &BootstrapPaths,
    description: &str,
) -> Result<()> {
    require_file(script, "PowerShell 安装脚本")?;
    let mut log = open_bootstrap_log(paths)?;
    writeln!(log, "\n=== {description} ===")?;
    // 9/17: 每轮先打构建身份。9/11 之前的包在机器上跑了一周, 日志里没有任何一行
    // 能说明是哪个版本 —— 这一行以后就是判断"修复到底装没装上"的依据。
    writeln!(log, "companion: {}", crate::commands::system::build_identity())?;
    writeln!(log, "script: {}", script.display())?;
    writeln!(log, "arguments: {:?}", args)?;

    let mut command = process::background_command("powershell.exe");
    command
        .args([
            OsString::from("-NoProfile"),
            OsString::from("-NonInteractive"),
            OsString::from("-ExecutionPolicy"),
            OsString::from("Bypass"),
            OsString::from("-File"),
        ])
        .arg(script)
        .args(args)
        .env("HERMES_HOME", &paths.hermes_home)
        // Do not reuse the distribution cache that failed with Windows error 4390.
        .env("UV_CACHE_DIR", paths.unique_sibling("uv-cache"))
        .env("UV_LINK_MODE", "copy")
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log));

    let mut child = command
        .spawn()
        .with_context(|| format!("启动 {description}"))?;
    let started = Instant::now();
    let status = loop {
        if let Some(status) = child.try_wait().context("检查 Windows 安装进程")? {
            break status;
        }
        if started.elapsed() >= Duration::from_secs(30 * 60) {
            // Stop only this installer and its descendants, before releasing the install lock.
            let status = process::background_command("taskkill.exe")
                .args(["/PID", &child.id().to_string(), "/T", "/F"])
                .status()
                .context("停止超时安装进程")?;
            anyhow::ensure!(status.success(), "安装超时，停止安装进程失败: {status}");
            child.wait().context("等待超时安装进程退出")?;
            anyhow::bail!("{description}超过 30 分钟，已停止。请查看 logs/catfish-companion-bootstrap.log");
        }
        std::thread::sleep(Duration::from_millis(250));
    };
    if !status.success() {
        anyhow::bail!(
            "{description}失败: {status}，详细日志: {}",
            paths.hermes_home.join("logs/catfish-companion-bootstrap.log").display()
        );
    }
    Ok(())
}

fn run_hidden_status(program: &Path, args: &[&str], description: &str) -> bool {
    let mut command = process::background_command(program);
    command.args(args).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            log::debug!("[windows-bootstrap] {description} 检查失败: {error}");
            return false;
        }
    };
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) if started.elapsed() < Duration::from_secs(30) => {
                std::thread::sleep(Duration::from_millis(100));
            }
            result => {
                log::warn!("[windows-bootstrap] {description} 检查超时或失败: {result:?}");
                #[cfg(windows)]
                let _ = process::background_command("taskkill.exe")
                    .args(["/PID", &child.id().to_string(), "/T", "/F"]).status();
                let _ = child.kill();
                let _ = child.wait();
                return false;
            }
        }
    }
}

/// 离线安装脚本来自 Hermes 上游，不能假设它一定会留下鲶鱼自己的状态文件。
/// Windows 安装完成后由客户端补齐这两个文件，健康检查和下次启动就能识别同一份 pin。
fn write_windows_install_markers(paths: &BootstrapPaths) -> Result<()> {
    if !paths.install_dir.is_dir() {
        return Ok(());
    }
    let version = format!("{}\n{}\n", hermes_pinned_tag(), hermes_pinned_commit());
    write_bytes_atomic(
        &paths.install_dir.join(".catfish-hermes-version"),
        version.as_bytes(),
    )
    .context("写 Windows Hermes 版本标记")?;
    write_bytes_atomic(
        &paths.install_dir.join(INSTALL_METHOD_MARKER),
        b"offline\n",
    )
    .context("写 Windows Hermes 安装方式标记")?;
    Ok(())
}

/// 离线安装器换版本时把旧目录挪成 `hermes-agent.replaced-<时间戳>`。
/// 那是有意的退路，但**从来没人清**：真机上攒了 22 个、18 GB。
///
/// 安装成功后保留最近一份（万一新版本有问题还能手工翻回去），更早的删掉。
/// 只认 `hermes-agent.replaced-` 这个前缀、只看 hermes_home 的直接子目录，
/// 绝不碰 `hermes-agent` 本身。
pub(crate) fn prune_replaced_backups(paths: &BootstrapPaths, keep: usize) -> usize {
    let prefix = format!(
        "{}.replaced-",
        paths
            .install_dir
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_else(|| "hermes-agent".to_owned())
    );
    let Ok(entries) = std::fs::read_dir(&paths.hermes_home) else {
        return 0;
    };
    let mut backups: Vec<PathBuf> = entries
        .filter_map(Result::ok)
        .filter(|entry| entry.path().is_dir())
        .map(|entry| entry.path())
        .filter(|path| {
            path.file_name()
                .map(|name| name.to_string_lossy().starts_with(&prefix))
                .unwrap_or(false)
        })
        .collect();
    if backups.len() <= keep {
        return 0;
    }
    // 名字尾巴是 yyyyMMdd-HHmmss，字典序即时间序
    backups.sort();
    let doomed = backups.len() - keep;
    let mut removed = 0;
    for path in backups.into_iter().take(doomed) {
        match std::fs::remove_dir_all(&path) {
            Ok(()) => {
                removed += 1;
                log::info!("[windows-bootstrap] 清理旧安装备份: {}", path.display());
            }
            Err(error) => log::warn!(
                "[windows-bootstrap] 清理旧安装备份失败 {}: {error}",
                path.display()
            ),
        }
    }
    removed
}

/// Old desktop installers could finish the runtime but fail before writing our
/// completion marker. Never rebuild that venv merely to retry an addon.
/// Adoption requires the actual pinned source identity AND working core tools.
pub(crate) fn reuse_verified_core(paths: &BootstrapPaths) -> Result<bool> {
    if core_health_problems(paths, true).is_empty() {
        return Ok(true);
    }
    if installed_hermes_commit_at(&paths.install_dir).as_deref() != Some(hermes_pinned_commit())
        || !paths.install_dir.join("pyproject.toml").is_file()
    {
        return Ok(false);
    }
    if !run_hidden_status(
        &hermes_venv_python(&paths.install_dir),
        &["-I", "-c", "import hermes_cli.main, httpx, rich, prompt_toolkit"],
        "existing Hermes core imports",
    ) || !run_hidden_status(
        &hermes_venv_tool(&paths.install_dir, "hermes"), &["--help"], "existing Hermes CLI",
    ) {
        return Ok(false);
    }
    write_windows_install_markers(paths)?;
    anyhow::ensure!(core_health_problems(paths, false).is_empty(), "Recovered core health check failed");
    write_completion_marker(&paths.install_dir)?;
    prune_replaced_backups(paths, 1);
    writeln!(open_bootstrap_log(paths)?, "Verified pinned Hermes core retained; retrying addons only")?;
    Ok(true)
}

fn timestamp() -> String {
    chrono::Local::now().format("%Y%m%d-%H%M%S").to_string()
}

/// 一步: 报"进行中" → 做 → 报"完成"。
fn step<T>(
    reporter: &ProgressReporter<'_>,
    stage: &str,
    title: &str,
    done: &mut u8,
    work: impl FnOnce() -> Result<T>,
) -> Result<T> {
    report(reporter, stage, BootstrapProgressState::Running, *done, TOTAL_STEPS, title, None);
    let value = work().with_context(|| format!("Windows Hermes 安装阶段 {stage}"))?;
    *done += 1;
    report(reporter, stage, BootstrapProgressState::Completed, *done, TOTAL_STEPS, format!("{title}完成"), None);
    Ok(value)
}

/// 邮件 / 微信读取器的安装脚本 (install-catfish-email.ps1 / install-wechat-reader.ps1) 用
/// `%HERMES_HOME%\bin\uv.exe` —— 原来是上游 install.ps1 放的, 现在由我们放。
fn publish_uv(artifacts: &RuntimeArtifacts, paths: &BootstrapPaths) -> Result<()> {
    let bin = paths.hermes_home.join("bin");
    std::fs::create_dir_all(&bin).with_context(|| format!("创建 {}", bin.display()))?;
    let dest = bin.join("uv.exe");
    let tmp = bin.join(format!("uv.exe.{}.tmp", std::process::id()));
    std::fs::copy(&artifacts.uv, &tmp).with_context(|| format!("复制 uv 到 {}", tmp.display()))?;
    let _ = std::fs::remove_file(&dest);
    std::fs::rename(&tmp, &dest).with_context(|| format!("放置 {}", dest.display()))
}

/// 旧安装挪成 `hermes-agent.replaced-<时间>` (跟原来 install.ps1 同一个约定, 成功后由
/// prune_replaced_backups 只留最近一份), 再把 MSI 里的源码归档解到安装目录。返回备份路径。
fn replace_source(artifacts: &RuntimeArtifacts, paths: &BootstrapPaths) -> Result<Option<PathBuf>> {
    let archive = artifacts.hermes_tar.as_ref().context("MSI 里没有 hermes-agent-bundle.tar.gz")?;
    let backup = if paths.install_dir.exists() {
        let backup = paths.hermes_home.join(format!("hermes-agent.replaced-{}", timestamp()));
        std::fs::rename(&paths.install_dir, &backup)
            .with_context(|| format!("挪开旧安装 {} (有程序占着它? 先退出 Companion 和 hermes)", paths.install_dir.display()))?;
        Some(backup)
    } else {
        None
    };
    let extract = (|| -> Result<()> {
        std::fs::create_dir_all(&paths.install_dir)
            .with_context(|| format!("创建 {}", paths.install_dir.display()))?;
        let mut untar = process::background_command("tar");
        untar.arg("-xzf").arg(archive).arg("-C").arg(&paths.install_dir);
        let status = untar.status().context("启动 tar")?;
        anyhow::ensure!(status.success(), "解压 hermes-agent-bundle.tar.gz 失败: {status}");
        super::hermes_install_steps::flatten_hermes_source_stage(&paths.install_dir)?;
        for name in super::hermes_install_steps::drop_bundled_plugins_shadowing_user(&paths.install_dir, &paths.hermes_home)? {
            log::info!("hermes 安装: {name} 用 plugins\\ 里自己装的那份, 不放树内副本 (树内优先会挡住它)");
        }
        if let Some(problem) = super::hermes_install_health::stamp_problem(&paths.install_dir) {
            anyhow::bail!("Hermes 源码归档不对: {problem}");
        }
        Ok(())
    })();
    if let Err(error) = extract {
        let _ = std::fs::remove_dir_all(&paths.install_dir);
        if let Some(backup) = &backup {
            let _ = std::fs::rename(backup, &paths.install_dir);
        }
        return Err(error);
    }
    Ok(backup)
}

pub(crate) fn bootstrap(
    resource_dir: &Path,
    paths: &BootstrapPaths,
    reporter: &ProgressReporter<'_>,
) -> Result<()> {
    // 10/14 (hermes v0.22.0): 不再跑上游 install.ps1 (整个重写成 hermes pm 联网装, 离线补丁全部失效),
    // 跟 macOS 同一套 hermes_install_native 步骤, 只用 MSI 带的资源, 不联网。
    let artifacts = RuntimeArtifacts::from_dir(resolve_runtime_dir(resource_dir)?);
    artifacts.validate_bootstrap_tools()?;
    if !artifacts.is_complete_bundle() {
        anyhow::bail!(
            "MSI 里的 Hermes 运行时不完整 ({}/{}), 请重新安装 Companion",
            artifacts.archive_count(),
            super::hermes_install_artifacts::RUNTIME_ARCHIVES.len()
        );
    }
    let mut log = open_bootstrap_log(paths)?;
    writeln!(log, "\n=== 安装 Windows Hermes 核心环境 ===")?;
    writeln!(log, "companion: {}", crate::commands::system::build_identity())?;
    writeln!(log, "runtime: {}", artifacts.dir.display())?;

    let mut done = 0u8;
    // 10/14: 网关占着旧目录的文件, 不停的话挪旧目录直接失败; 装完 / 换回旧版后再起
    let stopped_gateway = super::hermes_install_gateway::stop_for_install(paths);
    let backup = match step(reporter, "source", "正在解压 Hermes 运行环境", &mut done, || replace_source(&artifacts, paths)) {
        Ok(backup) => backup,
        Err(error) => {
            super::hermes_install_gateway::resume_after_install(paths, stopped_gateway);
            return Err(error);
        }
    };
    let result = (|| -> Result<()> {
        let python = step(reporter, "python", "正在准备 Python 3.14", &mut done, || {
            hermes_install_native::ensure_embedded_python(&artifacts, paths)
        })?;
        step(reporter, "venv", "正在准备 Python 环境", &mut done, || {
            hermes_install_native::create_venv(&artifacts, paths, &python)
        })?;
        step(reporter, "python-deps", "正在安装 Hermes 核心组件", &mut done, || {
            hermes_install_native::install_python_deps(&artifacts, paths)
        })?;
        step(reporter, "browser", "正在准备浏览器自动化组件", &mut done, || {
            hermes_install_native::install_chromium(&artifacts, paths);
            Ok(())
        })?;
        step(reporter, "config", "正在准备本机配置", &mut done, || {
            hermes_install_native::prepare_config(paths)?;
            publish_uv(&artifacts, paths)?;
            write_windows_install_markers(paths)
        })?;
        let core_problems = core_health_problems(paths, false);
        anyhow::ensure!(core_problems.is_empty(), "Windows Hermes 装完但健康检查失败: {}", core_problems.join("; "));
        write_completion_marker(&paths.install_dir)
    })();
    if let Err(error) = result {
        writeln!(log, "核心安装失败: {error:#}")?;
        if let Some(backup) = backup {
            // 新的装坏了 → 换回原来那份 (旧版本至少还能用), 新的留成 .failed-* 给排查
            let failed = paths.hermes_home.join(format!("hermes-agent.failed-{}", timestamp()));
            let _ = std::fs::rename(&paths.install_dir, &failed);
            if let Err(restore) = std::fs::rename(&backup, &paths.install_dir) {
                return Err(error).context(format!("且恢复旧安装失败: {restore}"));
            }
        }
        super::hermes_install_gateway::resume_after_install(paths, stopped_gateway);
        return Err(error);
    }
    super::hermes_install_gateway::resume_after_install(paths, stopped_gateway);
    // 装成功了才清 —— 失败时那些备份可能是现场唯一能翻回去的东西
    prune_replaced_backups(paths, 1);
    writeln!(log, "核心安装完成")?;

    report(
        reporter,
        "addons",
        BootstrapProgressState::Running,
        done,
        TOTAL_STEPS,
        "正在准备浏览器、分词和邮件组件",
        None,
    );
    let failures = install_optional_components(resource_dir, paths);
    let message = if failures.is_empty() {
        "Windows Hermes 及附加组件已准备完成".to_owned()
    } else {
        log::warn!(
            "[windows-bootstrap] 核心已完成，但附加组件有问题: {}",
            failures.join("; ")
        );
        "Windows Hermes 核心已完成，部分附加组件需要稍后重试（详见安装日志）".to_owned()
    };
    report(
        reporter,
        "complete",
        if failures.is_empty() { BootstrapProgressState::Completed } else { BootstrapProgressState::Failed },
        TOTAL_STEPS,
        TOTAL_STEPS,
        message,
        None,
    );
    anyhow::ensure!(failures.is_empty(), "附加组件准备失败: {}", failures.join("; "));
    Ok(())
}
