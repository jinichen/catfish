//! Hermes Agent 首次装机与版本对齐。
//!
//! 首启引导遵守四条约束：
//! 1. Tauri `setup` 只派发后台任务，不等待数百 MB 归档解压和依赖安装；
//! 2. 进程内 mutex + 跨进程文件锁保证同一时刻只有一个安装器；
//! 3. 只有完整健康检查通过后才原子写完成标记，不能再凭 `pyproject.toml` 猜完成；
//! 4. 源码先落在 `~/.hermes` 同盘 staging，随后 rename 到最终位置。安装失败或
//!    进程中断时恢复旧版本，避免 `/tmp` 解压后再 `cp -R` 六万多个文件。

use anyhow::{Context, Result};
use std::path::{Path, PathBuf};
use tauri::Emitter;

use super::hermes_install_artifacts::{resolve_runtime_dir, RuntimeArtifacts};
use super::hermes_install_base::{
    hermes_pinned_tag, report, BootstrapProgressState, HermesBootstrapProgress, ProgressReporter,
    HERMES_BOOTSTRAP_PROGRESS_EVENT, LAST_BOOTSTRAP_PROGRESS,
};
#[cfg(all(any(target_os = "macos", target_os = "linux"), not(debug_assertions)))]
use super::hermes_install_base::remember_progress;
use super::hermes_install_health::core_health_problems;
use super::hermes_install_recover::{acquire_bootstrap_lock, recover_interrupted_transaction};
use super::hermes_install_state::{
    record_failure, remove_any, write_completion_marker, BootstrapPaths, FailureRecord,
};
use crate::services::catfish_paths::{hermes_venv_python, hermes_venv_tool};
use super::hermes_install_steps::{
    activate_stage, install_catfish_email, install_catfish_wechat_reader, install_hermes_deps,
    link_catfish_email_bin, link_catfish_wechat_reader_bin, prepare_source_stage,
    rollback_install, run_install_stage,
};

/// 上一次装机失败的原因, 没失败过 / 已经装成功了返 None。
///
/// # 为什么要读回来
///
/// 装不上 hermes 的机器, 界面上其他部分看着都正常 —— 员工只会觉得"聊天没反应"。
/// 而 Dashboard 的服务状态在探不到 TCP 时, 原来一律报
/// "hermes 未启动 — 检查 brew services list hermes (launchd 应自动拉)",
/// 把人往 launchd 方向带; 真实原因是**它从来就没装上**, 没有任何东西可供
/// launchd 去拉。达华现场就是这么过去的, 最后靠手工装 hermes 收场。
///
/// 数据源是 `record_failure()` 落的
/// `~/.hermes/.catfish-hermes-bootstrap-last-error.json`, 装成功后由
/// `remove_any(&paths.last_error_file)` 删掉。
///
/// 落盘而不是放进程内存, 是为了让"昨天装挂了"这件事在今天重开 Companion
/// 之后仍然说得出来 —— 员工重启一次就把线索丢了的话, 这个字段等于没有。
pub fn last_bootstrap_error() -> Option<String> {
    let home = crate::util::paths::home_env().ok()?;
    let paths = BootstrapPaths::new(PathBuf::from(home));
    let raw = std::fs::read_to_string(&paths.last_error_file).ok()?;
    let rec: FailureRecord = serde_json::from_str(&raw).ok()?;
    let msg = rec.error.trim();
    if msg.is_empty() {
        return None;
    }
    Some(msg.to_string())
}

/// 严格安装判断。`pyproject.toml` 单独存在不再代表安装成功。
pub fn hermes_agent_installed() -> bool {
    let Ok(home) = crate::util::paths::home_env() else {
        return false;
    };
    core_health_problems(&BootstrapPaths::new(PathBuf::from(home)), true).is_empty()
}

