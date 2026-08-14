//! Codex 后端的路径解析与二进制探测。
//!
//! 2026-08-15 从 codex_backend.rs 切出来 (1112 行超限)。纯搬迁, 逻辑一行未改,
//! 只放宽了跨模块要用的几个的可见性。
//!
//! 这一层是另外三层 (helper / shim / gateway) 共同的地基: 它们都要问
//! "hermes 装在哪、python 在哪"。

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

#[derive(Debug, Clone)]
pub(crate) struct CodexProbe {
    pub(crate) path: PathBuf,
    pub(crate) version_text: String,
    pub(crate) version: (u32, u32, u32),
    pub(crate) logged_in: bool,
}

pub(crate) fn home_dir() -> Result<PathBuf, String> {
    crate::util::paths::home_env()
        .map(PathBuf::from)
        .map_err(|_| "找不到用户主目录".to_string())
}

pub(crate) fn hermes_root() -> Result<PathBuf, String> {
    if let Some(value) = std::env::var_os("HERMES_HOME") {
        return Ok(PathBuf::from(value));
    }
    Ok(home_dir()?.join(".hermes"))
}

pub(crate) fn hermes_agent_root() -> Result<PathBuf, String> {
    Ok(hermes_root()?.join("hermes-agent"))
}

pub(crate) fn hermes_python() -> Result<PathBuf, String> {
    let root = hermes_agent_root()?;
    let candidates = [
        root.join("venv/bin/python"),
        root.join("venv/bin/python3"),
        root.join("venv/Scripts/python.exe"),
    ];
    candidates
        .into_iter()
        .find(|path| path.is_file())
        .ok_or_else(|| "Hermes 运行时尚未安装".to_string())
}

fn codex_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(value) = std::env::var_os("CODEX_BINARY") {
        candidates.push(PathBuf::from(value));
    }
    if let Some(path) = std::env::var_os("PATH") {
        for dir in std::env::split_paths(&path) {
            candidates.push(dir.join(if cfg!(windows) { "codex.exe" } else { "codex" }));
        }
    }
    candidates.extend([
        PathBuf::from("/Applications/ChatGPT.app/Contents/Resources/codex"),
        PathBuf::from("/opt/homebrew/bin/codex"),
        PathBuf::from("/usr/local/bin/codex"),
    ]);
    if let Ok(home) = home_dir() {
        candidates.push(home.join("Applications/ChatGPT.app/Contents/Resources/codex"));
        candidates.push(home.join(".local/bin/codex"));
        candidates.push(home.join(".npm-global/bin/codex"));
        candidates.push(home.join("AppData/Roaming/npm/codex.cmd"));
    }

    let mut seen = HashSet::new();
    candidates
        .into_iter()
        .filter(|path| seen.insert(path.clone()))
        .collect()
}

fn parse_version(text: &str) -> Option<(u32, u32, u32)> {
    let re = regex::Regex::new(r"(\d+)\.(\d+)\.(\d+)").ok()?;
    let captures = re.captures(text)?;
    Some((
        captures.get(1)?.as_str().parse().ok()?,
        captures.get(2)?.as_str().parse().ok()?,
        captures.get(3)?.as_str().parse().ok()?,
    ))
}

fn command_output(binary: &Path, args: &[&str]) -> Option<Output> {
    Command::new(binary)
        .args(args)
        .stdin(Stdio::null())
        .output()
        .ok()
}

pub(crate) fn probe_codex() -> Option<CodexProbe> {
    for path in codex_candidates() {
        if !path.is_file() {
            continue;
        }
        let Some(output) = command_output(&path, &["--version"]) else {
            continue;
        };
        if !output.status.success() {
            continue;
        }
        let version_text = String::from_utf8_lossy(&output.stdout).trim().to_string();
        let Some(version) = parse_version(&version_text) else {
            continue;
        };
        let logged_in = command_output(&path, &["login", "status"])
            .filter(|value| value.status.success())
            .map(|value| {
                let combined = format!(
                    "{}\n{}",
                    String::from_utf8_lossy(&value.stdout),
                    String::from_utf8_lossy(&value.stderr)
                )
                .to_lowercase();
                combined.contains("logged in")
            })
            .unwrap_or(false);
        return Some(CodexProbe {
            path,
            version_text,
            version,
            logged_in,
        });
    }
    None
}

#[cfg(test)]
mod tests {
    use super::parse_version;

    #[test]
    fn parses_codex_version_with_suffix() {
        assert_eq!(
            parse_version("codex-cli 0.146.0-alpha.9.2"),
            Some((0, 146, 0))
        );
    }

    #[test]
    fn rejects_unparseable_version() {
        assert_eq!(parse_version("codex nightly"), None);
    }
}
