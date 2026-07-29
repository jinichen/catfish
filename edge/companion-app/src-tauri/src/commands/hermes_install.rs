//! Hermes Agent 首次装机与版本对齐。
//!
//! 首启引导遵守四条约束：
//! 1. Tauri `setup` 只派发后台任务，不等待数百 MB 归档解压和依赖安装；
//! 2. 进程内 mutex + 跨进程文件锁保证同一时刻只有一个安装器；
//! 3. 只有完整健康检查通过后才原子写完成标记，不能再凭 `pyproject.toml` 猜完成；
//! 4. 源码先落在 `~/.hermes` 同盘 staging，随后 rename 到最终位置。安装失败或
//!    进程中断时恢复旧版本，避免 `/tmp` 解压后再 `cp -R` 六万多个文件。

use anyhow::{Context, Result};
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use std::fs::{File, OpenOptions};
use std::io::{ErrorKind, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::Emitter;

pub const HERMES_BOOTSTRAP_PROGRESS_EVENT: &str = "hermes-bootstrap-progress";

const BOOTSTRAP_SCHEMA_VERSION: u32 = 1;
const COMPLETION_MARKER: &str = ".catfish-bootstrap-complete.json";
const INSTALL_METHOD_MARKER: &str = ".install_method";
const STAGE_READY_MARKER: &str = ".catfish-stage-ready";
const LOCK_FILE: &str = ".catfish-hermes-bootstrap.lock";
const TRANSACTION_FILE: &str = ".catfish-hermes-bootstrap-transaction.json";
const LAST_ERROR_FILE: &str = ".catfish-hermes-bootstrap-last-error.json";

static PROCESS_BOOTSTRAP_LOCK: Mutex<()> = Mutex::new(());
static LAST_BOOTSTRAP_PROGRESS: Mutex<Option<HermesBootstrapProgress>> = Mutex::new(None);

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
    Queued,
    Waiting,
    Running,
    Completed,
    Skipped,
    Failed,
}

type ProgressReporter<'a> = dyn Fn(HermesBootstrapProgress) + Send + Sync + 'a;

fn report(
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

fn remember_progress(progress: &HermesBootstrapProgress) {
    let mut last = LAST_BOOTSTRAP_PROGRESS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    *last = Some(progress.clone());
}

/// 编译期与打包脚本共享同一个 pin，避免联网安装悄悄跟随 main。
const HERMES_PINNED_COMMIT_RAW: &str = include_str!("../../../.hermes-git-commit");
const HERMES_PINNED_TAG_RAW: &str = include_str!("../../../.hermes-git-tag");

fn hermes_pinned_commit() -> &'static str {
    HERMES_PINNED_COMMIT_RAW.trim()
}

fn hermes_pinned_tag() -> &'static str {
    HERMES_PINNED_TAG_RAW.trim()
}

const RUNTIME_ARCHIVES: [&str; 4] = [
    "cpython-3.11.15-embed.tar.gz",
    "hermes-agent-bundle.tar.gz",
    "node-embed.tar.gz",
    "chromium-embed.tar.gz",
];

#[derive(Clone, Debug)]
struct RuntimeArtifacts {
    dir: PathBuf,
    install_sh: PathBuf,
    uv: PathBuf,
    python_tar: Option<PathBuf>,
    hermes_tar: Option<PathBuf>,
    node_tar: Option<PathBuf>,
    chromium_tar: Option<PathBuf>,
}

impl RuntimeArtifacts {
    fn from_dir(dir: PathBuf) -> Self {
        Self {
            install_sh: dir.join("install.sh"),
            uv: dir.join("uv"),
            python_tar: usable_artifact(&dir.join(RUNTIME_ARCHIVES[0])),
            hermes_tar: usable_artifact(&dir.join(RUNTIME_ARCHIVES[1])),
            node_tar: usable_artifact(&dir.join(RUNTIME_ARCHIVES[2])),
            chromium_tar: usable_artifact(&dir.join(RUNTIME_ARCHIVES[3])),
            dir,
        }
    }

    fn archive_count(&self) -> usize {
        [
            &self.python_tar,
            &self.hermes_tar,
            &self.node_tar,
            &self.chromium_tar,
        ]
        .into_iter()
        .filter(|p| p.is_some())
        .count()
    }

    fn is_complete_bundle(&self) -> bool {
        self.archive_count() == RUNTIME_ARCHIVES.len()
    }

    fn validate_bootstrap_tools(&self) -> Result<()> {
        for path in [&self.install_sh, &self.uv] {
            let meta = std::fs::metadata(path)
                .with_context(|| format!("运行时缺文件: {}", path.display()))?;
            if meta.len() <= 1024 {
                anyhow::bail!(
                    "运行时文件过小 (<= 1KB，可能仍是 placeholder): {}",
                    path.display()
                );
            }
        }
        Ok(())
    }
}

fn usable_artifact(path: &Path) -> Option<PathBuf> {
    std::fs::metadata(path)
        .ok()
        .filter(|m| m.is_file() && m.len() > 1024)
        .map(|_| path.to_path_buf())
}

