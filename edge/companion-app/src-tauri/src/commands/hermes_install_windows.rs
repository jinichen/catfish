//! Windows 首次启动安装器。
//!
//! MSI 只负责把 Companion 和离线资源落盘；Hermes 的长耗时准备在 GUI 启动后
//! 由这里后台执行。所有 PowerShell、tar、uv 子进程都没有窗口，输出统一写入
//! %LOCALAPPDATA%\hermes\logs\catfish-companion-bootstrap.log，前端通过
//! hermes-bootstrap-progress 显示阶段进度。

use anyhow::{Context, Result};
use std::ffi::OsString;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Stdio;

use super::hermes_install_artifacts::{
    resolve_addon_runtime_dir, RuntimeArtifacts, HERMES_DEPS_ARCHIVE,
};
use super::hermes_install_base::{
    report, BootstrapProgressState, ProgressReporter,
};
use super::hermes_install_health::core_health_problems;
use super::hermes_install_state::{write_completion_marker, BootstrapPaths};
use super::hermes_install_steps::install_hermes_deps;
use crate::services::catfish_paths::{hermes_venv_python, hermes_venv_tool};
use crate::services::process;

const TOTAL_STEPS: u8 = 5;

fn windows_resources(resource_dir: &Path) -> PathBuf {
    resource_dir.join("resources").join("windows")
}

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
    writeln!(log, "script: {}", script.display())?;

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
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log));

    let status = command
        .status()
        .with_context(|| format!("启动 {description}"))?;
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
    command.args(args);
    match command.status() {
        Ok(status) => status.success(),
        Err(error) => {
            log::debug!("[windows-bootstrap] {description} 检查失败: {error}");
            false
        }
    }
}

fn install_optional_components(resource_dir: &Path, paths: &BootstrapPaths) -> Vec<String> {
    let resources = match resolve_addon_runtime_dir(resource_dir) {
        Ok(resources) => resources,
        Err(error) => return vec![format!("附加组件资源不可用: {error:#}")],
    };
    let artifacts = RuntimeArtifacts::from_dir(resources.clone());
    let mut failures = Vec::new();

    let deps_ready = run_hidden_status(
        &hermes_venv_python(&paths.install_dir),
        &["-c", "import jieba, playwright.sync_api"],
        "jieba/playwright",
    );
    if !deps_ready {
        match artifacts.deps_tar.as_ref() {
            Some(_) => {
                if let Err(error) = install_hermes_deps(&artifacts, paths) {
                    failures.push(format!("{}: {error:#}", HERMES_DEPS_ARCHIVE));
                }
            }
            None => failures.push(format!("缺少 {HERMES_DEPS_ARCHIVE}")),
        }
    }

    let email_exe = hermes_venv_tool(&paths.install_dir, "catfish-email");
    // 仅判断 exe 存在是不够的：旧版本可能留下入口文件，但 wheel 或 pywin32
    // 已损坏，重装 MSI 又不会覆盖 Hermes venv。用同一个 Python 做无网络导入
    // 探针，失败时复用现有隐藏安装流程修复。
    let email_ready = email_exe.is_file()
        && run_hidden_status(&email_exe, &["discover", "--help"], "邮件发现 CLI 能力")
        && run_hidden_status(
            &hermes_venv_python(&paths.install_dir),
            &["-c", "import catfish_email, win32api"],
            "catfish-email/pywin32",
        );
    let script = resources.join("install-catfish-email.ps1");
    let marker = paths.install_dir.join(".catfish-email-installed.sha256");
    let expected = artifacts.email_tar.as_ref().and_then(|archive| {
        crate::services::addon_fingerprint::fingerprint(&[archive, &script]).ok()
    });
    let email_current = expected.as_ref().is_some_and(|hash| {
        crate::services::addon_fingerprint::matches(&marker, hash, email_ready)
    });
    if !email_current {
        match artifacts.email_tar.as_ref() {
            Some(archive) if script.is_file() => {
                let args = vec![
                    OsString::from("-DistributionPath"),
                    archive.clone().into_os_string(),
                ];
                let installed = run_hidden_powershell(&script, &args, paths, "安装/更新 catfish-email")
                    .and_then(|()| {
                        anyhow::ensure!(run_hidden_status(
                            &hermes_venv_python(&paths.install_dir),
                            &["-c", "import catfish_email.discovery, win32api"],
                            "新版邮件发现模块",
                        ), "邮件组件更新后自检失败");
                        let hash = expected.as_ref().context("无法读取邮件安装资源指纹")?;
                        super::hermes_install_state::write_bytes_atomic(&marker, hash.as_bytes())
                    });
                if let Err(error) = installed {
                    failures.push(format!("catfish-email: {error:#}"));
                }
            }
            Some(_) => failures.push("缺少 install-catfish-email.ps1".to_owned()),
            None => failures.push("缺少 catfish-email-dist.tar.gz".to_owned()),
        }
    }

    let reader_exe = hermes_venv_tool(&paths.install_dir, "catfish-wechat-reader");
    if !reader_exe.is_file() {
        let script = resources.join("install-wechat-reader.ps1");
        match artifacts.wechat_reader_tar.as_ref() {
            Some(archive) if script.is_file() => {
                let args = vec![
                    OsString::from("-DistributionPath"),
                    archive.clone().into_os_string(),
                ];
                if let Err(error) =
                    run_hidden_powershell(&script, &args, paths, "安装 catfish-wechat-reader")
                {
                    failures.push(format!("catfish-wechat-reader: {error:#}"));
                }
            }
            Some(_) => failures.push("缺少 install-wechat-reader.ps1".to_owned()),
            None => failures.push("缺少 catfish-wechat-reader-dist.tar.gz".to_owned()),
        }
    }

    failures
}

