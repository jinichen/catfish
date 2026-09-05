use serde_yaml::{Mapping, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::services::catfish_paths::hermes_venv_tool;

use super::expert_bots_config::{atomic_write, parse_yaml_root, read_optional};
use super::expert_bots_types::{is_valid_profile_name, MANAGED_PROFILE_MARKER};

const MANAGED_PROFILE_VERSION: &str = "expert-bot-v2";
pub const DEFAULT_EXPERT_SOUL: &str = r#"# Catfish 专家 Bot

你是小鲶背后的专业协作者，通过小鲶为员工提供判断和草稿。

- 只根据本轮输入、获准工具结果和明确提供的工作上下文行动。
- 历史记忆是线索，不是当前事实；涉及状态和截止日期时必须重新核验。
- 沿用员工当前选择的模型以及 Catfish 传入的身份、部门和权限。
- 未经员工明确授权，不发送消息、不修改外部系统、不扩大任务范围。
- 输出给小鲶整合，不做群聊式寒暄，不虚构来源。
"#;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HermesProfileInfo {
    pub id: String,
    pub display_name: String,
    pub description: String,
    pub configured_model: Option<String>,
    pub provider: Option<String>,
    pub skill_count: usize,
    pub managed_by_companion: bool,
    pub has_api_server_key: bool,
}

fn yaml_string(root: &Value, path: &[&str]) -> Option<String> {
    let mut current = root;
    for key in path {
        current = current.get(*key)?;
    }
    current.as_str().map(str::to_string)
}

fn count_skills(profile_dir: &Path) -> usize {
    let skill_dir = profile_dir.join("skills");
    fs::read_dir(skill_dir)
        .ok()
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
        .filter(|entry| entry.path().is_dir())
        .count()
}

pub fn env_value(raw: &str, wanted: &str) -> Option<String> {
    raw.lines().find_map(|line| {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            return None;
        }
        let (key, value) = line.split_once('=')?;
        if key.trim() != wanted {
            return None;
        }
        Some(value.trim().trim_matches(['\'', '"']).to_string())
    })
}

fn usable_api_server_key(profile_dir: &Path) -> bool {
    let Ok(raw) = read_optional(&profile_dir.join(".env")) else {
        return false;
    };
    let Some(value) = env_value(&raw, "API_SERVER_KEY") else {
        return false;
    };
    value.len() >= 16
        && !value.to_ascii_lowercase().contains("change-me")
        && !value.to_ascii_lowercase().contains("placeholder")
}

fn read_profile_info(id: &str, dir: &Path) -> HermesProfileInfo {
    let meta = parse_yaml_root(&read_optional(&dir.join("profile.yaml")).unwrap_or_default(), "profile.yaml")
        .unwrap_or_else(|_| Value::Mapping(Mapping::new()));
    let config = parse_yaml_root(&read_optional(&dir.join("config.yaml")).unwrap_or_default(), "config.yaml")
        .unwrap_or_else(|_| Value::Mapping(Mapping::new()));
    HermesProfileInfo {
        id: id.to_string(),
        display_name: yaml_string(&meta, &["display_name"]).unwrap_or_else(|| id.to_string()),
        description: yaml_string(&meta, &["description"]).unwrap_or_default(),
        configured_model: yaml_string(&config, &["model", "default"]),
        provider: yaml_string(&config, &["model", "provider"]),
        skill_count: count_skills(dir),
        managed_by_companion: dir.join(MANAGED_PROFILE_MARKER).is_file(),
        has_api_server_key: usable_api_server_key(dir),
    }
}

pub fn list_profiles(hermes_home: &Path) -> Result<Vec<HermesProfileInfo>, String> {
    let root = hermes_home.join("profiles");
    let mut profiles = Vec::new();
    let entries = match fs::read_dir(&root) {
        Ok(entries) => entries,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(profiles),
        Err(e) => return Err(format!("读取 Hermes Profiles 失败: {e}")),
    };
    for entry in entries.filter_map(Result::ok) {
        let path = entry.path();
        let id = entry.file_name().to_string_lossy().to_string();
        let tombstoned = root.join(".deleted").join(&id).exists();
        if path.is_dir() && is_valid_profile_name(&id) && !tombstoned {
            profiles.push(read_profile_info(&id, &path));
        }
    }
    profiles.sort_by(|a, b| a.id.cmp(&b.id));
    Ok(profiles)
}

fn hide_console(command: &mut Command) {
    #[cfg(not(windows))]
    let _ = command;
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
}

pub fn run_hermes(hermes_home: &Path, args: &[&str]) -> Result<(), String> {
    let install_dir = hermes_home.join("hermes-agent");
    let hermes = hermes_venv_tool(&install_dir, "hermes");
    if !hermes.is_file() {
        return Err(format!("Hermes CLI 不存在: {}", hermes.display()));
    }
    let mut command = Command::new(&hermes);
    command.args(args).env("HERMES_HOME", hermes_home);
    hide_console(&mut command);
    let output = command
        .output()
        .map_err(|e| format!("运行 Hermes 失败: {e}"))?;
    if output.status.success() {
        return Ok(());
    }
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let detail = if stderr.is_empty() { stdout } else { stderr };
    Err(format!("Hermes 命令失败 ({}): {detail}", output.status))
}