fn bootstrap_locked(
    resource_dir: &Path,
    paths: &BootstrapPaths,
    reporter: &ProgressReporter<'_>,
) -> Result<()> {
    let reusable_stage = recover_interrupted_transaction(paths)?;
    let current_health = core_health_problems(paths, true);
    if current_health.is_empty() {
        // 8/5 (达华现场): hermes 健康 ≠ catfish-email 装了。
        //
        // catfish-email 的 wheel 是 7/30 (beb25f0) 才开始进安装包的。在那之前
        // 装机的员工, 机器上 hermes 是完整健康的 —— commit 跟 pin 一致、
        // COMPLETION_MARKER 也在 —— 于是每次启动都走这条"跳过"分支,
        // install_catfish_email 永远够不着。给他们发新包也没用, 因为新包一样
        // 判定健康、一样跳过。
        //
        // 实测达华 (macOS M1) 就卡在这里: 邮件 tab 报「catfish-email CLI 没装」,
        // 而界面给的修复命令指向 `edge/email-agent/` —— 员工手里只有 dmg,
        // 那个目录根本不存在。四条路 (自动 bootstrap / 重装按钮 / 装新包 /
        // 照提示操作) 全堵死。
        //
        // 为什么不把 catfish-email 加进 core_health_problems: 那会让**所有**
        // 老机器判定不健康, 走完整路径重装几百 MB 的 python/node/chromium ——
        // 为一个 76K 的 wheel 付这个代价不合理, 而且升级时长会吓到现场。
        //
        // 这里只补缺的那一个: 文件在就什么都不做 (零代价), 不在才装。
        if !hermes_venv_tool(&paths.install_dir, "catfish-email").exists() {
            log::warn!(
                "[catfish-email] hermes 健康但 catfish-email 缺失 —— \
                 多半是 7/30 之前装的机器。只补装它, 不重装 hermes。"
            );
            match resolve_runtime_dir(resource_dir)
                .map(RuntimeArtifacts::from_dir)
                .and_then(|artifacts| install_catfish_email(&artifacts, paths))
            {
                Ok(()) => log::info!("[catfish-email] 补装完成"),
                // 补装失败不能挡住启动 —— 邮件不可用是局部功能缺失,
                // 起不来是整个 Companion 没了。但必须出声: 这条静默了
                // 一周多才被现场发现。
                Err(e) => log::warn!("[catfish-email] 补装失败, 邮件 tab 仍不可用: {e:#}"),
            }
        }
        // 同上: hermes 健康 ≠ jieba/playwright 装了。判据用 import 而不是
        // 看目录 —— site-packages 里有目录但 import 不了的情况见过 (装了一半)。
        let deps_ok = std::process::Command::new(hermes_venv_python(&paths.install_dir))
            .args(["-c", "import jieba, playwright.sync_api"])
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false);
        if !deps_ok {
            log::warn!(
                "[hermes-deps] hermes 健康但 jieba/playwright 缺失 —— \
                 只补装它们, 不重装 hermes。"
            );
            match resolve_runtime_dir(resource_dir)
                .map(RuntimeArtifacts::from_dir)
                .and_then(|artifacts| install_hermes_deps(&artifacts, paths))
            {
                Ok(()) => log::info!("[hermes-deps] 补装完成"),
                Err(e) => log::warn!("[hermes-deps] 补装失败, 浏览器工具和分词仍不可用: {e:#}"),
            }
        }
        if let Err(e) = link_catfish_email_bin(paths) {
            // 原来是 `let _ =` —— 建软链失败 (权限 / ~/.local/bin 被占成普通
            // 文件 / 磁盘满) 一个字都不会有, 员工只看到"CLI 没装"。
            log::warn!("[catfish-email-link] 建软链失败: {e:#}");
        }
        if !hermes_venv_tool(&paths.install_dir, "catfish-wechat-reader").exists() {
            match resolve_runtime_dir(resource_dir)
                .map(RuntimeArtifacts::from_dir)
                .and_then(|artifacts| install_catfish_wechat_reader(&artifacts, paths))
            {
                Ok(()) => log::info!("[wechat-reader] 补装完成"),
                Err(e) => log::warn!("[wechat-reader] 补装失败，聊天导出分析不可用: {e:#}"),
            }
        }
        if let Err(e) = link_catfish_wechat_reader_bin(paths) {
            log::warn!("[wechat-reader] 稳定入口创建失败: {e:#}");
        }
        report(
            reporter,
            "complete",
            BootstrapProgressState::Skipped,
            0,
            0,
            format!("Hermes {} 已完整安装，无需重复准备", hermes_pinned_tag()),
            None,
        );
        return Ok(());
    }
    log::warn!(
        "Hermes 健康检查未通过，将修复: {}",
        current_health.join("; ")
    );

    let runtime_dir = resolve_runtime_dir(resource_dir)?;
    let artifacts = RuntimeArtifacts::from_dir(runtime_dir);
    artifacts.validate_bootstrap_tools()?;
    log::info!(
        "[runtime] 使用 {}，离线归档 {}/4",
        artifacts.dir.display(),
        artifacts.archive_count()
    );

    // A source archive by itself is not a complete offline runtime.  Only a
    // verified 4/4 set may skip prerequisite/network fallbacks or enable the
    // installer's strict bundled Node/Chromium fast path.
    let complete_bundle = artifacts.is_complete_bundle();
    let total_steps = if complete_bundle { 7 } else { 8 };
    let mut completed_steps = 0;

    // 在线 clone 需要 git/network；完整离线包则跳过这个最多 16 秒的网络探测。
    if !complete_bundle {
        run_install_stage(
            "prerequisites",
            "正在检查基础运行环境",
            &artifacts,
            paths,
            &paths.install_dir,
            false,
            &mut completed_steps,
            total_steps,
            reporter,
        )?;
    }

    let stage = prepare_source_stage(
        reusable_stage,
        &artifacts,
        paths,
        &mut completed_steps,
        total_steps,
        reporter,
    )?;
    let backup = activate_stage(paths, &stage, reporter, completed_steps, total_steps)?;

    let install_result = (|| -> Result<()> {
        // 不传 --no-venv。健康契约明确要求 venv/bin/python + venv/bin/hermes。
        run_install_stage(
            "venv",
            "正在准备 Python 环境",
            &artifacts,
            paths,
            &paths.install_dir,
            false,
            &mut completed_steps,
            total_steps,
            reporter,
        )?;
        run_install_stage(
            "python-deps",
            "正在安装 Hermes 核心组件",
            &artifacts,
            paths,
            &paths.install_dir,
            false,
            &mut completed_steps,
            total_steps,
            reporter,
        )?;
        run_install_stage(
            "node-deps",
            "正在准备浏览器自动化组件",
            &artifacts,
            paths,
            &paths.install_dir,
            complete_bundle,
            &mut completed_steps,
            total_steps,
            reporter,
        )?;
        run_install_stage(
            "config",
            "正在准备本机配置",
            &artifacts,
            paths,
            &paths.install_dir,
            false,
            &mut completed_steps,
            total_steps,
            reporter,
        )?;
        run_install_stage(
            "path",
            "正在创建 Hermes 启动入口",
            &artifacts,
            paths,
            &paths.install_dir,
            false,
            &mut completed_steps,
            total_steps,
            reporter,
        )?;
        run_install_stage(
            "complete",
            "正在验证安装结果",
            &artifacts,
            paths,
            &paths.install_dir,
            false,
            &mut completed_steps,
            total_steps,
            reporter,
        )?;

        let pre_marker_problems = core_health_problems(paths, false);
        if !pre_marker_problems.is_empty() {
            anyhow::bail!(
                "安装器退出成功但健康检查失败: {}",
                pre_marker_problems.join("; ")
            );
        }
        write_completion_marker(&paths.install_dir)?;
        let final_problems = core_health_problems(paths, true);
        if !final_problems.is_empty() {
            anyhow::bail!("完成标记写入后健康检查失败: {}", final_problems.join("; "));
        }
        // 浏览器工具 / 中文分词也是附加功能, 同样不回滚, 但要出声。
        if let Err(e) = install_hermes_deps(&artifacts, paths) {
            log::warn!("[hermes-deps] 装 jieba/playwright 失败, 浏览器工具和分词不可用: {e:#}");
        }
        // 邮件是附加功能 —— 装不上不回滚整个 hermes, 但要留下能查的日志。
        if let Err(e) = install_catfish_email(&artifacts, paths) {
            log::warn!("[catfish-email] 装失败, 邮件 tab 会不可用: {e:#}");
        }
        if let Err(e) = install_catfish_wechat_reader(&artifacts, paths) {
            log::warn!("[wechat-reader] 安装失败，聊天导出分析不可用: {e:#}");
        }
        link_catfish_email_bin(paths)?;
        if let Err(e) = link_catfish_wechat_reader_bin(paths) {
            log::warn!("[wechat-reader] 稳定入口创建失败: {e:#}");
        }
        Ok(())
    })();

    if let Err(error) = install_result {
        let rollback_error = rollback_install(paths, backup.as_ref()).err();
        if let Some(rollback_error) = rollback_error {
            return Err(error).context(format!("且回滚失败: {rollback_error:#}"));
        }
        return Err(error);
    }

    if let Some(backup) = backup {
        if backup.preserve {
            log::warn!("现场残缺 Hermes 已保留用于诊断: {}", backup.path.display());
        } else {
            remove_any(&backup.path)?;
        }
    }
    remove_any(&paths.transaction_file)?;
    let _ = remove_any(&paths.last_error_file);
    report(
        reporter,
        "complete",
        BootstrapProgressState::Completed,
        total_steps,
        total_steps,
        "Hermes 运行环境已准备完成",
        None,
    );
    Ok(())
}

