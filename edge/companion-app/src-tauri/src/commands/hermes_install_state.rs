//! 装机状态的落盘: 路径、三种记录、原子写、以及删除。
//!
//! 2026-08-15 从 hermes_install.rs 切出来。纯搬迁, 逻辑一行未改。
//!
//! 这个文件是"装到一半断电"这件事的正确性所在: 写什么 (CompletionRecord /
//! TransactionRecord / FailureRecord)、怎么写 (先写临时文件再 rename, 见
//! write_bytes_atomic)、以及装成功后怎么清理 (remove_any)。
//! 恢复逻辑本身在 hermes_install_recover.rs, 它读的就是这里写下的东西。

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::fs::File;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use super::hermes_install_base::{
    hermes_pinned_commit, hermes_pinned_tag, BOOTSTRAP_SCHEMA_VERSION, COMPLETION_MARKER,
    LAST_ERROR_FILE, LOCK_FILE, TRANSACTION_FILE,
};

#[derive(Clone, Debug)]
pub(crate) struct BootstrapPaths {
    pub(crate) home: PathBuf,
    pub(crate) hermes_home: PathBuf,
    pub(crate) install_dir: PathBuf,
    pub(crate) lock_file: PathBuf,
    pub(crate) transaction_file: PathBuf,
    pub(crate) last_error_file: PathBuf,
}

impl BootstrapPaths {
    fn new(home: PathBuf) -> Self {
        let hermes_home = home.join(".hermes");
        Self {
            install_dir: hermes_home.join("hermes-agent"),
            lock_file: hermes_home.join(LOCK_FILE),
            transaction_file: hermes_home.join(TRANSACTION_FILE),
            last_error_file: hermes_home.join(LAST_ERROR_FILE),
            home,
            hermes_home,
        }
    }

    fn unique_sibling(&self, label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        self.hermes_home.join(format!(
            ".catfish-hermes-{label}-{}-{nanos}",
            std::process::id()
        ))
    }
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct CompletionRecord {
    pub(crate) schema_version: u32,
    pub(crate) commit: String,
    pub(crate) tag: String,
    pub(crate) installed_at_unix: u64,
    pub(crate) installer: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct TransactionRecord {
    pub(crate) schema_version: u32,
    pub(crate) phase: String,
    pub(crate) stage_path: Option<PathBuf>,
    pub(crate) backup_path: Option<PathBuf>,
    #[serde(default)]
    pub(crate) preserve_backup: bool,
    pub(crate) updated_at_unix: u64,
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct FailureRecord {
    pub(crate) schema_version: u32,
    pub(crate) failed_at_unix: u64,
    pub(crate) error: String,
}

fn unix_timestamp() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

pub(crate) fn write_bytes_atomic(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path
        .parent()
        .with_context(|| format!("{} 没有父目录", path.display()))?;
    std::fs::create_dir_all(parent).with_context(|| format!("创建目录 {}", parent.display()))?;
    let temp = parent.join(format!(
        ".{}.tmp-{}",
        path.file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("catfish-bootstrap"),
        std::process::id()
    ));
    let mut file =
        File::create(&temp).with_context(|| format!("创建临时状态文件 {}", temp.display()))?;
    file.write_all(bytes)
        .with_context(|| format!("写临时状态文件 {}", temp.display()))?;
    file.sync_all()?;
    // Unix rename 可原子覆盖；transaction 更新不能先删旧文件，否则两步之间断电
    // 会同时失去恢复信息。Windows bootstrap 不走本模块，但保留可编译 fallback。
    #[cfg(windows)]
    if path.exists() {
        std::fs::remove_file(path).with_context(|| format!("替换旧状态文件 {}", path.display()))?;
    }
    std::fs::rename(&temp, path).with_context(|| format!("提交状态文件 {}", path.display()))?;
    Ok(())
}

pub(crate) fn write_json_atomic(path: &Path, value: &impl Serialize) -> Result<()> {
    let mut bytes = serde_json::to_vec_pretty(value).context("序列化 bootstrap 状态")?;
    bytes.push(b'\n');
    write_bytes_atomic(path, &bytes)
}

pub(crate) fn write_transaction(
    paths: &BootstrapPaths,
    phase: &str,
    stage_path: Option<&Path>,
    backup_path: Option<&Path>,
    preserve_backup: bool,
) -> Result<()> {
    write_json_atomic(
        &paths.transaction_file,
        &TransactionRecord {
            schema_version: BOOTSTRAP_SCHEMA_VERSION,
            phase: phase.to_owned(),
            stage_path: stage_path.map(Path::to_path_buf),
            backup_path: backup_path.map(Path::to_path_buf),
            preserve_backup,
            updated_at_unix: unix_timestamp(),
        },
    )
}

pub(crate) fn write_completion_marker(install_dir: &Path) -> Result<()> {
    write_json_atomic(
        &install_dir.join(COMPLETION_MARKER),
        &CompletionRecord {
            schema_version: BOOTSTRAP_SCHEMA_VERSION,
            commit: hermes_pinned_commit().to_owned(),
            tag: hermes_pinned_tag().to_owned(),
            installed_at_unix: unix_timestamp(),
            installer: "catfish-companion-bootstrap-v2".to_owned(),
        },
    )
}

pub(crate) fn record_failure(paths: &BootstrapPaths, error: &anyhow::Error) {
    let _ = write_json_atomic(
        &paths.last_error_file,
        &FailureRecord {
            schema_version: BOOTSTRAP_SCHEMA_VERSION,
            failed_at_unix: unix_timestamp(),
            error: format!("{error:#}"),
        },
    );
}

pub(crate) fn remove_any(path: &Path) -> Result<()> {
    let Ok(meta) = std::fs::symlink_metadata(path) else {
        return Ok(());
    };
    if meta.is_dir() && !meta.file_type().is_symlink() {
        std::fs::remove_dir_all(path).with_context(|| format!("删除目录 {}", path.display()))?;
    } else {
        std::fs::remove_file(path).with_context(|| format!("删除文件 {}", path.display()))?;
    }
    Ok(())
}
