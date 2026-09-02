//! 装机的各个步骤: 跑安装器、准备源码 stage、原子切换、回滚、装 deps 和 email。
//!
//! 2026-08-15 从 hermes_install.rs 切出来 (512 行, 是原文件里最大的一块)。
//! 纯搬迁, 逻辑一行未改。
//!
//! 编排 (谁先谁后、失败了回滚到哪一步) 在 hermes_install.rs 的 bootstrap_locked,
//! 这里只放"每一步具体怎么做"。

use anyhow::{Context, Result};
use std::path::Path;
#[cfg(any(not(target_os = "windows"), test))]
use std::path::PathBuf;
use std::process::Command;

use crate::services::catfish_paths::hermes_venv_python;
#[cfg(any(not(target_os = "windows"), test))]
use crate::services::catfish_paths::hermes_venv_tool;

use super::hermes_install_artifacts::{RuntimeArtifacts, HERMES_DEPS_ARCHIVE};
#[cfg(any(not(target_os = "windows"), test))]
use super::hermes_install_artifacts::{CATFISH_EMAIL_ARCHIVE, CATFISH_WECHAT_READER_ARCHIVE};
#[cfg(any(not(target_os = "windows"), test))]
use super::hermes_install_base::{
    hermes_pinned_commit, hermes_pinned_tag, report, BootstrapProgressState, ProgressReporter,
    STAGE_READY_MARKER,
};
#[cfg(any(not(target_os = "windows"), test))]
use super::hermes_install_health::{core_health_problems, installed_hermes_commit_at};
use super::hermes_install_state::{remove_any, BootstrapPaths};
#[cfg(any(not(target_os = "windows"), test))]
use super::hermes_install_state::{write_bytes_atomic, write_transaction};

fn command_status(mut command: Command, description: &str) -> Result<()> {
    // Companion 是 Windows GUI 子系统；没有这个 flag，tar/uv/python 这类
    // console 子进程可能各自创建黑色控制台窗口。所有安装步骤统一从这里过，
    // 因此隐藏策略不会漏在某一个附加组件上。
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    let status = command
        .status()
        .with_context(|| format!("启动 {description}"))?;
    if !status.success() {
        anyhow::bail!("{description} 失败: {status}");
    }
    Ok(())
}

#[cfg(any(not(target_os = "windows"), test))]
fn copy_dir_recursive(source: &Path, destination: &Path) -> Result<()> {
    std::fs::create_dir_all(destination)
        .with_context(|| format!("创建目录 {}", destination.display()))?;
    for entry in std::fs::read_dir(source)
        .with_context(|| format!("读取目录 {}", source.display()))?
    {
        let entry = entry.with_context(|| format!("读取目录项 {}", source.display()))?;
        let source_path = entry.path();
        let destination_path = destination.join(entry.file_name());
        if source_path.is_dir() {
            copy_dir_recursive(&source_path, &destination_path)?;
        } else {
            std::fs::copy(&source_path, &destination_path).with_context(|| {
                format!("复制 {} 到 {}", source_path.display(), destination_path.display())
            })?;
        }
    }
    Ok(())
}

#[cfg(any(not(target_os = "windows"), test))]
fn add_common_installer_args(
    command: &mut Command,
    artifacts: &RuntimeArtifacts,
    paths: &BootstrapPaths,
    install_dir: &Path,
    mark_offline_source: bool,
) {
    command
        .arg("--skip-setup")
        .arg("--non-interactive")
        .arg("--dir")
        .arg(install_dir)
        .arg("--hermes-home")
        .arg(&paths.hermes_home)
        .arg("--offline-uv")
        .arg(&artifacts.uv)
        .env("HOME", &paths.home);
    if mark_offline_source {
        // stage protocol 不跑 clone_repo；这个参数只让 node-deps 走 bundle 快路径。
        command.arg("--offline-source-dir").arg(install_dir);
    }
    if let Some(path) = &artifacts.python_tar {
        command.arg("--offline-python-tar").arg(path);
    }
    if let Some(path) = &artifacts.node_tar {
        command.arg("--offline-node-tar").arg(path);
    }
    if let Some(path) = &artifacts.chromium_tar {
        command.arg("--offline-chromium-tar").arg(path);
    }
}

