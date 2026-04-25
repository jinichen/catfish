//! Skills + MCP servers 列表 —— 读 ~/.hermes/skills/ 和 ~/.hermes/config.yaml。
//!
//! Skills 目录结构（Hermes 约定）：
//!   ~/.hermes/skills/
//!     <namespace>/             apple / github / creative / dogfood / ...
//!       <skill-name>/
//!         SKILL.md             YAML frontmatter + body
//!         scripts/             skill 自带脚本
//!
//! MCP servers 在 config.yaml 的 `mcp_servers:` 段下：
//!   mcp_servers:
//!     <name>:
//!       command: <path>        stdio 启动命令
//!       args: [...]            可选
//!       env: {...}             可选

use std::path::{Path, PathBuf};

use serde::Serialize;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SkillEntry {
    pub name: String,
    pub description: String,
    pub version: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SkillNamespace {
    pub namespace: String,
    pub skills: Vec<SkillEntry>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct McpServerEntry {
    pub name: String,
    pub command: String,
    pub args: Vec<String>,
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

/// 解析 SKILL.md 的 YAML frontmatter
fn parse_skill_md(path: &Path) -> Option<SkillEntry> {
    let text = std::fs::read_to_string(path).ok()?;
    let trimmed = text.trim_start();
    if !trimmed.starts_with("---") {
        // 没 frontmatter，用文件名兜底
        let name = path
            .parent()
            .and_then(|p| p.file_name())
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_default();
        return Some(SkillEntry {
            name,
            description: "(无 SKILL.md frontmatter)".into(),
            version: None,
        });
    }
    // 找 frontmatter 边界：---\n....---\n
    let after_first = trimmed.strip_prefix("---")?.trim_start_matches('\n');
    let end_idx = after_first.find("\n---")?;
    let frontmatter = &after_first[..end_idx];

    let value: serde_yaml::Value = serde_yaml::from_str(frontmatter).ok()?;
    let name = value
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("(unknown)")
        .to_string();
    let description = value
        .get("description")
        .and_then(|v| v.as_str())
        .unwrap_or("(no description)")
        .to_string();
    let version = value
        .get("version")
        .and_then(|v| v.as_str())
        .map(String::from);
    Some(SkillEntry {
        name,
        description,
        version,
    })
}

fn list_skills_blocking() -> Result<Vec<SkillNamespace>, String> {
    let Some(home) = home_dir() else {
        return Ok(vec![]);
    };
    let skills_dir = home.join(".hermes").join("skills");
    if !skills_dir.exists() {
        return Ok(vec![]);
    }

    let mut result = Vec::new();
    let entries = std::fs::read_dir(&skills_dir)
        .map_err(|e| format!("读 skills/ 失败: {e}"))?;

    for ns_entry in entries.flatten() {
        let ns_path = ns_entry.path();
        if !ns_path.is_dir() {
            continue;
        }
        let ns_name = match ns_path.file_name().and_then(|s| s.to_str()) {
            Some(n) if !n.starts_with('.') => n.to_string(),
            _ => continue,
        };

        // 扫该 namespace 下每个 skill
        let mut skills = Vec::new();
        if let Ok(skill_iter) = std::fs::read_dir(&ns_path) {
            for skill_entry in skill_iter.flatten() {
                let skill_path = skill_entry.path();
                if !skill_path.is_dir() {
                    continue;
                }
                let manifest = skill_path.join("SKILL.md");
                if !manifest.exists() {
                    continue;
                }
                if let Some(entry) = parse_skill_md(&manifest) {
                    skills.push(entry);
                }
            }
        }

        if !skills.is_empty() {
            // 按 name 字母序，给 UI 稳定显示顺序
            skills.sort_by(|a, b| a.name.cmp(&b.name));
            result.push(SkillNamespace {
                namespace: ns_name,
                skills,
            });
        }
    }

    // namespace 也按字母序
    result.sort_by(|a, b| a.namespace.cmp(&b.namespace));
    Ok(result)
}

fn list_mcp_servers_blocking() -> Result<Vec<McpServerEntry>, String> {
    let Some(home) = home_dir() else {
        return Ok(vec![]);
    };
    let cfg_path = home.join(".hermes").join("config.yaml");
    let Ok(text) = std::fs::read_to_string(&cfg_path) else {
        return Ok(vec![]);
    };
    let Ok(value) = serde_yaml::from_str::<serde_yaml::Value>(&text) else {
        return Ok(vec![]);
    };

    let Some(mcp_map) = value.get("mcp_servers").and_then(|v| v.as_mapping()) else {
        return Ok(vec![]);
    };

    let mut result = Vec::new();
    for (k, v) in mcp_map {
        let name = match k.as_str() {
            Some(s) => s.to_string(),
            None => continue,
        };
        let command = v
            .get("command")
            .and_then(|x| x.as_str())
            .unwrap_or("(无 command)")
            .to_string();
        let args = v
            .get("args")
            .and_then(|x| x.as_sequence())
            .map(|seq| {
                seq.iter()
                    .filter_map(|x| x.as_str().map(String::from))
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        result.push(McpServerEntry {
            name,
            command,
            args,
        });
    }
    result.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(result)
}

#[tauri::command]
pub async fn list_skills() -> Result<Vec<SkillNamespace>, String> {
    tokio::task::spawn_blocking(list_skills_blocking)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

#[tauri::command]
pub async fn list_mcp_servers() -> Result<Vec<McpServerEntry>, String> {
    tokio::task::spawn_blocking(list_mcp_servers_blocking)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}
