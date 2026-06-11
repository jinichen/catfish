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

/// P3.3.3 (6/9 鸿波): tombstone 识别 — 防同 entity 多文件 (空格 vs 无空格 slug
/// 等情况) 在 list / graph 里都显示. tombstone 标准:
///   - 文件 <= 256 字节 (足够装 frontmatter `deprecated: true` + 一行注释)
///   - **且** (没 frontmatter, **或** frontmatter 含 `deprecated: true`)
///   - **且** body 命中废弃 marker (HTML 注释含"废弃"/"deprecated"/"请参见"/"tombstone",
///         或纯空 body)
///
/// list/graph 跳过 tombstone, 但 `wiki_read_file` 仍能读 (允许员工手动打开看 marker).
fn is_tombstone(size_bytes: u64, fm: &str, body: &str) -> bool {
    if size_bytes > 256 {
        return false;
    }
    let has_fm = !fm.is_empty();
    let fm_marks_deprecated = fm
        .lines()
        .any(|l| {
            let t = l.trim().to_lowercase();
            t == "deprecated: true" || t == "tombstone: true"
        });
    if has_fm && !fm_marks_deprecated {
        return false;
    }
    let body_trim = body.trim();
    if body_trim.is_empty() {
        return true;
    }
    let lower = body_trim.to_lowercase();
    // HTML 注释 (开头 <!-- ) + 含废弃 marker keyword
    let in_comment = lower.starts_with("<!--") && lower.trim_end().ends_with("-->");
    let has_marker = lower.contains("废弃")
        || lower.contains("deprecated")
        || lower.contains("tombstone")
        || lower.contains("请参见")
        || lower.contains("已迁移")
        || lower.contains("see also");
    in_comment && has_marker
}

fn build_file_info(home: &Path, abs_path: &Path) -> Option<WikiFileInfo> {
    build_file_info_inner(home, abs_path, /* allow_tombstone */ false)
}

/// allow_tombstone=true 时不跳 tombstone (wiki_read_file 用, 员工想直接打开看).
fn build_file_info_inner(
    home: &Path,
    abs_path: &Path,
    allow_tombstone: bool,
) -> Option<WikiFileInfo> {
    let rel_path = abs_path.strip_prefix(home).ok()?.to_string_lossy().to_string();
    // P3.3.18 Phase 4 (6/10): wiki-shared/<dept>/<部门>/<file_id>.md 也支持读 — 已装
    // 部门 wiki. kind 从 frontmatter type 抽 (publish 时已写).
    let kind = if rel_path.starts_with("wiki/entities/") {
        "entity".to_string()
    } else if rel_path.starts_with("wiki/concepts/") {
        "concept".to_string()
    } else if rel_path.starts_with("wiki/queries/") {
        "query".to_string()
    } else if rel_path.starts_with("wiki-shared/dept/") {
        // 已装部门 wiki — kind 从 frontmatter type 抽, fallback entity
        let content_peek = fs::read_to_string(abs_path).ok()?;
        let (fm_peek, _) = split_frontmatter(&content_peek);
        parse_frontmatter_field(&fm_peek, "type").unwrap_or_else(|| "entity".to_string())
    } else {
        return None;
    };
    let slug = abs_path
        .file_stem()
        .and_then(|s| s.to_str())
        .map(|s| s.to_string())
        .unwrap_or_default();
    let content = fs::read_to_string(abs_path).ok()?;
    let (fm, body) = split_frontmatter(&content);
    let meta = fs::metadata(abs_path).ok()?;
    let size_bytes = meta.len();

    // P3.3.3: tombstone 直接跳, list/graph 看不到 (wiki_read_file 用 inner+true 仍能读)
    if !allow_tombstone && is_tombstone(size_bytes, &fm, &body) {
        return None;
    }

    let title = parse_frontmatter_field(&fm, "title").unwrap_or_else(|| slug.clone());
    let subtype = parse_frontmatter_field(&fm, "entity_type")
        .or_else(|| parse_frontmatter_field(&fm, "concept_type"));
    let tags = parse_list_field(&fm, "tags");
    let related = parse_related(&fm);
    let sources = parse_list_field(&fm, "sources");
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

// ============================================================
// P3.3.18 Phase 4 (6/10) — wiki-shared 已装部门 wiki 扫描
// ============================================================

/// 已装部门 wiki 项. 跟 WikiFileInfo 不同:
/// - 含 namespace (dept/finance / dept/sales) 跟 file_id (UUID)
/// - 含 published_by / published_at / installed_at (来自 .meta.json sidecar)
/// - rel_path 是 `wiki-shared/<ns>/<file_id>.md` 相对 ~/.catfish/
#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct InstalledWikiSharedInfo {
    /// rel path from ~/.catfish/, e.g. "wiki-shared/dept/finance/abc123.md"
    pub rel_path: String,
    /// 部门 namespace (e.g. "dept/finance")
    pub namespace: String,
    /// hub 分配的 file_id (UUID)
    pub file_id: String,
    /// 标题 (从 sidecar.title 或 frontmatter title)
    pub title: String,
    /// kind: entity / concept / query
    pub kind: String,
    /// 原 publisher sub (e.g. "alice@ffcs.cn")
    pub published_by: String,
    /// 原 publish 时刻 (ISO-8601), 没 sidecar 时空字符串
    pub published_at: String,
    /// 本机装上时刻 (ISO-8601)
    pub installed_at: String,
    /// 文件 byte size
    pub size_bytes: u64,
}