#[cfg(any(not(target_os = "windows"), test))]
pub(crate) fn run_install_stage(
    stage: &str,
    title: &str,
    artifacts: &RuntimeArtifacts,
    paths: &BootstrapPaths,
    install_dir: &Path,
    mark_offline_source: bool,
    completed_steps: &mut u8,
    total_steps: u8,
    reporter: &ProgressReporter<'_>,
) -> Result<()> {
    report(
        reporter,
        stage,
        BootstrapProgressState::Running,
        *completed_steps,
        total_steps,
        title,
        None,
    );
    let mut command = Command::new("bash");
    command.arg(&artifacts.install_sh);
    add_common_installer_args(
        &mut command,
        artifacts,
        paths,
        install_dir,
        mark_offline_source,
    );
    command.arg("--stage").arg(stage).arg("--json");
    command_status(command, &format!("Hermes 安装阶段 {stage}"))?;
    *completed_steps += 1;
    report(
        reporter,
        stage,
        BootstrapProgressState::Completed,
        *completed_steps,
        total_steps,
        format!("{title}完成"),
        None,
    );
    Ok(())
}

#[cfg(any(not(target_os = "windows"), test))]
fn initialize_offline_git(stage: &Path) -> Result<()> {
    // 干净 macOS 可能尚未安装 Xcode Command Line Tools；离线包有自己的版本
    // 文件，运行并不依赖 git，因此这里只做 future-update 的 best effort。
    let initialized = Command::new("git")
        .arg("init")
        .arg(stage)
        .status()
        .map(|status| status.success())
        .unwrap_or(false);
    if !initialized {
        log::warn!("无法初始化离线 Hermes git 元数据；不影响当前运行，未来更新需先安装 git");
        return Ok(());
    }
    let _ = Command::new("git")
        .arg("-C")
        .arg(stage)
        .arg("config")
        .arg("core.autocrlf")
        .arg("false")
        .status();
    let _ = Command::new("git")
        .arg("-C")
        .arg(stage)
        .arg("remote")
        .arg("add")
        .arg("origin")
        .arg("https://github.com/NousResearch/hermes-agent.git")
        .status();
    Ok(())
}

/// 将离线包中的源码目录展平到 staging 根目录。
///
/// 历史包有两种布局：`pyproject.toml` 在归档根目录，或位于
/// `hermes-agent-src/pyproject.toml`。不能固定 `--strip-components`，否则
/// 两种包中必有一种会被解压到错误层级。
#[cfg(any(not(target_os = "windows"), test))]
fn flatten_hermes_source_stage(stage: &Path) -> Result<()> {
    if stage.join("pyproject.toml").is_file() {
        return Ok(());
    }

    let nested = std::fs::read_dir(stage)
        .with_context(|| format!("读取 staging {}", stage.display()))?
        .filter_map(|entry| entry.ok().map(|entry| entry.path()))
        .find(|path| path.is_dir() && path.join("pyproject.toml").is_file());
    let Some(nested) = nested else {
        anyhow::bail!("离线 staging 缺 pyproject.toml: {}", stage.display());
    };

    for entry in std::fs::read_dir(&nested)
        .with_context(|| format!("读取 Hermes 源码目录 {}", nested.display()))?
    {
        let entry = entry.with_context(|| format!("读取 {} 的目录项", nested.display()))?;
        let destination = stage.join(entry.file_name());
        if destination.exists() {
            anyhow::bail!("Hermes staging 目录冲突: {}", destination.display());
        }
        std::fs::rename(entry.path(), &destination).with_context(|| {
            format!("展平 Hermes 源码 {} -> {}", entry.path().display(), destination.display())
        })?;
    }
    remove_any(&nested).context("清理 Hermes 源码嵌套目录")?;
    Ok(())
}

