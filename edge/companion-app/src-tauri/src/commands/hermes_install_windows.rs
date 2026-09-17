//! Windows 首次启动安装器。
//!
//! MSI 只负责把 Companion 和离线资源落盘；Hermes 的长耗时准备在 GUI 启动后
//! 由这里后台执行。所有 PowerShell、tar、uv 子进程都没有窗口，输出统一写入
//! %LOCALAPPDATA%\hermes\logs\catfish-companion-bootstrap.log，前端通过
//! hermes-bootstrap-progress 显示阶段进度。

use anyhow::{Context, Result};
use std::ffi::OsString;
use std::fs::OpenOptions;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};

use super::hermes_install_artifacts::{
    resolve_addon_runtime_dir, RuntimeArtifacts, HERMES_DEPS_ARCHIVE,
};
use super::hermes_install_base::{
    hermes_pinned_commit, hermes_pinned_tag, report, BootstrapProgressState, ProgressReporter,
    INSTALL_METHOD_MARKER,
};
use super::hermes_install_health::{core_health_problems, installed_hermes_commit_at};
use super::hermes_install_state::{write_bytes_atomic, write_completion_marker, BootstrapPaths};
use super::hermes_install_steps::install_hermes_deps;
use crate::services::catfish_paths::{hermes_venv_python, hermes_venv_tool};
use crate::services::process;

const TOTAL_STEPS: u8 = 5;

#[cfg(all(test, unix))]
#[path = "hermes_install_windows_tests.rs"]
mod tests;

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

fn current_stage(paths: &BootstrapPaths) -> &'static str {
    let Ok(mut file) = std::fs::File::open(paths.hermes_home.join("logs/catfish-companion-bootstrap.log")) else {
        return "正在准备 Windows Hermes";
    };
    let length = file.metadata().map(|m| m.len()).unwrap_or(0);
    let _ = file.seek(SeekFrom::Start(length.saturating_sub(16384)));
    let mut bytes = Vec::new();
    let _ = file.take(16384).read_to_end(&mut bytes);
    // Only emit fixed descriptions, never forward arbitrary child-process output.
    for line in String::from_utf8_lossy(&bytes).lines().rev() {
        for (needle, label) in [
            ("Installation Complete", "Hermes 安装脚本已完成，正在检查结果"),
            ("configuration files", "正在保存 Hermes 配置"),
            ("TUI dependencies", "正在检查终端界面组件"),
            ("Chromium", "正在准备离线浏览器"),
            ("Node.js dependencies", "正在检查浏览器工具"),
            ("Downloaded ", "正在下载和安装 Python 依赖"),
            ("Downloading ", "正在下载 Python 依赖"),
            ("Trying tier:", "正在解析 Python 依赖"),
            ("Installing dependencies", "正在安装 Python 依赖"),
            ("virtual environment", "正在准备 Python 虚拟环境"),
            ("Preparing offline Git", "正在准备离线版本信息"),
            ("copying hermes-agent", "正在复制 Hermes 离线源码"),
            ("extracting hermes-agent", "正在解压 Hermes 离线源码"),
        ] {
            if line.contains(needle) { return label; }
        }
    }
    "正在准备 Windows Hermes"
}