pub fn profile_dir(hermes_home: &Path, id: &str) -> Result<PathBuf, String> {
    if !is_valid_profile_name(id) {
        return Err(format!("Profile id 不合法: {id}"));
    }
    Ok(hermes_home.join("profiles").join(id))
}

pub fn create_profile(
    hermes_home: &Path,
    id: &str,
    clone_from: &str,
    description: &str,
) -> Result<PathBuf, String> {
    let dir = profile_dir(hermes_home, id)?;
    if dir.exists() {
        return Err(format!("Hermes Profile {id} 已存在"));
    }
    let source = if clone_from.trim().is_empty() { "default" } else { clone_from.trim() };
    if source != "default" && !is_valid_profile_name(source) {
        return Err(format!("来源 Profile id 不合法: {source}"));
    }
    run_hermes(
        hermes_home,
        &[
            "profile",
            "create",
            id,
            "--clone-from",
            source,
            "--no-alias",
            "--description",
            description,
        ],
    )?;
    if !dir.is_dir() {
        return Err(format!("Hermes 报告创建成功，但 Profile 目录不存在: {id}"));
    }
    atomic_write(&dir.join(MANAGED_PROFILE_MARKER), &format!("{MANAGED_PROFILE_VERSION}\n"))?;
    Ok(dir)
}

fn set_mapping_string(root: &mut Value, key: &str, value: String) -> Result<(), String> {
    let mapping = root
        .as_mapping_mut()
        .ok_or_else(|| "profile.yaml 顶层必须是 mapping".to_string())?;
    mapping.insert(Value::String(key.to_string()), Value::String(value));
    Ok(())
}

pub fn update_profile_metadata(
    dir: &Path,
    display_name: Option<&str>,
    description: Option<&str>,
) -> Result<(), String> {
    let path = dir.join("profile.yaml");
    let mut root = parse_yaml_root(&read_optional(&path)?, "profile.yaml")?;
    if let Some(value) = display_name {
        let value = value.trim();
        if value.chars().count() > 64 {
            return Err("Bot 名称不能超过 64 个字符".to_string());
        }
        set_mapping_string(&mut root, "display_name", value.to_string())?;
    }
    if let Some(value) = description {
        let value = value.trim();
        if value.chars().count() > 500 {
            return Err("职责说明不能超过 500 个字符".to_string());
        }
        set_mapping_string(&mut root, "description", value.to_string())?;
        let mapping = root.as_mapping_mut().expect("validated mapping");
        mapping.insert(Value::String("description_auto".to_string()), Value::Bool(false));
    }
    let serialized = serde_yaml::to_string(&root).map_err(|e| format!("序列化 profile.yaml 失败: {e}"))?;
    atomic_write(&path, &serialized)
}

pub fn update_profile_soul(dir: &Path, soul: &str) -> Result<(), String> {
    if soul.chars().count() > 50_000 {
        return Err("Bot 人设内容过长".to_string());
    }
    atomic_write(&dir.join("SOUL.md"), soul)
}

pub fn delete_managed_profile(hermes_home: &Path, id: &str) -> Result<(), String> {
    let dir = profile_dir(hermes_home, id)?;
    if !dir.join(MANAGED_PROFILE_MARKER).is_file() {
        return Err(format!("Profile {id} 不归 Companion 管理，不能删除"));
    }
    run_hermes(hermes_home, &["profile", "delete", id, "--yes"])
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn lists_only_valid_profile_directories_without_secrets() {
        let dir = tempdir().unwrap();
        let profile = dir.path().join("profiles/finance");
        fs::create_dir_all(profile.join("skills/a")).unwrap();
        fs::write(profile.join("SOUL.md"), "hello").unwrap();
        fs::write(profile.join("profile.yaml"), "display_name: 财务专家\ndescription: 审预算\n").unwrap();
        fs::write(profile.join("config.yaml"), "model:\n  default: m1\n  api_key: secret\n").unwrap();
        fs::write(profile.join(".env"), "API_SERVER_KEY=0123456789abcdef\nSECRET=x\n").unwrap();
        let bad = dir.path().join("profiles/../bad");
        fs::create_dir_all(&bad).unwrap();
        let minimal = dir.path().join("profiles/minimal");
        fs::create_dir_all(&minimal).unwrap();

        let profiles = list_profiles(dir.path()).unwrap();
        assert_eq!(profiles.len(), 2);
        assert_eq!(profiles[0].display_name, "财务专家");
        assert_eq!(profiles[0].configured_model.as_deref(), Some("m1"));
        assert!(profiles[0].has_api_server_key);
    }

    #[test]
    fn metadata_update_preserves_unknown_keys() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("profile.yaml"), "distribution: keep\ndescription: old\n").unwrap();
        update_profile_metadata(dir.path(), Some("新名称"), Some("新职责")).unwrap();
        let root: Value = serde_yaml::from_str(&fs::read_to_string(dir.path().join("profile.yaml")).unwrap()).unwrap();
        assert_eq!(root["distribution"].as_str(), Some("keep"));
        assert_eq!(root["display_name"].as_str(), Some("新名称"));
        assert_eq!(root["description_auto"].as_bool(), Some(false));
    }

    #[test]
    fn unmanaged_profile_cannot_be_deleted() {
        let dir = tempdir().unwrap();
        fs::create_dir_all(dir.path().join("profiles/external")).unwrap();
        assert!(delete_managed_profile(dir.path(), "external").unwrap_err().contains("不能删除"));
    }
}