#[cfg(any(not(target_os = "windows"), test))]
pub(crate) fn prepare_source_stage(
    reusable_stage: Option<PathBuf>,
    artifacts: &RuntimeArtifacts,
    paths: &BootstrapPaths,
    completed_steps: &mut u8,
    total_steps: u8,
    reporter: &ProgressReporter<'_>,
) -> Result<PathBuf> {
    if let Some(stage) = reusable_stage {
        *completed_steps += 1;
        report(
            reporter,
            "source",
            BootstrapProgressState::Completed,
            *completed_steps,
            total_steps,
            "已恢复上次完成的源码暂存",
            None,
        );
        return Ok(stage);
    }

    let stage = paths.unique_sibling("stage");
    write_transaction(paths, "extracting", Some(&stage), None, false)?;
    report(
        reporter,
        "source",
        BootstrapProgressState::Running,
        *completed_steps,
        total_steps,
        if artifacts.hermes_tar.is_some() {
            "正在同盘解压 Hermes 运行环境"
        } else {
            "正在下载固定版本的 Hermes 源码"
        },
        None,
    );

    if let Some(archive) = &artifacts.hermes_tar {
        std::fs::create_dir(&stage).with_context(|| format!("创建 staging {}", stage.display()))?;
        let mut command = Command::new("tar");
        command
            .arg("-xzf")
            .arg(archive)
            .arg("-C")
            .arg(&stage);
        command_status(command, "解压 hermes-agent-bundle.tar.gz")?;
        flatten_hermes_source_stage(&stage)?;
        // 发布归档在构建/签名流水线中已校验，但部分历史包没有携带版本文件，
        // 也没有 .git。解压成功并确认源码骨架后，由本客户端原子落同源 pin，
        // 后续健康检查和升级判断不再把它误判成“版本未知”。
        let version = format!("{}\n{}\n", hermes_pinned_tag(), hermes_pinned_commit());
        write_bytes_atomic(&stage.join(".catfish-hermes-version"), version.as_bytes())
            .context("写离线 Hermes 版本标记")?;
        initialize_offline_git(&stage)?;
    } else {
        // 在线路径也先 clone 到同盘 staging，依赖尚未写入；失败不会碰旧版本。
        let mut command = Command::new("bash");
        command.arg(&artifacts.install_sh);
        add_common_installer_args(&mut command, artifacts, paths, &stage, false);
        command
            .arg("--commit")
            .arg(hermes_pinned_commit())
            .arg("--stage")
            .arg("repository")
            .arg("--json");
        command_status(command, "下载固定版本 Hermes 源码")?;
    }

    if installed_hermes_commit_at(&stage).as_deref() != Some(hermes_pinned_commit()) {
        anyhow::bail!(
            "staging 中 Hermes 版本不正确，需要 {}",
            hermes_pinned_commit()
        );
    }
    if !stage.join("pyproject.toml").is_file() {
        anyhow::bail!("staging 缺 pyproject.toml: {}", stage.display());
    }
    let source_kind = if artifacts.hermes_tar.is_some() {
        b"offline\n".as_slice()
    } else {
        b"online\n".as_slice()
    };
    std::fs::write(stage.join(STAGE_READY_MARKER), source_kind).context("写 staging 完成标记")?;
    write_transaction(paths, "staged", Some(&stage), None, false)?;

    *completed_steps += 1;
    report(
        reporter,
        "source",
        BootstrapProgressState::Completed,
        *completed_steps,
        total_steps,
        "Hermes 源码已准备好",
        None,
    );
    Ok(stage)
}

#[derive(Debug)]
#[cfg(any(not(target_os = "windows"), test))]
pub(crate) struct PreviousInstall {
    pub(crate) path: PathBuf,
    /// 残缺安装可能包含现场诊断线索，成功后也保留为 `.broken-*`。
    pub(crate) preserve: bool,
}

