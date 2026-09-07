//! Wiki 本体关系目标校验。

use serde::Serialize;
use std::fs;
use std::path::Path;
use std::path::PathBuf;

use super::wiki_read::{build_file_info, collect_all_wiki_md, parse_related};

const DEFAULT_RELATION: &str = "关联";

#[derive(Debug, Serialize, Clone)]
pub struct WikiRelationMigrationResult {
    pub dry_run: bool,
    pub scanned_files: usize,
    pub changed_files: usize,
    pub converted_relations: usize,
    pub backup_dir: Option<String>,
}

fn quote_yaml(value: &str) -> String {
    format!(
        "\"{}\"",
        value.replace('\\', "\\\\").replace('"', "\\\"")
    )
}

fn serialize_relations(raw: &str) -> (String, usize) {
    let relations = parse_related(raw);
    let converted = relations.iter().filter(|relation| relation.rel.is_none()).count();
    if converted == 0 {
        return (raw.to_string(), 0);
    }
    let serialized = relations
        .iter()
        .map(|relation| {
            format!(
                "{{name: {}, rel: {}}}",
                quote_yaml(&relation.name),
                quote_yaml(relation.rel.as_deref().unwrap_or(DEFAULT_RELATION)),
            )
        })
        .collect::<Vec<_>>()
        .join(", ");
    (format!("[{serialized}]"), converted)
}

fn migrate_content(content: &str) -> (String, usize) {
    if !content.starts_with("---\n") {
        return (content.to_string(), 0);
    }
    let Some(end) = content.find("\n---") else {
        return (content.to_string(), 0);
    };
    let frontmatter = &content[4..end];
    let Some(related_line) = frontmatter
        .lines()
        .find(|line| line.trim_start().starts_with("related:"))
    else {
        return (content.to_string(), 0);
    };
    let Some(value_start) = related_line.find(':') else {
        return (content.to_string(), 0);
    };
    let (replacement, converted) = serialize_relations(frontmatter);
    if converted == 0 {
        return (content.to_string(), 0);
    }
    let line_start = frontmatter
        .find(related_line)
        .expect("related line was found in frontmatter");
    let line_end = line_start + related_line.len();
    let replacement_line = format!("{}: {replacement}", &related_line[..value_start]);
    let mut next_frontmatter = String::with_capacity(frontmatter.len() + replacement_line.len());
    next_frontmatter.push_str(&frontmatter[..line_start]);
    next_frontmatter.push_str(&replacement_line);
    next_frontmatter.push_str(&frontmatter[line_end..]);
    (
        format!("---\n{next_frontmatter}\n---{}", &content[end + 4..]),
        converted,
    )
}

fn local_wiki_files(home: &Path) -> Vec<PathBuf> {
    ["wiki/entities", "wiki/concepts", "wiki/queries"]
        .into_iter()
        .flat_map(|relative| {
            let dir = home.join(relative);
            fs::read_dir(dir)
                .into_iter()
                .flat_map(|entries| entries.flatten())
                .map(|entry| entry.path())
                .filter(|path| path.extension().and_then(|value| value.to_str()) == Some("md"))
                .collect::<Vec<_>>()
        })
        .collect()
}

fn atomic_replace(path: &Path, content: &str) -> Result<(), String> {
    let temporary = path.with_extension("md.catfish-migration-tmp");
    fs::write(&temporary, content).map_err(|error| format!("写迁移临时文件失败 {temporary:?}: {error}"))?;
    fs::rename(&temporary, path).map_err(|error| format!("替换 Wiki 文件失败 {path:?}: {error}"))
}

/// 将历史 `related: ["[[目标]]"]` 规范化为带通用关系类型的结构化关系。
///
/// dry_run 默认只预览；正式执行只扫描员工自己的三类 Wiki，不改写 wiki-shared。
#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_migrate_legacy_relations(
    dry_run: Option<bool>,
) -> Result<WikiRelationMigrationResult, String> {
    let dry_run = dry_run.unwrap_or(true);
    let home = super::wiki_read::catfish_home()?;
    let files = local_wiki_files(&home);
    let mut changes = Vec::new();
    for path in &files {
        let content = fs::read_to_string(path).map_err(|error| format!("读取 Wiki 文件失败 {path:?}: {error}"))?;
        let (migrated, converted) = migrate_content(&content);
        if converted > 0 {
            changes.push((path.clone(), migrated, converted));
        }
    }

    let backup_dir = if dry_run || changes.is_empty() {
        None
    } else {
        let timestamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|value| value.as_secs())
            .unwrap_or(0);
        let directory = home
            .join("wiki")
            .join(".migration-backups")
            .join(format!("legacy-relations-{timestamp}"));
        fs::create_dir_all(&directory).map_err(|error| format!("创建迁移备份目录失败 {directory:?}: {error}"))?;
        for (path, _, _) in &changes {
            let relative = path
                .strip_prefix(&home)
                .map_err(|error| format!("计算 Wiki 相对路径失败: {error}"))?;
            let backup = directory.join(relative);
            if let Some(parent) = backup.parent() {
                fs::create_dir_all(parent).map_err(|error| format!("创建备份子目录失败 {parent:?}: {error}"))?;
            }
            fs::copy(path, &backup).map_err(|error| format!("备份 Wiki 文件失败 {path:?}: {error}"))?;
        }
        for (path, migrated, _) in &changes {
            atomic_replace(path, migrated)?;
        }
        Some(directory.to_string_lossy().into_owned())
    };

    Ok(WikiRelationMigrationResult {
        dry_run,
        scanned_files: files.len(),
        changed_files: changes.len(),
        converted_relations: changes.iter().map(|(_, _, count)| count).sum(),
        backup_dir,
    })
}

/// 判断关系目标是否是唯一且 active 的本体节点。
pub fn ontology_target_is_active(home: &Path, name: &str) -> bool {
    let needle = name.trim().to_lowercase();
    if needle.is_empty() {
        return false;
    }
    let matches = collect_all_wiki_md(home)
        .into_iter()
        .filter_map(|path| build_file_info(home, &path))
        .filter(|info| matches!(info.kind.as_str(), "entity" | "concept"))
        .filter(|info| {
            let status = info.ontology_status.as_deref().unwrap_or("active").to_lowercase();
            status == "active"
                && (info.title.to_lowercase() == needle
                    || info.slug.to_lowercase() == needle
                    || info
                        .aliases
                        .iter()
                        .any(|alias| alias.trim().to_lowercase() == needle))
        })
        .count();
    matches == 1
}

#[cfg(test)]
mod tests {
    use super::migrate_content;

    #[test]
    fn migrates_legacy_relations_without_changing_targets() {
        let source = "---\ntitle: 示例\nrelated: [\"[[目标 A]]\", {name: \"目标 B\", rel: \"依据\"}]\n---\n\n正文\n";
        let (result, converted) = migrate_content(source);
        assert_eq!(converted, 1);
        assert!(result.contains("{name: \"目标 A\", rel: \"关联\"}"));
        assert!(result.contains("{name: \"目标 B\", rel: \"依据\"}"));
        assert!(result.contains("\n正文\n"));
    }

    #[test]
    fn migration_is_idempotent_for_typed_relations() {
        let source = "---\ntitle: 示例\nrelated: [{name: \"目标\", rel: \"依据\"}]\n---\n正文\n";
        let (result, converted) = migrate_content(source);
        assert_eq!(converted, 0);
        assert_eq!(result, source);
    }
}
