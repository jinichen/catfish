//! BL-CATFISH-WIKI-MODE P3.3.2 (6/4) — wiki read API.
//!
//! 暴露 2 个 tauri command:
//!   - wiki_list_files: 列 ~/.catfish/wiki/{entities,concepts,queries}/*.md
//!     返 Vec<WikiFileInfo> — frontmatter parse (type/title/tags/related/sources)
//!   - wiki_read_file: 读单 file 真 content + frontmatter
//!
//! Frontmatter parse: 真**简单 YAML scan**, 不引 serde_yaml dep —
//! 真**catfish wiki 真 frontmatter format 真**真**真**fixed (P1.1.1 Generation prompt 真**), 真**几个**真**已知字段** 抽即可.

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct WikiFileInfo {
    /// rel path from ~/.catfish/, e.g. "wiki/entities/openai.md"
    pub rel_path: String,
    /// kind: "entity" | "concept" | "query" (从 path 真**抽**)
    pub kind: String,
    /// slug (filename 真**真**去 .md)
    pub slug: String,
    /// title (frontmatter 真 `title:` field, 或 fallback slug)
    pub title: String,
    /// type tag: entity_type / concept_type (e.g. "person", "process")
    pub subtype: Option<String>,
    /// tags 真**list (frontmatter `tags: [...]`)
    pub tags: Vec<String>,
    /// related wikilinks (frontmatter `related: [...]`) — 真**`[[name]]`** 真**抽** name
    pub related: Vec<String>,
    /// sources (frontmatter `sources: [...]`)
    pub sources: Vec<String>,
    /// 文件 byte size
    pub size_bytes: u64,
    /// modified ts (unix epoch sec)
    pub mtime: f64,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct WikiFileFull {
    pub info: WikiFileInfo,
    /// 完整 markdown content (含 frontmatter)
    pub content: String,
    /// frontmatter block (---...--- 之间)
    pub frontmatter: String,
    /// body (frontmatter 后真**Markdown**)
    pub body: String,
}

fn home_dir() -> Result<PathBuf, String> {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .map_err(|_| "找不到 HOME".to_string())
}

fn catfish_home() -> Result<PathBuf, String> {
    Ok(home_dir()?.join(".catfish"))
}

/// 切 `---` 标记真**frontmatter** + body
fn split_frontmatter(text: &str) -> (String, String) {
    let trimmed = text.trim_start();
    if !trimmed.starts_with("---") {
        return (String::new(), text.to_string());
    }
    let after_first = &trimmed[3..];
    // 找下一个 `---` 真**line start**
    if let Some(end_idx) = after_first.find("\n---") {
        let fm = after_first[..end_idx].trim().to_string();
        let body = after_first[end_idx + 4..].trim_start_matches('\n').to_string();
        return (fm, body);
    }
    (String::new(), text.to_string())
}

/// 真**简单 YAML 字段抽** — 真**只抽 `key: value` 真**`key: [v1, v2]`**, 不真**真**多层 nested**.
fn parse_frontmatter_field(fm: &str, key: &str) -> Option<String> {
    for line in fm.lines() {
        let line = line.trim();
        if let Some(rest) = line.strip_prefix(&format!("{key}:")) {
            return Some(rest.trim().to_string());
        }
    }
    None
}

/// 真**`tags: [a, b, c]`** 或 `tags: ["a", "b"]` 都接受 → Vec<String>.
fn parse_list_field(fm: &str, key: &str) -> Vec<String> {
    let Some(raw) = parse_frontmatter_field(fm, key) else {
        return Vec::new();
    };
    let inner = raw.trim().trim_start_matches('[').trim_end_matches(']');
    inner
        .split(',')
        .map(|s| s.trim().trim_matches('"').trim_matches('\'').to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

/// 真**`related: ["[[陈鸿波]]", "[[FFCS]]"]`** → 真**`["陈鸿波", "FFCS"]`** (去 `[[` `]]`).
fn parse_related(fm: &str) -> Vec<String> {
    parse_list_field(fm, "related")
        .into_iter()
        .map(|s| {
            s.trim_start_matches("[[")
                .trim_end_matches("]]")
                .to_string()
        })
        .filter(|s| !s.is_empty())
        .collect()
}

fn build_file_info(home: &Path, abs_path: &Path) -> Option<WikiFileInfo> {
    let rel_path = abs_path.strip_prefix(home).ok()?.to_string_lossy().to_string();
    let kind = if rel_path.starts_with("wiki/entities/") {
        "entity"
    } else if rel_path.starts_with("wiki/concepts/") {
        "concept"
    } else if rel_path.starts_with("wiki/queries/") {
        "query"
    } else {
        return None;
    }
    .to_string();
    let slug = abs_path
        .file_stem()
        .and_then(|s| s.to_str())
        .map(|s| s.to_string())
        .unwrap_or_default();
    let content = fs::read_to_string(abs_path).ok()?;
    let (fm, _body) = split_frontmatter(&content);

    let title = parse_frontmatter_field(&fm, "title").unwrap_or_else(|| slug.clone());
    let subtype = parse_frontmatter_field(&fm, "entity_type")
        .or_else(|| parse_frontmatter_field(&fm, "concept_type"));
    let tags = parse_list_field(&fm, "tags");
    let related = parse_related(&fm);
    let sources = parse_list_field(&fm, "sources");

    let meta = fs::metadata(abs_path).ok()?;
    let size_bytes = meta.len();
    let mtime = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);

    Some(WikiFileInfo {
        rel_path,
        kind,
        slug,
        title,
        subtype,
        tags,
        related,
        sources,
        size_bytes,
        mtime,
    })
}

#[tauri::command]
pub async fn wiki_list_files() -> Result<Vec<WikiFileInfo>, String> {
    let home = catfish_home()?;
    let mut out = Vec::new();
    for sub in &["wiki/entities", "wiki/concepts", "wiki/queries"] {
        let dir = home.join(sub);
        if !dir.is_dir() {
            continue;
        }
        let entries = fs::read_dir(&dir)
            .map_err(|e| format!("read_dir {dir:?} 失败: {e}"))?;
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()) != Some("md") {
                continue;
            }
            if let Some(info) = build_file_info(&home, &path) {
                out.push(info);
            }
        }
    }
    // 按 mtime 真**最近真**优先
    out.sort_by(|a, b| b.mtime.partial_cmp(&a.mtime).unwrap_or(std::cmp::Ordering::Equal));
    Ok(out)
}

#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_read_file(rel_path: String) -> Result<WikiFileFull, String> {
    // 真**安全**: rel_path 必须 `wiki/{entities|concepts|queries}/<slug>.md`,
    // 不允许真**`..`** 真**path traversal**
    if rel_path.contains("..") || !rel_path.starts_with("wiki/") {
        return Err(format!("rel_path 真**白名单不通过: {rel_path}"));
    }
    let home = catfish_home()?;
    let abs_path = home.join(&rel_path);
    if !abs_path.is_file() {
        return Err(format!("file 不存在: {abs_path:?}"));
    }
    let content = fs::read_to_string(&abs_path)
        .map_err(|e| format!("read {abs_path:?} 失败: {e}"))?;
    let info = build_file_info(&home, &abs_path)
        .ok_or_else(|| format!("build_file_info 失败: {rel_path}"))?;
    let (frontmatter, body) = split_frontmatter(&content);
    Ok(WikiFileFull {
        info,
        content,
        frontmatter,
        body,
    })
}
