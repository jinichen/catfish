//! 中断事务的恢复, 以及装机锁。
//!
//! 2026-08-15 从 hermes_install.rs 切出来。纯搬迁, 逻辑一行未改。
//!
//! 它读的是 hermes_install_state.rs 写下的 TransactionRecord: staging 解压完
//! 就复用, 已经切到半成品就把旧版本恢复回来。

use anyhow::{Context, Result};
use fs2::FileExt;
use std::fs::{File, OpenOptions};
use std::io::ErrorKind;
use std::path::PathBuf;

use super::hermes_install_base::{
    report, BootstrapProgressState, ProgressReporter, PROCESS_BOOTSTRAP_LOCK,
};
use super::hermes_install_health::{core_health_problems, source_stage_ready};
use super::hermes_install_state::{
    remove_any, write_transaction, BootstrapPaths, TransactionRecord,
};

/// 崩溃恢复：staging 解压完成就复用；已经切换到半成品则恢复旧版本。
pub(crate) fn recover_interrupted_transaction(paths: &BootstrapPaths) -> Result<Option<PathBuf>> {
    let Ok(bytes) = std::fs::read(&paths.transaction_file) else {
        return Ok(None);
    };
    let transaction: TransactionRecord = match serde_json::from_slice(&bytes) {
        Ok(value) => value,
        Err(error) => {
            log::warn!("bootstrap transaction 损坏，丢弃后重试: {error}");
            remove_any(&paths.transaction_file)?;
            return Ok(None);
        }
    };

    let TransactionRecord {
        phase,
        stage_path,
        backup_path,
        preserve_backup,
        ..
    } = transaction;

    if matches!(phase.as_str(), "extracting" | "staged") {
        if let Some(stage) = stage_path {
            if source_stage_ready(&stage) {
                log::info!("复用上次已完成的 Hermes staging: {}", stage.display());
                return Ok(Some(stage));
            }
            remove_any(&stage)?;
        }
        remove_any(&paths.transaction_file)?;
        return Ok(None);
    }

    if phase == "activating" {
        if let Some(stage) = stage_path.filter(|path| source_stage_ready(path)) {
            // stage 还在 = 新目录尚未 rename 到最终位置。若旧目录已经挪到
            // backup，先恢复它；然后保留 ready stage 供本轮直接重用。
            if !paths.install_dir.exists() {
                if let Some(backup) = &backup_path {
                    if backup.exists() {
                        std::fs::rename(backup, &paths.install_dir).with_context(|| {
                            format!(
                                "恢复切换中断前的 Hermes {} -> {}",
                                backup.display(),
                                paths.install_dir.display()
                            )
                        })?;
                    }
                }
            }
            write_transaction(paths, "staged", Some(&stage), None, false)?;
            return Ok(Some(stage));
        }
        // stage 已消失表示 rename 已完成，只是还没来得及把 transaction 更新为
        // installing；按半成品回滚处理。
    }

    // 新版本若其实已经完整提交，只需清理旧备份和 transaction。
    if core_health_problems(paths, true).is_empty() {
        if let Some(backup) = backup_path {
            if preserve_backup {
                log::warn!("现场残缺 Hermes 已保留用于诊断: {}", backup.display());
            } else {
                remove_any(&backup)?;
            }
        }
        remove_any(&paths.transaction_file)?;
        return Ok(None);
    }

    // activated/installing 阶段中断：最终目录是我们写入的半成品，可以安全移除。
    remove_any(&paths.install_dir)?;
    if let Some(backup) = backup_path {
        if backup.exists() {
            std::fs::rename(&backup, &paths.install_dir).with_context(|| {
                format!(
                    "恢复 Hermes 旧版本 {} -> {}",
                    backup.display(),
                    paths.install_dir.display()
                )
            })?;
        }
    }
    remove_any(&paths.transaction_file)?;
    Ok(None)
}

struct BootstrapLock {
    _process_guard: std::sync::MutexGuard<'static, ()>,
    _file: File,
}

pub(crate) fn acquire_bootstrap_lock(
    paths: &BootstrapPaths,
    reporter: &ProgressReporter<'_>,
) -> Result<BootstrapLock> {
    let process_guard = PROCESS_BOOTSTRAP_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    std::fs::create_dir_all(&paths.hermes_home)
        .with_context(|| format!("创建 {}", paths.hermes_home.display()))?;
    let file = OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .open(&paths.lock_file)
        .with_context(|| format!("打开 bootstrap 锁 {}", paths.lock_file.display()))?;
    match FileExt::try_lock_exclusive(&file) {
        Ok(()) => {}
        Err(error) if error.kind() == ErrorKind::WouldBlock => {
            report(
                reporter,
                "lock",
                BootstrapProgressState::Waiting,
                0,
                0,
                "另一个 Catfish 正在准备运行环境，等待它完成",
                None,
            );
            FileExt::lock_exclusive(&file).context("等待跨进程 Hermes bootstrap 锁")?;
        }
        Err(error) => return Err(error).context("获取跨进程 Hermes bootstrap 锁"),
    }
    Ok(BootstrapLock {
        _process_guard: process_guard,
        _file: file,
    })
}