#[cfg(any(not(target_os = "windows"), test))]
pub(crate) fn activate_stage(
    paths: &BootstrapPaths,
    stage: &Path,
    reporter: &ProgressReporter<'_>,
    completed_steps: u8,
    total_steps: u8,
) -> Result<Option<PreviousInstall>> {
    report(
        reporter,
        "activate",
        BootstrapProgressState::Running,
        completed_steps,
        total_steps,
        "正在原子切换 Hermes 运行环境",
        None,
    );
    let backup = if paths.install_dir.exists() {
        // 一个完整但版本较旧的安装只是普通 backup；缺 venv/.install_method 等
        // 现场半成品则改名为 .broken-* 并保留，既不挡 fresh install，也不丢诊断线索。
        let preserve = !core_health_problems(paths, false).is_empty();
        let backup_path = paths.unique_sibling(if preserve { "broken" } else { "backup" });
        Some(PreviousInstall {
            path: backup_path,
            preserve,
        })
    } else {
        None
    };
    // 先持久化“准备切换”及计划 backup 路径，再做两个 rename，消除进程在
    // rename 与状态写入之间退出时找不到旧目录的窗口。
    write_transaction(
        paths,
        "activating",
        Some(stage),
        backup.as_ref().map(|value| value.path.as_path()),
        backup.as_ref().is_some_and(|value| value.preserve),
    )?;
    if let Some(backup) = &backup {
        std::fs::rename(&paths.install_dir, &backup.path).with_context(|| {
            format!(
                "备份旧 Hermes {} -> {}",
                paths.install_dir.display(),
                backup.path.display()
            )
        })?;
    }
    if let Err(error) = std::fs::rename(stage, &paths.install_dir) {
        if let Some(backup) = &backup {
            let _ = std::fs::rename(&backup.path, &paths.install_dir);
        }
        let _ = write_transaction(paths, "staged", Some(stage), None, false);
        return Err(error).with_context(|| {
            format!(
                "原子切换 staging {} -> {}",
                stage.display(),
                paths.install_dir.display()
            )
        });
    }
    write_transaction(
        paths,
        "installing",
        None,
        backup.as_ref().map(|value| value.path.as_path()),
        backup.as_ref().is_some_and(|value| value.preserve),
    )?;
    Ok(backup)
}

#[cfg(any(not(target_os = "windows"), test))]
pub(crate) fn rollback_install(paths: &BootstrapPaths, backup: Option<&PreviousInstall>) -> Result<()> {
    remove_any(&paths.install_dir)?;
    if let Some(backup) = backup {
        if backup.path.exists() {
            std::fs::rename(&backup.path, &paths.install_dir).with_context(|| {
                format!(
                    "安装失败后恢复旧 Hermes {} -> {}",
                    backup.path.display(),
                    paths.install_dir.display()
                )
            })?;
        }
    }
    remove_any(&paths.transaction_file)?;
    Ok(())
}