pub(crate) fn ensure_optional_components(resource_dir: &Path, paths: &BootstrapPaths) -> Result<()> {
    let mut log = open_bootstrap_log(paths)?;
    writeln!(log, "Component check: executable={:?}, resources={}, runtime={}",
        std::env::current_exe(), resource_dir.display(), paths.install_dir.display())?;
    let failures = install_optional_components(resource_dir, paths);
    writeln!(log, "Component check result: {:?}", failures)?;
    for failure in &failures {
        log::warn!("[windows-bootstrap] 附加组件未就绪: {failure}");
    }
    anyhow::ensure!(failures.is_empty(), "附加组件准备失败: {}", failures.join("; "));
    Ok(())
}

pub(crate) fn bootstrap(
    resource_dir: &Path,
    paths: &BootstrapPaths,
    reporter: &ProgressReporter<'_>,
) -> Result<()> {
    let resources = windows_resources(resource_dir);
    let install_script = resources.join("install.ps1");
    let uv = resources.join("uv.exe");
    let python_zip = resources.join("cpython-3.11.15-embed.zip");
    let hermes_tar = resources.join("hermes-agent-bundle.tar.gz");
    let chromium_tar = resources.join("chromium-embed.tar.gz");
    for (path, label) in [
        (&install_script, "Windows Hermes 安装脚本"),
        (&uv, "Windows uv"),
        (&python_zip, "Windows Python 离线包"),
        (&hermes_tar, "Hermes 源码离线包"),
        (&chromium_tar, "Chromium 离线包"),
    ] {
        require_file(path, label)?;
    }

    report(
        reporter,
        "core",
        BootstrapProgressState::Running,
        0,
        TOTAL_STEPS,
        "正在准备 Windows Hermes 核心环境（首次安装可能需要几分钟）",
        None,
    );
    let core_args = vec![
        OsString::from("-OfflineSourceTar"),
        hermes_tar.into_os_string(),
        OsString::from("-OfflineUvExe"),
        uv.into_os_string(),
        OsString::from("-OfflinePythonZip"),
        python_zip.into_os_string(),
        OsString::from("-OfflineChromiumTar"),
        chromium_tar.into_os_string(),
    ];
    run_hidden_powershell(
        &install_script,
        &core_args,
        paths,
        "安装 Windows Hermes 核心环境",
    )?;

    let core_problems = core_health_problems(paths, false);
    if !core_problems.is_empty() {
        anyhow::bail!(
            "Windows Hermes 安装器已退出，但健康检查失败: {}",
            core_problems.join("; ")
        );
    }
    write_completion_marker(&paths.install_dir)?;
    report(
        reporter,
        "core",
        BootstrapProgressState::Completed,
        1,
        TOTAL_STEPS,
        "Windows Hermes 核心环境已准备完成",
        None,
    );

    report(
        reporter,
        "addons",
        BootstrapProgressState::Running,
        1,
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