/// 选择运行时时按“归档完整度”排序，而不是无条件偏爱 App bundle。
///
/// 旧逻辑看到 bundle 内 `install.sh + uv` 就立即返回，即使它是 0/4；结果会
/// 永远忽略 `~/.catfish/runtime` 中用户已经准备好的 4/4 离线包。
fn resolve_runtime_dir_for_home(resource_dir: &Path, home: Option<&Path>) -> Result<PathBuf> {
    let bundle = resource_dir.join("resources").join("mac");
    let external = home.map(|h| h.join(".catfish").join("runtime"));
    let mut candidates: Vec<(usize, usize, PathBuf)> = Vec::new();
    let mut tried = Vec::new();

    // 第二个排序字段是 tie-break：完整度相同时沿用 bundle 优先，保持兼容。
    for (bundle_preference, candidate) in [(1usize, Some(bundle)), (0usize, external)] {
        let Some(candidate) = candidate else {
            continue;
        };
        let artifacts = RuntimeArtifacts::from_dir(candidate.clone());
        let valid = usable_artifact(&artifacts.install_sh).is_some()
            && usable_artifact(&artifacts.uv).is_some();
        tried.push(format!(
            "{} (tools={}, archives={}/4)",
            candidate.display(),
            if valid { "ok" } else { "missing" },
            artifacts.archive_count()
        ));
        if valid {
            candidates.push((artifacts.archive_count(), bundle_preference, candidate));
        }
    }

    candidates.sort_by(|a, b| (b.0, b.1).cmp(&(a.0, a.1)));
    if let Some((archive_count, _, selected)) = candidates.into_iter().next() {
        log::info!(
            "[runtime] 选择 {} · 离线归档 {archive_count}/4",
            selected.display()
        );
        return Ok(selected);
    }

    anyhow::bail!(
        "找不到有效运行时目录 (需要 install.sh + uv > 1KB)。已尝试:\n  {}",
        tried.join("\n  ")
    )
}

fn resolve_runtime_dir(resource_dir: &Path) -> Result<PathBuf> {
    let home = crate::util::paths::home_env().ok().map(PathBuf::from);
    resolve_runtime_dir_for_home(resource_dir, home.as_deref())
}

