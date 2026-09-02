//! 装完了没有 —— 版本解析、可执行探测、核心健康检查。
//!
//! 2026-08-15 从 hermes_install.rs 切出来。纯搬迁, 逻辑一行未改。
//!
//! 对外的 `hermes_agent_installed()` 仍留在 hermes_install.rs (lib.rs 和
//! hermes.rs 按老路径调它), 它只是这里 core_health_problems 的一层薄封装。

use std::path::Path;
use std::process::Command;

use crate::services::catfish_paths::{hermes_venv_python, hermes_venv_tool};

use super::hermes_install_base::{
    hermes_pinned_commit, BOOTSTRAP_SCHEMA_VERSION, COMPLETION_MARKER, INSTALL_METHOD_MARKER,
    STAGE_READY_MARKER,
};
use super::hermes_install_state::{BootstrapPaths, CompletionRecord};

pub(crate) fn parse_version_file(s: &str) -> Option<String> {
    s.lines()
        .map(str::trim)
        .find(|line| line.len() == 40 && line.chars().all(|ch| ch.is_ascii_hexdigit()))
        .map(str::to_owned)
}

pub(crate) fn installed_hermes_commit_at(install_dir: &Path) -> Option<String> {
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

pub(crate) fn core_health_problems(paths: &BootstrapPaths, require_completion_marker: bool) -> Vec<String> {
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
        ("Python", hermes_venv_python(dir)),
        ("Hermes CLI", hermes_venv_tool(dir, "hermes")),
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

pub(crate) fn source_stage_ready(stage: &Path) -> bool {
    stage.join(STAGE_READY_MARKER).is_file()
        && stage.join("pyproject.toml").is_file()
        && installed_hermes_commit_at(stage).as_deref() == Some(hermes_pinned_commit())
}
