//! BL-CATFISH-WIKI-MODE P3.3.7 (6/4) — wiki write API.
//!
//! 2 个 tauri command:
//!   - wiki_create_entity_or_concept: 创建新 entity/concept file 含 frontmatter + body
//!   - wiki_update_file: 真**真**更新已有真 file body** (frontmatter 简单替, 复杂场景 future)

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct WikiWriteResult {
    pub rel_path: String,
    pub bytes: u64,
    pub created: bool, // true 新建, false update
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

/// slug 校验 — unicode word char + `-` + `_`, 不允许 path-unsafe.
/// Rust 真 char::is_alphanumeric 接受 unicode (CJK / accented).
fn validate_slug(slug: &str) -> Result<(), String> {
    if slug.is_empty() {
        return Err("slug 不能空".to_string());
    }
    if slug.len() > 100 {
        return Err(format!("slug 太长 (>100 chars): {slug}"));
    }
    for c in slug.chars() {
        if !c.is_alphanumeric() && c != '-' && c != '_' {
            return Err(format!("slug 含非法字符 '{c}': {slug}"));
        }
    }
    if slug.starts_with('-') || slug.starts_with('.') {
        return Err(format!("slug 不能 - / . 开头: {slug}"));
    }
    Ok(())
}

/// title slugify — 真**真**真**简单**真**replace 非 word char 真 `-`** (跟 P1.2.2 wiki_save 真 slugify 一致).
fn slugify(title: &str, max_chars: usize) -> String {
    let bad: &[char] = &[
        '/', '\\', ':', '*', '?', '"', '<', '>', '|', '\n', '\r', '\t', ' ', '\u{3000}', '.',
    ];
    let cleaned: String = title
        .chars()
        .take(max_chars)
        .map(|c| if bad.contains(&c) { '-' } else { c })
        .collect();
    let trimmed = cleaned.trim_matches('-').trim_matches('.');
    if trimmed.is_empty() {
        "untitled".to_string()
    } else {
        trimmed.to_string()
    }
}

#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_create_entity_or_concept(
    kind: String, // "entity" | "concept"
    title: String,
    subtype: String, // entity_type or concept_type
    tags: Vec<String>,
    related: Vec<String>, // 真**LLM 输出真 `[[name]]` 真**真**rendered**真**真**, 这里**真**name only**真
    body: String,
) -> Result<WikiWriteResult, String> {
    if kind != "entity" && kind != "concept" {
        return Err(format!("kind 必须 entity 或 concept: {kind}"));
    }
    let title_trimmed = title.trim();
    if title_trimmed.is_empty() {
        return Err("title 不能空".to_string());
    }

    let slug = slugify(title_trimmed, 50);
    validate_slug(&slug)?;

    let home = catfish_home()?;
    let sub_dir = if kind == "entity" { "wiki/entities" } else { "wiki/concepts" };
    let dir = home.join(sub_dir);
    fs::create_dir_all(&dir).map_err(|e| format!("建目录 {dir:?} 失败: {e}"))?;

    let path = dir.join(format!("{slug}.md"));
    if path.exists() {
        return Err(format!("file 已存在: {sub_dir}/{slug}.md (用 update 不真**真**create)"));
    }

    let today = chrono_today();
    let type_field = if kind == "entity" { "entity_type" } else { "concept_type" };
    let tags_yaml = tags
        .iter()
        .map(|t| t.replace('\"', ""))
        .collect::<Vec<_>>()
        .join(", ");
    let related_yaml = related
        .iter()
        .map(|r| format!("\"[[{}]]\"", r.trim_matches('"').replace('\"', "")))
        .collect::<Vec<_>>()
        .join(", ");

    let content = format!(
        "---\n\
         type: {kind}\n\
         title: {title_trimmed}\n\
         {type_field}: {subtype}\n\
         created: {today}\n\
         updated: {today}\n\
         tags: [{tags_yaml}]\n\
         related: [{related_yaml}]\n\
         sources: [manual]\n\
         ---\n\
         \n\
         # {title_trimmed}\n\
         \n\
         {body}\n"
    );

    fs::write(&path, &content).map_err(|e| format!("写 {path:?} 失败: {e}"))?;
    let bytes = content.len() as u64;
    let rel_path = format!("{sub_dir}/{slug}.md");

    Ok(WikiWriteResult { rel_path, bytes, created: true })
}

#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_update_file(
    rel_path: String,
    content: String,
) -> Result<WikiWriteResult, String> {
    // 路径白名单 — 跟 wiki_read_file 同
    if rel_path.contains("..") || !rel_path.starts_with("wiki/") {
        return Err(format!("rel_path 白名单不通过: {rel_path}"));
    }
    let home = catfish_home()?;
    let abs_path = home.join(&rel_path);
    if !abs_path.is_file() {
        return Err(format!("file 不存在 (用 create): {rel_path}"));
    }
    fs::write(&abs_path, &content).map_err(|e| format!("写 {abs_path:?} 失败: {e}"))?;
    Ok(WikiWriteResult {
        rel_path,
        bytes: content.len() as u64,
        created: false,
    })
}

/// 真**简单 today 真 YYYY-MM-DD format** — 不引 chrono dep (太重), 用 std time + hand calc.
fn chrono_today() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Unix epoch (1970-01-01) → year/month/day
    let days_since_epoch = (secs / 86400) as i64;
    let (y, m, d) = days_to_ymd(days_since_epoch);
    format!("{y:04}-{m:02}-{d:02}")
}

/// 真**真 days since epoch → (year, month, day)**, civil_from_days (Howard Hinnant algorithm).
fn days_to_ymd(z: i64) -> (i64, u32, u32) {
    let z = z + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}