#[tauri::command]
pub async fn list_installed_wiki_shared() -> Result<Vec<InstalledWikiSharedInfo>, String> {
    let home = catfish_home()?;
    let shared_root = home.join("wiki-shared");
    let mut out = Vec::new();

    if !shared_root.is_dir() {
        return Ok(out);
    }

    // 结构: ~/.catfish/wiki-shared/dept/<部门>/<file_id>.md
    //                                            <file_id>.meta.json
    // 我们 walk 两层目录 dept/* 然后扫每个部门里的 .md
    let dept_outer = match fs::read_dir(&shared_root) {
        Ok(d) => d,
        Err(_) => return Ok(out),  // 目录不存在不报错
    };

    for outer in dept_outer.flatten() {
        let outer_path = outer.path();
        if !outer_path.is_dir() {
            continue;
        }
        // outer_path 应该是 wiki-shared/dept
        let dept_name = outer_path.file_name().and_then(|s| s.to_str()).unwrap_or("");
        if dept_name.is_empty() {
            continue;
        }
        let dept_inner = match fs::read_dir(&outer_path) {
            Ok(d) => d,
            Err(_) => continue,
        };
        for inner in dept_inner.flatten() {
            let inner_path = inner.path();
            if !inner_path.is_dir() {
                // 单层 wiki-shared/<file_id>.md 不允许, 必须 dept/<name>/file_id.md
                continue;
            }
            let inner_name = inner_path.file_name().and_then(|s| s.to_str()).unwrap_or("");
            let namespace = format!("{}/{}", dept_name, inner_name);
            // 扫 .md 文件
            let files = match fs::read_dir(&inner_path) {
                Ok(d) => d,
                Err(_) => continue,
            };
            for file in files.flatten() {
                let path = file.path();
                if path.extension().and_then(|s| s.to_str()) != Some("md") {
                    continue;
                }
                let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("").to_string();
                if stem.is_empty() {
                    continue;
                }

                // 算 rel_path
                let rel_path = match path.strip_prefix(&home) {
                    Ok(p) => p.to_string_lossy().replace('\\', "/"),
                    Err(_) => continue,
                };
                let size_bytes = path.metadata().map(|m| m.len()).unwrap_or(0);

                // 尝试读 .meta.json sidecar
                let meta_path = inner_path.join(format!("{stem}.meta.json"));
                let mut title = stem.clone();
                let mut kind = String::from("entity");
                let mut published_by = String::new();
                let mut published_at = String::new();
                let mut installed_at = String::new();
                if let Ok(meta_text) = fs::read_to_string(&meta_path) {
                    if let Ok(meta_json) = serde_json::from_str::<serde_json::Value>(&meta_text) {
                        if let Some(t) = meta_json.get("title").and_then(|v| v.as_str()) {
                            title = t.to_string();
                        }
                        if let Some(k) = meta_json.get("kind").and_then(|v| v.as_str()) {
                            kind = k.to_string();
                        }
                        if let Some(p) = meta_json.get("published_by").and_then(|v| v.as_str()) {
                            published_by = p.to_string();
                        }
                        if let Some(p) = meta_json.get("published_at").and_then(|v| v.as_str()) {
                            published_at = p.to_string();
                        }
                        if let Some(p) = meta_json.get("installed_at").and_then(|v| v.as_str()) {
                            installed_at = p.to_string();
                        }
                    }
                }

                out.push(InstalledWikiSharedInfo {
                    rel_path,
                    namespace: namespace.clone(),
                    file_id: stem,
                    title,
                    kind,
                    published_by,
                    published_at,
                    installed_at,
                    size_bytes,
                });
            }
        }
    }

    // 按 installed_at desc 排
    out.sort_by(|a, b| b.installed_at.cmp(&a.installed_at));
    Ok(out)
}

// ============================================================
// P37 (6/5 鸿波) — wiki 全文搜索 (BM25 + scoring)
// ============================================================

