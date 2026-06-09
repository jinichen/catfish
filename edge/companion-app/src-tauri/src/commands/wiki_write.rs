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

/// P3.3.3 (6/9 鸿波) — slug normalize, 防 `FFCS数字鲶鱼` / `FFCS 数字鲶鱼` /
/// `FFCS-数字鲶鱼` / `ffcs_数字鲶鱼` 被当成 4 个不同 entity.
///
/// 规则: 去所有 whitespace / `-` / `_` / `.`, 再 lowercase.
/// 比对用, 不参与文件名生成 (文件名仍走 slugify).
fn normalize_slug(s: &str) -> String {
    s.chars()
        .filter(|c| {
            !c.is_whitespace()
                && *c != '-'
                && *c != '_'
                && *c != '.'
                && *c != '\u{3000}' // 全角空格
        })
        .collect::<String>()
        .to_lowercase()
}

/// 扫现有 entities/ / concepts/ 找 normalize-等价的 slug, 命中返已有 rel_path.
fn find_normalized_collision(
    home: &Path,
    sub_dir: &str,
    new_slug: &str,
) -> Option<String> {
    let norm_new = normalize_slug(new_slug);
    if norm_new.is_empty() {
        return None;
    }
    let dir = home.join(sub_dir);
    if !dir.is_dir() {
        return None;
    }
    let entries = fs::read_dir(&dir).ok()?;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|s| s.to_str()) != Some("md") {
            continue;
        }
        let stem = match path.file_stem().and_then(|s| s.to_str()) {
            Some(s) => s,
            None => continue,
        };
        if normalize_slug(stem) == norm_new {
            return Some(format!("{sub_dir}/{stem}.md"));
        }
    }
    None
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
        return Err(format!("file 已存在: {sub_dir}/{slug}.md (用 update 不 create)"));
    }

    // P3.3.3 (6/9): normalize-等价 slug 防重复 — 例如 `FFCS数字鲶鱼` 跟
    // `FFCS 数字鲶鱼` / `ffcs-数字鲶鱼` 都规范化到同一 key, 命中报错让 LLM 改走 update.
    if let Some(existing) = find_normalized_collision(&home, sub_dir, &slug) {
        return Err(format!(
            "等价 slug 已存在: {existing} — '{slug}' 规范化后等同已有文件. \
             用 wiki_update_file 改原文件, 不要 create 新的."
        ));
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

// ============================================================
// P3.3.4 (6/9 鸿波) — wiki 软删 (move to .trash)
//
// 之前 wiki 系统只有 create / update / ingest, 没 delete. 员工想清错误 entity
// 唯一办法是手动 rm ~/.catfish/wiki/entities/*.md, 体验差 + 容易误删别的.
//
// 软删策略 (跟 session_soft_delete 风格一致):
//   - mv 文件到 ~/.catfish/wiki/.trash/<unix_ts>-<原文件名>.md
//   - .trash 不在 wiki_list_files 扫描目录内, list / graph 立即看不到
//   - 员工想 restore 自己 mv 回来 (有 grace 期但没 UI restore button)
//   - 30+ 天后员工自己清 .trash, 不加 cron (catfish 没 cron 设施)
// ============================================================

#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_delete_file(rel_path: String) -> Result<WikiWriteResult, String> {
    // 路径白名单 — 同 wiki_read_file / wiki_update_file
    if rel_path.contains("..") || !rel_path.starts_with("wiki/") {
        return Err(format!("rel_path 白名单不通过: {rel_path}"));
    }
    // 不准删 .trash 自身 (防递归 / 防员工误操作)
    if rel_path.starts_with("wiki/.trash/") {
        return Err("不能删 .trash 内文件 (用 Finder/Terminal 手动清)".to_string());
    }
    // 只允许删 entities/ concepts/ queries/ 下 .md
    let allowed_dir = rel_path.starts_with("wiki/entities/")
        || rel_path.starts_with("wiki/concepts/")
        || rel_path.starts_with("wiki/queries/");
    if !allowed_dir {
        return Err(format!(
            "只能删 wiki/entities/ wiki/concepts/ wiki/queries/ 下文件: {rel_path}"
        ));
    }
    if !rel_path.ends_with(".md") {
        return Err(format!("只能删 .md 文件: {rel_path}"));
    }

    let home = catfish_home()?;
    let abs = home.join(&rel_path);
    if !abs.is_file() {
        return Err(format!("file 不存在: {rel_path}"));
    }

    // mv 到 .trash/<ts>-<原文件名>.md, 不覆盖 (ts 保证唯一)
    let trash_dir = home.join("wiki").join(".trash");
    fs::create_dir_all(&trash_dir)
        .map_err(|e| format!("建 .trash 目录失败: {e}"))?;
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let fname = abs
        .file_name()
        .and_then(|n| n.to_str())
        .ok_or("无法取文件名")?;
    let trashed_name = format!("{ts}-{fname}");
    let dst = trash_dir.join(&trashed_name);

    let bytes = fs::metadata(&abs).map(|m| m.len()).unwrap_or(0);
    fs::rename(&abs, &dst).map_err(|e| format!("mv {abs:?} → {dst:?} 失败: {e}"))?;

    Ok(WikiWriteResult {
        rel_path: format!("wiki/.trash/{trashed_name}"),
        bytes,
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

// ============================================================
// P16 (6/5 鸿波): 对话上传文件 auto ingest → wiki/raw/sources/
//
// chat 上传 PDF/Word/Text/CSV/Excel → parse_file_from_b64 已 ship preview
// + sidecar (大文件 .parsed.txt 在 ~/.catfish/uploads/). 这命令把全文
// (sidecar 优先, 小文件 preview=full) 写到 ~/.catfish/wiki/raw/sources/
// <ts>-<slug>.md, frontmatter 标 type:source / source:upload.
//
// catfish-memory plugin 后台 sync_turn 3b 会扫这 dir 真未 ingest *.md
// merge 进 Analysis input → 抽 entity/concept → wiki/entities/ + concepts/.
//
// 与 wiki/queries/ 区别: queries 是 dataview-style "请基于现有 wiki 答 X",
// sources 是 "这是新原始资料". P1.2.3 hook 复用同样 ingested state JSON
// (key 前缀 `source:` 防冲突).
// ============================================================

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct WikiIngestSourceResult {
    pub rel_path: String,
    pub bytes: u64,
    pub full_text_chars: usize,
}

#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_ingest_source(
    kept_path: String,
    parsed_text_path: Option<String>,
    preview_text: String,
    filename: String,
    kind: String, // pdf / word / text / csv / excel / audio / image / video
) -> Result<WikiIngestSourceResult, String> {
    // 1. 读全文: sidecar 优先 (大文件 ≥50KB), fallback preview (小文件 preview = full)
    let full_text: String = if let Some(sp) = parsed_text_path.as_deref() {
        match fs::read_to_string(sp) {
            Ok(t) if !t.trim().is_empty() => t,
            _ => preview_text.clone(),
        }
    } else {
        preview_text.clone()
    };

    if full_text.trim().is_empty() {
        return Err("全文空, 跳过 ingest".to_string());
    }

    // 2. 目标目录 + 文件名 (<ts>-<slug>.md)
    let home = catfish_home()?;
    let dir = home.join("wiki").join("raw").join("sources");
    fs::create_dir_all(&dir).map_err(|e| format!("建目录 {dir:?} 失败: {e}"))?;

    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);

    let stem_part = std::path::Path::new(&filename)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("upload");
    let slug = slugify(stem_part, 60);
    let safe_slug = if slug.is_empty() { "upload".to_string() } else { slug };
    let final_name = format!("{ts}-{safe_slug}.md");
    let path = dir.join(&final_name);

    // 3. frontmatter + body. 全文直接塞 body (后台 Analysis LLM 拿 full text 抽 entity/concept).
    let today = chrono_today();
    let bytes_n = full_text.len();
    let safe_kept = kept_path.replace('\n', " ").replace('\r', " ");
    let safe_filename = filename.replace('\n', " ").replace('\r', " ");

    let content = format!(
        "---\n\
         type: source\n\
         filename: {safe_filename}\n\
         kind: {kind}\n\
         uploaded: {today}\n\
         kept_path: {safe_kept}\n\
         bytes: {bytes_n}\n\
         source: upload\n\
         ---\n\
         \n\
         # Upload: {safe_filename}\n\
         \n\
         {full_text}\n",
    );

    fs::write(&path, &content).map_err(|e| format!("写 {path:?} 失败: {e}"))?;

    Ok(WikiIngestSourceResult {
        rel_path: format!("wiki/raw/sources/{final_name}"),
        bytes: content.len() as u64,
        full_text_chars: full_text.chars().count(),
    })
}