/// 把 catfish-email 装进 hermes 的 venv (7/30 达华现场)。
///
/// # 在补什么
///
/// 之前员工装完 Companion, 邮件 tab 是挂的, 界面提示
///     "CLI 没装 (bash edge/email-agent/install.sh)"
/// —— 而员工手里只有 dmg, **没有那个目录**。查下来这东西从来没进过交付链路:
/// bundle 里没有、打包脚本没装过, 而 `link_catfish_email_bin` 只建软链、
/// 前提是 `venv/bin/catfish-email` 已存在, 于是它永远走 warn 分支静默跳过。
///
/// # 为什么装 wheel、为什么用 uv
///
/// hermes 的 venv 是 `uv venv` 建的, **不带 pip 也不带 setuptools**。
///   - 用 pip 装 → email-agent 自己的 install.sh 那套 ensurepip / `curl
///     get-pip.py` 兜底, 内网机器上是死路
///   - 装源码目录 → 要跑构建后端, uv 会去联网拉 setuptools, 同样死在内网
///
/// 所以构建期就把它做成 wheel (build-mac-resources.sh), 装机时用 `.app` 里
/// 自带的 uv 纯解包拷贝, **零构建零联网**。
///
/// `--no-deps` 是因为这个包零运行时依赖 (pyproject.toml `dependencies = []`,
/// 只用标准库; pywin32 是 Windows 可选)。显式写出来, 免得 uv 去碰索引。
///
/// # 失败为什么不让整个装机挂
///
/// 邮件是附加功能, 缺了 hermes 和聊天都正常。装机在这一步失败就整体回滚,
/// 代价远大于收益。所以这里 warn + 继续, 但**warn 里必须写清楚后果**
/// (邮件 tab 会挂), 不能只写一句"失败了"。
/// 把 jieba / playwright 装进 hermes 的 venv (8/5 达华现场)。
///
/// # 为什么需要
///
/// autostart.rs 的 check_jieba_installed / check_playwright_installed 早就
/// 会报警了, 但**只报不装** —— 而装机流程里这两个包的安装语句是 0 处。
/// 于是每台新机器都缺, 员工那边表现为:
///   · 让鲶鱼开网页 → 「缺 playwright 包, 导航没走成」
///   · 文书风格 → 分词退化成字符二元组, top_words 全是碎片
///
/// 达华现场无外网, 员工机不可能 pip install, 只能随包带。
///
/// # 跟 install_catfish_email 的不同
///
/// email 是**单个** wheel, 零依赖, 所以 `--no-deps` 直接装。
/// 这里是一组 wheel (jieba / playwright / greenlet / pyee), 有依赖关系,
/// 所以用 `--find-links <dir> --no-index` 让 uv 在本地目录里解析 ——
/// `--no-index` 是关键: 少了它 uv 会去连 PyPI, 离线现场直接卡死超时。
///
/// # 不跑 `playwright install`
///
/// catfish_tools_browser.py:46 写明「不装 chromium binary (Playwright 默认会
/// 装 ~150MB), 用 connect_over_cdp 复用员工 Chrome」。只要 Python 包。
pub(crate) fn install_hermes_deps(artifacts: &RuntimeArtifacts, paths: &BootstrapPaths) -> Result<()> {
    let Some(tar) = artifacts.deps_tar.as_ref() else {
        log::warn!(
            "[hermes-deps] 资源里没有 {} —— 浏览器工具和中文分词会不可用。\
             这个包由 scripts/build-mac-resources.sh 产出, 检查打包流程。",
            HERMES_DEPS_ARCHIVE
        );
        return Ok(());
    };
    let venv_py = hermes_venv_python(&paths.install_dir);
    if !venv_py.exists() {
        anyhow::bail!("hermes venv 的 python 不存在: {}", venv_py.display());
    }

    let stage = paths.unique_sibling("hermes-deps");
    remove_any(&stage)?;
    std::fs::create_dir_all(&stage).with_context(|| format!("创建 {}", stage.display()))?;

    let mut untar = Command::new("tar");
    untar.arg("-xzf").arg(tar).arg("-C").arg(&stage);
    let result = command_status(untar, "解压 hermes-deps-dist.tar.gz").and_then(|()| {
        let mut pip = Command::new(&artifacts.uv);
        pip.arg("pip")
            .arg("install")
            .arg("--python")
            .arg(&venv_py)
            .arg("--no-index")           // 离线现场必须 —— 否则会去连 PyPI 干等超时
            .arg("--find-links")
            .arg(&stage)
            .arg("jieba")
            .arg("playwright");
        command_status(pip, "uv pip install jieba playwright")?;

        // 装了但 import 不了等于没装 —— 而下游只会 warn 一句, 现场查不出来。
        // 判据跟 autostart.rs 的自检一致 (playwright.sync_api, 不是 playwright)。
        let mut check = Command::new(&venv_py);
        check.args(["-c", "import jieba, playwright.sync_api"]);
        command_status(check, "验证 jieba / playwright 可导入")
    });

    let _ = remove_any(&stage);
    result
}

