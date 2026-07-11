//! BL-CATFISH-WIKI-MODE P3.3.7 (6/4) — wiki write API.
//!
//! 2 个 tauri command:
//!   - wiki_create_entity_or_concept: 创建新 entity/concept file 含 frontmatter + body
//!   - wiki_update_file: 更新已有 file body (frontmatter 简单替, 复杂场景 future)

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

/// title slugify — 简单 replace 非 word char 为 `-` (跟 P1.2.2 wiki_save slugify 一致).
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

/// P3.5.132 #5 (6/29 鸿波): typed relations 真 input shape.
///
/// `#[serde(untagged)]` 让前端dual-shape 调用兼容:
///   - 旧 string: `related: ["陈鸿波", "FFCS"]` (老 caller / WikiCreateModal 简单输入)
///   - 新对象: `related: [{name: "陈鸿波", rel: "同事"}, ...]` (typed 真路径)
///
/// 都会自动 deserialize 到 RelatedInput → 渲染成 frontmatter 时 dual-shape.
#[derive(Debug, serde::Deserialize)]
#[serde(untagged)]
pub enum RelatedInput {
    /// 旧 caller / 没 rel 时, bare string (兼容)
    Bare(String),
    /// 新真 typed: `{name, rel?}` (rel 选填)
    Typed { name: String, #[serde(default)] rel: Option<String> },
}

#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_create_entity_or_concept(
    kind: String, // "entity" | "concept"
    title: String,
    subtype: String, // entity_type or concept_type
    tags: Vec<String>,
    related: Vec<RelatedInput>, // P3.5.132 #5: dual-shape, 兼容旧 string caller + 新 typed
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
    // P3.5.132 #5: dual-shape render —
    //   - rel=None → 旧 `"[[name]]"` 形态 (兼容现有 62 entities 真 frontmatter)
    //   - rel=Some → 新 `{name: ..., rel: ...}` 形态
    let related_yaml = related
        .iter()
        .map(|r| match r {
            RelatedInput::Bare(name) => {
                let cleaned = name.trim_matches('"').replace('\"', "");
                format!("\"[[{cleaned}]]\"")
            }
            RelatedInput::Typed { name, rel: None } => {
                let cleaned = name.trim_matches('"').replace('\"', "");
                format!("\"[[{cleaned}]]\"")
            }
            RelatedInput::Typed { name, rel: Some(rel_val) } => {
                let n = name.trim_matches('"').replace('\"', "");
                let r = rel_val.trim_matches('"').replace('\"', "");
                format!("{{name: \"{n}\", rel: \"{r}\"}}")
            }
        })
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

/// P3.5.132 #3 (6/29 鸿波): 删除前先报"谁引用我"防 silent dangling.
#[derive(Debug, serde::Serialize)]
pub struct AffectedFile {
    pub rel_path: String,
    pub title: String,
}

#[derive(Debug, serde::Serialize)]
pub struct WikiDeleteResult {
    /// dry_run=true 时返 affected_files, dry_run=false 时返删除结果
    pub dry_run: bool,
    /// 哪些文件 frontmatter related 真指向被删 target (dangling 会变多 N 处)
    pub affected_files: Vec<AffectedFile>,
    /// 真删时填, dry_run 时空
    pub trash_path: Option<String>,
    pub bytes: u64,
}

/// P3.5.132 #3 (6/29 鸿波): wiki_delete_file 加 dryRun 路径.
/// `dry_run`: true 时只算 affected_files 不删, false / 老 caller 真传 null 都当 false (真删).
/// Option<bool> 兼容老 caller — tauri 真 deserialize 接受 missing field 当 None.
#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_delete_file(
    rel_path: String,
    dry_run: Option<bool>,
) -> Result<WikiDeleteResult, String> {
    let dry_run = dry_run.unwrap_or(false);

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

    // P3.5.132 #3: 计算 affected_files — 扫所有 wiki, 谁 frontmatter related 真指向 target
    // (target match by title 或 slug, 跟 WikiTree.danglingMap 同款规则)
    let affected_files = compute_affected_files(&home, &rel_path)?;

    if dry_run {
        return Ok(WikiDeleteResult {
            dry_run: true,
            affected_files,
            trash_path: None,
            bytes: fs::metadata(&abs).map(|m| m.len()).unwrap_or(0),
        });
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

    Ok(WikiDeleteResult {
        dry_run: false,
        affected_files,
        trash_path: Some(format!("wiki/.trash/{trashed_name}")),
        bytes,
    })
}

/// P3.5.132 #3: 扫所有 wiki, 找 frontmatter related 真指向 target 真文件.
/// target match: target_title (frontmatter) / target_slug (filename) — 跟 WikiTree
/// danglingMap 真 match 规则一致 (case-insensitive title / slug).
fn compute_affected_files(
    catfish_home: &std::path::Path,
    target_rel_path: &str,
) -> Result<Vec<AffectedFile>, String> {
    use crate::commands::wiki_read::collect_all_wiki_md;
    let target_abs = catfish_home.join(target_rel_path);
    let target_title = read_title_from_md(&target_abs).unwrap_or_default();
    let target_slug = target_abs
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_string();
    let target_title_lower = target_title.trim().to_lowercase();
    let target_slug_lower = target_slug.trim().to_lowercase();

    let mut out = Vec::new();
    for path in collect_all_wiki_md(catfish_home) {
        if path == target_abs {
            continue; // 跳自己
        }
        let content = match fs::read_to_string(&path) {
            Ok(c) => c,
            Err(_) => continue,
        };
        // 简单匹配: frontmatter 真 related 段含 target_title / target_slug
        // body 真 [[name]] 也匹配 (跟 WikiTree danglingMap 一致)
        let lower = content.to_lowercase();
        let matched = !target_title_lower.is_empty()
            && (lower.contains(&format!("[[{target_title_lower}]]"))
                || lower.contains(&format!("\"{target_title_lower}\""))
                || lower.contains(&format!(", {target_title_lower}")))
            || (!target_slug_lower.is_empty()
                && lower.contains(&format!("[[{target_slug_lower}]]")));
        if !matched {
            continue;
        }
        let title = read_title_from_md(&path).unwrap_or_else(|| {
            path.file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .to_string()
        });
        let rel = path
            .strip_prefix(catfish_home)
            .ok()
            .map(|p| p.to_string_lossy().replace('\\', "/"))
            .unwrap_or_default();
        out.push(AffectedFile { rel_path: rel, title });
    }
    Ok(out)
}

fn read_title_from_md(path: &std::path::Path) -> Option<String> {
    let content = fs::read_to_string(path).ok()?;
    let after_first = content.strip_prefix("---")?.strip_prefix('\n')?;
    let end = after_first.find("\n---")?;
    let fm = &after_first[..end];
    for line in fm.lines() {
        if let Some(rest) = line.strip_prefix("title:") {
            return Some(rest.trim().trim_matches('"').to_string());
        }
    }
    None
}

// ============================================================
// P3.3.18 Phase 4 P2 (6/10): 卸载本机部门 wiki 副本
//
// manifesto 公理 3/4 compliant — 这是**员工自己点 button**触发, 不是中央 push.
// 走跟 wiki_delete_file 同款软删 (mv 到 .trash 目录), 同时删 .meta.json sidecar.
// 软删 30 天后员工自己用 Finder/Terminal 清.
// ============================================================

#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_uninstall_shared(rel_path: String) -> Result<WikiWriteResult, String> {
    // 路径白名单 — 只接受 wiki-shared/dept/<部门>/<file_id>.md
    if rel_path.contains("..") {
        return Err(format!("rel_path 含 ..: {rel_path}"));
    }
    if !rel_path.starts_with("wiki-shared/dept/") {
        return Err(format!(
            "只能卸载 wiki-shared/dept/<部门>/ 下文件 (拿到 {rel_path})"
        ));
    }
    if !rel_path.ends_with(".md") {
        return Err(format!("只能卸载 .md 文件: {rel_path}"));
    }

    let home = catfish_home()?;
    let abs = home.join(&rel_path);
    if !abs.is_file() {
        return Err(format!("file 不存在: {rel_path}"));
    }

    let stem = abs
        .file_stem()
        .and_then(|s| s.to_str())
        .ok_or("无法取 file stem")?
        .to_string();
    let meta_path = abs
        .parent()
        .ok_or("无 parent dir")?
        .join(format!("{stem}.meta.json"));

    // mv 到 wiki-shared/.trash/<ts>-<orig-path>.md, .meta.json 一起
    let trash_dir = home.join("wiki-shared").join(".trash");
    fs::create_dir_all(&trash_dir)
        .map_err(|e| format!("建 wiki-shared/.trash 目录失败: {e}"))?;
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);

    // 给 trash 文件名带上 dept 路径片段防撞 (a/b/c.md → ts-a_b_c.md)
    let safe_orig = rel_path.replace('/', "_");
    let trashed_name = format!("{ts}-{safe_orig}");
    let dst = trash_dir.join(&trashed_name);

    let bytes = fs::metadata(&abs).map(|m| m.len()).unwrap_or(0);
    fs::rename(&abs, &dst).map_err(|e| format!("mv {abs:?} → {dst:?} 失败: {e}"))?;

    // sidecar 一起 mv (不在意失败 — sidecar 是辅助)
    if meta_path.exists() {
        let meta_trashed = trash_dir.join(format!("{ts}-{safe_orig}.meta.json"));
        let _ = fs::rename(&meta_path, &meta_trashed);
    }

    Ok(WikiWriteResult {
        rel_path: format!("wiki-shared/.trash/{trashed_name}"),
        bytes,
        created: false,
    })
}

