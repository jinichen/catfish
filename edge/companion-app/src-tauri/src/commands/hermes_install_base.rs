//! hermes 装机的地基: 常量、进度上报、pinned 版本号。
//!
//! 2026-08-15 从 hermes_install.rs 切出来 (1730 行, 全仓最大)。纯搬迁, 逻辑一行未改。
//!
//! 单独拿出来是为了**理顺依赖方向**: 这几样东西 state/health/recover/steps 全都要用。
//! 如果留在 hermes_install.rs 里, 就变成下面四层反过来 import 前门文件 —— 编译能过,
//! 但谁依赖谁就说不清了。现在是 base ← state ← health ← recover/steps ← hermes_install。

use serde::Serialize;
use std::sync::Mutex;

pub const HERMES_BOOTSTRAP_PROGRESS_EVENT: &str = "hermes-bootstrap-progress";

pub(crate) const BOOTSTRAP_SCHEMA_VERSION: u32 = 1;
pub(crate) const COMPLETION_MARKER: &str = ".catfish-bootstrap-complete.json";
pub(crate) const INSTALL_METHOD_MARKER: &str = ".install_method";
pub(crate) const STAGE_READY_MARKER: &str = ".catfish-stage-ready";
pub(crate) const LOCK_FILE: &str = ".catfish-hermes-bootstrap.lock";
pub(crate) const TRANSACTION_FILE: &str = ".catfish-hermes-bootstrap-transaction.json";
pub(crate) const LAST_ERROR_FILE: &str = ".catfish-hermes-bootstrap-last-error.json";

pub(crate) static PROCESS_BOOTSTRAP_LOCK: Mutex<()> = Mutex::new(());
pub(crate) static LAST_BOOTSTRAP_PROGRESS: Mutex<Option<HermesBootstrapProgress>> = Mutex::new(None);

/// 发送给前端的稳定事件载荷。前端无需解析安装器 stdout。
#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct HermesBootstrapProgress {
    pub phase: String,
    pub state: BootstrapProgressState,
    pub completed_steps: u8,
    pub total_steps: u8,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BootstrapProgressState {
    #[cfg_attr(debug_assertions, allow(dead_code))]
    Queued,
    Waiting,
    Running,
    Completed,
    Skipped,
    Failed,
}

pub(crate) type ProgressReporter<'a> = dyn Fn(HermesBootstrapProgress) + Send + Sync + 'a;

pub(crate) fn report(
    reporter: &ProgressReporter<'_>,
    phase: &str,
    state: BootstrapProgressState,
    completed_steps: u8,
    total_steps: u8,
    message: impl Into<String>,
    error: Option<String>,
) {
    let progress = HermesBootstrapProgress {
        phase: phase.to_owned(),
        state,
        completed_steps,
        total_steps,
        message: message.into(),
        error,
    };
    remember_progress(&progress);
    reporter(progress);
}

pub(crate) fn remember_progress(progress: &HermesBootstrapProgress) {
    let mut last = LAST_BOOTSTRAP_PROGRESS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    *last = Some(progress.clone());
}

/// 编译期与打包脚本共享同一个 pin，避免联网安装悄悄跟随 main。
const HERMES_PINNED_COMMIT_RAW: &str = include_str!("../../../.hermes-git-commit");
const HERMES_PINNED_TAG_RAW: &str = include_str!("../../../.hermes-git-tag");

pub(crate) fn hermes_pinned_commit() -> &'static str {
    HERMES_PINNED_COMMIT_RAW.trim()
}

pub(crate) fn hermes_pinned_tag() -> &'static str {
    HERMES_PINNED_TAG_RAW.trim()
}