fn ensure_hermes_installed_with_reporter(
    resource_dir: &Path,
    reporter: &ProgressReporter<'_>,
) -> Result<()> {
    let home = crate::util::paths::home_env()
        .map(PathBuf::from)
        .context("无法确定用户家目录")?;
    let paths = BootstrapPaths::new(home);
    report(
        reporter,
        "lock",
        BootstrapProgressState::Running,
        0,
        0,
        "正在检查 Hermes 运行环境",
        None,
    );
    let _lock = acquire_bootstrap_lock(&paths, reporter)?;
    let result = bootstrap_locked(resource_dir, &paths, reporter);
    if let Err(error) = &result {
        record_failure(&paths, error);
        report(
            reporter,
            "complete",
            BootstrapProgressState::Failed,
            0,
            0,
            "Hermes 运行环境准备失败，可稍后重试",
            Some(format!("{error:#}")),
        );
    }
    result
}

// 这里原来有个 `pub fn ensure_hermes_installed(resource_dir)`, 注释写着
// "保留同步 API，供测试和非 UI 调用复用" —— 但全仓库没有任何一处调它,
// 编译器 dead_code 也报了。注释描述的是一个意图, 不是事实。
//
// 需要同步语义的直接用 ensure_hermes_installed_with_reporter(dir, &|_| {}),
// 一行的事; 留一个没人走的公开包装只会让人以为它还在链路上。