#[cfg(any(not(target_os = "windows"), test))]
pub(crate) fn install_catfish_email(artifacts: &RuntimeArtifacts, paths: &BootstrapPaths) -> Result<()> {
    let Some(tar) = artifacts.email_tar.as_ref() else {
        log::warn!(
            "[catfish-email] 资源里没有 {} —— 邮件 tab 会不可用。\
             这个包由 scripts/build-mac-resources.sh 产出, 检查打包流程。",
            CATFISH_EMAIL_ARCHIVE
        );
        return Ok(());
    };

    let venv_py = hermes_venv_python(&paths.install_dir);
    if !venv_py.exists() {
        anyhow::bail!("hermes venv 的 python 不存在: {}", venv_py.display());
    }

    let stage = paths.unique_sibling("email-dist");
    remove_any(&stage)?;
    std::fs::create_dir_all(&stage).with_context(|| format!("创建 {}", stage.display()))?;

    // tar 内容是平铺的: 一个 *.whl + hermes-skill/
    let mut untar = Command::new("tar");
    untar.arg("-xzf").arg(tar).arg("-C").arg(&stage);
    let extract = command_status(untar, "解压 catfish-email-dist.tar.gz");

    let result = extract.and_then(|()| {
        // 找 wheel。构建脚本已经保证正好一个, 这里再确认一次 ——
        // 有两个的话装哪个是随机的, 那种不确定性不该带到员工机上。
        let mut wheels: Vec<PathBuf> = std::fs::read_dir(&stage)
            .with_context(|| format!("读 {}", stage.display()))?
            .filter_map(|e| e.ok().map(|e| e.path()))
            // 用 to_str 比, 不靠 &OsStr 的 PartialEq —— 那个实现容易记岔
            .filter(|p| p.extension().and_then(|e| e.to_str()) == Some("whl"))
            .collect();
        wheels.sort();
        let wheel = match wheels.len() {
            1 => wheels.remove(0),
            0 => anyhow::bail!("catfish-email 包里没有 wheel: {}", stage.display()),
            n => anyhow::bail!("catfish-email 包里有 {n} 个 wheel, 不确定装哪个"),
        };

        let mut pip = Command::new(&artifacts.uv);
        pip.arg("pip")
            .arg("install")
            .arg("--python")
            .arg(&venv_py)
            .arg("--no-deps")
            .arg(&wheel);
        command_status(pip, "uv pip install catfish-email")?;

        // entry point 必须真落地 —— 装了但没有可执行文件等于没装,
        // 而下游 link_catfish_email_bin 只会 warn 一句, 现场查不出来。
        let bin = hermes_venv_tool(&paths.install_dir, "catfish-email");
        if !bin.exists() {
            anyhow::bail!(
                "uv 报告安装成功, 但 {} 不存在 —— entry point 没生成",
                bin.display()
            );
        }

        // hermes skill: 让 LLM 知道有这个工具, 不装的话 CLI 在但模型不会用
        let skill_src = stage.join("hermes-skill/catfish-email");
        if skill_src.is_dir() {
            let skill_dst = paths.hermes_home.join("skills/productivity/catfish-email");
            if let Some(parent) = skill_dst.parent() {
                std::fs::create_dir_all(parent)
                    .with_context(|| format!("创建 {}", parent.display()))?;
            }
            remove_any(&skill_dst)?;
            copy_dir_recursive(&skill_src, &skill_dst)
                .with_context(|| format!("拷贝 catfish-email skill 到 {}", skill_dst.display()))?;
        } else {
            log::warn!("[catfish-email] 源码包里没有 hermes-skill/, 模型不会主动用邮件工具");
        }
        Ok(())
    });

    let _ = remove_any(&stage);
    result
}

#[cfg(any(not(target_os = "windows"), test))]
pub(crate) fn link_catfish_email_bin(paths: &BootstrapPaths) -> Result<()> {
    let venv_bin = hermes_venv_tool(&paths.install_dir, "catfish-email");
    if !venv_bin.exists() {
        log::warn!(
            "[catfish-email-link] {} 不存在，邮件功能可能未随 Hermes 安装",
            venv_bin.display()
        );
        return Ok(());
    }
    let local_bin_dir = paths.home.join(".local/bin");
    std::fs::create_dir_all(&local_bin_dir)
        .with_context(|| format!("创建 {}", local_bin_dir.display()))?;
    let link = local_bin_dir.join("catfish-email");
    if std::fs::symlink_metadata(&link).is_ok() {
        remove_any(&link)?;
    }
    #[cfg(unix)]
    std::os::unix::fs::symlink(&venv_bin, &link)
        .with_context(|| format!("创建软链 {} -> {}", link.display(), venv_bin.display()))?;
    #[cfg(not(unix))]
    log::debug!("Windows 由 MSI 管理 catfish-email launcher");
    Ok(())
}

