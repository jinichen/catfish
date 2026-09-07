//! Wiki action bindings.
//!
//! Wiki frontmatter keeps only stable action ids.  The executable mapping lives
//! in a local, declarative registry so knowledge data does not become coupled to
//! a model, a platform, or an implementation-specific tool name.

use serde::{Deserialize, Serialize};
use serde_json::json;
use std::fs;
use std::path::PathBuf;
use std::time::Duration;

use super::tool_bridge::ToolCallResult;
use super::wiki_slug::catfish_home;

const REGISTRY_ENV: &str = "CATFISH_ACTION_REGISTRY";
const REGISTRY_FILE: &str = "action-registry.yaml";
const BUILTIN_REGISTRY: &str = include_str!("../../../../contracts/action_registry.default.yaml");

#[derive(Debug, Clone, Deserialize)]
struct ActionRegistryFile {
    #[serde(default = "registry_version")]
    version: u32,
    #[serde(default)]
    actions: Vec<ActionDefinition>,
}

fn registry_version() -> u32 {
    1
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActionDefinition {
    pub id: String,
    pub label: String,
    #[serde(default)]
    pub description: String,
    pub executor: String,
    #[serde(default)]
    pub skill_path: Option<String>,
    #[serde(default = "default_approval")]
    pub approval: String,
    #[serde(default = "default_enabled")]
    pub enabled: bool,
    #[serde(default)]
    pub platforms: Vec<String>,
    pub timeout_seconds: u64,
}

fn default_approval() -> String {
    "required".into()
}

fn default_enabled() -> bool {
    true
}

#[derive(Debug, Clone, Serialize)]
pub struct WikiActionBinding {
    pub id: String,
    pub label: String,
    pub description: String,
    pub executor: String,
    pub approval: String,
    pub available: bool,
    pub reason: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct WikiActionRun {
    pub action_id: String,
    pub label: String,
    pub result: ToolCallResult,
}

fn parse_registry(raw: &str, source: &str) -> Result<ActionRegistryFile, String> {
    let registry: ActionRegistryFile = serde_yaml::from_str(raw)
        .map_err(|e| format!("解析行动注册表失败 {source}: {e}"))?;
    if registry.version != 1 {
        return Err(format!("不支持的行动注册表版本: {}", registry.version));
    }
    Ok(registry)
}

fn validate_registry(registry: &ActionRegistryFile) -> Result<(), String> {
    let mut ids = std::collections::HashSet::new();
    for action in &registry.actions {
        validate_action_id(&action.id)?;
        if !ids.insert(action.id.as_str()) {
            return Err(format!("行动注册表包含重复 action_id: {}", action.id));
        }
        if action.label.trim().is_empty() {
            return Err(format!("行动 {} 缺少 label", action.id));
        }
        if action.approval != "required" && action.approval != "optional" {
            return Err(format!("行动 {} 的 approval 必须是 required 或 optional", action.id));
        }
        if action.timeout_seconds == 0 || action.timeout_seconds > 3600 {
            return Err(format!(
                "行动 {} 的 timeout_seconds 必须在 1 到 3600 之间",
                action.id
            ));
        }
    }
    Ok(())
}

fn load_registry_file(path: &PathBuf) -> Result<ActionRegistryFile, String> {
    let raw = fs::read_to_string(path).map_err(|e| format!("读取行动注册表失败 {path:?}: {e}"))?;
    parse_registry(&raw, &path.to_string_lossy())
}

fn merge_registry(base: &mut ActionRegistryFile, custom: ActionRegistryFile) {
    for item in custom.actions {
        if let Some(existing) = base.actions.iter_mut().find(|candidate| candidate.id == item.id) {
            *existing = item;
        } else {
            base.actions.push(item);
        }
    }
}

/// 内置注册表面向普通员工；本机文件只作为高级扩展，按 action_id 覆盖或追加。
/// 显式环境变量保留为开发/测试 escape hatch，并按旧语义完整替换内置内容。
fn load_registry() -> Result<ActionRegistryFile, String> {
    if let Some(path) = std::env::var(REGISTRY_ENV)
        .ok()
        .as_deref()
        .map(str::trim)
        .filter(|path| !path.is_empty())
    {
        let registry = load_registry_file(&PathBuf::from(path))?;
        validate_registry(&registry)?;
        return Ok(registry);
    }

    let mut registry = parse_registry(BUILTIN_REGISTRY, "Companion 内置注册表")?;
    let local_path = catfish_home()?.join(REGISTRY_FILE);
    if local_path.exists() {
        let local = load_registry_file(&local_path)?;
        validate_registry(&local)?;
        merge_registry(&mut registry, local);
    }
    validate_registry(&registry)?;
    Ok(registry)
}

fn validate_action_id(id: &str) -> Result<(), String> {
    if id.is_empty() || id.len() > 120 {
        return Err(format!("非法 action_id: {id:?}"));
    }
    if !id.bytes().all(|byte| {
        byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b':' | b'_' | b'-')
    }) {
        return Err(format!("非法 action_id: {id:?}，只允许字母、数字、. : _ -"));
    }
    Ok(())
}

fn current_platform() -> &'static str {
    if cfg!(target_os = "windows") {
        "windows"
    } else if cfg!(target_os = "macos") {
        "macos"
    } else {
        "linux"
    }
}