/// Tauri setup 使用：立即返回，所有磁盘/网络工作在后台线程完成。
#[cfg(all(any(target_os = "macos", target_os = "linux"), not(debug_assertions)))]
pub fn spawn_hermes_bootstrap(app: tauri::AppHandle, resource_dir: PathBuf) {
    let queued_app = app.clone();
    let queued = HermesBootstrapProgress {
        phase: "queued".to_owned(),
        state: BootstrapProgressState::Queued,
        completed_steps: 0,
        total_steps: 0,
        message: "Hermes 运行环境将在后台准备".to_owned(),
        error: None,
    };
    remember_progress(&queued);
    let _ = queued_app.emit(HERMES_BOOTSTRAP_PROGRESS_EVENT, queued);
    if let Err(error) = std::thread::Builder::new()
        .name("hermes-bootstrap".to_owned())
        .spawn(move || {
            let event_app = app.clone();
            let reporter = move |progress: HermesBootstrapProgress| {
                if let Err(error) = event_app.emit(HERMES_BOOTSTRAP_PROGRESS_EVENT, progress) {
                    log::warn!("发送 Hermes bootstrap 进度失败: {error}");
                }
            };
            if let Err(error) = ensure_hermes_installed_with_reporter(&resource_dir, &reporter) {
                log::warn!("Hermes 后台 bootstrap 失败: {error:#}");
            }
        })
    {
        log::warn!("创建 Hermes bootstrap 后台线程失败: {error}");
    }
}

/// 前端监听器可能晚于 Tauri setup 挂载；提供当前快照，避免首个事件丢失后
/// 安装卡片一直不出现。事件仍负责实时更新，这个 command 只补初始状态。
#[tauri::command]
pub fn hermes_bootstrap_status() -> Option<HermesBootstrapProgress> {
    LAST_BOOTSTRAP_PROGRESS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .clone()
}