#[derive(Debug, serde::Serialize)]
pub struct WikiSearchHit {
    pub rel_path: String,
    pub title: String,
    pub kind: String,
    pub score: f64,
    /// matched body snippet (~120 chars 含 query, 高亮 in UI)
    pub snippet: String,
    /// match locations: "title" / "body" / "tags"
    pub matched_in: Vec<String>,
}

/// 简化 BM25: 1) title 完全 match score +10; 2) title contains +5;
/// 3) body word count for each query token; 4) tag match +3.
/// 不是真 BM25 (没 doc freq / length norm), 但够 1000 entries 内 work.
#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_search_text(query: String) -> Result<Vec<WikiSearchHit>, String> {
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        return Ok(vec![]);
    }
    let home = catfish_home()?;
    let mut hits: Vec<WikiSearchHit> = Vec::new();
    let tokens: Vec<&str> = q.split_whitespace().collect();

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
            let info = match build_file_info(&home, &path) {
                Some(i) => i,
                None => continue,
            };
            let content = match fs::read_to_string(&path) {
                Ok(c) => c,
                Err(_) => continue,
            };
            let content_lower = content.to_lowercase();
            let title_lower = info.title.to_lowercase();
            let tags_lower: Vec<String> = info.tags.iter().map(|t| t.to_lowercase()).collect();

            let mut score = 0.0f64;
            let mut matched_in: Vec<String> = Vec::new();
            let mut hit_pos: Option<usize> = None;

            // title match
            if title_lower == q {
                score += 20.0;
                matched_in.push("title".into());
            } else if title_lower.contains(&q) {
                score += 10.0;
                matched_in.push("title".into());
            }
            // tag match (任一 tag 含 query)
            if tags_lower.iter().any(|t| t.contains(&q)) {
                score += 5.0;
                matched_in.push("tags".into());
            }
            // body: 每 token 出现次数
            for tok in &tokens {
                if tok.is_empty() {
                    continue;
                }
                let count = content_lower.matches(tok).count();
                if count > 0 {
                    score += (count as f64).min(10.0);
                    if hit_pos.is_none() {
                        hit_pos = content_lower.find(tok);
                    }
                }
            }
            if hit_pos.is_some() && !matched_in.contains(&"body".to_string()) {
                matched_in.push("body".into());
            }
            if score <= 0.0 {
                continue;
            }

            // snippet: 含 hit_pos 真**`±60 chars`**, 没 hit_pos 用 body 前 120
            let snippet = if let Some(pos) = hit_pos {
                let start = pos.saturating_sub(60);
                let end = (pos + 60).min(content.len());
                // 安全 slice (按 char boundary)
                let safe_slice = content
                    .char_indices()
                    .filter(|(i, _)| *i >= start && *i < end)
                    .map(|(_, c)| c)
                    .collect::<String>();
                format!("…{}…", safe_slice.replace('\n', " "))
            } else {
                content
                    .chars()
                    .take(120)
                    .collect::<String>()
                    .replace('\n', " ")
            };

            hits.push(WikiSearchHit {
                rel_path: info.rel_path.clone(),
                title: info.title.clone(),
                kind: info.kind.clone(),
                score,
                snippet,
                matched_in,
            });
        }
    }
    // 高 score 优先
    hits.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
    hits.truncate(50); // top-50 cap
    Ok(hits)
}

#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_read_file(rel_path: String) -> Result<WikiFileFull, String> {
    // 真**安全**: rel_path 必须 `wiki/{entities|concepts|queries}/<slug>.md`
    // 或 P3.3.18 Phase 4: `wiki-shared/dept/<部门>/<file_id>.md` (已装部门 wiki).
    // 不允许真**`..`** 真**path traversal**.
    let allowed = (rel_path.starts_with("wiki/")
        || rel_path.starts_with("wiki-shared/dept/"))
        && !rel_path.contains("..");
    if !allowed {
        return Err(format!("rel_path 真**白名单不通过: {rel_path}"));
    }
    let home = catfish_home()?;
    let abs_path = home.join(&rel_path);
    if !abs_path.is_file() {
        return Err(format!("file 不存在: {abs_path:?}"));
    }
    let content = fs::read_to_string(&abs_path)
        .map_err(|e| format!("read {abs_path:?} 失败: {e}"))?;
    // P3.3.3: tombstone 也能读 (员工想看为啥废弃), 用 inner + allow_tombstone=true
    let info = build_file_info_inner(&home, &abs_path, /* allow_tombstone */ true)
        .ok_or_else(|| format!("build_file_info 失败: {rel_path}"))?;
    let (frontmatter, body) = split_frontmatter(&content);
    Ok(WikiFileFull {
        info,
        content,
        frontmatter,
        body,
    })
}