fn binding(action: &ActionDefinition) -> WikiActionBinding {
    let mut reason = None;
    if !action.enabled {
        reason = Some("注册表已禁用".into());
    } else if !action.platforms.is_empty()
        && !action.platforms.iter().any(|p| p == current_platform())
    {
        reason = Some(format!("当前平台不支持（{}）", current_platform()));
    } else if action.executor != "skill" {
        reason = Some("当前只允许通过现有 skill bridge 执行".into());
    } else if action.skill_path.as_deref().map(str::trim).unwrap_or("").is_empty() {
        reason = Some("注册表缺少 skill_path".into());
    }
    WikiActionBinding {
        id: action.id.clone(),
        label: action.label.clone(),
        description: action.description.clone(),
        executor: action.executor.clone(),
        approval: action.approval.clone(),
        available: reason.is_none(),
        reason,
    }
}

fn find_action<'a>(
    registry: &'a ActionRegistryFile,
    id: &str,
) -> Result<&'a ActionDefinition, String> {
    validate_action_id(id)?;
    registry
        .actions
        .iter()
        .find(|action| action.id == id)
        .ok_or_else(|| format!("行动未注册: {id}"))
}

#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_resolve_actions(
    action_refs: Vec<String>,
) -> Result<Vec<WikiActionBinding>, String> {
    let registry = load_registry()?;
    action_refs
        .into_iter()
        .map(|id| match find_action(&registry, &id) {
            Ok(action) => Ok(binding(action)),
            Err(reason) => Ok(WikiActionBinding {
                id,
                label: "未注册行动".into(),
                description: String::new(),
                executor: String::new(),
                approval: "required".into(),
                available: false,
                reason: Some(reason),
            }),
        })
        .collect()
}

/// 返回内置行动目录，供普通员工在界面中按名称选择。
///
/// 不把 Hermes 的全部工具暴露成 Wiki action：这里只返回 Catfish 明确允许
/// 作为知识条目关联入口的行动。
#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_list_actions() -> Result<Vec<WikiActionBinding>, String> {
    let registry = load_registry()?;
    Ok(registry.actions.iter().map(binding).collect())
}

#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_execute_action(
    action_id: String,
    confirmed: bool,
) -> Result<WikiActionRun, String> {
    let registry = load_registry()?;
    let action = find_action(&registry, &action_id)?;
    let resolved = binding(action);
    if !resolved.available {
        return Err(resolved.reason.unwrap_or_else(|| "行动不可执行".into()));
    }
    if action.approval == "required" && !confirmed {
        return Err("该行动需要员工明确确认".into());
    }
    let skill_path = action.skill_path.as_deref().unwrap_or_default().to_string();
    let raw_result = crate::services::tool_bridge_rpc::call_with_timeout(
        "tools/dispatch",
        json!({
            "name": "catfish_run_skill",
            "args": { "skill_path": skill_path },
        }),
        Duration::from_secs(action.timeout_seconds),
    )
    .await?;
    let result = serde_json::from_value::<ToolCallResult>(raw_result)
        .map_err(|e| format!("解析行动执行结果失败: {e}"))?;
    Ok(WikiActionRun {
        action_id,
        label: action.label.clone(),
        result,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn action(executor: &str) -> ActionDefinition {
        ActionDefinition {
            id: "skill.example".into(),
            label: "示例".into(),
            description: String::new(),
            executor: executor.into(),
            skill_path: Some("catfish/example".into()),
            approval: "required".into(),
            enabled: true,
            platforms: vec![],
            timeout_seconds: 300,
        }
    }

    fn action_named(id: &str) -> ActionDefinition {
        let mut item = action("skill");
        item.id = id.into();
        item
    }

    #[test]
    fn action_id_validation_rejects_path_traversal() {
        assert!(validate_action_id("../shell").is_err());
        assert!(validate_action_id("skill.example").is_ok());
    }

    #[test]
    fn unsupported_executor_is_not_available() {
        let result = binding(&action("tool"));
        assert!(!result.available);
        assert!(result.reason.unwrap().contains("skill bridge"));
    }

    #[test]
    fn platform_filter_is_reported() {
        let mut item = action("skill");
        item.platforms = vec!["never-this-platform".into()];
        let result = binding(&item);
        assert!(!result.available);
        assert!(result.reason.unwrap().contains("当前平台"));
    }

    #[test]
    fn built_in_registry_is_valid_without_a_user_file() {
        let registry = parse_registry(BUILTIN_REGISTRY, "test").unwrap();
        validate_registry(&registry).unwrap();
        assert!(registry.actions.iter().any(|item| item.id == "skill.weekly-report"));
    }

    #[test]
    fn local_registry_overrides_and_appends_without_replacing_defaults() {
        let mut base = ActionRegistryFile {
            version: 1,
            actions: vec![action_named("skill.example")],
        };
        let mut override_item = action_named("skill.example");
        override_item.label = "本机版本".into();
        let custom = ActionRegistryFile {
            version: 1,
            actions: vec![override_item, action_named("skill.local")],
        };
        merge_registry(&mut base, custom);
        assert_eq!(base.actions.len(), 2);
        assert_eq!(base.actions[0].label, "本机版本");
        assert_eq!(base.actions[1].id, "skill.local");
    }
}