/// Dashboard 手工重装入口保留原 command 名称与“等待结果”语义，但把阻塞工作
/// 放进 blocking worker，避免占用 Tauri async runtime/UI 线程。
#[tauri::command]
pub async fn reinstall_hermes_agent(app: tauri::AppHandle) -> Result<(), String> {
    use tauri::Manager;
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| format!("拿不到 resource_dir: {error}"))?;
    let event_app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let reporter = move |progress: HermesBootstrapProgress| {
            if let Err(error) = event_app.emit(HERMES_BOOTSTRAP_PROGRESS_EVENT, progress) {
                log::warn!("发送 Hermes reinstall 进度失败: {error}");
            }
        };
        ensure_hermes_installed_with_reporter(&resource_dir, &reporter)
            .map_err(|error| format!("装 Hermes 失败: {error:#}"))
    })
    .await
    .map_err(|error| format!("Hermes 安装 worker 异常结束: {error}"))?
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    // 只有测试用到的 —— 放在 mod 内部, 放到文件顶上会让非测试编译报 unused import。
    //
    // ⚠ 这里必须写 crate::commands::, 不能写 super::。文件顶上的 `super` 指的是
    // commands, 但在 mod tests 里面 `super` 指的是 hermes_install 本身, 差一层。
    use crate::commands::hermes_install_artifacts::{
        resolve_runtime_dir_for_home, RUNTIME_ARCHIVES,
    };
    use crate::commands::hermes_install_base::{
        hermes_pinned_commit, COMPLETION_MARKER, INSTALL_METHOD_MARKER, STAGE_READY_MARKER,
    };
    use crate::commands::hermes_install_health::parse_version_file;
    use crate::commands::hermes_install_state::{
        write_json_atomic, write_transaction, CompletionRecord, TransactionRecord,
    };

    fn write_large_file(path: &Path) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, vec![b'x'; 2048]).unwrap();
    }

    fn make_runtime(dir: &Path, archive_count: usize) {
        write_large_file(&dir.join("install.sh"));
        write_large_file(&dir.join("uv"));
        for archive in RUNTIME_ARCHIVES.iter().take(archive_count) {
            write_large_file(&dir.join(archive));
        }
    }

    fn make_executable(path: &Path) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, b"#!/bin/sh\nexit 0\n").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut permissions = fs::metadata(path).unwrap().permissions();
            permissions.set_mode(0o755);
            fs::set_permissions(path, permissions).unwrap();
        }
    }

    fn make_core_install(paths: &BootstrapPaths) {
        let dir = &paths.install_dir;
        fs::create_dir_all(dir).unwrap();
        fs::write(dir.join("pyproject.toml"), b"[project]\n").unwrap();
        fs::write(
            dir.join(".catfish-hermes-version"),
            format!("{}\n{}\n", hermes_pinned_tag(), hermes_pinned_commit()),
        )
        .unwrap();
        fs::write(dir.join(INSTALL_METHOD_MARKER), b"git\n").unwrap();
        make_executable(&hermes_venv_python(dir));
        make_executable(&hermes_venv_tool(dir, "hermes"));
    }

    #[test]
    fn runtime_selection_prefers_more_complete_external_bundle() {
        let temp = tempfile::tempdir().unwrap();
        let resources = temp.path().join("app-resources");
        let home = temp.path().join("home");
        make_runtime(&resources.join("resources/mac"), 0);
        make_runtime(&home.join(".catfish/runtime"), 4);

        let selected = resolve_runtime_dir_for_home(&resources, Some(&home)).unwrap();
        assert_eq!(selected, home.join(".catfish/runtime"));
    }

    #[test]
    fn runtime_selection_keeps_bundle_as_tie_breaker() {
        let temp = tempfile::tempdir().unwrap();
        let resources = temp.path().join("app-resources");
        let home = temp.path().join("home");
        make_runtime(&resources.join("resources/mac"), 2);
        make_runtime(&home.join(".catfish/runtime"), 2);

        let selected = resolve_runtime_dir_for_home(&resources, Some(&home)).unwrap();
        assert_eq!(selected, resources.join("resources/mac"));
    }

    #[test]
    fn only_four_archives_enable_the_complete_bundle_fast_path() {
        let temp = tempfile::tempdir().unwrap();
        let partial_dir = temp.path().join("partial");
        let complete_dir = temp.path().join("complete");
        make_runtime(&partial_dir, 3);
        make_runtime(&complete_dir, 4);

        assert!(!RuntimeArtifacts::from_dir(partial_dir).is_complete_bundle());
        assert!(RuntimeArtifacts::from_dir(complete_dir).is_complete_bundle());
    }

    #[test]
    fn pyproject_alone_is_not_a_healthy_install() {
        let temp = tempfile::tempdir().unwrap();
        let paths = BootstrapPaths::new(temp.path().to_path_buf());
        fs::create_dir_all(&paths.install_dir).unwrap();
        fs::write(paths.install_dir.join("pyproject.toml"), b"[project]\n").unwrap();

        let problems = core_health_problems(&paths, true);
        assert!(problems.iter().any(|p| p.contains("Hermes CLI")));
        assert!(problems.iter().any(|p| p.contains(COMPLETION_MARKER)));
    }

    #[test]
    fn completion_marker_is_required_and_validated() {
        let temp = tempfile::tempdir().unwrap();
        let paths = BootstrapPaths::new(temp.path().to_path_buf());
        make_core_install(&paths);
        assert!(core_health_problems(&paths, false).is_empty());
        assert!(!core_health_problems(&paths, true).is_empty());

        write_completion_marker(&paths.install_dir).unwrap();
        assert!(core_health_problems(&paths, true).is_empty());

        let mut marker: CompletionRecord =
            serde_json::from_slice(&fs::read(paths.install_dir.join(COMPLETION_MARKER)).unwrap())
                .unwrap();
        marker.commit = "0".repeat(40);
        write_json_atomic(&paths.install_dir.join(COMPLETION_MARKER), &marker).unwrap();
        assert!(core_health_problems(&paths, true)
            .iter()
            .any(|p| p.contains("完成标记版本无效")));
    }

    #[test]
    fn interrupted_activation_restores_previous_install() {
        let temp = tempfile::tempdir().unwrap();
        let paths = BootstrapPaths::new(temp.path().to_path_buf());
        fs::create_dir_all(&paths.hermes_home).unwrap();
        fs::create_dir_all(&paths.install_dir).unwrap();
        fs::write(paths.install_dir.join("partial"), b"new").unwrap();
        let backup = paths.unique_sibling("backup-test");
        fs::create_dir_all(&backup).unwrap();
        fs::write(backup.join("old"), b"old").unwrap();
        write_transaction(&paths, "installing", None, Some(&backup), false).unwrap();

        assert!(recover_interrupted_transaction(&paths).unwrap().is_none());
        assert!(paths.install_dir.join("old").is_file());
        assert!(!paths.install_dir.join("partial").exists());
        assert!(!paths.transaction_file.exists());
    }

    #[test]
    fn ready_staging_is_reused_after_interruption() {
        let temp = tempfile::tempdir().unwrap();
        let paths = BootstrapPaths::new(temp.path().to_path_buf());
        fs::create_dir_all(&paths.hermes_home).unwrap();
        let stage = paths.unique_sibling("stage-test");
        fs::create_dir_all(&stage).unwrap();
        fs::write(stage.join("pyproject.toml"), b"[project]\n").unwrap();
        fs::write(
            stage.join(".catfish-hermes-version"),
            format!("{}\n", hermes_pinned_commit()),
        )
        .unwrap();
        fs::write(stage.join(STAGE_READY_MARKER), b"offline\n").unwrap();
        write_transaction(&paths, "staged", Some(&stage), None, false).unwrap();

        assert_eq!(
            recover_interrupted_transaction(&paths).unwrap(),
            Some(stage)
        );
    }

    #[test]
    fn interrupted_atomic_switch_restores_old_install_and_reuses_stage() {
        let temp = tempfile::tempdir().unwrap();
        let paths = BootstrapPaths::new(temp.path().to_path_buf());
        fs::create_dir_all(&paths.hermes_home).unwrap();

        // 模拟 old -> backup 已完成、stage -> final 尚未发生时进程退出。
        let backup = paths.unique_sibling("broken-test");
        fs::create_dir_all(&backup).unwrap();
        fs::write(backup.join("old"), b"old").unwrap();
        let stage = paths.unique_sibling("stage-test");
        fs::create_dir_all(&stage).unwrap();
        fs::write(stage.join("pyproject.toml"), b"[project]\n").unwrap();
        fs::write(
            stage.join(".catfish-hermes-version"),
            format!("{}\n", hermes_pinned_commit()),
        )
        .unwrap();
        fs::write(stage.join(STAGE_READY_MARKER), b"offline\n").unwrap();
        write_transaction(&paths, "activating", Some(&stage), Some(&backup), true).unwrap();

        assert_eq!(
            recover_interrupted_transaction(&paths).unwrap(),
            Some(stage.clone())
        );
        assert!(paths.install_dir.join("old").is_file());
        assert!(stage.is_dir());
        let transaction: TransactionRecord =
            serde_json::from_slice(&fs::read(&paths.transaction_file).unwrap()).unwrap();
        assert_eq!(transaction.phase, "staged");
    }

    #[test]
    fn commit_pin_and_version_parser_are_strict() {
        let commit = hermes_pinned_commit();
        assert_eq!(commit.len(), 40);
        assert!(commit.chars().all(|ch| ch.is_ascii_hexdigit()));
        assert!(hermes_pinned_tag().starts_with('v'));
        let text = format!("{}\n{}\n", hermes_pinned_tag(), commit);
        assert_eq!(parse_version_file(&text).as_deref(), Some(commit));
        assert!(parse_version_file("3ef6bbd\n").is_none());
        assert!(parse_version_file(&"z".repeat(40)).is_none());
    }
}