/// 安装 Catfish 自有的安全聊天导出读取器，不安装或修改微信客户端。
#[cfg(any(not(target_os = "windows"), test))]
pub(crate) fn install_catfish_wechat_reader(
    artifacts: &RuntimeArtifacts,
    paths: &BootstrapPaths,
) -> Result<()> {
    let Some(tar) = artifacts.wechat_reader_tar.as_ref() else {
        anyhow::bail!(
            "资源里没有 {}，聊天导出分析不可用",
            CATFISH_WECHAT_READER_ARCHIVE
        );
    };
    let venv_py = hermes_venv_python(&paths.install_dir);
    if !venv_py.exists() {
        anyhow::bail!("Hermes venv Python 不存在: {}", venv_py.display());
    }
    let stage = paths.unique_sibling("wechat-reader-dist");
    remove_any(&stage)?;
    std::fs::create_dir_all(&stage).with_context(|| format!("创建 {}", stage.display()))?;
    let mut untar = Command::new("tar");
    untar.arg("-xzf").arg(tar).arg("-C").arg(&stage);
    let result = command_status(untar, "解压 catfish-wechat-reader 分发包").and_then(|()| {
        let mut wheels: Vec<PathBuf> = std::fs::read_dir(&stage)
            .with_context(|| format!("读 {}", stage.display()))?
            .filter_map(|entry| entry.ok().map(|entry| entry.path()))
            .filter(|path| path.extension().and_then(|ext| ext.to_str()) == Some("whl"))
            .collect();
        wheels.sort();
        if wheels.len() != 1 {
            anyhow::bail!("聊天读取器分发包必须正好包含一个 wheel，实际 {}", wheels.len());
        }
        let mut pip = Command::new(&artifacts.uv);
        pip.arg("pip")
            .arg("install")
            .arg("--python")
            .arg(&venv_py)
            .arg("--no-deps")
            .arg(&wheels[0]);
        command_status(pip, "uv pip install catfish-wechat-reader")?;
        let reader = hermes_venv_tool(&paths.install_dir, "catfish-wechat-reader");
        let mut doctor = Command::new(&reader);
        doctor.args(["doctor", "--json"]);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            doctor.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }
        let output = doctor
            .output()
            .with_context(|| format!("运行 {} doctor", reader.display()))?;
        if !output.status.success() || !String::from_utf8_lossy(&output.stdout).contains("\"read_only\":true") {
            anyhow::bail!("catfish-wechat-reader 安全自检失败");
        }
        Ok(())
    });
    let _ = remove_any(&stage);
    result
}

#[cfg(any(not(target_os = "windows"), test))]
pub(crate) fn link_catfish_wechat_reader_bin(paths: &BootstrapPaths) -> Result<()> {
    let reader = hermes_venv_tool(&paths.install_dir, "catfish-wechat-reader");
    if !reader.exists() {
        anyhow::bail!("聊天导出读取器未安装: {}", reader.display());
    }
    let bin_dir = paths.home.join(".catfish/bin");
    std::fs::create_dir_all(&bin_dir).with_context(|| format!("创建 {}", bin_dir.display()))?;
    let link = bin_dir.join("catfish-wechat-reader");
    if std::fs::symlink_metadata(&link).is_ok() {
        remove_any(&link)?;
    }
    #[cfg(unix)]
    std::os::unix::fs::symlink(&reader, &link)
        .with_context(|| format!("创建软链 {} -> {}", link.display(), reader.display()))?;
    Ok(())
}