#[derive(Clone, Debug)]
struct BootstrapPaths {
    home: PathBuf,
    hermes_home: PathBuf,
    install_dir: PathBuf,
    lock_file: PathBuf,
    transaction_file: PathBuf,
    last_error_file: PathBuf,
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
struct CompletionRecord {
    schema_version: u32,
    commit: String,
    tag: String,
    installed_at_unix: u64,
    installer: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct TransactionRecord {
    schema_version: u32,
    phase: String,
    stage_path: Option<PathBuf>,
    backup_path: Option<PathBuf>,
    #[serde(default)]
    preserve_backup: bool,
    updated_at_unix: u64,
}

#[derive(Debug, Serialize)]
struct FailureRecord {
    schema_version: u32,
    failed_at_unix: u64,
    error: String,
}

fn unix_timestamp() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn write_bytes_atomic(path: &Path, bytes: &[u8]) -> Result<()> {
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

fn write_json_atomic(path: &Path, value: &impl Serialize) -> Result<()> {
    let mut bytes = serde_json::to_vec_pretty(value).context("序列化 bootstrap 状态")?;
    bytes.push(b'\n');
    write_bytes_atomic(path, &bytes)
}

fn write_transaction(
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

fn write_completion_marker(install_dir: &Path) -> Result<()> {
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

fn record_failure(paths: &BootstrapPaths, error: &anyhow::Error) {
    let _ = write_json_atomic(
        &paths.last_error_file,
        &FailureRecord {
            schema_version: BOOTSTRAP_SCHEMA_VERSION,
            failed_at_unix: unix_timestamp(),
            error: format!("{error:#}"),
        },
    );
}

fn parse_version_file(s: &str) -> Option<String> {
    s.lines()
        .map(str::trim)
        .find(|line| line.len() == 40 && line.chars().all(|ch| ch.is_ascii_hexdigit()))
        .map(str::to_owned)
}

fn installed_hermes_commit_at(install_dir: &Path) -> Option<String> {
    if let Ok(contents) = std::fs::read_to_string(install_dir.join(".catfish-hermes-version")) {
        if let Some(commit) = parse_version_file(&contents) {
            return Some(commit);
        }
    }

    let output = Command::new("git")
        .arg("-C")
        .arg(install_dir)
        .arg("rev-parse")
        .arg("HEAD")
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let commit = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    (commit.len() == 40 && commit.chars().all(|ch| ch.is_ascii_hexdigit())).then_some(commit)
}

fn executable_exists(path: &Path) -> bool {
    let Ok(meta) = std::fs::metadata(path) else {
        return false;
    };
    if !meta.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        meta.permissions().mode() & 0o111 != 0
    }
    #[cfg(not(unix))]
    {
        true
    }
}

fn core_health_problems(paths: &BootstrapPaths, require_completion_marker: bool) -> Vec<String> {
    let dir = &paths.install_dir;
    let mut problems = Vec::new();

    if !dir.is_dir() {
        problems.push(format!("安装目录不存在: {}", dir.display()));
        return problems;
    }
    if !dir.join("pyproject.toml").is_file() {
        problems.push("缺 pyproject.toml".to_owned());
    }
    for (label, executable) in [
        ("Python", dir.join("venv/bin/python")),
        ("Hermes CLI", dir.join("venv/bin/hermes")),
    ] {
        if !executable_exists(&executable) {
            problems.push(format!(
                "{label} 不存在或不可执行: {}",
                executable.display()
            ));
        }
    }
    match std::fs::read_to_string(dir.join(INSTALL_METHOD_MARKER)) {
        Ok(value) if !value.trim().is_empty() => {}
        _ => problems.push(format!("缺或空的 {INSTALL_METHOD_MARKER}")),
    }
    match installed_hermes_commit_at(dir) {
        Some(commit) if commit == hermes_pinned_commit() => {}
        Some(commit) => problems.push(format!(
            "版本不匹配: 已装 {commit}, 需要 {}",
            hermes_pinned_commit()
        )),
        None => problems.push("无法确认已装 Hermes commit".to_owned()),
    }

    if require_completion_marker {
        let marker_path = dir.join(COMPLETION_MARKER);
        match std::fs::read(&marker_path)
            .ok()
            .and_then(|bytes| serde_json::from_slice::<CompletionRecord>(&bytes).ok())
        {
            Some(marker)
                if marker.schema_version == BOOTSTRAP_SCHEMA_VERSION
                    && marker.commit == hermes_pinned_commit() => {}
            Some(marker) => problems.push(format!(
                "完成标记版本无效: schema={}, commit={}",
                marker.schema_version, marker.commit
            )),
            None => problems.push(format!("缺或损坏的完成标记 {COMPLETION_MARKER}")),
        }
    }

    problems
}

/// 严格安装判断。`pyproject.toml` 单独存在不再代表安装成功。
pub fn hermes_agent_installed() -> bool {
    let Ok(home) = crate::util::paths::home_env() else {
        return false;
    };
    core_health_problems(&BootstrapPaths::new(PathBuf::from(home)), true).is_empty()
}

fn source_stage_ready(stage: &Path) -> bool {
    stage.join(STAGE_READY_MARKER).is_file()
        && stage.join("pyproject.toml").is_file()
        && installed_hermes_commit_at(stage).as_deref() == Some(hermes_pinned_commit())
}

fn remove_any(path: &Path) -> Result<()> {
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

/// 崩溃恢复：staging 解压完成就复用；已经切换到半成品则恢复旧版本。
fn recover_interrupted_transaction(paths: &BootstrapPaths) -> Result<Option<PathBuf>> {
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

fn acquire_bootstrap_lock(
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

fn command_status(mut command: Command, description: &str) -> Result<()> {
    let status = command
        .status()
        .with_context(|| format!("启动 {description}"))?;
    if !status.success() {
        anyhow::bail!("{description} 失败: {status}");
    }
    Ok(())
}

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

fn run_install_stage(
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

fn prepare_source_stage(
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
            .arg(&stage)
            .arg("--strip-components=1");
        command_status(command, "解压 hermes-agent-bundle.tar.gz")?;
        if !stage.join("pyproject.toml").is_file() {
            anyhow::bail!("离线 staging 缺 pyproject.toml: {}", stage.display());
        }
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
struct PreviousInstall {
    path: PathBuf,
    /// 残缺安装可能包含现场诊断线索，成功后也保留为 `.broken-*`。
    preserve: bool,
}

fn activate_stage(
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

fn rollback_install(paths: &BootstrapPaths, backup: Option<&PreviousInstall>) -> Result<()> {
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

fn link_catfish_email_bin(paths: &BootstrapPaths) -> Result<()> {
    let venv_bin = paths.install_dir.join("venv/bin/catfish-email");
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

fn bootstrap_locked(
    resource_dir: &Path,
    paths: &BootstrapPaths,
    reporter: &ProgressReporter<'_>,
) -> Result<()> {
    let reusable_stage = recover_interrupted_transaction(paths)?;
    let current_health = core_health_problems(paths, true);
    if current_health.is_empty() {
        let _ = link_catfish_email_bin(paths);
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
        link_catfish_email_bin(paths)?;
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

/// 保留同步 API，供测试和非 UI 调用复用；Tauri setup 不再直接调用它。
pub fn ensure_hermes_installed(resource_dir: &Path) -> Result<()> {
    ensure_hermes_installed_with_reporter(resource_dir, &|_| {})
}

/// Tauri setup 使用：立即返回，所有磁盘/网络工作在后台线程完成。
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
        make_executable(&dir.join("venv/bin/python"));
        make_executable(&dir.join("venv/bin/hermes"));
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
