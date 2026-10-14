//! 装完了没有 —— 版本解析、可执行探测、核心健康检查。
//!
//! 2026-08-15 从 hermes_install.rs 切出来。纯搬迁, 逻辑一行未改。
//!
//! 对外的 `hermes_agent_installed()` 仍留在 hermes_install.rs (lib.rs 和
//! hermes.rs 按老路径调它), 它只是这里 core_health_problems 的一层薄封装。

use std::path::Path;

use crate::services::catfish_paths::{hermes_venv_python, hermes_venv_tool};

use super::hermes_install_base::{
    hermes_pinned_commit, BOOTSTRAP_SCHEMA_VERSION, COMPLETION_MARKER, INSTALL_METHOD_MARKER,
    STAGE_READY_MARKER,
};
use super::hermes_install_state::{BootstrapPaths, CompletionRecord};

/// hermes v0.22 的安装戳 (打包时由上游 scripts/write_install_stamp.py 写进源码树)。
pub(crate) const INSTALL_STAMP: &str = "install-stamp.json";

/// 安装戳是不是 "external" (更新归 Companion 管)。不是的话 hermes v0.22 会认领成自更新安装,
/// 每次启动去联网下 Python 3.14 另建环境 —— 健康检查要把它当成"坏了"。
pub(crate) fn stamp_problem(install_dir: &Path) -> Option<String> {
    if install_dir.join(".git").exists() {
        return Some("安装目录里有 .git (hermes v0.22 会把它当成自更新安装)".to_owned());
    }
    let stamp = install_dir.join(INSTALL_STAMP);
    let value: serde_json::Value = match std::fs::read(&stamp).ok().and_then(|b| serde_json::from_slice(&b).ok()) {
        Some(v) => v,
        None => return Some(format!("缺或损坏的 {INSTALL_STAMP}")),
    };
    (value.get("updateMechanism").and_then(|v| v.as_str()) != Some("external"))
        .then(|| format!("{INSTALL_STAMP} 的 updateMechanism 不是 external"))
}

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

    // 9/23: 统一走 background_command (原来这里手写一份 CREATE_NO_WINDOW)
    let mut command = crate::services::process::background_command("git");
    command
        .arg("-C")
        .arg(install_dir)
        .arg("rev-parse")
        .arg("HEAD");
    let output = command.output().ok()?;
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
    // 10/14 (hermes v0.22): 安装戳必须是 external、不许有 .git —— 否则 hermes 会把这里认领成
    // 自更新安装, 每次启动联网下 Python 3.14 另建环境 (Companion 装进去的额外包随之失效)。
    if let Some(problem) = stamp_problem(dir) {
        problems.push(problem);
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stamp_must_be_external_and_no_git() {
        let dir = tempfile::tempdir().unwrap();
        assert!(stamp_problem(dir.path()).unwrap().contains(INSTALL_STAMP));
        std::fs::write(dir.path().join(INSTALL_STAMP), br#"{"updateMechanism":"self"}"#).unwrap();
        assert!(stamp_problem(dir.path()).unwrap().contains("external"));
        std::fs::write(dir.path().join(INSTALL_STAMP), br#"{"schemaVersion":2,"updateMechanism":"external"}"#).unwrap();
        assert_eq!(stamp_problem(dir.path()), None);
        std::fs::create_dir(dir.path().join(".git")).unwrap();
        assert!(stamp_problem(dir.path()).unwrap().contains(".git"));
    }
}
