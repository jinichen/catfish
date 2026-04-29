//! Skills + MCP servers 列表 —— 读两套来源:
//!   1. ~/.hermes/skills/                — Hermes 加载的所有 skill (LLM 自学 / 内置)
//!   2. <catfish_root>/skills/           — catfish 工程审定 skill (公文 / 合规模板)
//!
//! 两套都显示在仪表盘 SKILLS 区, 用 namespace 前缀区分:
//!   - hermes 的: 直接用原 namespace (productivity / data-science / ...)
//!   - catfish 的: 加 "🐟 " 前缀强调来源 (🐟 catfish:department)
//!
//! 鸿波 2026-04-29 反馈: 仪表盘看不到 leadership-briefing — 这是因为它在
//! catfish/skills/ 不在 ~/.hermes/skills/. 这次改完两套都能看见.
//!
//! Skills 目录结构 (两套通用)：
//!   <root>/
//!     <namespace>/             apple / github / department / ...
//!       <skill-name>/
//!         SKILL.md             YAML frontmatter + body
//!         scripts/             skill 自带脚本 (hermes 用)
//!         script.py            skill render 入口 (catfish 用)
//!
//! MCP servers 在 config.yaml 的 `mcp_servers:` 段下：
//!   mcp_servers:
//!     <name>:
//!       command: <path>        stdio 启动命令
//!       args: [...]            可选
//!       env: {...}             可选

use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::services::catfish_paths;

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

/// 扫一个 skills root 目录, 返回所有 namespace + skill.
///
/// `ns_prefix` 给 namespace 名加前缀 (例如 "🐟 catfish:" 让 catfish skill 视觉
/// 区别于 hermes). 空字符串 = 不加前缀.
fn scan_skills_root(root: &Path, ns_prefix: &str) -> Vec<SkillNamespace> {
    let mut result = Vec::new();
    if !root.exists() {
        return result;
    }
    let Ok(entries) = std::fs::read_dir(root) else {
        return result;
    };

    for ns_entry in entries.flatten() {
        let ns_path = ns_entry.path();
        if !ns_path.is_dir() {
            continue;
        }
        let ns_name = match ns_path.file_name().and_then(|s| s.to_str()) {
            Some(n) if !n.starts_with('.') => n.to_string(),
            _ => continue,
        };

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
            skills.sort_by(|a, b| a.name.cmp(&b.name));
            result.push(SkillNamespace {
                namespace: format!("{ns_prefix}{ns_name}"),
                skills,
            });
        }
    }

    result
}

fn list_skills_blocking() -> Result<Vec<SkillNamespace>, String> {
    let mut result = Vec::new();

    // 1. catfish 工程审定 skill — 顶部显示, 带 🐟 前缀强调来源
    if let Some(catfish_root) = catfish_paths::catfish_root() {
        let catfish_skills_root = catfish_root.join("skills");
        let mut catfish_namespaces =
            scan_skills_root(&catfish_skills_root, "🐟 catfish:");
        catfish_namespaces.sort_by(|a, b| a.namespace.cmp(&b.namespace));
        result.extend(catfish_namespaces);
    }

    // 2. hermes skills (LLM 自学 + 内置)
    if let Some(home) = home_dir() {
        let hermes_skills = home.join(".hermes").join("skills");
        let mut hermes_namespaces = scan_skills_root(&hermes_skills, "");
        hermes_namespaces.sort_by(|a, b| a.namespace.cmp(&b.namespace));
        result.extend(hermes_namespaces);
    }

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