fn run_hidden_powershell(
    script: &Path,
    args: &[OsString],
    paths: &BootstrapPaths,
    description: &str,
    reporter: Option<&ProgressReporter<'_>>,
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
    let mut last_update = Instant::now();
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
        if last_update.elapsed() >= Duration::from_secs(10) {
            if let Some(reporter) = reporter {
                report(reporter, "core", BootstrapProgressState::Running, 0, TOTAL_STEPS,
                    format!("{}，本轮已运行 {} 秒", current_stage(paths), started.elapsed().as_secs()), None);
            }
            last_update = Instant::now();
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
    writeln!(open_bootstrap_log(paths)?, "Verified pinned Hermes core retained; retrying addons only")?;
    Ok(true)
}

/// 某些旧资源包会把源码和 venv 解压成功，但没有生成 Hermes 入口 exe。
/// 在健康检查前用现有 uv/venv 做一次无网络 editable 安装，修复入口而不引入联网 fallback。
fn repair_missing_hermes_cli(paths: &BootstrapPaths) -> Result<()> {
    let cli = hermes_venv_tool(&paths.install_dir, "hermes");
    if cli.is_file() {
        return Ok(());
    }
    let python = hermes_venv_python(&paths.install_dir);
    if !python.is_file() {
        return Ok(());
    }

    let uv = paths.hermes_home.join("bin/uv.exe");
    if uv.is_file() {
        let mut command = process::background_command(&uv);
        command
            .current_dir(&paths.install_dir)
            .args([
                OsString::from("pip"),
                OsString::from("install"),
                OsString::from("--offline"),
                OsString::from("--python"),
                python.clone().into_os_string(),
                OsString::from("--reinstall"),
                OsString::from("--no-deps"),
                OsString::from("--no-build-isolation"),
                OsString::from("--editable"),
            ])
            .arg(&paths.install_dir)
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        if command
            .status()
            .context("修复 Windows Hermes CLI 入口")?
            .success()
            && cli.is_file()
        {
            return Ok(());
        }
    }

    // 兼容已经安装 pip 的旧 venv；仍通过 --no-index 保证不联网。
    let mut command = process::background_command(&python);
    command
        .current_dir(&paths.install_dir)
        .args([
            OsString::from("-m"),
            OsString::from("pip"),
            OsString::from("install"),
            OsString::from("--no-index"),
            OsString::from("--no-deps"),
            OsString::from("--no-build-isolation"),
            OsString::from("--force-reinstall"),
            OsString::from("--editable"),
        ])
        .arg(&paths.install_dir)
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if command
        .status()
        .context("修复 Windows Hermes CLI 入口")?
        .success()
        && cli.is_file()
    {
        return Ok(());
    }
    anyhow::bail!(
        "Hermes CLI 入口修复失败: {}",
        cli.display()
    );
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
                let installed = run_hidden_powershell(&script, &args, paths, "安装/更新 catfish-email", None)
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
    let script = resources.join("install-wechat-reader.ps1");
    let marker = paths.install_dir.join(".catfish-wechat-reader-installed.sha256");
    let expected = artifacts.wechat_reader_tar.as_ref().and_then(|archive| {
        crate::services::addon_fingerprint::fingerprint(&[archive, &script]).ok()
    });
    // An exe left behind by a failed safety check is NOT a successful install.
    let reader_current = expected.as_ref().is_some_and(|hash| {
        crate::services::addon_fingerprint::matches(&marker, hash, reader_exe.is_file())
            && run_hidden_status(
                &hermes_venv_python(&paths.install_dir),
                &["-I", "-c", r#"import json, subprocess, sys
r = json.loads(subprocess.check_output([sys.argv[1], 'doctor', '--json'], timeout=20))
assert isinstance(r, dict) and type(r.get('protocol_version')) is int and r['protocol_version'] == 1
assert all(r.get(k) is True for k in ('read_only', 'secure_key_store', 'ephemeral_plaintext_cache'))
assert r.get('modifies_wechat_app') is False
"#, &reader_exe.to_string_lossy()],
                "微信读取器安全协议",
            )
    });
    if !reader_current {
        match artifacts.wechat_reader_tar.as_ref() {
            Some(archive) if script.is_file() => {
                let args = vec![
                    OsString::from("-DistributionPath"),
                    archive.clone().into_os_string(),
                ];
                let installed = run_hidden_powershell(
                    &script, &args, paths, "安装/校验 catfish-wechat-reader", None,
                ).and_then(|()| {
                    let hash = expected.as_ref().context("无法读取微信读取器资源指纹")?;
                    write_bytes_atomic(&marker, hash.as_bytes())
                });
                if let Err(error) = installed {
                    failures.push(format!("catfish-wechat-reader: {error:#}"));
                }
            }
            Some(_) => failures.push("缺少 install-wechat-reader.ps1".to_owned()),
            None => failures.push("缺少 catfish-wechat-reader-dist.tar.gz".to_owned()),
        }
    }

    failures
}

/// 只有邮件组件也升级完成后，Windows 后台服务才允许启动。
///
/// 核心 Hermes 的完成标记不能代表附加组件已经更新：旧机器可能保留着
/// 没有 `discover` 子命令的 catfish-email，若此处只看核心标记，邮件扫描器
/// 会过早启动并继续使用旧入口。
pub(crate) fn optional_components_ready(paths: &BootstrapPaths) -> bool {
    let email_exe = hermes_venv_tool(&paths.install_dir, "catfish-email");
    email_exe.is_file()
        && run_hidden_status(
            &email_exe,
            &["discover", "--help"],
            "邮件发现 CLI 能力",
        )
        && run_hidden_status(
            &hermes_venv_python(&paths.install_dir),
            &["-c", "import catfish_email.discovery, win32api"],
            "新版邮件发现模块",
        )
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
        OsString::from("-NonInteractive"),
        // Upstream's full-install catch exits nonzero only in JSON/stage mode.
        OsString::from("-Json"),
        OsString::from("-SkipSetup"),
        // Optional GUI automation is installed on demand, never in first-use setup.
        OsString::from("-SkipComputerUse"),
        OsString::from("-Commit"),
        OsString::from(hermes_pinned_commit()),
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
        Some(reporter),
    )?;

    if let Err(error) = repair_missing_hermes_cli(paths) {
        log::warn!("[windows-bootstrap] {error:#}");
    }
    write_windows_install_markers(paths)?;

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