// ============================================================
// P3.3.18 Phase 4 P2 (6/10): 敏感词文件 onboarding
//
// catfish_wiki_publish 扫敏感词从 ~/.catfish/wiki/sensitive_terms.txt 读 (Phase 2).
// 文件不存在时 publish 不扫敏感词 (其他 3 层凭据/PII/内网 仍扫). 这命令给员工首次
// 创建默认模板, WikiTree 顶部"敏感词文件没设"提示按钮触发.
// ============================================================

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SensitiveTermsCheck {
    pub exists: bool,
    pub path: String,
    pub created: bool,
}

#[tauri::command]
pub async fn wiki_sensitive_terms_ensure() -> Result<SensitiveTermsCheck, String> {
    let home = catfish_home()?;
    let wiki_dir = home.join("wiki");
    fs::create_dir_all(&wiki_dir)
        .map_err(|e| format!("建 wiki/ 目录失败: {e}"))?;
    let path = wiki_dir.join("sensitive_terms.txt");
    if path.exists() {
        return Ok(SensitiveTermsCheck {
            exists: true,
            path: path.to_string_lossy().to_string(),
            created: false,
        });
    }

    // 默认模板 — 全部注释掉, 员工自己填
    let template = "# ~/.catfish/wiki/sensitive_terms.txt — 员工自配 wiki publish 敏感词\n\
# 一行一个词. # 开头注释跳过. 大小写不敏感, 子串匹配.\n\
# catfish_wiki_publish 扫到给 warning (不自动 redact, 员工自己拍).\n\
#\n\
# 推荐列:\n\
#   - 客户名 (e.g. FFCS / 中电福富 / ...)\n\
#   - 项目代号 (e.g. CSMM-4 / ITSS / ...)\n\
#   - 关键人姓名 (e.g. 老李 / 黄捷 / ...)\n\
#\n\
# 改了不用重启, 下次 catfish_wiki_publish 即时生效.\n\
#\n\
# === 删 # 启用对应行, 或自己加新行 ===\n\
# FFCS\n\
# 中电福富\n\
# CSMM-4\n\
# ITSS\n\
# 老李\n\
# 黄捷\n";

    fs::write(&path, template)
        .map_err(|e| format!("写 sensitive_terms.txt 失败: {e}"))?;

    Ok(SensitiveTermsCheck {
        exists: true,
        path: path.to_string_lossy().to_string(),
        created: true,
    })
}

/// 简单 today YYYY-MM-DD format — 不引 chrono dep (太重), 用 std time + hand calc.
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

/// days since epoch → (year, month, day), civil_from_days (Howard Hinnant algorithm).
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
    let safe_kept = kept_path.replace(['\n', '\r'], " ");
    let safe_filename = filename.replace(['\n', '\r'], " ");

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
